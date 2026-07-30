"""
How the proxy behaves when LiteLLM does not answer.

An exception left to propagate shows up in Codex as "We're currently
experiencing high demand": the message hides the real cause and sends you
looking for the problem on the wrong side.
"""
import httpx
import pytest

from proxy import sanitizing_proxy


def _unreachable_upstream(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("All connection attempts failed", request=request)


@pytest.fixture
def client_with_dead_upstream(monkeypatch: pytest.MonkeyPatch) -> httpx.AsyncClient:
    monkeypatch.setattr(
        sanitizing_proxy,
        "client",
        httpx.AsyncClient(transport=httpx.MockTransport(_unreachable_upstream)),
    )
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=sanitizing_proxy.app),
        base_url="http://proxy",
    )


@pytest.mark.anyio
async def test_dead_upstream_answers_bad_gateway(
    client_with_dead_upstream: httpx.AsyncClient,
) -> None:
    response = await client_with_dead_upstream.post("/v1/responses", json={"model": "x"})

    assert response.status_code == 502


@pytest.mark.anyio
async def test_dead_upstream_names_the_unreachable_service(
    client_with_dead_upstream: httpx.AsyncClient,
) -> None:
    response = await client_with_dead_upstream.post("/v1/responses", json={"model": "x"})

    assert "4001" in response.text


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
