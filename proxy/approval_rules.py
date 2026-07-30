"""
Pré-filtre déterministe des actions soumises à l'auto-review de Codex.

Tranche localement, sans solliciter de modèle, les actions que la configuration
permet de décider avec certitude : refus sur un préfixe interdit, approbation
sur un préfixe sûr. Tout le reste est escaladé vers le modèle local, trop lent
pour être placé sur le chemin critique de chaque approbation.

L'ordre d'évaluation est une propriété de sécurité : la liste noire passe avant
la liste blanche, jamais l'inverse.
"""
from collections.abc import Sequence
from typing import Protocol, Self

from proxy.json_types import JSONDict, JSONValue

# Opérateurs de contrôle du shell : un préfixe sûr ne garantit plus rien dès
# qu'ils apparaissent, puisqu'ils permettent d'enchaîner, de substituer ou de
# rediriger vers une commande arbitraire.
SHELL_CONTROL_CHARACTERS = frozenset(";&|`$()<>\n\r")

# Forcer, c'est passer outre une protection que la commande applique par
# défaut : le préfixe reste sûr, l'effet ne l'est plus.
FORCING_OPTIONS = frozenset({"--force", "-f"})


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

        # Un refus l'emporte toujours : la liste noire passe avant la voie
        # rapide, sinon un préfixe autorisé suffirait à la contourner.
        denied = _matching_prefix(words, self._denied_prefixes)
        if denied is not None:
            outcome.deny(f"préfixe interdit : {' '.join(denied)}")
            return

        # Un enchaînement disqualifie la voie rapide d'approbation, il ne
        # dispense pas de rendre un verdict : la décision revient au modèle.
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
    `git -C <dir> add x` se décide comme `git add x` : l'option déplace le
    répertoire d'exécution, elle ne change pas la commande évaluée.

    Seule `-C` est neutralisée. `-c <clé>=<valeur>` ne l'est pas : plusieurs
    clés de configuration (`alias.*`, `core.pager`, `core.sshCommand`)
    exécutent une commande arbitraire, ce qui en fait un enchaînement déguisé.
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
    return not _chains_other_commands(shell_command) and not _forces_past_a_safeguard(words)


def _chains_other_commands(shell_command: str) -> bool:
    return bool(SHELL_CONTROL_CHARACTERS & set(shell_command))


def _forces_past_a_safeguard(words: Sequence[str]) -> bool:
    return bool(FORCING_OPTIONS & set(words))
