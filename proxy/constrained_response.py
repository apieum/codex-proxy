"""
Turning the upstream answer back into the protocol Codex acts on.

Under a constrained schema the provider returns the whole turn as ordinary
`output_text`, which Codex would merely display. Here it becomes a
`function_call` when the model asked for a tool, and a message otherwise.

Text that does not fit the schema is carried through as a message: visible to
the user, never executed. Guessing a command out of prose is the one thing
this must not do, since these calls are auto-approved before they run.
"""
import json
from collections.abc import Callable, Iterable, Iterator

from proxy.codex_sse import assistant_text_stream, function_call_stream
from proxy.constrained_turn import deliver
from proxy.json_types import JSONDict, JSONValue

TEXT_DELTA = "response.output_text.delta"


class _RewrittenTurn:
    """Receives the turn's case and holds the stream that carries it to Codex."""

    def __init__(self, response_id: str, call_id: str, report: Callable[[str], None]) -> None:
        self._response_id = response_id
        self._call_id = call_id
        self._report = report
        self._stream: Iterator[bytes] = iter(())

    def message(self, text: str) -> None:
        self._stream = assistant_text_stream(text=text, response_id=self._response_id)

    def tool_call(self, name: str, arguments: JSONDict) -> None:
        self._stream = function_call_stream(
            name=name,
            arguments=arguments,
            call_id=self._call_id,
            response_id=self._response_id,
        )

    def unparsable(self, raw: str) -> None:
        # An off-schema turn means constrained decoding is not in effect --
        # staying silent would hide that the whole mechanism is inert.
        self._report(
            "the model answered off-schema, so constrained decoding is not in "
            f"effect; shown as a message, not executed: {raw[:120]!r}"
        )
        # Shown, not run: the user sees exactly what the model produced.
        self.message(raw)

    def frames(self) -> Iterator[bytes]:
        return self._stream


def rewrite_constrained_response(
    upstream: Iterable[bytes],
    response_id: str,
    call_id: str,
    report: Callable[[str], None],
) -> Iterator[bytes]:
    turn = _RewrittenTurn(response_id, call_id, report)
    deliver("".join(_text_deltas(upstream)), turn)
    return turn.frames()


def _text_deltas(upstream: Iterable[bytes]) -> Iterator[str]:
    # Join first: network chunks land where TCP decides, so splitting each one
    # on its own drops any frame that straddles a boundary.
    for line in b"".join(upstream).split(b"\n\n"):
        event = _parsed_event(line)
        if event is None or event.get("type") != TEXT_DELTA:
            continue
        delta = event.get("delta")
        if isinstance(delta, str):
            yield delta


def _parsed_event(line: bytes) -> JSONDict | None:
    if not line.startswith(b"data: "):
        return None
    try:
        parsed: JSONValue = json.loads(line.removeprefix(b"data: "))
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None
