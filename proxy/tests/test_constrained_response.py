"""
Rewriting the upstream response into what Codex executes.

Under a constrained schema the provider returns the turn as plain
`output_text`, so Codex would only ever display it. The proxy turns it back
into the protocol Codex acts on: a `function_call` when the model asked for a
tool, a message otherwise.

Text that does not fit the schema is carried through as a message and never as
a call. Guessing a command out of prose is the one thing this must not do:
these turns are auto-approved before they run.
"""
import json

from proxy.constrained_response import rewrite_constrained_response
from proxy.json_types import JSONDict


def _upstream(text: str) -> list[bytes]:
    """The provider streams the constrained JSON as ordinary text deltas."""
    frames = [b'data: {"type": "response.created", "response": {"id": "up_1"}}\n\n']
    for piece in (text[i : i + 7] for i in range(0, len(text), 7)):
        frames.append(
            b"data: "
            + json.dumps({"type": "response.output_text.delta", "delta": piece}).encode()
            + b"\n\n"
        )
    frames.append(b'data: {"type": "response.completed", "response": {"id": "up_1"}}\n\n')
    return frames


def _rewritten(text: str) -> list[JSONDict]:
    chunks = rewrite_constrained_response(
        _upstream(text), response_id="resp_1", call_id="call_1"
    )
    events: list[JSONDict] = []
    for chunk in chunks:
        for line in chunk.split(b"\n\n"):
            if line.startswith(b"data: "):
                payload = json.loads(line.removeprefix(b"data: "))
                assert isinstance(payload, dict)
                events.append(payload)
    return events


def _done_items(events: list[JSONDict]) -> list[JSONDict]:
    items = []
    for e in events:
        if e["type"] == "response.output_item.done":
            item = e["item"]
            assert isinstance(item, dict)
            items.append(item)
    return items


TOOL_TURN = json.dumps(
    {"kind": "tool_call", "tool": "exec_command", "arguments": {"cmd": "git status"}}
)
MESSAGE_TURN = json.dumps({"kind": "message", "text": "Nothing left to do."})


def test_a_tool_turn_becomes_an_executable_call() -> None:
    assert _done_items(_rewritten(TOOL_TURN))[0]["type"] == "function_call"


def test_the_call_carries_the_arguments_the_model_chose() -> None:
    arguments = _done_items(_rewritten(TOOL_TURN))[0]["arguments"]
    assert isinstance(arguments, str)

    assert json.loads(arguments) == {"cmd": "git status"}


def test_a_message_turn_stays_a_message() -> None:
    assert _done_items(_rewritten(MESSAGE_TURN))[0]["type"] == "message"


def test_narrated_text_is_never_turned_into_a_call() -> None:
    """The failure this whole mechanism exists for: describing is not running."""
    narration = "I'll stage the files and commit: `git add -A`"

    assert all(item["type"] != "function_call" for item in _done_items(_rewritten(narration)))


def test_narrated_text_still_reaches_the_user() -> None:
    """Dropping it would hide the turn entirely; it must stay visible."""
    narration = "I'll stage the files and commit: `git add -A`"

    assert narration in json.dumps(_done_items(_rewritten(narration)))


def test_the_rewritten_stream_ends_with_the_mandatory_completed_event() -> None:
    assert _rewritten(TOOL_TURN)[-1]["type"] == "response.completed"
