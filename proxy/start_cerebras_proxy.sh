#!/usr/bin/env bash
set -euo pipefail

# NOTE gpt-oss-120b (tier payant "Developer" Cerebras) :
#   - contexte 131k tokens, sortie max 40k tokens
#   - 1000 requêtes/min, 1M tokens entrée/min — largement suffisant pour Codex en agentique
#   - reasoning_effort valides : low | medium | high (PAS "none")
#   - $0.35 / M tokens en entrée, $0.75 / M tokens en sortie

# --- Vérifie qu'Ollama tourne (nécessaire pour codex-auto-review + compaction locale) ---
if ! curl -s -o /dev/null "http://localhost:11434/api/tags"; then
  echo "⚠ Ollama ne répond pas sur localhost:11434."
  echo "  Lance-le d'abord : ollama serve"
  echo "  Et vérifie que le modèle est bien pull : ollama pull hf.co/LiquidAI/LFM2.5-1.2B-Instruct-GGUF"
  echo "  On continue quand même (fail-open : compaction et auto-review se dégraderont silencieusement)."
fi
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
cd "$SCRIPT_DIR"
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
uv run --python "$VENV_DIR/bin/python" uvicorn sanitizing_proxy:app --host 0.0.0.0 --port 4000
