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
from typing import Protocol

from proxy.json_types import JSONDict

# Opérateurs de contrôle du shell : un préfixe sûr ne garantit plus rien dès
# qu'ils apparaissent, puisqu'ils permettent d'enchaîner, de substituer ou de
# rediriger vers une commande arbitraire.
SHELL_CONTROL_CHARACTERS = frozenset(";&|`$()<>\n\r")


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

    def evaluate(self, action: JSONDict, outcome: ApprovalOutcome) -> None:
        shell_command = self._shell_command(action)
        words = shell_command.split()

        # Un refus l'emporte toujours : la liste noire passe avant la voie
        # rapide, sinon un préfixe autorisé suffirait à la contourner.
        denied = _matching_prefix(words, self._denied_prefixes)
        if denied is not None:
            outcome.deny(f"préfixe interdit : {' '.join(denied)}")
            return

        # Un enchaînement disqualifie la voie rapide d'approbation, il ne
        # dispense pas de rendre un verdict : la décision revient au modèle.
        safe = _matching_prefix(words, self._safe_prefixes)
        if safe is not None and not _chains_other_commands(shell_command):
            outcome.allow()
            return

        outcome.escalate()

    def _shell_command(self, action: JSONDict) -> str:
        command = action.get("command")
        if not isinstance(command, list) or not command:
            return ""
        shell_command = command[-1]
        return shell_command if isinstance(shell_command, str) else ""


def _matching_prefix(
    words: Sequence[str], prefixes: Sequence[Sequence[str]]
) -> Sequence[str] | None:
    for prefix in prefixes:
        if list(words[: len(prefix)]) == list(prefix):
            return prefix
    return None


def _chains_other_commands(shell_command: str) -> bool:
    return bool(SHELL_CONTROL_CHARACTERS & set(shell_command))
