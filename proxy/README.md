# Codex CLI + Cerebras (gpt-oss-120b)

Fait tourner Codex CLI sur les crédits Cerebras au lieu d'OpenAI.

## Architecture

```
Codex CLI  --http://localhost:4000-->  sanitizing_proxy.py  --http://localhost:4001-->  LiteLLM  -->  Cerebras API
                                        (assainit le JSON            (bridge Responses
                                         Responses API avant           API -> Chat
                                         que LiteLLM le traduise)       Completions)
```

Codex parle nativement l'API Responses d'OpenAI (`/v1/responses`). Cerebras
n'expose que du Chat Completions (`/v1/chat/completions`). LiteLLM fait le
pont entre les deux, mais plusieurs de ses comportements par défaut sont
incompatibles avec Cerebras — d'où `sanitizing_proxy.py`, qui corrige la
requête *avant* qu'elle n'atteigne LiteLLM.

## Fichiers

| Fichier | Rôle |
|---|---|
| `start_cerebras_proxy.sh` | Installe les dépendances (via `uv`) et démarre les deux proxys |
| `litellm_cerebras_config.yaml` | Config LiteLLM : modèles Cerebras exposés, clé API, filtrage de paramètres |
| `custom_handler.py` | Fonction `sanitize_body()` : nettoie le JSON Responses API (voir "Problèmes résolus") |
| `sanitizing_proxy.py` | Reverse-proxy FastAPI (port 4000) qui applique `sanitize_body()` avant de relayer à LiteLLM (port 4001) |
| `approval_rules.py` / `approval_rules.json` | Pré-filtre déterministe des actions soumises à l'auto-review (liste noire, puis liste blanche) |
| `guardian.py` | Tranche localement les requêtes `codex-auto-review` que le pré-filtre décide avec certitude |
| `codex_sse.py` | Fabrique le flux SSE Responses attendu par Codex pour un verdict local |
| `codex-config.toml` | Config à copier dans `~/.codex/config.toml` pour pointer Codex sur ce proxy |

## Installation

