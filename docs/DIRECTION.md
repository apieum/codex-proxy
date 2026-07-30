# PROJECT DIRECTION — codex-proxy

Compass document for any agent (human or LLM) working on this codebase.
Read it AFTER `CLAUDE.md` (which sets the execution rules): this file sets the
WHAT and the WHY; `CLAUDE.md` sets the HOW.

---

## 1. Vision

A **local proxy/router** placed between agentic tools (Codex CLI first, others
later) and LLM providers, which:

1. **Translates and sanitises** requests so tools and providers that do not
   natively speak to each other can (Codex speaks OpenAI's Responses API;
   Cerebras/OpenRouter speak Chat Completions).
2. **Routes by model name** to the right backend: Cerebras, OpenRouter, or
   local models.
3. **Cuts token cost** by optimising context before it reaches a paid provider.
4. **Replaces the proprietary OpenAI services** Codex depends on
   (`codex-auto-review` first) with local equivalents where that is viable.

Current architecture (working, to be preserved):

```
Codex CLI ──:4000──> proxy/sanitizing_proxy.py ──:4001──> LiteLLM ──> Cerebras
                     (sanitising + pre-filter)            (Responses→ChatCompletions bridge)
```

Target architecture:

```
Agentic tool ──> PROXY (port 4000)
                  ├─ pipeline of stages (sanitise, compact, optimise, hooks)
                  ├─ ROUTER by model name
                  │    ├─ cerebras-*        ──> LiteLLM ──> Cerebras
                  │    ├─ or-* / openrouter ──> LiteLLM ──> OpenRouter
                  │    └─ codex-auto-review ──> review backend
                  └─ responses translated back to what the tool expects
```

---

## 2. Ordered priorities

Do NOT work on priority N+1 until priority N is working and tested. When in
doubt about scope: ask.

### P0 — The existing Cerebras pipeline keeps working (permanent invariant)

Every change must preserve the behaviour documented in `README.md`. It is the
only thing actually in production. Any regression here cancels the benefit of
whatever new feature caused it.

### P1 — Local `codex-auto-review` service

**Problem.** Codex sends `POST /v1/responses` with `model=codex-auto-review`
for its automatic tool-approval review. A proxy that blindly relays it to
Cerebras gets a 400 "Invalid model name", so Codex shows
`Automatic approval review denied (risk: high)` and falls back to manual
approval every time.

**Target solution.** The proxy detects `model == "codex-auto-review"` and
serves the request itself instead of relaying it unchanged.

**Mandated steps, in order:**

1. **M1.1 — Capture the real contract. ✅ DONE (2026-07-30).** The full
   contract is documented in `docs/CODEX_API.md` (detail) and
   `docs/api_codex.summary.json` (machine summary — agents should read this
   one first). The **SSE response format** is established too
   (`CODEX_API.md` section 6), not from a capture but by reading Codex's
   source: two events are enough to build a valid response, with
   `response.completed` the only mandatory one.
2. **M1.2 — Route inside the proxy and rewrite the prompt. ✅ DONE
   (2026-07-30).** The alternative — "a LiteLLM alias that relays the request
   unchanged" — is **ruled out** by the performance measurements below: the
   full Guardian prompt (~8,000+ tokens) would take ~11 minutes to ingest on
   the target hardware. So the proxy intercepts `model == "codex-auto-review"`
   and **builds a radically reduced request**:
   - the security policy distilled into a short, FIXED system prompt
     (≤ ~300 tokens);
   - minimal variable part: the `Planned action JSON` object;
   - NOT the 18k-char Guardian prompt, NOT the full AGENTS.md, NOT the whole
     transcript, NOT the tools (a direct answer is forced).

   Result: `guardian.compact_review_request` takes a realistic request from
   48,942 chars down to 884 (~220 tokens).

   **Decision (2026-07-30) — the grey zone goes to Cerebras, not to a local
   model.** Even shrunk to 220 tokens, the request needs ~19 s of ingestion at
   11.65 tok/s, past Codex's idle timeout; and a 1.2B model judges a security
   question badly. Escalation is therefore routed to `cerebras-review` — the
   model already in service, `reasoning_effort: low` (the floor for
   gpt-oss-120b, which rejects `none`), for ~$0.0001 per verdict.

   Trade-off to own: **the command being judged leaves the machine**, which
   eats into goal 4 (replacing OpenAI services with local equivalents).
   Exposure stays bounded — the same Codex traffic already goes to Cerebras,
   and the pre-filter (M1.3) settles most cases locally with no call at all.
   Going back to local needs a GPU, not a setting.
