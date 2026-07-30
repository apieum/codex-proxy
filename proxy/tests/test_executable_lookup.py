"""
Locating the LiteLLM console script.

Looking next to `sys.executable` only works when the proxy runs from a venv
that also holds litellm. Under a system Python, litellm typically lives in
`~/.local/bin` while the interpreter is in `/usr/bin` -- the spawn then fails
with a bare FileNotFoundError.
"""
from proxy.executable_lookup import console_script


def test_a_script_on_the_path_is_preferred() -> None:
    def on_path(name: str) -> str | None:
        return f"/home/user/.local/bin/{name}"

    found = console_script("litellm", search_path=on_path, interpreter="/usr/bin/python3")

    assert found == "/home/user/.local/bin/litellm"


def test_the_interpreter_directory_is_the_fallback() -> None:
    """Inside a venv the script sits beside the interpreter, off the PATH."""
    def nothing_on_path(name: str) -> str | None:
        return None

    found = console_script(
        "litellm", search_path=nothing_on_path, interpreter="/repo/.venv/bin/python"
    )

    assert found == "/repo/.venv/bin/litellm"
