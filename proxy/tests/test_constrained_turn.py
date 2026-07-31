"""
The contract a constrained turn must honour.

gpt-oss-120b intermittently narrates an action instead of calling the tool --
observed on a real session, 2 turns out of 6, one of them claiming five
commits that were never made. Constraining generation to a schema removes the
choice: a turn is either a message or a tool call, and nothing else can be
emitted.

The turn dictates its case to a collaborator rather than handing back a value
to branch on, so a new case means a new method, not a new condition.
"""
import json

from proxy.constrained_turn import deliver
from proxy.json_types import JSONDict


class TurnSpy:
    """The collaborator a turn dictates its case to."""

    def __init__(self) -> None:
        self.said: str | None = None
        self.called: tuple[str, JSONDict] | None = None
        self.rejected: str | None = None

    def message(self, text: str) -> None:
        self.said = text

    def tool_call(self, name: str, arguments: JSONDict) -> None:
        self.called = (name, arguments)

    def unparsable(self, raw: str) -> None:
        self.rejected = raw


def test_a_message_turn_is_said() -> None:
    turn = TurnSpy()

    deliver(json.dumps({"kind": "message", "text": "Nothing left to do."}), turn)

    assert turn.said == "Nothing left to do."


def test_a_tool_call_turn_carries_its_name() -> None:
    turn = TurnSpy()

    deliver(
        json.dumps({"kind": "tool_call", "tool": "exec_command", "arguments": {"cmd": "ls"}}),
        turn,
    )

    assert turn.called is not None and turn.called[0] == "exec_command"


def test_a_tool_call_turn_carries_its_arguments() -> None:
    turn = TurnSpy()

    deliver(
        json.dumps({"kind": "tool_call", "tool": "exec_command", "arguments": {"cmd": "ls -la"}}),
        turn,
    )

    assert turn.called is not None and turn.called[1] == {"cmd": "ls -la"}


def test_a_narrated_turn_is_never_taken_for_a_tool_call() -> None:
    """The exact failure this whole mechanism exists to make impossible."""
    turn = TurnSpy()

    deliver("I'll stage the files and commit: `git add -A`", turn)

    assert turn.called is None


def test_an_unparsable_turn_is_handed_back_whole() -> None:
    """Never guess an intent: the caller decides what to do with the raw text."""
    turn = TurnSpy()

    deliver("I'll stage the files", turn)

    assert turn.rejected == "I'll stage the files"


def test_a_tool_call_without_a_name_is_not_executed() -> None:
    turn = TurnSpy()

    deliver(json.dumps({"kind": "tool_call", "arguments": {"cmd": "ls"}}), turn)

    assert turn.called is None


def test_a_message_turn_without_text_says_nothing() -> None:
    turn = TurnSpy()

    deliver(json.dumps({"kind": "message"}), turn)

    assert turn.said is None
