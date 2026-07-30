"""
Locating a console script the proxy needs to launch.

Two installations coexist in practice: a virtualenv, where the script sits
beside the interpreter but usually outside the PATH, and a user-level install,
where the script is on the PATH while the interpreter is in /usr/bin. Checking
the PATH first covers the second case without breaking the first.

An explicit override wins over both, so a specific install can be pinned when
several coexist.
"""
from collections.abc import Callable
from pathlib import Path


def console_script(
    name: str,
    search_path: Callable[[str], str | None],
    interpreter: str,
    override: str | None = None,
) -> str:
    # An exported-but-empty variable means "unset", not "run nothing".
    if override:
        return override

    on_path = search_path(name)
    if on_path is not None:
        return on_path
    return str(Path(interpreter).with_name(name))
