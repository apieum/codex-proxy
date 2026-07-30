"""
Traitement local des requêtes `codex-auto-review` (le « Guardian » de Codex).

Le pré-filtre tranche les cas certains sans solliciter aucun modèle. Quand il
n'en fait pas partie, rien n'est répondu localement : la requête suit son
chemin normal et, si elle échoue, Codex retombe sur l'approbation manuelle.
Ce défaut vers l'humain est voulu — jamais d'approbation par défaut.
"""
import json
from collections.abc import Iterator
from itertools import pairwise

from proxy.approval_rules import SafeCommandRules
from proxy.codex_sse import assistant_text_stream
from proxy.json_types import JSONDict, JSONValue

GUARDIAN_MODEL = "codex-auto-review"
PLANNED_ACTION_MARKER = "Planned action JSON:"

# Remplace la politique de sécurité de Codex (18 k chars) : le modèle local
# ingère à ~12 tok/s, la version d'origine demanderait des minutes par verdict.
LOCAL_REVIEW_INSTRUCTIONS = (
    "Tu juges UNE action de shell planifiée. Réponds uniquement par un objet "
    "JSON {\"outcome\":\"allow\"} si l'action est sans danger, sinon "
    "{\"outcome\":\"deny\",\"rationale\":\"<une phrase>\"}. Refuse si l'action "
    "détruit des données, exfiltre des informations, affaiblit la sécurité du "
    "poste, ou dépasse ce que l'utilisateur a demandé."
)


class _CodexVerdict:
    """Reçoit le verdict du pré-filtre et le met en forme pour Codex."""

    def __init__(self) -> None:
        self._text: str | None = None

    def allow(self) -> None:
        self._text = json.dumps({"outcome": "allow"})

    def deny(self, rationale: str) -> None:
        self._text = json.dumps({"outcome": "deny", "rationale": rationale})

    def escalate(self) -> None:
        self._text = None

    def stream(self, response_id: str) -> Iterator[bytes] | None:
        if self._text is None:
            return None
        return assistant_text_stream(text=self._text, response_id=response_id)


def local_review(body: JSONDict, rules: SafeCommandRules) -> Iterator[bytes] | None:
    action = _planned_action(body)
    if action is None:
        return None

    verdict = _CodexVerdict()
    rules.evaluate(action, verdict)
    return verdict.stream(_response_id(body))


def compact_review_request(body: JSONDict) -> JSONDict:
    """Réduit la requête escaladée à ce que le modèle local peut ingérer à temps."""
    action = _planned_action(body)
    if action is None:
        return body

    compacted: JSONDict = {
        "model": body.get("model"),
        "instructions": LOCAL_REVIEW_INSTRUCTIONS,
        "input": [
            {
                "type": "message",
                "role": "user",
                "content": [
                    {"type": "input_text", "text": f"{PLANNED_ACTION_MARKER}\n{json.dumps(action)}"}
                ],
            }
        ],
    }

    # Le schéma de sortie est ce qui rend le verdict parsable : jamais de
    # texte libre à interpréter côté proxy.
    for preserved in ("text", "stream", "prompt_cache_key"):
        if preserved in body:
            compacted[preserved] = body[preserved]

    return compacted


def _response_id(body: JSONDict) -> str:
    key = body.get("prompt_cache_key")
    return key if isinstance(key, str) else "resp_local_review"


def _planned_action(body: JSONDict) -> JSONDict | None:
    texts = list(_input_texts(body))
    for previous, current in pairwise(texts):
        if PLANNED_ACTION_MARKER in previous:
            return _parsed_object(current)
    return None


def _input_texts(body: JSONDict) -> Iterator[str]:
    items = body.get("input")
    if not isinstance(items, list):
        return
    for item in items:
        if not isinstance(item, dict):
            continue
        parts = item.get("content")
        if not isinstance(parts, list):
            continue
        for part in parts:
            if isinstance(part, dict):
                text = part.get("text")
                if isinstance(text, str):
                    yield text


def _parsed_object(text: str) -> JSONDict | None:
    try:
        parsed: JSONValue = json.loads(text)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None
