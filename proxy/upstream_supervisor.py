"""
Ownership of the LiteLLM process lifecycle by the proxy.

The proxy alone relays nowhere: without LiteLLM on 4001, every request ends in
a 502. So it starts its upstream on launch -- but only if nothing is listening
already, and on shutdown it stops only the process it started itself, never
one someone else launched.
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
