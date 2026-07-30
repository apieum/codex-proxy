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
