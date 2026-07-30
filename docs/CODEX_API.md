# Observed API: Codex CLI ↔ proxy (/v1/responses)

Documented from a real capture (2026-07-30, 23 MB debug log): **94 requests** —
93 from the main model (`cerebras-gpt-oss-120b`) and **1 `codex-auto-review`
request** (the "Guardian").

The log contains **no responses at all** (0 `data:` lines across 94 requests):
the tee in `sanitizing_proxy.py` accumulated the whole stream and only wrote at
the very end, so a disconnect or an abandoned generator lost the entire
capture. The response format documented in section 6 therefore does not come
from a capture but from **Codex's own source**
(`codex-rs/codex-api/src/sse/responses.rs`, cross-checked against the strings
in the installed binary) — which is more reliable, since it describes what
Codex *accepts*, not merely what one server happened to emit.

Compact machine-readable summary: `docs/api_codex.summary.json` (preferred for
LLM agents; this file is the detailed reference).

---

## 1. Endpoints Codex uses

| Endpoint | Usage |
|---|---|
| `POST /v1/responses` | All traffic: main model AND auto-review. Always `stream: true`, `store: false`. |
| `GET /v1/models` | Referenced by error messages ("Call /v1/models to view available models"); not logged. Worth checking whether Codex calls it to validate model names. |

## 2. Common request envelope (Responses API)

Root keys present in **100% of the 94 requests** unless noted:

| Key | Observed value | Notes |
|---|---|---|
| `model` | `cerebras-gpt-oss-120b` (93) / `codex-auto-review` (1) | The natural routing point. |
| `instructions` | string ~20,751 chars (main) / 18,446 chars (Guardian) | Full system prompt, resent on EVERY request. |
| `input` | array of items (see section 3) | Full history resent every turn: 2,004 call/output pairs across the session's 93 requests. |
| `tools` | array (see section 4) | |
| `tool_choice` | `"auto"` | |
| `parallel_tool_calls` | `false` (main) / `true` (Guardian) | Stripped by sanitising (unsupported on gpt-oss-120b). |
| `reasoning` | `{"effort":"medium","summary":"auto"}` (main) / `{"effort":"low",...}` (Guardian) | |
| `store` | `false` | |
| `stream` | `true` | |
| `include` | `["reasoning.encrypted_content"]` | OpenAI-specific; drop for any non-OpenAI backend. |
| `prompt_cache_key` | `<session_id>` (main) / `guardian:<session_id>` (Guardian) | |
| `client_metadata` | `{x-codex-installation-id, thread_id, turn_id, session_id, x-codex-window-id, x-codex-turn-metadata}` | Stripped by sanitising. |
| `text` | **Guardian only**: `{"verbosity":"low","format":{json_schema}}` | See section 5.4. |

## 3. Items in the `input` array (3 types observed)

### 3.1 `message`
```json
{"type":"message", "id":"msg_<uuid7>", "role":"developer|user|assistant",
 "content":[{"type":"input_text|output_text", "text":"..."}]}
```
- `developer`: a `<permissions instructions>` block (sandbox/escalation rules).
- `user`: an `<environment_context>` block (XML: cwd, shell, date, timezone,
  workspace_roots, permission_profile), `AGENTS.md` content, user prompts.
- `assistant`: `output_text` — **sometimes with empty text** (streaming
  checkpoints inserted between a `function_call` and its output; this is what
  `request_sanitizer._is_empty_assistant_message` removes).
- No `reasoning` item appears in the history sent back.

### 3.2 `function_call`
```json
{"type":"function_call", "name":"exec_command",
 "arguments":"{\"cmd\":\"ls -R\"}", "call_id":"c5e8effe2"}
```
`arguments` is a **JSON string**, not an object.

### 3.3 `function_call_output`
```json
{"type":"function_call_output", "id":"fco_<uuid7>", "call_id":"c5e8effe2",
 "output":"Chunk ID: 00f63c\nWall time: 0.0000 seconds\nProcess exited with code 0\nOriginal token count: 2723\nOutput:\n<actual output, possibly truncated by Codex>"}
```
The `output` field carries a **systematic metadata prefix** (chunk ID, wall
time, exit code, original token count) followed by `Output:\n`. Useful for any
compaction: the prefix should be preserved verbatim, only the body after
`Output:\n` is worth summarising.

