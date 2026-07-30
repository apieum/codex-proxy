"""
Prise en charge du cycle de vie de LiteLLM par le proxy.

Le proxy seul ne relaie nulle part : sans LiteLLM sur 4001, chaque requête
finit en 502. Il démarre donc son upstream au lancement -- mais uniquement
s'il n'écoute pas déjà, et il n'arrête à la fermeture que le processus qu'il
a lui-même lancé, jamais celui d'un script tiers.
"""
from collections.abc import Awaitable, Callable
from typing import Protocol


class StoppableProcess(Protocol):
    def terminate(self) -> None: ...
    async def wait(self) -> int: ...


class UpstreamSupervisor:
    def __init__(
        self,
        is_listening: Callable[[], Awaitable[bool]],
        spawn: Callable[[], Awaitable[StoppableProcess]],
    ) -> None:
        self._is_listening = is_listening
        self._spawn = spawn
        self._own_process: StoppableProcess | None = None

    async def ensure_available(self) -> None:
        if await self._is_listening():
            return
        self._own_process = await self._spawn()

    async def release(self) -> None:
        if self._own_process is None:
            return
        self._own_process.terminate()
        await self._own_process.wait()
        self._own_process = None
