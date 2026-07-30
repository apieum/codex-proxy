#!/usr/bin/env bash
set -euo pipefail

# NOTE gpt-oss-120b (tier payant "Developer" Cerebras) :
#   - contexte 131k tokens, sortie max 40k tokens
#   - 1000 requêtes/min, 1M tokens entrée/min — largement suffisant pour Codex en agentique
#   - reasoning_effort valides : low | medium | high (PAS "none")
#   - $0.35 / M tokens en entrée, $0.75 / M tokens en sortie

if [[ -z "${CEREBRAS_API_KEY:-}" ]]; then
  echo "Erreur : variable CEREBRAS_API_KEY non définie."
  echo "Fais d'abord : export CEREBRAS_API_KEY='ta_clé_cerebras'"
  exit 1
fi

if ! command -v uv &> /dev/null; then
  echo "uv n'est pas installé. Installation :"
  echo "  curl -LsSf https://astral.sh/uv/install.sh | sh"
  exit 1
fi

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

# Le port 4000 est celui que Codex doit utiliser ; LiteLLM (4001) est démarré
# par l'application elle-même, pour que `uvicorn` lancé à la main fonctionne
# aussi. Lancé depuis la racine du dépôt : `proxy` doit être résoluble comme
# paquet Python.
echo "Démarrage du proxy sur http://localhost:4000 (LiteLLM suivra sur 4001) ..."
exec uv run uvicorn proxy.sanitizing_proxy:app --host 0.0.0.0 --port 4000
