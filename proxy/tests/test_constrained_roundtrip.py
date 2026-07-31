"""
The whole constrained path, through the proxy itself.

The pieces are tested apart; this is what proves they are actually wired:
a main-traffic request leaves constrained, and the provider's plain-text
answer comes back as something Codex will execute.
"""
import json
from collections.abc import AsyncIterator

import httpx
import pytest

from proxy import sanitizing_proxy

CODEX_REQUEST = {
    "model": "cerebras-gpt-oss-120b",
    "instructions": "You are a coding agent.",
    "tools": [{"type": "function", "name": "exec_command"}],
    "input": [],
}

TOOL_TURN = json.dumps(
    {"kind": "tool_call", "tool": "exec_command", "arguments": {"cmd": "git status"}}
)


def _provider_answer(text: str) -> bytes:
    """The provider streams the constrained turn as ordinary text deltas."""
    frames = [b'data: {"type": "response.created", "response": {"id": "up_1"}}\n\n']
    for piece in (text[i : i + 11] for i in range(0, len(text), 11)):
        frames.append(
            b"data: "
            + json.dumps({"type": "response.output_text.delta", "delta": piece}).encode()
            + b"\n\n"
        )
    frames.append(b'data: {"type": "response.completed", "response": {"id": "up_1"}}\n\n')
    return b"".join(frames)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def relayed(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """Captures what actually reached the provider."""
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)

        async def streamed() -> AsyncIterator[bytes]:
            yield _provider_answer(TOOL_TURN)

        return httpx.Response(200, content=streamed())

    monkeypatch.setattr(
        sanitizing_proxy, "client", httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    return seen


async def _post(body: dict[str, object]) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=sanitizing_proxy.app), base_url="http://proxy"
    ) as client:
        return await client.post("/v1/responses", json=body)


@pytest.mark.anyio
async def test_the_provider_receives_no_native_tools(relayed: dict[str, object]) -> None:
    await _post(dict(CODEX_REQUEST))

    assert "tools" not in relayed["body"]  # type: ignore[operator]


@pytest.mark.anyio
async def test_the_provider_receives_an_output_schema(relayed: dict[str, object]) -> None:
    await _post(dict(CODEX_REQUEST))
    body = relayed["body"]
    assert isinstance(body, dict)

    assert body["text"]["format"]["type"] == "json_schema"


@pytest.mark.anyio
async def test_codex_receives_an_executable_call(relayed: dict[str, object]) -> None:
    response = await _post(dict(CODEX_REQUEST))

    assert '"type": "function_call"' in response.text


@pytest.mark.anyio
async def test_the_executed_command_is_the_one_the_model_chose(
    relayed: dict[str, object],
) -> None:
    """A mangled round trip would run something nobody asked for."""
    response = await _post(dict(CODEX_REQUEST))

    assert "git status" in response.text


@pytest.mark.anyio
async def test_each_call_gets_its_own_identifier(relayed: dict[str, object]) -> None:
    """Codex pairs outputs back by call_id; a fixed one would collide."""
    first = await _post(dict(CODEX_REQUEST))
    second = await _post(dict(CODEX_REQUEST))

    def call_id(text: str) -> str:
        for line in text.split("\n\n"):
            if line.startswith("data: ") and '"function_call"' in line:
                return str(json.loads(line.removeprefix("data: "))["item"]["call_id"])
        raise AssertionError("no function_call in the answer")

    assert call_id(first.text) != call_id(second.text)
