"""
Builds a Responses API reply in the shape Codex expects.

Codex requires a single event to consider a response complete:
`response.completed`. Its absence is a fatal client-side failure ("stream
closed before response.completed"), never a silent degradation -- see
`docs/CODEX_API.md` section 6.

Its `usage` must be complete: Codex deserialises it into a struct with
required fields, and an empty object closes the stream on "failed to parse
ResponseCompleted: missing field `input_tokens`".
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