3. **M1.3 — Deterministic pre-filter BEFORE the LLM. ✅ DONE (2026-07-30).**
   Most evaluated actions are mundane (`git add`, `ls`, reading files). A set
   of local rules (allowlist of safe command prefixes, denylist of obviously
   destructive patterns) settles the clear cases instantly; the model is only
   consulted for the grey zone. Rules are configuration, not hard-coded. A
   case covered by neither the rules nor a timely LLM verdict propagates as an
   error (= no auto-approval on the Codex side, see the invariant).

   **Decision (2026-07-30) — denylist AND allowlist, combined.** The proxy
   runs locally, on the user's machine: a denylist is an acceptable choice
   here, where it would be insufficient for an exposed service. Both
   mechanisms coexist and are configurable:

   | Level | Denylist | Allowlist |
   |---|---|---|
   | Characters | shell metacharacters rejected (default, permissive) | allowed character set, everything else rejected (strict mode, optional) |
   | Commands | destructive patterns → never approved | safe prefixes → approved immediately |

   **Evaluation order is a security property, not a preference:**
   1. denylist (characters, then patterns) — a denial always wins;
   2. allowlist of prefixes — immediate approval;
   3. otherwise, grey zone → `escalate` to the model.

   An allowlist must **never** be able to cancel a denylist entry. Swapping
   these two steps would recreate exactly the hole closed by rejecting shell
   chaining: a safe prefix followed by an arbitrary command.
4. **M1.4 — Constrain the output.** The verdict must be parsable every time:
   structured output conforming to `codex_output_schema` (see
   `docs/CODEX_API.md` section 5.4), temperature ≤ 0.2, output capped at a few
   dozen tokens. Never parse free-form text.
5. **M1.5 — Expose the model in `/v1/models`** if Codex checks it.

**Measured local-model performance (2026-07-30, user hardware,
LFM2.5-1.2B-Instruct via llama-cli, CPU).** Kept because it is what ruled out
the local route.

| Metric | Measurement | Consequence |
|---|---|---|
| Prompt ingestion | **11.65 tok/s** (85.8 ms/token) | 8,000 tokens ≈ 11.5 min; 300 tokens ≈ 26 s; the non-cached prompt budget must stay ≤ ~300 tokens |
| Generation | **6.11 tok/s** (163.8 ms/token) | a full JSON verdict (~40 tokens) ≈ 7 s; `{"outcome":"allow"}` ≈ 1.5 s |
| Model loading | 0.33 s | negligible if the server stays resident |

Directives that follow:
- **A resident server is mandatory** if a local model is ever used again —
  never a CLI launch per request — with prompt caching enabled.
- End-to-end latency target for a verdict: **< 30 s** in the grey zone,
  **< 1 s** for cases settled by the pre-filter (M1.3).
- The review backend is **configurable** (model name and token budget), not
  hard-coded. If the hardware changes (a GPU), only the budgets change, not
  the architecture.

**Security invariant (non-negotiable).** If the review backend fails
(unreachable, timeout, unparsable output), the auto-review service must **fail
towards refusing auto-approval** (Codex asks a human again). NEVER a fail-open
that would approve by default: a failed compaction costs tokens, a wrong
approval runs a dangerous command.

### P2 — Multi-provider router (Cerebras + OpenRouter)

