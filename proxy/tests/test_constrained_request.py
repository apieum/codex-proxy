"""
Rewriting a request so the model cannot narrate instead of acting.

Constrained decoding replaces native tool calling: the provider will only emit
text matching the schema, so `tools` becomes dead weight and the tool
signatures have to travel inside `instructions` instead.

Measured on a real capture: the 12 tool schemas weigh ~2,700 tokens and are
already sent on every request, so moving them is close to cost-neutral.
"""
import json

from proxy.constrained_request import constrain_output
from proxy.json_types import JSONDict

CODEX_REQUEST: JSONDict = {
    "model": "cerebras-gpt-oss-120b",
    "instructions": "You are a coding agent.",
    "tools": [
        {
            "type": "function",
            "name": "exec_command",
            "description": "Run a shell command",
            "parameters": {"type": "object", "properties": {"cmd": {"type": "string"}}},
        },
        {"type": "function", "name": "request_user_input"},
    ],
    "input": [],
}


def _constrained() -> JSONDict:
    return constrain_output(json.loads(json.dumps(CODEX_REQUEST)))


def test_native_tools_are_removed() -> None:
    """They cannot be called once generation is constrained to the schema."""
    assert "tools" not in _constrained()


def test_an_output_schema_is_imposed() -> None:
    text = _constrained()["text"]
    assert isinstance(text, dict)
    fmt = text["format"]
    assert isinstance(fmt, dict)

    assert fmt["type"] == "json_schema"


def test_the_schema_admits_exactly_two_kinds_of_turn() -> None:
    text = _constrained()["text"]
    assert isinstance(text, dict)

    assert '"message"' in json.dumps(text) and '"tool_call"' in json.dumps(text)


def test_every_tool_name_is_described_to_the_model() -> None:
    """Removed from `tools`, they must still be known or they can never be called."""
    instructions = _constrained()["instructions"]
    assert isinstance(instructions, str)

    assert "exec_command" in instructions and "request_user_input" in instructions


def test_a_tool_parameter_schema_reaches_the_model() -> None:
    instructions = _constrained()["instructions"]
    assert isinstance(instructions, str)

    assert "cmd" in instructions


def test_the_original_instructions_are_preserved() -> None:
    instructions = _constrained()["instructions"]
    assert isinstance(instructions, str)

    assert "You are a coding agent." in instructions


def test_a_request_without_tools_is_left_untouched() -> None:
    """Nothing to constrain: the auto-review path already has its own schema."""
    unchanged: JSONDict = {"model": "cerebras-gpt-oss-120b", "input": []}

    assert constrain_output(unchanged) == unchanged
