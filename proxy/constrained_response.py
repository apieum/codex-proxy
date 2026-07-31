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
from collections.abc import Callable, Collection, Iterable, Iterator

from proxy.codex_sse import assistant_text_stream, function_call_stream
from proxy.constrained_turn import deliver
from proxy.json_types import JSONDict, JSONValue

TEXT_DELTA = "response.output_text.delta"
TEXT_DONE = "response.output_text.done"


class _RewrittenTurn:
    """Receives the turn's case and holds the stream that carries it to Codex."""

    def __init__(
        self,
        response_id: str,
        call_id: str,
        report: Callable[[str], None],
        declared_tools: Collection[str],
    ) -> None:
        self._response_id = response_id
        self._call_id = call_id
        self._report = report
        self._declared_tools = declared_tools
        self._stream: Iterator[bytes] = iter(())

    def message(self, text: str) -> None:
        self._stream = assistant_text_stream(text=text, response_id=self._response_id)

    def tool_call(self, name: str, arguments: JSONDict) -> None:
        if name not in self._declared_tools:
            # Codex cannot run a tool it never declared: the call vanishes, the
            # state never changes, and the model retries the same turn forever.
            self._report(
                f"the model called {name!r}, which Codex never declared; shown "
                "as a message, not executed"
            )
            self.message(json.dumps({"tool": name, "arguments": arguments}))
            return

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
    declared_tools: Collection[str],
) -> Iterator[bytes]:
    turn = _RewrittenTurn(response_id, call_id, report, declared_tools)
    deliver(_answered_text(upstream), turn)
    return turn.frames()


def _answered_text(upstream: Iterable[bytes]) -> str:
    """
    Prefer the completed text over the deltas.

    Measured against Cerebras: 3 turns in 26 streamed deltas that were missing
    the opening characters, while `output_text.done` carried the whole answer.
    """
    events = list(_events(upstream))
    for event in reversed(events):
        if event.get("type") == TEXT_DONE and isinstance(event.get("text"), str):
            return str(event["text"])
    return "".join(
        str(e["delta"])
        for e in events
        if e.get("type") == TEXT_DELTA and isinstance(e.get("delta"), str)
    )


def _events(upstream: Iterable[bytes]) -> Iterator[JSONDict]:
    # Join first: network chunks land where TCP decides, so splitting each one
    # on its own drops any frame that straddles a boundary.
    for line in b"".join(upstream).split(b"\n\n"):
        event = _parsed_event(line)
        if event is not None:
            yield event


def _parsed_event(line: bytes) -> JSONDict | None:
    if not line.startswith(b"data: "):
        return None
    try:
        parsed: JSONValue = json.loads(line.removeprefix(b"data: "))
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None
