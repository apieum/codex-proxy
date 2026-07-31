# codex-proxy

Run [Codex CLI](https://github.com/openai/codex) on backends other than
OpenAI — Cerebras today, anything LiteLLM speaks tomorrow — and answer its
auto-approval reviews locally instead of paying a model to rubber-stamp
`git status`.

## Architecture

```
Codex CLI  --:4000-->  sanitizing_proxy.py  --:4001-->  LiteLLM  -->  Cerebras API
                       (sanitises the JSON             (bridges Responses API
                        before LiteLLM sees it,         to Chat Completions)
                        settles auto-review locally)
```

Codex natively speaks OpenAI's Responses API (`/v1/responses`). Cerebras only
exposes Chat Completions. LiteLLM bridges the two, but several of its defaults
are incompatible with Cerebras — hence `sanitizing_proxy.py`, which fixes the
request *before* it reaches LiteLLM.

LiteLLM is not started separately: the proxy launches it if nothing is
listening on 4001, and on shutdown stops only the process it started itself.

## What it does

**1. Makes Codex traffic acceptable to Chat Completions backends.** Codex
sends OpenAI-hosted tool types, orphan `function_call` pairs and empty
assistant messages that Cerebras rejects. See *Problems solved* below.

**2. Answers `codex-auto-review` without a round trip.** Before running a
command, Codex asks a model whether it is safe. Most of those questions are
about `git status`, `ls` or `grep`. A deterministic pre-filter settles them
locally in milliseconds:

| Step | Rule |
|---|---|
| 1 | denylist of command prefixes — a match always denies |
| 2 | allowlist of safe prefixes — a match approves, unless disqualified |
| 3 | anything else escalates to the model |

Shell chaining (`;`, `&&`, `\|`, backticks, `$()`, redirections), forcing
options (`--force`, `-f`) and `git -c key=value` always disqualify the fast
path — the first two smuggle in a second command, the third executes code
through `alias.*`, `core.pager` or `core.sshCommand`. `git -C <dir>` is
neutralised instead: it moves the working directory without changing the
command being judged.

Rules live in `proxy/approval_rules.json`, not in code.

**Security invariant.** When the pre-filter cannot decide and the model fails,
the error propagates and Codex falls back to asking a human. There is no
fail-open path that approves by default: a failed review costs a prompt, a
wrong approval runs a dangerous command.

**3. Shrinks escalated reviews.** Codex's review prompt is ~49 000 characters
(full security policy, AGENTS.md, transcript). Only the planned action and the
output schema survive: **884 characters**, which keeps the verdict under a
second and costs about $0.0001.

## Files

| File | Role |
|---|---|
| `proxy/sanitizing_proxy.py` | FastAPI reverse proxy (port 4000); owns the LiteLLM lifecycle |
| `proxy/request_sanitizer.py` | `sanitize_body()`: cleans the Responses API JSON |
| `proxy/approval_rules.py` / `.json` | Deterministic auto-review pre-filter (denylist, then allowlist) |
| `proxy/guardian.py` | Settles `codex-auto-review` locally; shrinks what it escalates |
| `proxy/codex_sse.py` | Builds the Responses SSE stream Codex accepts |
| `proxy/upstream_supervisor.py` | Starts LiteLLM if absent, stops only what it started |
| `proxy/credentials.py` | Reports a missing API key at startup |
| `proxy/litellm_cerebras_config.yaml` | LiteLLM config: exposed models, keys, dropped parameters |
| `proxy/codex-config.toml` | Config to copy into `~/.codex/config.toml` |

## Installation

### 1. Requirements
- [`uv`](https://docs.astral.sh/uv/)
- A Cerebras API key from [cloud.cerebras.ai](https://cloud.cerebras.ai)
- Codex CLI

### 2. Start the proxy from the repository root
```bash
export CEREBRAS_API_KEY="your_cerebras_key"
uv run uvicorn proxy.sanitizing_proxy:app --port 4000
```

Two things this command gets right, and that are easy to get wrong:
- **from the repository root**, never from a copy of the files:
  `sanitizing_proxy` imports its modules through the `proxy.` package, and a
  detached copy silently freezes the code at the version you copied;
- **`--port 4000`** — without it uvicorn listens on 8000 and Codex finds
  nobody.

Leave that terminal open while you use Codex.

The proxy locates the `litellm` console script in this order: the
`LITELLM_EXECUTABLE` environment variable, then the `PATH`, then the directory
holding the running interpreter. Set the variable only when several installs
coexist and the `PATH` picks the wrong one:

```bash
export LITELLM_EXECUTABLE="$HOME/.local/bin/litellm"
```

## Constrained output (opt-in)

By default the proxy relays Codex's native tool protocol untouched. Setting
`CODEX_PROXY_CONSTRAIN=1` instead strips the tools, imposes a JSON schema on
the answer, and rebuilds a `function_call` from it.

That workaround exists for models that narrate an action rather than calling
the tool. It has a cost: the schema carries `arguments` as a serialised
string, so the model escapes them by hand and gets it wrong on large payloads
such as a multi-line patch. Prefer the native path unless narration is
actually the problem you are seeing.

If LiteLLM cannot be started at all, the proxy says so and keeps running:
requests then answer 502 naming the missing upstream, rather than the proxy
exiting on you.

### 3. Configure Codex CLI

A ready-made file is provided: **`proxy/codex-config.toml`**. Copy its content
into `~/.codex/config.toml` (merge it if you already have settings — do not
replace the whole file).

```toml
model = "cerebras-gpt-oss-120b"
model_provider = "cerebras-local"
model_reasoning_effort = "medium" # gpt-oss-120b accepts low|medium|high only

[model_providers.cerebras-local]
name = "Cerebras via LiteLLM"
base_url = "http://localhost:4000/v1"
env_key = "LITELLM_MASTER_KEY"
wire_api = "responses"

# Recommended: avoids the startup hang on Codex's internal Apps connector,
# which cannot authenticate without a ChatGPT session.
[apps._default]
enabled = false
```

Watch out for:
- `base_url` points at port **4000** (the proxy), never 4001 (internal
  LiteLLM) — otherwise you lose all JSON sanitising.
- `env_key` names an environment variable in your shell, not the Cerebras key
  itself.
- To change model, use one of the aliases defined in
  `proxy/litellm_cerebras_config.yaml`, not a raw Cerebras model name.
  `cerebras-review` is reserved for auto-review; do not put it here.

And in your shell:
```bash
export LITELLM_MASTER_KEY="sk-local-proxy-1234"   # must match master_key in the yaml
```

### 4. Run Codex normally
```bash
codex
```

## Problems solved (and why)

Cerebras (Chat Completions) and Codex (Responses API, with OpenAI-specific
native tools) are not natively compatible. This setup fixes, in the order the
problems were hit:

1. **Invalid `reasoning_effort`** — gpt-oss-120b accepts only
   `low`/`medium`/`high`, not `none`. Codex sometimes sends a value Cerebras
   rejects; it is forced to `medium`.
2. **Fields Cerebras does not support** — `metadata`, `client_metadata`,
   `store`, `previous_response_id`, `parallel_tool_calls` and others are
   stripped before sending.
3. **Codex native tools with no Cerebras equivalent** — `namespace`,
   `local_shell`, `computer_use`, `code_interpreter`, `file_search`,
   `image_generation` and `web_search` belong to OpenAI's hosted
   infrastructure. They are removed from `tools`; only `function` tools reach
   the provider.
4. **Orphan tool calls** — removing those tools can leave incomplete
   `function_call`/`function_call_output` pairs, which Cerebras rejects. They
   are re-paired, and true orphans dropped.
5. **Empty assistant messages in between** — Codex sometimes inserts an empty
   assistant message between a `function_call` and its output. Cerebras
   requires the tool message to follow the call *immediately*.
6. **`response.completed` without a full `usage`** — Codex deserialises that
   field into a struct with required fields. An empty object fails the whole
   stream with ``missing field `input_tokens` ``, which surfaces to the user as
   a denied action, not as a parse error.
7. **`codex_apps` hanging at startup** — a Codex-internal connector that tries
   to reach the ChatGPT backend and fails without an active session. Disabled
   in `config.toml` (a Codex setting, not a proxy one).

**Worth knowing**: LiteLLM's `drop_params` and its hook system
(`async_pre_call_hook`) do NOT cover `/v1/responses` — only
`/chat/completions`, `/embeddings` and `/image/generation`. That is why this
proxy exists: it is the only place where Codex traffic can reliably be
intercepted and fixed.

## Debugging

```bash
export CODEX_PROXY_DEBUG=1
uv run uvicorn proxy.sanitizing_proxy:app --port 4000
```

Every `/v1/responses` request is then written to `/tmp/codex_proxy_debug.log`
(careful: it contains your file contents and commands — do not share it as is).
Look for the last `BEFORE sanitize_body` block to see exactly what Codex sent.

## Known limitations

- The Cerebras free tier is capped at 8K context and 5 requests/min, which is
  not enough for agentic use. The paid Developer tier (131K context, 1000
  req/min) is recommended.
- OpenAI-native features (image generation, computer use, hosted code
  interpreter, file search, web search) do not work through Cerebras. Only
  `function` tools pass.
- Escalated reviews leave the machine, like the rest of Codex traffic. The
  pre-filter keeps most decisions local, but not all of them.
- `[apps._default] enabled = false` also disables the legitimate Apps
  connectors (Slack, Notion, ...) if you were using them.

## Development

```bash
uv run pytest ./proxy/tests   # tests
uv run ruff check .           # lint
uv run mypy proxy             # types
```

Every behaviour here was specified by a failing test first. The three
commands above are the gate: a change is not done until all three pass.

Two conventions worth knowing before contributing:
- **Tell, don't ask.** Objects expose behaviour, not state. The pre-filter
  hands its verdict to a collaborator (`outcome.allow()`, `outcome.deny(...)`)
  rather than returning a value for the caller to branch on.
- **Comments explain why, not what.** Most comments in this codebase record a
  measurement or a decision that is not visible in the code.

## License

MIT — see [LICENSE](LICENSE).
