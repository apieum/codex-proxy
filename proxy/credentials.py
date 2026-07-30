"""
Checks the credentials the configured backends expect.

Without a key, LiteLLM starts normally and the failure only surfaces on the
first request, as an opaque authentication error on the Codex side. Reporting
it at startup saves looking for the fault inside the proxy.
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
                report(f"{name} is not set: calls to the model will fail.")
