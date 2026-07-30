"""
Small reverse proxy sitting IN FRONT OF LiteLLM.

Why this file exists: LiteLLM's async_pre_call_hook only covers
/chat/completions, /embeddings and /image/generation -- NOT /v1/responses,
the endpoint Codex actually uses. A LiteLLM callback would therefore never
run for Codex traffic.

Architecture:
    Codex --> this proxy (port 4000) --> LiteLLM (port 4001, internal) --> Cerebras

It reads the body of every POST /v1/responses, sanitises it with
sanitize_body() (request_sanitizer.py), then relays it to LiteLLM. Everything
else (GET /v1/models, etc.) is passed straight through.
"""
import asyncio
import json
import os
import sys
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

from proxy.approval_rules import SafeCommandRules
from proxy.credentials import RequiredCredentials
from proxy.guardian import GUARDIAN_MODEL, compact_review_request, local_review
from proxy.json_types import JSONValue
from proxy.request_sanitizer import sanitize_body
from proxy.upstream_supervisor import StoppableProcess, UpstreamSupervisor

LITELLM_UPSTREAM = "http://127.0.0.1:4001"
LITELLM_CONFIG_PATH = Path(__file__).with_name("litellm_cerebras_config.yaml")
LITELLM_STARTUP_TIMEOUT_SECONDS = 60
BACKEND_CREDENTIALS = RequiredCredentials(("CEREBRAS_API_KEY",))
DEBUG_LOG_PATH = "/tmp/codex_proxy_debug.log"
DEBUG_ENABLED = os.environ.get("CODEX_PROXY_DEBUG", "").lower() in ("1", "true", "yes")

client = httpx.AsyncClient(timeout=None)


def _report(message: str) -> None:
    """Explicit `flush`: outside a terminal the buffer is lost on SIGTERM."""
    print(f"[codex-proxy] {message}", flush=True)


async def _litellm_is_listening() -> bool:
    try:
        await client.get(f"{LITELLM_UPSTREAM}/health/liveliness", timeout=2.0)
    except httpx.TransportError:
        return False
    return True


def _litellm_executable() -> str:
    """`litellm` is a console script: `python -m litellm` does not exist."""
    return str(Path(sys.executable).with_name("litellm"))


async def _spawn_litellm() -> StoppableProcess:
    _report(f"LiteLLM not running, starting it on {LITELLM_UPSTREAM} ...")
    return await asyncio.create_subprocess_exec(
        _litellm_executable(), "--config", str(LITELLM_CONFIG_PATH), "--port", "4001"
    )


async def _await_litellm() -> None:
    for _ in range(LITELLM_STARTUP_TIMEOUT_SECONDS):
        if await _litellm_is_listening():
            _report("LiteLLM ready.")
            return
        await asyncio.sleep(1)
    _report("LiteLLM did not start in time; requests will answer 502.")


@asynccontextmanager
async def _managed_upstream(app: FastAPI) -> AsyncGenerator[None, None]:
    BACKEND_CREDENTIALS.report_missing(os.environ, _report)
    supervisor = UpstreamSupervisor(is_listening=_litellm_is_listening, spawn=_spawn_litellm)
    await supervisor.ensure_available()
    await _await_litellm()
    try:
        yield
    finally:
        await supervisor.release()


app = FastAPI(lifespan=_managed_upstream)


def _load_approval_rules() -> SafeCommandRules:
    """Loads the pre-filter rules; with no file, nothing is approved locally."""
    try:
        config = json.loads(Path(__file__).with_name("approval_rules.json").read_text("utf-8"))
    except (OSError, ValueError) as exc:
        _report(f"approval rules unreadable, pre-filter disabled: {exc}")
        return SafeCommandRules(safe_prefixes=())
    if not isinstance(config, dict):
        return SafeCommandRules(safe_prefixes=())
    return SafeCommandRules.from_config(config)


APPROVAL_RULES = _load_approval_rules()


def _unreachable_upstream(exc: httpx.TransportError) -> StreamingResponse:
    """Names the broken service: Codex only shows a generic "high demand"."""
    message = f"LiteLLM unreachable on {LITELLM_UPSTREAM}: {exc}"
    _report(message)
    return StreamingResponse(
        iter([json.dumps({"error": {"message": message, "type": "upstream_unreachable"}}).encode()]),
        status_code=502,
        media_type="application/json",
    )


def _append_debug_log(text: str) -> None:
    with open(DEBUG_LOG_PATH, "a") as f:
        f.write(text)


def _debug_log(label: str, data: JSONValue) -> None:
    if not DEBUG_ENABLED:
        return
    try:
        _append_debug_log(f"\n===== {label} =====\n{json.dumps(data, indent=2, ensure_ascii=False)}\n")
    except (OSError, TypeError, ValueError) as exc:
        _report(f"could not write debug log: {exc}")


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def proxy(path: str, request: Request) -> StreamingResponse:
    url = f"{LITELLM_UPSTREAM}/{path}"

    headers = dict(request.headers)
    headers.pop("host", None)
    headers.pop("content-length", None)

    body = await request.body()

    if request.method == "POST" and path == "v1/responses" and body:
        try:
            data: JSONValue = json.loads(body)

            # Codex's Guardian is settled locally when the rules allow it:
            # immediate answer, no network call and no model.
            if isinstance(data, dict) and data.get("model") == GUARDIAN_MODEL:
                verdict_stream = local_review(data, APPROVAL_RULES)
                if verdict_stream is not None:
                    _debug_log("LOCAL VERDICT (auto-review)", data)
                    return StreamingResponse(verdict_stream, media_type="text/event-stream")

                # Grey zone: the model decides, but only if it receives a
                # prompt it can ingest before the timeout.
                data = compact_review_request(data)
                _debug_log("ESCALATION (shrunk request)", data)

            _debug_log("BEFORE sanitize_body", data)
            data = sanitize_body(data)
            _debug_log("AFTER sanitize_body", data)
            body = json.dumps(data).encode()
        except (ValueError, TypeError, KeyError) as exc:
            _report(f"parsing/sanitising failed, request relayed unchanged: {exc}")

    upstream_request = client.build_request(
        request.method,
        url,
        headers=headers,
        content=body,
        params=request.query_params,
    )
    try:
        upstream_response = await client.send(upstream_request, stream=True)
    except httpx.TransportError as exc:
        return _unreachable_upstream(exc)

    if DEBUG_ENABLED and path == "v1/responses":
        async def _tee_and_log() -> AsyncGenerator[bytes, None]:
            chunks: list[bytes] = []
            async for chunk in upstream_response.aiter_raw():
                chunks.append(chunk)
                yield chunk
            try:
                raw = b"".join(chunks).decode("utf-8", errors="replace")
                await asyncio.to_thread(
                    _append_debug_log, f"\n===== RESPONSE (raw SSE stream) =====\n{raw}\n"
                )
            except OSError as exc:
                _report(f"could not write response debug log: {exc}")

        response_iterator: AsyncIterator[bytes] = _tee_and_log()
    else:
        response_iterator = upstream_response.aiter_raw()

    return StreamingResponse(
        response_iterator,
        status_code=upstream_response.status_code,
        headers={
            k: v
            for k, v in upstream_response.headers.items()
            if k.lower() not in ("content-length", "transfer-encoding", "connection")
        },
    )