### 1. Prérequis
- [`uv`](https://docs.astral.sh/uv/) installé (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
- Une clé API Cerebras (gratuite ou payante) depuis [cloud.cerebras.ai](https://cloud.cerebras.ai)
- Codex CLI installé

### 2. Lancer le proxy depuis le dépôt
```bash
export CEREBRAS_API_KEY="ta_clé_cerebras"
./proxy/start_cerebras_proxy.sh
```

Le script se lance **depuis la racine du dépôt**, jamais depuis une copie des
fichiers : `sanitizing_proxy` importe ses modules via le paquet `proxy.`, et
une copie détachée fige silencieusement le code à la version copiée — les
correctifs suivants n'ont alors aucun effet.
Laisse ce terminal ouvert tant que tu utilises Codex avec Cerebras.

### 3. Configurer Codex CLI

Un fichier prêt à l'emploi est fourni : **`codex-config.toml`**. Copie son
contenu dans `~/.codex/config.toml` (fusionne-le si tu as déjà des réglages
existants — ne remplace pas tout le fichier).

```bash
cat codex-config.toml >> ~/.codex/config.toml
```

Contenu :

```toml
model = "cerebras-gpt-oss-120b"   # ou cerebras-llama-3.3-70b / cerebras-qwen3-32b
model_provider = "cerebras-local"
model_reasoning_effort = "medium" # gpt-oss-120b n'accepte que low|medium|high

[model_providers.cerebras-local]
name = "Cerebras via LiteLLM"
base_url = "http://localhost:4000/v1"
env_key = "LITELLM_MASTER_KEY"
wire_api = "responses"

# Recommandé : évite le blocage au démarrage sur le connecteur Apps interne
# de Codex, qui ne peut pas s'authentifier sans session ChatGPT.
[apps._default]
enabled = false
```

**Points d'attention :**
- `base_url` pointe sur le port **4000** (`sanitizing_proxy.py`), jamais sur
  4001 (LiteLLM interne) — sinon tu perds tout l'assainissement du JSON.
- `env_key` doit correspondre à une variable d'environnement définie dans
  ton shell (voir ci-dessous), pas à la clé Cerebras elle-même.
- Pour changer de modèle, change `model` pour un des trois alias définis
  dans `litellm_cerebras_config.yaml` — pas un nom de modèle Cerebras brut
  (`gpt-oss-120b` seul ne fonctionnera pas, il faut le préfixe `cerebras-`).
- Si tu utilises déjà les connecteurs Apps de Codex (Slack, Notion...),
  retire le bloc `[apps._default]` — sinon garde-le pour éviter le
  blocage au démarrage documenté dans "Problèmes résolus" (#6).

Et dans ton shell (à ajouter dans `~/.bashrc`/`~/.zshrc` pour que ce soit permanent) :
```bash
export LITELLM_MASTER_KEY="sk-local-proxy-1234"   # doit matcher master_key dans le yaml
```

### 4. Lancer Codex normalement
```bash
codex
```

## Problèmes résolus (et pourquoi)

Cerebras (via l'API Chat Completions) et Codex (qui parle l'API Responses,
avec ses outils natifs propres à OpenAI) ne sont pas nativement
compatibles. Ce setup corrige, dans l'ordre où on les a rencontrés :

1. **`reasoning_effort` invalide** — gpt-oss-120b n'accepte que
   `low`/`medium`/`high` (pas `none`). Codex envoie parfois une valeur que
   Cerebras rejette ; on la force à `medium`.
2. **Champs non supportés par Cerebras** — `metadata`, `client_metadata`,
   `store`, `previous_response_id`, `parallel_tool_calls` (non supporté par
   gpt-oss-120b spécifiquement), etc. sont retirés avant l'envoi.
3. **Tools natifs Codex sans équivalent Cerebras** — les types `namespace`,
   `local_shell`, `computer_use`, `code_interpreter`, `file_search`,
   `image_generation` sont propres à l'infrastructure hébergée d'OpenAI et
   n'ont pas d'équivalent Chat Completions. On les retire du tableau
   `tools`.
4. **Tool_calls orphelins** — retirer les tools ci-dessus peut laisser des
   paires `function_call`/`function_call_output` incomplètes dans
   l'historique, ce que Cerebras rejette. On les ré-apparie et supprime les
   orphelins.
5. **Messages assistant vides intercalés** — Codex insère parfois un
   message assistant à contenu vide entre un `function_call` et son
   `function_call_output` (point de contrôle de streaming). Cerebras exige
   que le message tool suive *immédiatement* l'appel d'outil : on retire
   ces messages vides.
6. **`codex_apps` bloqué au démarrage** — connecteur interne Codex qui tente
   de joindre le backend ChatGPT et échoue sans session ChatGPT active.
   Désactivé via `[apps._default] enabled = false` dans `config.toml`
   (Codex, pas ce proxy).

**Point important** : `drop_params`/`additional_drop_params` de LiteLLM et
son système de hooks (`async_pre_call_hook`) ne couvrent PAS l'endpoint
`/v1/responses` — seulement `/chat/completions`, `/embeddings`,
`/image/generation`. C'est pourquoi `sanitizing_proxy.py` existe : c'est le
seul point où on peut fiablement intercepter et corriger ce que Codex
envoie.

## Débogage

Si une nouvelle erreur apparaît côté Cerebras, active le logging du JSON
brut avant/après assainissement :

```bash
export CEREBRAS_PROXY_DEBUG=1
./proxy/start_cerebras_proxy.sh
```

Le JSON de chaque requête `/v1/responses` est alors écrit dans
`/tmp/cerebras_proxy_debug.log` (attention : contient le contenu de tes
fichiers et commandes — à ne pas partager tel quel si sensible). Repère le
dernier bloc `AVANT sanitize_body` pour voir exactement ce que Codex a
envoyé.

## Limites connues

- Le tier gratuit Cerebras est limité à 8K tokens de contexte et 5
  requêtes/min — largement insuffisant pour un usage agentique avec Codex.
  Un tier payant (Developer) est recommandé (131K contexte, 1000 req/min).
- Les fonctionnalités natives OpenAI (génération d'images, computer use,
  code interpreter hébergé, recherche de fichiers) ne fonctionnent pas via
  Cerebras. Seuls les tools `function`/`mcp`/`web_search` passent.
- `[apps._default] enabled = false` désactive aussi les connecteurs Apps
  légitimes (Slack, Notion, etc.) si tu comptais les utiliser.
