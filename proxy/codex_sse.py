"""
Fabrication d'une réponse au format Responses API, telle que Codex l'attend.

Codex n'exige qu'un seul événement pour considérer une réponse aboutie :
`response.completed`. Son absence provoque un échec fatal côté client
(« stream closed before response.completed »), jamais une dégradation
silencieuse — voir `docs/API_CODEX.md` §6.
"""
import json
from collections.abc import Iterator

from proxy.json_types import JSONDict


def assistant_text_stream(text: str, response_id: str) -> Iterator[bytes]:
    yield _frame({"type": "response.created", "response": {"id": response_id}})
    yield _frame(
        {
            "type": "response.output_item.done",
            "item": {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text}],
            },
        }
    )
    yield _frame(
        {
            "type": "response.completed",
            "response": {"id": response_id, "usage": {}, "end_turn": True},
        }
    )


def _frame(payload: JSONDict) -> bytes:
    return b"data: " + json.dumps(payload).encode() + b"\n\n"
