"""
Pré-filtre déterministe des actions soumises à l'auto-review de Codex.

Tranche localement, sans solliciter de modèle, les actions dont la commande
correspond à un préfixe déclaré sûr — le modèle local, trop lent pour être
sur le chemin critique de chaque approbation, reste réservé à la zone grise.
"""
from collections.abc import Sequence
from typing import Protocol

from proxy.json_types import JSONDict


class ApprovalOutcome(Protocol):
    def allow(self) -> None: ...


class SafeCommandRules:
    def __init__(self, safe_prefixes: Sequence[Sequence[str]]) -> None:
        self._safe_prefixes = safe_prefixes

    def evaluate(self, action: JSONDict, outcome: ApprovalOutcome) -> None:
        words = self._shell_words(action)
        for prefix in self._safe_prefixes:
            if words[: len(prefix)] == list(prefix):
                outcome.allow()
                return

    def _shell_words(self, action: JSONDict) -> list[str]:
        command = action.get("command")
        if not isinstance(command, list) or not command:
            return []
        shell_command = command[-1]
        if not isinstance(shell_command, str):
            return []
        return shell_command.split()
