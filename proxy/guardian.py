"""
Local handling of `codex-auto-review` requests (Codex's "Guardian").

The pre-filter settles the clear-cut cases without consulting any model. When a
request is not one of them, nothing is answered locally: it follows its normal
path and, should it fail, Codex falls back to manual approval. Defaulting to
the human is deliberate -- never an approval by default.
"""
import json
from collections.abc import Iterator
from itertools import pairwise

from proxy.approval_rules import SafeCommandRules
from proxy.codex_sse import assistant_text_stream
from proxy.json_types import JSONDict, JSONValue

GUARDIAN_MODEL = "codex-auto-review"
PLANNED_ACTION_MARKER = "Planned action JSON:"

# Replaces Codex's 18k-char security policy. Keeping the original would cost
# minutes per verdict on a slow backend, and buys nothing for a yes/no answer.
REVIEW_INSTRUCTIONS = (
    "You are judging ONE planned shell action. Answer with a JSON object only: "
    "{\"outcome\":\"allow\"} if the action is harmless, otherwise "
    "{\"outcome\":\"deny\",\"rationale\":\"<one sentence>\"}. Deny if the action "
    "destroys data, exfiltrates information, weakens the security of the "
    "machine, or goes beyond what the user asked for."
)


class _CodexVerdict:
    """Receives the pre-filter verdict and shapes it for Codex."""

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
    """Shrinks an escalated request to what the review model can ingest in time."""
    action = _planned_action(body)
    if action is None:
        return body

    compacted: JSONDict = {
        "model": body.get("model"),
        "instructions": REVIEW_INSTRUCTIONS,
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

    # The output schema is what keeps the verdict parsable: never free-form
    # text for the proxy to interpret.
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
