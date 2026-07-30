"""
Petit reverse-proxy placé DEVANT LiteLLM.

Pourquoi ce fichier existe : le hook async_pre_call_hook de LiteLLM ne
couvre que /chat/completions, /embeddings, /image/generation -- PAS
/v1/responses, l'endpoint que Codex utilise. custom_handler.py n'était
donc jamais appelé pour les requêtes de Codex, malgré ce qu'on pensait.

Architecture :
    Codex --> ce proxy (port 4000) --> LiteLLM (port 4001, interne) --> Cerebras

Ce proxy lit le corps de toute requête POST /v1/responses, l'assainit
avec sanitize_body() (custom_handler.py), puis la relaie telle quelle à
LiteLLM. Tout le reste (GET /v1/models, etc.) est simplement transmis.
"""
import asyncio
import json
import os
from collections.abc import AsyncGenerator, AsyncIterator
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

from proxy.approval_rules import SafeCommandRules
from proxy.custom_handler import sanitize_body
from proxy.guardian import GUARDIAN_MODEL, compact_review_request, local_review
from proxy.json_types import JSONValue

LITELLM_UPSTREAM = "http://127.0.0.1:4001"
DEBUG_LOG_PATH = "/tmp/cerebras_proxy_debug.log"
DEBUG_ENABLED = os.environ.get("CEREBRAS_PROXY_DEBUG", "").lower() in ("1", "true", "yes")

app = FastAPI()
client = httpx.AsyncClient(timeout=None)


def _load_approval_rules() -> SafeCommandRules:
    """Charge les règles du pré-filtre ; sans fichier, aucune approbation locale."""
    try:
        config = json.loads(Path(__file__).with_name("approval_rules.json").read_text("utf-8"))
    except (OSError, ValueError) as exc:
        print(f"[sanitizing_proxy] règles d'approbation illisibles, pré-filtre inactif : {exc}")
        return SafeCommandRules(safe_prefixes=())
    if not isinstance(config, dict):
        return SafeCommandRules(safe_prefixes=())
    return SafeCommandRules.from_config(config)


APPROVAL_RULES = _load_approval_rules()


def _unreachable_upstream(exc: httpx.TransportError) -> StreamingResponse:
    """Nomme le service en panne : Codex n'affiche qu'un « high demand » générique."""
    message = f"LiteLLM injoignable sur {LITELLM_UPSTREAM} : {exc}"
    print(f"[sanitizing_proxy] {message}")
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
        print(f"[sanitizing_proxy] échec écriture debug log: {exc}")


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

            # Le Guardian de Codex est tranché localement quand les règles le
            # permettent : réponse immédiate, sans appel réseau ni modèle.
            if isinstance(data, dict) and data.get("model") == GUARDIAN_MODEL:
                verdict_stream = local_review(data, APPROVAL_RULES)
                if verdict_stream is not None:
                    _debug_log("VERDICT LOCAL (auto-review)", data)
                    return StreamingResponse(verdict_stream, media_type="text/event-stream")

                # Zone grise : le modèle local tranche, mais seulement s'il
                # reçoit un prompt qu'il peut ingérer avant le timeout.
                data = compact_review_request(data)
                _debug_log("ESCALADE (requete reduite)", data)

            _debug_log("AVANT sanitize_body", data)
            data = sanitize_body(data)
            _debug_log("APRES sanitize_body", data)
            body = json.dumps(data).encode()
        except (ValueError, TypeError, KeyError) as exc:
            print(f"[sanitizing_proxy] échec du parsing/assainissement, requête transmise telle quelle: {exc}")

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
                    _append_debug_log, f"\n===== REPONSE (stream brut, SSE) =====\n{raw}\n"
                )
            except OSError as exc:
                print(f"[sanitizing_proxy] échec écriture debug log réponse: {exc}")

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
