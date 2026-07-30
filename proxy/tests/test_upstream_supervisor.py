"""
The proxy starting LiteLLM itself.

Running `uvicorn proxy.sanitizing_proxy:app` with no LiteLLM behind it answers
502 to every request: the proxy alone has nowhere to relay. So it takes charge
of starting its upstream, without ever stepping on a LiteLLM the user started
themselves.

A process can also start and die at once -- an incomplete install exits on
`ModuleNotFoundError` in under a second. Waiting the full startup timeout to
say so leaves the operator staring at nothing for a minute.
"""
import pytest

from proxy.upstream_supervisor import UpstreamSupervisor


class ProcessSpy:
    """The spawned process, which the supervisor tells to stop."""

    def __init__(self, returncode: int | None = None) -> None:
        self.returncode = returncode
        self.stopped = False

    def terminate(self) -> None:
        self.stopped = True

    async def wait(self) -> int:
        return 0


class SpawnSpy:
    def __init__(self, returncode: int | None = None) -> None:
        self.process = ProcessSpy(returncode)
        self.calls = 0

    async def __call__(self) -> ProcessSpy:
        self.calls += 1
        return self.process


class ReportSpy:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def __call__(self, message: str) -> None:
        self.messages.append(message)


class UnspawnableUpstream:
    """Stands in for an executable that is not on this machine."""

    async def __call__(self) -> ProcessSpy:
        raise FileNotFoundError(2, "No such file or directory")


def _supervisor(
    *,
    already_listening: bool,
    spawn: SpawnSpy | UnspawnableUpstream,
    report: ReportSpy | None = None,
) -> UpstreamSupervisor:
    async def probe() -> bool:
        return already_listening

    return UpstreamSupervisor(is_listening=probe, spawn=spawn, report=report or ReportSpy())


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


@pytest.mark.anyio
async def test_an_upstream_that_cannot_be_started_does_not_abort_startup() -> None:
    """A proxy that exits helps nobody: requests answer 502 and name the cause."""
    await _supervisor(already_listening=False, spawn=UnspawnableUpstream()).ensure_available()


@pytest.mark.anyio
async def test_an_upstream_that_cannot_be_started_is_reported() -> None:
    report = ReportSpy()

    await _supervisor(
        already_listening=False, spawn=UnspawnableUpstream(), report=report
    ).ensure_available()

    assert "LiteLLM" in report.messages[0]


@pytest.mark.anyio
async def test_an_upstream_that_died_at_once_is_reported() -> None:
    """An incomplete install exits on ModuleNotFoundError within a second."""
    report = ReportSpy()

    supervisor = _supervisor(
        already_listening=False, spawn=SpawnSpy(returncode=1), report=report
    )
    await supervisor.ensure_available()

    assert "exited" in report.messages[0]


@pytest.mark.anyio
async def test_a_running_upstream_is_not_reported_as_dead() -> None:
    report = ReportSpy()

    supervisor = _supervisor(
        already_listening=False, spawn=SpawnSpy(returncode=None), report=report
    )
    await supervisor.ensure_available()

    assert report.messages == []
