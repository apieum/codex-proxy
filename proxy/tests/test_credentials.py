"""
Signalement des credentials manquants au démarrage.

Sans `CEREBRAS_API_KEY`, LiteLLM démarre sans rien dire : l'échec ne surgit
qu'à la première requête, sous forme d'erreur d'authentification opaque
renvoyée à Codex. Le dire au démarrage évite de chercher la panne ailleurs.
"""
from proxy.credentials import RequiredCredentials


class ReportSpy:
    """Le destinataire à qui les credentials dictent ce qui manque."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    def __call__(self, message: str) -> None:
        self.messages.append(message)


def test_a_missing_credential_is_reported() -> None:
    report = ReportSpy()

    RequiredCredentials(("CEREBRAS_API_KEY",)).report_missing({}, report)

    assert "CEREBRAS_API_KEY" in report.messages[0]


def test_a_present_credential_is_not_reported() -> None:
    report = ReportSpy()

    RequiredCredentials(("CEREBRAS_API_KEY",)).report_missing(
        {"CEREBRAS_API_KEY": "sk-reelle"}, report
    )

    assert report.messages == []


def test_an_empty_credential_counts_as_missing() -> None:
    """Une variable exportée à vide passe les contrôles naïfs mais ne s'authentifie pas."""
    report = ReportSpy()

    RequiredCredentials(("CEREBRAS_API_KEY",)).report_missing({"CEREBRAS_API_KEY": ""}, report)

    assert len(report.messages) == 1
