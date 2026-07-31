"""
Sanitising of the tools Codex declares.

Only `function` tools have a Chat Completions equivalent. The rest belong to
OpenAI's hosted infrastructure and must not reach Cerebras -- measured on a
real capture, 80 of 80 requests were carrying `web_search` through untouched.
"""
import json

from proxy.json_types import JSONDict
from proxy.request_sanitizer import sanitize_body


def _tool_types(body: JSONDict) -> list[str]:
    sanitised = sanitize_body(json.loads(json.dumps(body)))
    assert isinstance(sanitised, dict)
    tools = sanitised["tools"]
    assert isinstance(tools, list)
    return [t["type"] for t in tools if isinstance(t, dict) and isinstance(t["type"], str)]


def test_the_web_search_tool_is_stripped() -> None:
    body: JSONDict = {
        "tools": [
            {"type": "function", "name": "exec_command"},
            {"type": "web_search", "external_web_access": False},
        ]
    }

    assert _tool_types(body) == ["function"]


def test_a_namespace_tool_is_stripped() -> None:
    body: JSONDict = {
        "tools": [
            {"type": "function", "name": "exec_command"},
            {"type": "namespace", "name": "multi_agent_v1", "tools": []},
        ]
    }

    assert _tool_types(body) == ["function"]


def test_function_tools_are_carried_through_unchanged() -> None:
    """Stripping must never reach the tools the agent actually needs."""
    tool: JSONDict = {
        "type": "function",
        "name": "exec_command",
        "parameters": {"type": "object", "properties": {"cmd": {"type": "string"}}},
    }
    sanitised = sanitize_body({"tools": [json.loads(json.dumps(tool))]})
    assert isinstance(sanitised, dict)

    assert sanitised["tools"] == [tool]


def test_the_tool_search_tool_is_stripped() -> None:
    """Cerebras 400s on it: "tools.N.function: Field required"."""
    body: JSONDict = {
        "tools": [
            {"type": "function", "name": "exec_command"},
            {"type": "tool_search", "description": "…", "execution": {}, "parameters": {}},
        ]
    }

    assert _tool_types(body) == ["function"]


def test_a_custom_tool_survives_because_litellm_converts_it() -> None:
    """
    `apply_patch` arrives as a freeform `custom` tool. Stripping it left Codex
    able to run the tool while the model was never told it existed.
    """
    body: JSONDict = {
        "tools": [
            {"type": "function", "name": "exec_command"},
            {
                "type": "custom",
                "name": "apply_patch",
                "description": "Edit files",
                "format": {"type": "grammar", "syntax": "lark", "definition": "start: x"},
            },
        ]
    }

    assert "custom" in _tool_types(body)


def _input_types(body: JSONDict) -> list[str]:
    sanitised = sanitize_body(json.loads(json.dumps(body)))
    assert isinstance(sanitised, dict)
    items = sanitised["input"]
    assert isinstance(items, list)
    return [i["type"] for i in items if isinstance(i, dict) and isinstance(i["type"], str)]


CUSTOM_PAIR: JSONDict = {
    "input": [
        {
            "type": "custom_tool_call",
            "call_id": "c1",
            "name": "apply_patch",
            "input": "*** Begin Patch",
            "status": "completed",
        },
        {"type": "custom_tool_call_output", "call_id": "c1", "output": "done"},
    ]
}


def test_a_custom_tool_output_is_not_dropped_as_an_orphan() -> None:
    """
    Cerebras 400s otherwise: "tool_call_ids did not have response messages".
    The call was unrecognised while its output was, so the pair was broken.
    """
    assert "custom_tool_call_output" in _input_types(CUSTOM_PAIR)


def test_a_custom_tool_call_keeps_its_output_adjacent() -> None:
    assert _input_types(CUSTOM_PAIR) == ["custom_tool_call", "custom_tool_call_output"]


def test_a_custom_call_without_its_output_is_dropped() -> None:
    """An unanswered call is exactly what Cerebras refuses."""
    orphan: JSONDict = {
        "input": [{"type": "custom_tool_call", "call_id": "c9", "name": "apply_patch"}]
    }

    assert _input_types(orphan) == []
