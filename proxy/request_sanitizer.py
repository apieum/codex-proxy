"""
Sanitising of Responses API requests before they reach Cerebras, applied by
our own reverse proxy: LiteLLM hooks do not cover /v1/responses, the only
endpoint Codex uses.
"""
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
    "web_search",
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
    Detects the empty assistant 'message' items Codex sometimes inserts
    between a function_call and its function_call_output (likely a streaming
    checkpoint). They sit in the middle of the history and break Cerebras's
    strict requirement: an assistant message carrying tool_calls must be
    IMMEDIATELY followed by the matching tool message.
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
    Guarantees every function_call is IMMEDIATELY followed by its
    function_call_output, as Cerebras requires. Codex has no such constraint
    on the Responses API side and sometimes inserts other items (empty or not)
    between the two -- which breaks the conversion to Chat Completions. So each
    output is moved right after its call, and orphan halves (a call with no
    output, or the reverse) are dropped since they trigger the same error.
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
            # Already re-placed right after its call -> skip the original
            # occurrence. If orphaned (no matching call), drop it too.
            continue

        if _is_function_call(item):
            cid = item.get("call_id")
            if not isinstance(cid, str) or cid not in matched_ids:
                # orphan call, no output anywhere -> drop it
                continue
            result.append(item)
            result.append(output_by_call_id[cid])
            continue

        result.append(item)

    return result


def _clean_orphan_tool_calls(input_items: JSONValue) -> JSONValue:
    # First drop fully empty assistant messages (pure noise), then guarantee
    # call/output adjacency for everything else.
    if not isinstance(input_items, list):
        return input_items
    filtered = [
        item for item in input_items
        if not (isinstance(item, dict) and _is_empty_assistant_message(item))
    ]
    return _fix_tool_call_adjacency(filtered)


def sanitize_body(data: JSONValue) -> JSONValue:
    """Sanitises a request body (Responses API or Chat Completions format)."""
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
