"""
Fabrication d'une réponse au format Responses API, telle que Codex l'attend.

Codex n'exige qu'un seul événement pour considérer une réponse aboutie :
`response.completed`. Son absence provoque un échec fatal côté client
(« stream closed before response.completed »), jamais une dégradation
silencieuse — voir `docs/API_CODEX.md` §6.

Son `usage` doit être complet : Codex le désérialise dans une structure aux
champs obligatoires, et un objet vide referme le flux sur
« failed to parse ResponseCompleted: missing field `input_tokens` ».
"""
import json
from collections.abc import Iterator

from proxy.json_types import JSONDict

NO_TOKENS_CONSUMED: JSONDict = {
    "input_tokens": 0,
    "cached_input_tokens": 0,
    "output_tokens": 0,
    "reasoning_output_tokens": 0,
    "total_tokens": 0,
}


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
            "response": {"id": response_id, "usage": NO_TOKENS_CONSUMED, "end_turn": True},
        }
    )


def _frame(payload: JSONDict) -> bytes:
    return b"data: " + json.dumps(payload).encode() + b"\n\n"
