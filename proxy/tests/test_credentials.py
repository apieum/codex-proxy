"""
Reporting of missing credentials at startup.

Without `CEREBRAS_API_KEY`, LiteLLM starts silently: the failure only surfaces
on the first request, as an opaque authentication error handed back to Codex.
Saying it at startup saves looking for the fault elsewhere.
"""
from proxy.credentials import RequiredCredentials


class ReportSpy:
    """The recipient the credentials dictate what is missing to."""

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
        {"CEREBRAS_API_KEY": "sk-real-key"}, report
    )

    assert report.messages == []


def test_an_empty_credential_counts_as_missing() -> None:
    """A variable exported empty passes naive checks but authenticates nothing."""
    report = ReportSpy()

    RequiredCredentials(("CEREBRAS_API_KEY",)).report_missing({"CEREBRAS_API_KEY": ""}, report)

    assert len(report.messages) == 1
