"""
Reading one turn produced under a constrained output schema.

The model is given a schema with exactly two shapes, so narrating an action
instead of performing it is not something it can express. Anything that does
not fit is handed back whole: the intent behind free text is never guessed,
since these turns become shell commands.
"""
import json
from typing import Protocol

from proxy.json_types import JSONDict, JSONValue


class TurnOutcome(Protocol):
    def message(self, text: str) -> None: ...
    def tool_call(self, name: str, arguments: JSONDict) -> None: ...
    def unparsable(self, raw: str) -> None: ...


def deliver(raw: str, outcome: TurnOutcome) -> None:
    turn = _parsed_object(raw)
    if turn is None:
        outcome.unparsable(raw)
        return

    kind = turn.get("kind")
    if kind == "message":
        _deliver_message(turn, outcome, raw)
    elif kind == "tool_call":
        _deliver_tool_call(turn, outcome, raw)
    else:
        outcome.unparsable(raw)


def _deliver_message(turn: JSONDict, outcome: TurnOutcome, raw: str) -> None:
    text = turn.get("text")
    if isinstance(text, str):
        outcome.message(text)


def _deliver_tool_call(turn: JSONDict, outcome: TurnOutcome, raw: str) -> None:
    name = turn.get("tool")
    if not isinstance(name, str) or not name:
        outcome.unparsable(raw)
        return

    arguments = _arguments(turn.get("arguments"))
    if arguments is None:
        outcome.unparsable(raw)
        return
    outcome.tool_call(name, arguments)


def _arguments(value: JSONValue) -> JSONDict | None:
    """The schema carries them serialised; callers still receive a mapping."""
    if isinstance(value, dict):
        return value
    if value is None:
        return {}
    if isinstance(value, str):
        return _parsed_object(value)
    return None


def _parsed_object(raw: str) -> JSONDict | None:
    try:
        parsed: JSONValue = json.loads(raw)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None
