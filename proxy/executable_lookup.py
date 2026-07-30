"""
Locating a console script the proxy needs to launch.

Two installations coexist in practice: a virtualenv, where the script sits
beside the interpreter but usually outside the PATH, and a user-level install,
where the script is on the PATH while the interpreter is in /usr/bin. Checking
the PATH first covers the second case without breaking the first.
"""
from collections.abc import Callable
from pathlib import Path


def console_script(
    name: str,
    search_path: Callable[[str], str | None],
    interpreter: str,
) -> str:
    on_path = search_path(name)
    if on_path is not None:
        return on_path
    return str(Path(interpreter).with_name(name))
