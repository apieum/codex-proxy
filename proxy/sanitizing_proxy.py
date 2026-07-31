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
import shutil
import sys
import uuid
from collections.abc import AsyncGenerator, AsyncIterator, Collection
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

from proxy.approval_rules import SafeCommandRules
from proxy.constrained_request import constrain_output
from proxy.constrained_response import rewrite_constrained_response
from proxy.credentials import RequiredCredentials
from proxy.executable_lookup import console_script
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

# Constraining the output is a workaround for a model that narrates instead of
# calling tools. The native protocol is what Codex and the provider already
# agree on -- and it encodes tool arguments itself, where a constrained schema
# makes the model serialise them by hand and it fails on large payloads.
# Opt in with CODEX_PROXY_CONSTRAIN=1 to compare the two.
CONSTRAIN_ENABLED = os.environ.get("CODEX_PROXY_CONSTRAIN", "").lower() in ("1", "true", "yes")

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
    return console_script(
        "litellm",
        search_path=shutil.which,
        interpreter=sys.executable,
        exists=os.path.exists,
        override=os.environ.get("LITELLM_EXECUTABLE"),
    )


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
    supervisor = UpstreamSupervisor(
        is_listening=_litellm_is_listening, spawn=_spawn_litellm, report=_report
    )
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


def _declared_tool_names(body: JSONValue) -> frozenset[str]:
    """What Codex can actually run: anything else would vanish and loop."""
    if not isinstance(body, dict):
        return frozenset()
    tools = body.get("tools")
    if not isinstance(tools, list):
        return frozenset()
    names = set()
    for tool in tools:
        if isinstance(tool, dict):
            name = tool.get("name")
            if isinstance(name, str):
                names.add(name)
    return frozenset(names)


def _constrained_answer(
    upstream: httpx.Response, declared_tools: Collection[str]
) -> StreamingResponse:
    """Rebuilds the protocol Codex acts on from the schema-constrained text."""

    async def rewritten() -> AsyncGenerator[bytes, None]:
        chunks = [chunk async for chunk in upstream.aiter_raw()]
        if DEBUG_ENABLED:
            await asyncio.to_thread(
                _append_debug_log,
                "\n===== CONSTRAINED ANSWER (upstream) =====\n"
                + b"".join(chunks).decode("utf-8", errors="replace")
                + "\n",
            )
        for frame in rewrite_constrained_response(
            chunks,
            response_id=f"resp_{uuid.uuid4().hex}",
            call_id=f"call_{uuid.uuid4().hex}",
            report=_report,
            declared_tools=declared_tools,
        ):
            yield frame

    return StreamingResponse(rewritten(), media_type="text/event-stream")


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
    constrained = False
    declared_tools: Collection[str] = frozenset()

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

            # Main traffic answers under a schema instead of calling tools
            # natively: narrating an action stops being expressible.
            if (
                CONSTRAIN_ENABLED
                and isinstance(data, dict)
                and data.get("model") != GUARDIAN_MODEL
            ):
                before = data
                declared_tools = _declared_tool_names(before)
                data = constrain_output(data)
                constrained = data is not before

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

    if constrained:
        return _constrained_answer(upstream_response, declared_tools)

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
