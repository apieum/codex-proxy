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

# --- Vérifie que uv est installé ---
if ! command -v uv &> /dev/null; then
  echo "uv n'est pas installé. Installation :"
  echo "  curl -LsSf https://astral.sh/uv/install.sh | sh"
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
CONFIG_PATH="$SCRIPT_DIR/litellm_cerebras_config.yaml"
VENV_DIR="$SCRIPT_DIR/.venv"

# --- Crée le venv si absent, et garde litellm à jour (correctifs fréquents sur le bridge responses) ---
if [[ ! -d "$VENV_DIR" ]]; then
  echo "Création du venv via uv..."
  uv venv "$VENV_DIR"
fi
uv pip install --python "$VENV_DIR/bin/python" --upgrade 'litellm[proxy]' fastapi httpx uvicorn

cleanup() {
  echo "Arrêt des proxys..."
  [[ -n "${LITELLM_PID:-}" ]] && kill "$LITELLM_PID" 2>/dev/null
}
trap cleanup EXIT

echo "Démarrage de LiteLLM (interne, port 4001) ..."
cd "$REPO_ROOT"
uv run --python "$VENV_DIR/bin/python" litellm --config "$CONFIG_PATH" --port 4001 &
LITELLM_PID=$!

echo "Attente du démarrage de LiteLLM..."
for i in $(seq 1 30); do
  if curl -s -f -o /dev/null -H "Authorization: Bearer sk-local-proxy-1234" "http://127.0.0.1:4001/v1/models"; then
    break
  fi
  sleep 1
done

echo "Démarrage du proxy assainisseur sur http://localhost:4000 (celui que Codex doit utiliser) ..."
# Lancé depuis REPO_ROOT (pas SCRIPT_DIR) pour que `proxy` soit résoluble comme
# paquet Python -- sanitizing_proxy.py importe via `proxy.custom_handler`
# plutôt qu'en imports plats.
uv run --python "$VENV_DIR/bin/python" uvicorn proxy.sanitizing_proxy:app --host 0.0.0.0 --port 4000
