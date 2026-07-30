from proxy.harness import is_operational


def test_is_operational_reports_true() -> None:
    assert is_operational() is True
