"""
Ownership of the LiteLLM process lifecycle by the proxy.

The proxy alone relays nowhere: without LiteLLM on 4001, every request ends in
a 502. So it starts its upstream on launch -- but only if nothing is listening
already, and on shutdown it stops only the process it started itself, never
one someone else launched.

A failed launch is reported, not raised: killing the proxy on startup helps
nobody, whereas a running proxy answers 502 and names the missing upstream.
"""
from collections.abc import Awaitable, Callable
from typing import Protocol


class StoppableProcess(Protocol):
    @property
    def returncode(self) -> int | None:
        """None while the process is still running."""

    def terminate(self) -> None: ...
    async def wait(self) -> int: ...


class UpstreamSupervisor:
    def __init__(
        self,
        is_listening: Callable[[], Awaitable[bool]],
        spawn: Callable[[], Awaitable[StoppableProcess]],
        report: Callable[[str], None],
    ) -> None:
        self._is_listening = is_listening
        self._spawn = spawn
        self._report = report
        self._own_process: StoppableProcess | None = None

    async def ensure_available(self) -> None:
        if await self._is_listening():
            return
        try:
            process = await self._spawn()
        except OSError as exc:
            self._report(f"could not start LiteLLM ({exc}); requests will answer 502.")
            return

        self._own_process = process
        if process.returncode is not None:
            self._report(
                f"LiteLLM exited immediately (code {process.returncode}); "
                "see its output above. Requests will answer 502."
            )

    async def release(self) -> None:
        if self._own_process is None:
            return
        self._own_process.terminate()
        await self._own_process.wait()
        self._own_process = None
