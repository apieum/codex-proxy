"""
Logique d'assainissement des requêtes Responses API avant transmission à
Cerebras. Exposée en fonction pure `sanitize_body()` pour être réutilisable
à la fois par le hook LiteLLM (chat/completions) et par notre reverse-proxy
maison (qui couvre /v1/responses, non couvert par les hooks LiteLLM).
"""
from typing import Any

from litellm.caching.dual_cache import DualCache
from litellm.integrations.custom_logger import CustomLogger

from proxy.json_types import JSONDict, JSONValue

FIELDS_TO_STRIP = [
    "metadata",
    "client_metadata",
    "store",
    "previous_response_id",
    "truncation",
    "background",
    "include",
    "max_tokens",
    "n",
    "parallel_tool_calls",
]

FORCED_REASONING_EFFORT = "medium"

NON_STANDARD_TOOL_TYPES = {
    "namespace",
    "local_shell",
    "custom",
    "computer_use",
    "code_interpreter",
    "file_search",
    "image_generation",
    "web_search_preview",
}


def _clean_tools(tools: JSONValue) -> JSONValue:
    if not isinstance(tools, list):
        return tools
    cleaned: list[JSONValue] = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        if tool.get("type") in NON_STANDARD_TOOL_TYPES:
            continue
        cleaned.append(tool)
    return cleaned


def _is_empty_assistant_message(item: JSONDict) -> bool:
    """
    Détecte les items 'message' assistant au contenu vide que Codex insère
    parfois entre un function_call et son function_call_output (probablement
    un point de contrôle de streaming). Ils s'intercalent dans l'historique
    et cassent l'exigence stricte de Cerebras : un message assistant avec
    tool_calls doit être IMMÉDIATEMENT suivi du message tool correspondant.
    """
    if item.get("type") != "message" or item.get("role") != "assistant":
        return False
    content = item.get("content")
    if not content:
        return True
    if isinstance(content, str):
        return content.strip() == ""
    if isinstance(content, list):
        for part in content:
            text = part.get("text", "") if isinstance(part, dict) else ""
            if isinstance(text, str) and text.strip():
                return False
        return True
    return False


def _is_function_call(item: JSONDict) -> bool:
    return item.get("type") == "function_call" or (
        "call_id" in item and "name" in item and "arguments" in item
    )


def _is_function_call_output(item: JSONDict) -> bool:
    return item.get("type") == "function_call_output" or (
        "call_id" in item and "output" in item
    )


def _fix_tool_call_adjacency(input_items: JSONValue) -> JSONValue:
    """
    Garantit que chaque function_call est IMMÉDIATEMENT suivi de son
    function_call_output, comme l'exige Cerebras. Codex n'a pas cette
    contrainte côté Responses API et intercale parfois d'autres items
    (messages, vides ou non) entre les deux -- ce qui casse la conversion
    vers Chat Completions. On déplace donc chaque output juste après son
    call, et on retire les paires orphelines (call sans output, ou
    inversement) qui provoqueraient la même erreur.
    """
    if not isinstance(input_items, list):
        return input_items

    output_by_call_id: dict[str, JSONDict] = {}
    call_ids: set[str] = set()
    for item in input_items:
        if not isinstance(item, dict):
            continue
        cid = item.get("call_id")
        if not isinstance(cid, str):
            continue
        if _is_function_call(item):
            call_ids.add(cid)
        elif _is_function_call_output(item):
            output_by_call_id[cid] = item

    matched_ids = call_ids & output_by_call_id.keys()

    result: list[JSONValue] = []
    for item in input_items:
        if not isinstance(item, dict):
            result.append(item)
            continue

        if _is_function_call_output(item):
            # Déjà replacé juste après son call -> on saute l'occurrence
            # d'origine. Si jamais orphelin (pas de call correspondant),
            # on le retire aussi.
            continue

        if _is_function_call(item):
            cid = item.get("call_id")
            if not isinstance(cid, str) or cid not in matched_ids:
                # call orphelin, sans output nulle part -> on le retire
                continue
            result.append(item)
            result.append(output_by_call_id[cid])
            continue

        result.append(item)

    return result


def _clean_orphan_tool_calls(input_items: JSONValue) -> JSONValue:
    # Retire d'abord les messages assistant totalement vides (bruit inutile),
    # puis garantit l'adjacence call/output pour tout le reste.
    if not isinstance(input_items, list):
        return input_items
    filtered = [
        item for item in input_items
        if not (isinstance(item, dict) and _is_empty_assistant_message(item))
    ]
    return _fix_tool_call_adjacency(filtered)


def sanitize_body(data: JSONValue) -> JSONValue:
    """Assainit un corps de requête (format Responses API ou Chat Completions)."""
    if not isinstance(data, dict):
        return data

    for field in FIELDS_TO_STRIP:
        data.pop(field, None)

    if "reasoning" in data:
        data["reasoning"] = {"effort": FORCED_REASONING_EFFORT}
    if "reasoning_effort" in data:
        data["reasoning_effort"] = FORCED_REASONING_EFFORT

    if "tools" in data:
        data["tools"] = _clean_tools(data["tools"])

    if "input" in data:
        data["input"] = _clean_orphan_tool_calls(data["input"])

    return data


class CerebrasSanitizer(CustomLogger):
    """
    Conservé pour /chat/completions, /embeddings, /image/generation — les
    seuls endpoints réellement couverts par ce hook LiteLLM. Inoffensif à
    laisser branché même si Codex n'y passe pas.
    """
    async def async_pre_call_hook(
        self,
        user_api_key_dict: Any,  # signature imposée par litellm.CustomLogger, non typée en amont
        cache: DualCache,
        data: JSONDict,
        call_type: str,
    ) -> JSONDict | None:
        result = sanitize_body(data)
        return result if isinstance(result, dict) else None


proxy_handler_instance = CerebrasSanitizer()