## 4. Tools declared by the main model

Type `function` (kept by sanitising) — 8 to 11 depending on session config:
`exec_command`, `write_stdin`, `view_image`, `list_mcp_resources`,
`list_mcp_resource_templates`, `read_mcp_resource`, `update_plan`,
`request_user_input`, and (present in 80 of 93 requests) `get_goal`,
`create_goal`, `update_goal`.

Non-`function` types (removed by sanitising, no Chat Completions equivalent):
- `{"type":"namespace","name":"multi_agent_v1","tools":[...]}` — sub-agents.
- `{"type":"namespace","name":"mcp__openaiDeveloperDocs","tools":[...]}` — MCP.
- `{"type":"web_search","external_web_access":false}`.

`exec_command` schema (the important one): `cmd` (required), `justification`,
`login`, `max_output_tokens`, `prefix_rule[]`, `sandbox_permissions`
(`use_default`|`require_escalated`), `shell`, `tty`, `workdir`,
`yield_time_ms`.

## 5. The `codex-auto-review` (Guardian) contract

### 5.1 Envelope
Same as section 2 with: `model="codex-auto-review"`, `reasoning.effort="low"`,
`parallel_tool_calls=true`, `prompt_cache_key="guardian:<session_id>"`, plus
the `text` field (5.4). Tools: `exec_command`, `write_stdin`, `view_image`
only — the Guardian is meant to be able to run **read-only checks** before
deciding ("You can only run read-only commands. You have no network access").

### 5.2 `instructions` (18,446 chars)
A complete security policy, sections in order: role (judge ONE planned
action), Evidence Handling (only user/developer messages, AGENTS.md and
`request_user_input` answers are trusted), User Authorization Scoring
(`high|medium|low|unknown`), Base Risk Taxonomy (`low|medium|high|critical`),
Security Policy (Environment Profile plus allow/deny rules per category: Data
Exfiltration, Credential Probing, Persistent Security Weakening, Destructive
Actions, Low-Risk Actions), Investigation Guidelines, Execution Environment,
Outcome Policy (deriving `outcome` from risk × authorization; default
thresholds: low/medium→allow, high→allow iff authorization≥medium,
critical→deny).

### 5.3 `input`: exactly 3 messages
1. `developer`: `<permissions instructions>` (empty in the capture).
2. `user`: the project's `AGENTS.md` content, prefixed
   `# AGENTS.md instructions for <path>`.
