"""
The proxy starting LiteLLM itself.

Running `uvicorn proxy.sanitizing_proxy:app` with no LiteLLM behind it answers
502 to every request: the proxy alone has nowhere to relay. So it takes charge
of starting its upstream, without ever stepping on a LiteLLM the user started
themselves.
"""
import pytest

from proxy.upstream_supervisor import UpstreamSupervisor


class ProcessSpy:
    """The spawned process, which the supervisor tells to stop."""

    def __init__(self) -> None:
        self.stopped = False

    def terminate(self) -> None:
        self.stopped = True

    async def wait(self) -> int:
        return 0


class SpawnSpy:
    def __init__(self) -> None:
        self.process = ProcessSpy()
        self.calls = 0

    async def __call__(self) -> ProcessSpy:
        self.calls += 1
        return self.process


def _supervisor(*, already_listening: bool, spawn: SpawnSpy) -> UpstreamSupervisor:
    async def probe() -> bool:
        return already_listening

    return UpstreamSupervisor(is_listening=probe, spawn=spawn)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_an_absent_upstream_is_started() -> None:
    spawn = SpawnSpy()

    await _supervisor(already_listening=False, spawn=spawn).ensure_available()

    assert spawn.calls == 1


@pytest.mark.anyio
async def test_an_upstream_already_listening_is_not_started_again() -> None:
    spawn = SpawnSpy()

    await _supervisor(already_listening=True, spawn=spawn).ensure_available()

    assert spawn.calls == 0


@pytest.mark.anyio
async def test_the_process_it_started_is_stopped_on_shutdown() -> None:
    spawn = SpawnSpy()
    supervisor = _supervisor(already_listening=False, spawn=spawn)
    await supervisor.ensure_available()

    await supervisor.release()

    assert spawn.process.stopped is True


@pytest.mark.anyio
async def test_an_upstream_it_did_not_start_survives_shutdown() -> None:
    """Killing someone else's LiteLLM would cut the proxy the user drives."""
    spawn = SpawnSpy()
    supervisor = _supervisor(already_listening=True, spawn=spawn)
    await supervisor.ensure_available()

    await supervisor.release()

    assert spawn.process.stopped is False
