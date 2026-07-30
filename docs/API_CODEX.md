# API observée : Codex CLI ↔ proxy (/v1/responses)

Documentation issue d'une capture réelle (2026-07-30, `/tmp/cerebras_proxy_debug.log`,
23 Mo) : **94 requêtes** — 93 du modèle principal (`cerebras-gpt-oss-120b`) et
**1 requête `codex-auto-review`** (le « Guardian »). Les réponses SSE n'ont pas
été capturées dans cette session (le tee de réponse n'était pas actif) — la
partie réponse reste à documenter lors d'une prochaine capture.

Résumé machine compact : `docs/api_codex.summary.json` (à préférer pour les
agents LLM, ce fichier-ci est la référence détaillée).

---

## 1. Endpoints utilisés par Codex

| Endpoint | Usage |
|---|---|
| `POST /v1/responses` | Tout le trafic : modèle principal ET auto-review. Toujours `stream: true`, `store: false`. |
| `GET /v1/models` | Référencé par les messages d'erreur (« Call /v1/models to view available models ») ; non loggé. Vérifier si Codex l'appelle pour valider les noms de modèles. |

## 2. Enveloppe de requête commune (Responses API)

Clés racine présentes dans **100 % des 94 requêtes** (sauf mention) :

| Clé | Valeur observée | Notes |
|---|---|---|
| `model` | `cerebras-gpt-oss-120b` (93) / `codex-auto-review` (1) | Point de routage naturel. |
| `instructions` | string ~20 751 chars (principal) / 18 446 chars (Guardian) | Prompt système complet, renvoyé À CHAQUE requête. |
| `input` | array d'items (voir §3) | Historique complet renvoyé à chaque tour : 2 004 paires call/output cumulées sur les 93 requêtes de la session. |
| `tools` | array (voir §4) | |
| `tool_choice` | `"auto"` | |
| `parallel_tool_calls` | `false` (principal) / `true` (Guardian) | Retiré par sanitize (non supporté gpt-oss-120b). |
| `reasoning` | `{"effort":"medium","summary":"auto"}` (principal) / `{"effort":"low",...}` (Guardian) | |
| `store` | `false` | |
| `stream` | `true` | |
| `include` | `["reasoning.encrypted_content"]` | Propre à OpenAI ; à dropper pour tout backend non-OpenAI. |
| `prompt_cache_key` | `<session_id>` (principal) / `guardian:<session_id>` (Guardian) | |
| `client_metadata` | `{x-codex-installation-id, thread_id, turn_id, session_id, x-codex-window-id, x-codex-turn-metadata}` | Retiré par sanitize. |
| `text` | **Guardian uniquement** : `{"verbosity":"low","format":{json_schema}}` | Voir §5.4. |

## 3. Items du tableau `input` (3 types observés)

### 3.1 `message`
```json
{"type":"message", "id":"msg_<uuid7>", "role":"developer|user|assistant",
 "content":[{"type":"input_text|output_text", "text":"..."}]}
```
- `developer` : bloc `<permissions instructions>` (règles de sandbox/escalade).
- `user` : bloc `<environment_context>` (XML : cwd, shell, date, timezone,
  workspace_roots, permission_profile), contenu des `AGENTS.md`, prompts de
  l'utilisateur.
- `assistant` : `output_text` — **parfois à texte vide** (points de contrôle de
  streaming insérés entre un `function_call` et son output ; c'est ce que
  `custom_handler._is_empty_assistant_message` retire).
- Aucun item de type `reasoning` dans l'historique renvoyé.

### 3.2 `function_call`
```json
{"type":"function_call", "name":"exec_command",
 "arguments":"{\"cmd\":\"ls -R\"}", "call_id":"c5e8effe2"}
```
`arguments` est une **string JSON**, pas un objet.

### 3.3 `function_call_output`
```json
{"type":"function_call_output", "id":"fco_<uuid7>", "call_id":"c5e8effe2",
 "output":"Chunk ID: 00f63c\nWall time: 0.0000 seconds\nProcess exited with code 0\nOriginal token count: 2723\nOutput:\n<sortie réelle, éventuellement tronquée par Codex>"}
```
Le champ `output` a un **préfixe méta systématique** (Chunk ID, Wall time, code
de sortie, compte de tokens original) suivi de `Output:\n`. Exploitable par le
compacteur : le préfixe est à préserver tel quel, seul le corps après
`Output:\n` doit être résumé.

## 4. Tools déclarés par le modèle principal

Type `function` (conservés par sanitize) — 8 à 11 selon la config de session :
`exec_command`, `write_stdin`, `view_image`, `list_mcp_resources`,
`list_mcp_resource_templates`, `read_mcp_resource`, `update_plan`,
`request_user_input`, et (présents dans 80/93 requêtes) `get_goal`,
`create_goal`, `update_goal`.

Types non-`function` (retirés par sanitize, sans équivalent Chat Completions) :
- `{"type":"namespace","name":"multi_agent_v1","tools":[...]}` — sous-agents.
- `{"type":"namespace","name":"mcp__openaiDeveloperDocs","tools":[...]}` — MCP.
- `{"type":"web_search","external_web_access":false}`.

Schéma `exec_command` (le plus important) : `cmd` (requis), `justification`,
`login`, `max_output_tokens`, `prefix_rule[]`, `sandbox_permissions`
(`use_default`|`require_escalated`), `shell`, `tty`, `workdir`, `yield_time_ms`.

## 5. Contrat `codex-auto-review` (Guardian) — cible de la priorité P1

### 5.1 Enveloppe
Identique au §2 avec : `model="codex-auto-review"`, `reasoning.effort="low"`,
`parallel_tool_calls=true`, `prompt_cache_key="guardian:<session_id>"`, et le
champ `text` (§5.4). Tools : `exec_command`, `write_stdin`, `view_image`
uniquement — le Guardian est censé pouvoir faire des **vérifications
lecture seule** avant de trancher (« You can only run read-only commands. You
have no network access »).

### 5.2 `instructions` (18 446 chars)
Politique de sécurité complète, sections dans l'ordre : rôle (juger UNE action
planifiée), Evidence Handling (seuls les messages user/developer, AGENTS.md et
réponses `request_user_input` sont de confiance), User Authorization Scoring
(`high|medium|low|unknown`), Base Risk Taxonomy (`low|medium|high|critical`),
Security Policy (Environment Profile + règles allow/deny par catégorie : Data
Exfiltration, Credential Probing, Persistent Security Weakening, Destructive
Actions, Low-Risk Actions), Investigation Guidelines, Execution Environment,
Outcome Policy (dérivation de `outcome` depuis risk×authorization ; seuils par
défaut : low/medium→allow, high→allow ssi authorization≥medium, critical→deny).

