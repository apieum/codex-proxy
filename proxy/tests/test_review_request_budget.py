"""
Shrinking the auto-review request before escalating it.

Codex sends ~8,000 tokens (security policy, AGENTS.md, a 59-part transcript).
Ingested at 11.65 tok/s on the measured hardware, that is ~11.5 min before the
first token -- far past Codex's idle timeout. The workable budget is ~300
non-cached tokens, i.e. ~1,200 characters.
"""
import json

from proxy.guardian import compact_review_request
from proxy.json_types import JSONDict

LOCAL_PROMPT_BUDGET_CHARS = 1200

PLANNED_ACTION = {
    "command": ["/usr/bin/zsh", "-lc", "npm publish"],
    "cwd": "/home/user/project",
    "justification": "Publish the fixed release",
    "tool": "exec_command",
}

OUTPUT_SCHEMA: JSONDict = {
    "format": {
        "type": "json_schema",
        "name": "codex_output_schema",
        "schema": {"properties": {"outcome": {"enum": ["allow", "deny"]}}},
    }
}


def _codex_review_request() -> JSONDict:
    return {
        "model": "codex-auto-review",
        "instructions": "FULL SECURITY POLICY. " * 600,
        "tools": [{"type": "function", "name": "exec_command"}],
        "text": OUTPUT_SCHEMA,
        "input": [
            {
                "type": "message",
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "# AGENTS.md instructions " + "rule " * 400},
                    {"type": "input_text", "text": ">>> TRANSCRIPT START"},
                    {"type": "input_text", "text": "[1] tool exec_command result: " + "output " * 400},
                    {"type": "input_text", "text": "Planned action JSON:"},
                    {"type": "input_text", "text": json.dumps(PLANNED_ACTION)},
                    {"type": "input_text", "text": ">>> APPROVAL REQUEST END"},
                ],
            },
        ],
    }


def test_compacted_request_fits_the_local_prompt_budget() -> None:
    compacted = compact_review_request(_codex_review_request())

    assert len(json.dumps(compacted)) < LOCAL_PROMPT_BUDGET_CHARS


def test_compacted_request_still_carries_the_planned_command() -> None:
    compacted = compact_review_request(_codex_review_request())

    assert "npm publish" in json.dumps(compacted)


def test_compacted_request_drops_the_tools_a_small_model_cannot_use() -> None:
    compacted = compact_review_request(_codex_review_request())

    assert "tools" not in compacted


def test_compacted_request_keeps_the_output_schema_that_constrains_the_verdict() -> None:
    compacted = compact_review_request(_codex_review_request())

    assert compacted["text"] == OUTPUT_SCHEMA


def test_a_request_without_planned_action_is_left_untouched() -> None:
    """With no action to judge, nothing justifies rewriting the request."""
    unchanged: JSONDict = {"model": "codex-auto-review", "input": []}

    assert compact_review_request(unchanged) == unchanged
