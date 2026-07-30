"""
Démarrage de LiteLLM par le proxy lui-même.

Lancer `uvicorn proxy.sanitizing_proxy:app` sans LiteLLM derrière produit une
502 sur chaque requête : le proxy seul ne sait relayer nulle part. Il prend
donc en charge le démarrage de son upstream, sans jamais marcher sur un
LiteLLM que l'utilisateur a déjà lancé lui-même.
"""
import pytest

from proxy.upstream_supervisor import UpstreamSupervisor


class ProcessSpy:
    """Le processus lancé, à qui le superviseur dicte son arrêt."""

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
    """Tuer le LiteLLM d'un autre couperait le proxy que l'utilisateur pilote."""
    spawn = SpawnSpy()
    supervisor = _supervisor(already_listening=True, spawn=spawn)
    await supervisor.ensure_available()

    await supervisor.release()

    assert spawn.process.stopped is False
