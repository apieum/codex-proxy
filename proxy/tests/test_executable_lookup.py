"""
Locating the LiteLLM console script.

Which install to launch is not a matter of taste. `pyproject` declares
`litellm[proxy]` as a dependency of this project, so the correct script is the
one resolved alongside the running interpreter. A different install found on
the PATH may be older, or lack the `[proxy]` extras entirely, and then dies on
`ModuleNotFoundError: apscheduler` right after being spawned.

The PATH stays the fallback: under a system Python nothing sits beside the
interpreter, and a user-level install is all there is.
"""
from proxy.executable_lookup import console_script


def _on_path(name: str) -> str | None:
    return f"/home/user/.local/bin/{name}"


def _nothing_on_path(name: str) -> str | None:
    return None


def _venv_only(path: str) -> bool:
    return path.startswith("/repo/.venv/")


def _nothing_exists(path: str) -> bool:
    return False


def test_the_script_beside_the_interpreter_wins_over_the_path() -> None:
    """Its dependencies were resolved together with the proxy's own."""
    found = console_script(
        "litellm",
        search_path=_on_path,
        interpreter="/repo/.venv/bin/python",
        exists=_venv_only,
    )

    assert found == "/repo/.venv/bin/litellm"


def test_the_path_is_used_when_nothing_sits_beside_the_interpreter() -> None:
    found = console_script(
        "litellm",
        search_path=_on_path,
        interpreter="/usr/bin/python3",
        exists=_nothing_exists,
    )

    assert found == "/home/user/.local/bin/litellm"


def test_the_interpreter_directory_is_the_last_resort() -> None:
    """Nothing found anywhere: report the expected location, not an empty name."""
    found = console_script(
        "litellm",
        search_path=_nothing_on_path,
        interpreter="/repo/.venv/bin/python",
        exists=_nothing_exists,
    )

    assert found == "/repo/.venv/bin/litellm"


def test_an_explicit_override_outranks_everything() -> None:
    found = console_script(
        "litellm",
        search_path=_on_path,
        interpreter="/repo/.venv/bin/python",
        exists=_venv_only,
        override="/opt/pinned/litellm",
    )

    assert found == "/opt/pinned/litellm"


def test_an_empty_override_is_ignored() -> None:
    """`export LITELLM_EXECUTABLE=` must not resolve to an empty command."""
    found = console_script(
        "litellm",
        search_path=_on_path,
        interpreter="/usr/bin/python3",
        exists=_nothing_exists,
        override="",
    )

    assert found == "/home/user/.local/bin/litellm"