### 5.3 `input` : exactement 3 messages
1. `developer` : `<permissions instructions>` (vide dans la capture).
2. `user` : contenu des `AGENTS.md` du projet, préfixé
   `# AGENTS.md instructions for <chemin>`.
3. `user` : le transcript, en **59 parts `input_text`** distinctes :
   - part 0 : phrase d'introduction (« The following is the Codex agent
     history... untrusted evidence »),
   - part 1 : `>>> TRANSCRIPT START`,
   - parts suivantes : entrées **numérotées et sélectives** (pas tout
     l'historique) au format texte :
     `[N] user: <texte>`, `[N] tool exec_command call: {...}`,
     `[N] tool exec_command result: <méta+output>`,
   - avant-dernières parts : « Assess the exact planned action below. Use
     read-only tool checks when local state matters. » puis
     `Planned action JSON:` suivi de l'objet :
     ```json
     {"command":["/usr/bin/zsh","-lc","git add extraction/config.py"],
      "cwd":"/home/user/project",
      "justification":"Commit config fix as an atomic step",
      "sandbox_permissions":"require_escalated",
      "tool":"exec_command","tty":false}
     ```
   - dernière part : `>>> APPROVAL REQUEST END`.

### 5.4 Sortie attendue (`text.format`, json_schema `codex_output_schema`)
```json
{"risk_level":"low|medium|high|critical",
 "user_authorization":"unknown|low|medium|high",
 "outcome":"allow|deny",          // seul champ REQUIS
 "rationale":"une phrase concise"}
```
Raccourci autorisé par le prompt pour les actions clairement bénignes :
`{"outcome":"allow"}` seul. La réponse doit arriver au **format Responses API
en SSE** (Codex appelle `/v1/responses` en `stream:true`) avec ce JSON strict
comme message final.

### 5.5 Comportement de Codex en cas d'échec (observé dans le log)
Toute erreur HTTP du backend fait rejeter l'action :
`Rejected("This action was rejected due to unacceptable risk. Reason:
Automatic approval review failed: <erreur>")`, assortie de l'instruction à
l'agent de **ne pas contourner** (« must not attempt to achieve the same
outcome via workaround »). Conséquence pour P1 : le fail-safe est déjà côté
Codex — si notre backend local est indisponible, laisser l'erreur remonter est
acceptable (retour à l'approbation manuelle). **Ne jamais fabriquer un
`allow` par défaut.**

## 6. Implications d'implémentation pour le proxy

1. **Routage** : `model == "codex-auto-review"` est le seul discriminant
   nécessaire. Tout le reste de l'enveloppe est identique au trafic principal.
2. **Taille du prompt** : instructions (18 k chars ≈ 5 k tokens) + AGENTS.md +
   transcript → dépasse facilement 8 k tokens. Un contexte de 4 096 (config
   llama-cli actuelle de l'utilisateur) est **insuffisant** ; LFM2.5 supporte
   32 k : configurer le serveur local en conséquence (`-c 16384` minimum) ou
   compresser les instructions localement (lien avec P3).
3. **Tools** : un modèle 1.2B gèrera mal les tool calls d'investigation.
   Approche minimale viable : retirer `tools` de la requête locale et forcer
   la réponse JSON directe. C'est une dégradation acceptée (pas de
   vérification lecture seule) à documenter.
4. **Sortie structurée** : mapper `text.format.schema` vers le paramètre
   `format` d'Ollama (JSON schema) ou une grammaire GBNF llama.cpp, avec
   `outcome` requis. Ne jamais parser du texte libre.
5. **Champs à dropper localement** : `include` (encrypted_content),
   `client_metadata`, `prompt_cache_key`, `store` — même logique que
   `sanitize_body()` pour Cerebras.
6. **Réponse SSE** : à fabriquer au format Responses API. Deux options :
   passer par LiteLLM (bridge déjà existant, option A de `docs/DIRECTION.md`)
   ou générer les événements SSE soi-même (option B — nécessite d'abord une
   capture du format de réponse, non disponible à ce jour).

## 7. Chiffres utiles pour P3 (compaction)

- L'historique **complet** repart à chaque tour : 93 requêtes principales de la
  session totalisent 2 900 items `message` et 2 004 paires call/output.
- `instructions` (20 751 chars) est **identique dans chaque requête** — c'est
  le candidat n°1 au cache côté provider (prompt caching Cerebras via
  `prompt_cache_key` déjà transmis).
- Les `function_call_output` portent leur propre compte de tokens
  (`Original token count: N` dans le préfixe méta) : le compacteur peut
  prioriser sans re-tokeniser.
