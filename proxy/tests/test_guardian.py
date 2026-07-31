import json

from proxy.approval_rules import SafeCommandRules
from proxy.guardian import local_review
from proxy.json_types import JSONDict, JSONValue


def _guardian_request_with_history(*shell_commands: str) -> JSONDict:
    """Codex resends past actions in the transcript; the judged one comes last."""
    parts: list[JSONValue] = [{"type": "input_text", "text": ">>> TRANSCRIPT START"}]
    for shell_command in shell_commands:
        planned = json.dumps(
            {"command": ["/usr/bin/zsh", "-lc", shell_command], "cwd": "/home/user/project"}
        )
        parts.append({"type": "input_text", "text": "Planned action JSON:"})
        parts.append({"type": "input_text", "text": planned})
    parts.append({"type": "input_text", "text": ">>> APPROVAL REQUEST END"})
    return {
        "model": "codex-auto-review",
        "input": [{"type": "message", "role": "user", "content": parts}],
    }


def _guardian_request(shell_command: str) -> JSONDict:
    planned = json.dumps(
        {
            "command": ["/usr/bin/zsh", "-lc", shell_command],
            "cwd": "/home/user/project",
            "tool": "exec_command",
        }
    )
    return {
        "model": "codex-auto-review",
        "input": [
            {
                "type": "message",
                "role": "developer",
                "content": [{"type": "input_text", "text": "<permissions instructions>"}],
            },
            {
                "type": "message",
                "role": "user",
                "content": [
                    {"type": "input_text", "text": ">>> TRANSCRIPT START"},
                    {"type": "input_text", "text": "[1] user: clean things up"},
                    {"type": "input_text", "text": "Planned action JSON:"},
                    {"type": "input_text", "text": planned},
                    {"type": "input_text", "text": ">>> APPROVAL REQUEST END"},
                ],
            },
        ],
    }


def _verdict_text(chunks: list[bytes]) -> str:
    for chunk in chunks:
        payload = json.loads(chunk.removeprefix(b"data: "))
        if payload["type"] == "response.output_item.done":
            text: str = payload["item"]["content"][0]["text"]
            return text
    raise AssertionError("le flux ne porte aucun item de message")


def test_safe_command_is_answered_with_an_allow_verdict() -> None:
    rules = SafeCommandRules(safe_prefixes=(("git", "add"),))

    stream = local_review(_guardian_request("git add file.py"), rules)

    assert json.loads(_verdict_text(list(stream or [])))["outcome"] == "allow"


def test_destructive_command_is_answered_with_a_deny_verdict() -> None:
    rules = SafeCommandRules(safe_prefixes=(), denied_prefixes=(("rm", "-rf"),))

    stream = local_review(_guardian_request("rm -rf /home/user"), rules)

    assert json.loads(_verdict_text(list(stream or [])))["outcome"] == "deny"


def test_grey_zone_is_not_answered_locally() -> None:
    rules = SafeCommandRules(safe_prefixes=(("git", "add"),))

    stream = local_review(_guardian_request("npm publish"), rules)

    assert stream is None


def test_the_judged_action_is_the_last_of_the_transcript() -> None:
    """Observed in production: an old benign entry got judged instead of the new one."""
    rules = SafeCommandRules(safe_prefixes=(("git", "status"),))

    stream = local_review(
        _guardian_request_with_history("git status --porcelain", "npm publish"), rules
    )

    assert stream is None


def test_an_earlier_safe_action_cannot_approve_a_destructive_one() -> None:
    """The exact exploit: `git status` first, `rm -rf` second, verdict was allow."""
    rules = SafeCommandRules(
        safe_prefixes=(("git", "status"),), denied_prefixes=(("rm", "-rf"),)
    )

    stream = local_review(
        _guardian_request_with_history(
            "git status --porcelain", "rm -rf .git && git init"
        ),
        rules,
    )

    assert json.loads(_verdict_text(list(stream or [])))["outcome"] == "deny"