3. `user`: the transcript, split into **59 distinct `input_text` parts**:
   - part 0: an introduction ("The following is the Codex agent
     history... untrusted evidence"),
   - part 1: `>>> TRANSCRIPT START`,
   - following parts: **numbered and selective** entries (not the whole
     history) in text form:
     `[N] user: <text>`, `[N] tool exec_command call: {...}`,
     `[N] tool exec_command result: <metadata+output>`,
   - second-to-last parts: "Assess the exact planned action below. Use
     read-only tool checks when local state matters." followed by
     `Planned action JSON:` and the object:
     ```json
     {"command":["/usr/bin/zsh","-lc","git add config.py"],
      "cwd":"/home/user/project",
      "justification":"Commit config fix as an atomic step",
      "sandbox_permissions":"require_escalated",
      "tool":"exec_command","tty":false}
     ```
   - last part: `>>> APPROVAL REQUEST END`.

### 5.4 Expected output (`text.format`, json_schema `codex_output_schema`)
```json
{"risk_level":"low|medium|high|critical",
 "user_authorization":"unknown|low|medium|high",
 "outcome":"allow|deny",          // the only REQUIRED field
 "rationale":"one concise sentence"}
```
The prompt allows a shortcut for clearly benign actions: `{"outcome":"allow"}`
on its own. The answer must arrive as **Responses API SSE** (Codex calls
`/v1/responses` with `stream:true`) with that strict JSON as the final message.

### 5.5 How Codex behaves on failure (observed in the log)
Any HTTP error from the backend rejects the action:
`Rejected("This action was rejected due to unacceptable risk. Reason:
Automatic approval review failed: <error>")`, together with an instruction to
the agent **not to work around it** ("must not attempt to achieve the same
outcome via workaround").

Consequence: the fail-safe already lives in Codex — if our backend is
unavailable, letting the error propagate is acceptable (it falls back to
manual approval). **Never fabricate a default `allow`.**

## 6. Responses: the SSE stream Codex expects

Source: `codex-rs/codex-api/src/sse/responses.rs` (function
`process_responses_event`) plus strings extracted from the `codex` 0.146.0
binary. Each SSE event is a JSON object whose `type` field carries the name
below.

| Event | Fields Codex reads |
|---|---|
| `response.created` | only checks that `response` is present |
| `response.output_item.added` | `item` → `ResponseItem` |
| `response.output_item.done` | `item` → `ResponseItem` |
| `response.output_text.delta` | `delta`, `content_index` |
| `response.reasoning_text.delta` | `delta`, `content_index` |
| `response.reasoning_summary_text.delta` | `delta`, `summary_index` |
| `response.reasoning_summary_text.done` | `item_id`, `text`, `summary_index` |
| `response.reasoning_summary_part.added` | — |
| `response.custom_tool_call_input.delta` | `delta`, `item_id`, `call_id` |
| `response.failed` | `response.error`: `code`, `message`, `plan_type`, `resets_at` |
| `response.incomplete` | `response.incomplete_details.reason` |
| `response.completed` | `response`: `id`, `usage` (complete, see below), `end_turn` |

**Hard rule: `response.completed` is mandatory.** If the stream closes without
it, Codex fails with `stream closed before response.completed` — a fatal
error, not a silent degradation. Every other event is optional: the `delta`
events only drive progressive display.

**Hard rule 2: `usage` must carry all five `TokenUsage` fields** —
`input_tokens`, `cached_input_tokens`, `output_tokens`,
`reasoning_output_tokens`, `total_tokens`. They are not optional on the Codex
side: a `"usage":{}` fails the deserialisation of the event, and therefore the
whole stream, with

```
stream disconnected before completion: failed to parse ResponseCompleted:
missing field `input_tokens`
```

Observed in production on 2026-07-30 (an earlier version of this section
prescribed `"usage":{}`, which made every local verdict unusable). Field names
were verified in the string table of the installed `codex` binary, not
inferred.

**Direct consequence for the local Guardian**: building a valid response takes
only two events, the verdict being carried by the message item.

```
data: {"type":"response.created","response":{"id":"resp_local_1"}}

data: {"type":"response.output_item.done","item":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"{\"outcome\":\"allow\"}"}]}}

data: {"type":"response.completed","response":{"id":"resp_local_1","usage":{"input_tokens":0,"cached_input_tokens":0,"output_tokens":0,"reasoning_output_tokens":0,"total_tokens":0},"end_turn":true}}
```

Codex also applies an **idle timeout** to the stream (`idle timeout waiting for
SSE`): a local response must start emitting without waiting for the model to
finish computing.

## 7. Implementation consequences for the proxy

1. **Routing**: `model == "codex-auto-review"` is the only discriminator
   needed. The rest of the envelope is identical to main traffic.
2. **Prompt size**: instructions (18k chars ≈ 5k tokens) + AGENTS.md +
   transcript easily exceeds 8k tokens. Any backend used for review must
   either handle that, or — as this proxy does — receive a shrunk request.
3. **Tools**: a small model handles investigation tool calls badly. Minimal
   viable approach: strip `tools` from the review request and force a direct
   JSON answer. That is an accepted degradation (no read-only verification)
   and is documented as such.
4. **Structured output**: keep `text.format.schema` so the verdict is
   constrained, with `outcome` required. Never parse free-form text.
5. **Fields to drop**: `include` (encrypted_content), `client_metadata`,
   `store` — the same logic `sanitize_body()` applies for Cerebras.
6. **SSE response**: the format is known (section 6), so generating it
   ourselves is possible without depending on LiteLLM. Two events suffice,
   `response.completed` being the only mandatory one.

## 8. Figures worth knowing (context optimisation)

- The **entire** history is resent every turn: the session's 93 main requests
  total 2,900 `message` items and 2,004 call/output pairs.
- `instructions` (20,751 chars) is **identical in every request** — the prime
  candidate for provider-side caching (Cerebras prompt caching through the
  `prompt_cache_key` already transmitted).
- `function_call_output` items carry their own token count (`Original token
  count: N` in the metadata prefix): a compactor can prioritise without
  re-tokenising.