- Routing **by model name**, driven by configuration, not code: adding a
  provider means adding config entries, not `if` branches.
- OpenRouter already goes through LiteLLM (`openrouter/<model>`): start with
  extra `model_list` entries.
- Later, not now: fallback when a provider is down, and per-provider
  token/cost accounting in a local log.

### P3 — Local context optimisation (to restart from scratch)

A first version (`proxy/local_compactor.py`, removed 2026-07-30) summarised
large old `function_call_output` items through Ollama, fail-open. It was
removed because it put **one sequential HTTP call per output, 15 s timeout
each, on the critical path of every request** — for no result at all whenever
Ollama did not answer, leaving only failure traces behind. Any revival must be
asynchronous or cached, never blocking.

Leads, by decreasing value:
1. **Summary cache**: Codex resends the whole history EVERY turn, so the same
   output would be re-summarised on every request. Cache by hash of the
   original content.
2. **Deduplication**: repeated reads of the same file, identical command
   outputs → replace older occurrences with a short reference.
3. **Prompt optimisation**: local rewriting of verbose instructions before
   sending to the paid provider. CAREFUL: this risks degrading the big
   model's answers. Do it last, behind a flag disabled by default, and make it
   measurable (before/after token log).

Unchanged philosophy: **fail-open** (an optimisation that fails leaves the
request intact, it never breaks it).

### P4 — Hooks / agents on the proxy

Turn the pipeline into a chain of **pluggable stages** (pre-request and
post-response): secret redaction before sending to the cloud, metrics,
triggering auxiliary local agents. Do NOT build this abstraction before there
are at least two real consumers — no speculative architecture.

---

## 3. Permanent technical constraints

- **Modest hardware**: local models are small (~1B) and latency matters. Any
  local step on the critical path of a request must be bounded by a short
  timeout and be skippable.
- **API formats**: keep the **Responses** API (what Codex speaks,
  `function_call`/`function_call_output` items, `/v1/responses`) distinct from
  **Chat Completions** (what Cerebras/OpenRouter speak). Any locally built
  response must be in Responses format, including SSE streaming when Codex
  asks for it. LiteLLM already provides the bridge — lean on it before writing
  a translator by hand.
- **JSON types**: use `proxy/json_types.py` (`JSONValue`/`JSONDict`), never
  `Any` (except at the litellm boundary), never a bare `dict`.
- **Fail-open for optimisation, fail-safe for approval** (see P1).
- **Strict Red/Green TDD**, the Gauntlet (`ruff` + strict `mypy` + `pytest`),
  package-style `proxy.*` imports: see `CLAUDE.md`, which wins on conflict.
- **No new dependency** without explicit agreement from the user.
- **Secrets**: payloads contain the user's code and commands. Nothing goes to
  any external service other than the chosen provider; debug logs stay local
  and are flagged as sensitive.

## 4. Known traps (do not rediscover)

- LiteLLM hooks (`async_pre_call_hook`) **do not cover `/v1/responses`**; that
  is the whole reason `sanitizing_proxy.py` exists. Do not try again to do
  everything inside LiteLLM.
- Cerebras requires strict `function_call` → `function_call_output` adjacency;
  the sanitising in `request_sanitizer.py` is there for that, do not bypass it.
- `response.completed` needs a complete `usage` object; an empty one kills the
  whole stream (see `CODEX_API.md` section 6).
- The Cerebras free tier (8K context, 5 req/min) is unusable for agentic work;
  manual integration tests assume the Developer tier.
- `codex-config.toml` must point at port **4000** (the proxy), never 4001
  (internal LiteLLM).
- `uvicorn` without `--port` listens on 8000, and Codex then finds nobody.

## 5. Definition of "done" for each step

A step is done when: the behaviour is specified by a RED test first, the
implementation makes it pass, the Gauntlet is green, `README.md` and this
document are updated if visible behaviour changed, and the P0 pipeline still
works (manual verification with a real Codex session for any change touching
the request path).
