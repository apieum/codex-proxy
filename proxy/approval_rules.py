"""
Deterministic pre-filter for the actions Codex submits to auto-review.

Decides locally, without consulting any model, the actions configuration can
settle with certainty: deny on a forbidden prefix, allow on a safe one.
Everything else is escalated to the review model, too slow to sit on the
critical path of every approval.

Evaluation order is a security property: the denylist runs before the
allowlist, never the other way round.
"""
from collections.abc import Sequence
from typing import Protocol, Self

from proxy.json_types import JSONDict, JSONValue

# Shell control operators: a safe prefix guarantees nothing once they appear,
# since they allow chaining, substituting or redirecting into an arbitrary
# command.
SHELL_CONTROL_CHARACTERS = frozenset(";&|`$()<>\n\r")

# Forcing means overriding a safeguard the command applies by default: the
# prefix stays safe, the effect no longer is.
FORCING_OPTIONS = frozenset({"--force", "-f"})

# A prefix like `git add` promises a scoped action. These arguments break that
# promise by sweeping in every modified file, which is how unrelated work ends
# up inside a commit meant to be atomic.
WHOLE_TREE_ARGUMENTS = frozenset({"-A", "--all", "."})


class ApprovalOutcome(Protocol):
    def allow(self) -> None: ...
    def deny(self, rationale: str) -> None: ...
    def escalate(self) -> None: ...


class SafeCommandRules:
    def __init__(
        self,
        safe_prefixes: Sequence[Sequence[str]],
        denied_prefixes: Sequence[Sequence[str]] = (),
    ) -> None:
        self._safe_prefixes = safe_prefixes
        self._denied_prefixes = denied_prefixes

    @classmethod
    def from_config(cls, config: JSONDict) -> Self:
        return cls(
            safe_prefixes=_configured_prefixes(config.get("safe_prefixes")),
            denied_prefixes=_configured_prefixes(config.get("denied_prefixes")),
        )

    def evaluate(self, action: JSONDict, outcome: ApprovalOutcome) -> None:
        shell_command = self._shell_command(action)
        words = _without_git_working_directory(shell_command.split())

        # A denial always wins: the denylist runs before the fast path, or an
        # allowed prefix would be enough to bypass it.
        denied = _matching_prefix(words, self._denied_prefixes)
        if denied is not None:
            outcome.deny(f"forbidden prefix: {' '.join(denied)}")
            return

        # Chaining disqualifies the fast approval path; it does not excuse us
        # from returning a verdict, so the decision goes to the model.
        safe = _matching_prefix(words, self._safe_prefixes)
        if safe is not None and _stays_within_the_safe_prefix(shell_command, words):
            outcome.allow()
            return

        outcome.escalate()

    def _shell_command(self, action: JSONDict) -> str:
        command = action.get("command")
        if not isinstance(command, list) or not command:
            return ""
        shell_command = command[-1]
        return shell_command if isinstance(shell_command, str) else ""


def _without_git_working_directory(words: Sequence[str]) -> Sequence[str]:
    """
    `git -C <dir> add x` is decided like `git add x`: the option moves the
    working directory, it does not change the command being evaluated.

    Only `-C` is neutralised. `-c <key>=<value>` is not: several configuration
    keys (`alias.*`, `core.pager`, `core.sshCommand`) run an arbitrary command,
    which makes it chaining in disguise.
    """
    if not words or words[0] != "git":
        return words

    remaining = list(words[1:])
    while remaining and remaining[0] == "-C":
        del remaining[:2]
    return ["git", *remaining]


def _configured_prefixes(value: JSONValue) -> tuple[tuple[str, ...], ...]:
    if not isinstance(value, list):
        return ()

    prefixes = []
    for entry in value:
        if isinstance(entry, list):
            words = tuple(word for word in entry if isinstance(word, str))
            if words:
                prefixes.append(words)
    return tuple(prefixes)


def _matching_prefix(
    words: Sequence[str], prefixes: Sequence[Sequence[str]]
) -> Sequence[str] | None:
    for prefix in prefixes:
        if list(words[: len(prefix)]) == list(prefix):
            return prefix
    return None


def _stays_within_the_safe_prefix(shell_command: str, words: Sequence[str]) -> bool:
    return (
        not _chains_other_commands(shell_command)
        and not _forces_past_a_safeguard(words)
        and not _reaches_the_whole_tree(words)
    )


def _reaches_the_whole_tree(words: Sequence[str]) -> bool:
    return bool(WHOLE_TREE_ARGUMENTS & set(words))


def _chains_other_commands(shell_command: str) -> bool:
    return bool(SHELL_CONTROL_CHARACTERS & set(shell_command))


def _forces_past_a_safeguard(words: Sequence[str]) -> bool:
    return bool(FORCING_OPTIONS & set(words))
