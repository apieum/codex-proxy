"""
Pré-filtre déterministe des actions soumises à l'auto-review de Codex.

Tranche localement, sans solliciter de modèle, les actions dont la commande
correspond à un préfixe déclaré sûr — le modèle local, trop lent pour être
sur le chemin critique de chaque approbation, reste réservé à la zone grise.
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
    def escalate(self) -> None: ...


class SafeCommandRules:
    def __init__(self, safe_prefixes: Sequence[Sequence[str]]) -> None:
        self._safe_prefixes = safe_prefixes

    def evaluate(self, action: JSONDict, outcome: ApprovalOutcome) -> None:
        shell_command = self._shell_command(action)

        # Un enchaînement disqualifie la voie rapide d'approbation, il ne
        # dispense pas de rendre un verdict : la décision revient au modèle.
        if not _chains_other_commands(shell_command):
            words = shell_command.split()
            for prefix in self._safe_prefixes:
                if words[: len(prefix)] == list(prefix):
                    outcome.allow()
                    return

        outcome.escalate()

    def _shell_command(self, action: JSONDict) -> str:
        command = action.get("command")
        if not isinstance(command, list) or not command:
            return ""
        shell_command = command[-1]
        return shell_command if isinstance(shell_command, str) else ""


def _chains_other_commands(shell_command: str) -> bool:
    return bool(SHELL_CONTROL_CHARACTERS & set(shell_command))
