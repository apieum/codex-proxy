"""
Contrôle des credentials attendus par les backends configurés.

Sans clé, LiteLLM démarre normalement et l'échec ne surgit qu'à la première
requête, en erreur d'authentification opaque côté Codex. Le signaler au
démarrage évite de chercher la panne dans le proxy.
"""
from collections.abc import Callable, Mapping, Sequence


class RequiredCredentials:
    def __init__(self, names: Sequence[str]) -> None:
        self._names = names

    def report_missing(
        self, environment: Mapping[str, str], report: Callable[[str], None]
    ) -> None:
        for name in self._names:
            if not environment.get(name):
                report(f"{name} n'est pas définie : les appels au modèle échoueront.")
