"""
Locating a console script the proxy needs to launch.

Which install to launch is not a matter of taste. `pyproject` declares
`litellm[proxy]` as a dependency of this project, so the correct script is the
one resolved alongside the running interpreter -- a different install on the
PATH may be older, or lack the `[proxy]` extras, and then dies on
`ModuleNotFoundError` right after being spawned.

The PATH is the fallback: under a system Python nothing sits beside the
interpreter, and a user-level install is all there is.
"""
from collections.abc import Callable
from pathlib import Path


def console_script(
    name: str,
    search_path: Callable[[str], str | None],
    interpreter: str,
    exists: Callable[[str], bool],
    override: str | None = None,
) -> str:
    # An exported-but-empty variable means "unset", not "run nothing".
    if override:
        return override

    beside_interpreter = str(Path(interpreter).with_name(name))
    if exists(beside_interpreter):
        return beside_interpreter

    on_path = search_path(name)
    if on_path is not None:
        return on_path

    # Nothing found: name the place it was expected, so the failure report
    # points somewhere rather than at an empty command.
    return beside_interpreter
