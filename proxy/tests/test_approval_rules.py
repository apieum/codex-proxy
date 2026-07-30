from proxy.approval_rules import SafeCommandRules
from proxy.json_types import JSONDict


class OutcomeSpy:
    """Collaborateur à qui les règles dictent leur verdict."""

    def __init__(self) -> None:
        self.verdict: str | None = None

    def allow(self) -> None:
        self.verdict = "allow"

    def deny(self, rationale: str) -> None:
        self.verdict = "deny"

    def escalate(self) -> None:
        self.verdict = "escalate"


def test_safe_command_prefix_is_allowed() -> None:
    outcome = OutcomeSpy()
    action: JSONDict = {"command": ["/usr/bin/zsh", "-lc", "git add proxy/harness.py"]}

    SafeCommandRules(safe_prefixes=(("git", "add"),)).evaluate(action, outcome)

    assert outcome.verdict == "allow"
