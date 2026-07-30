"""
Compaction de contexte via un modèle local (Ollama).

Codex renvoie l'historique complet à chaque tour -- un gros
function_call_output (ex: un `ls -R` de 2700 tokens) reste tel quel dans
CHAQUE requête suivante tant que la conversation continue, et se refacture
intégralement à chaque fois côté Cerebras.

Ce module résume localement (gratuit, sur ta machine) les anciens outputs
volumineux avant que la requête ne parte vers Cerebras -- le dernier output
(le plus récent) reste inchangé, au cas où le modèle en a encore besoin en
détail.

Fail-open : si Ollama n'est pas joignable ou échoue, on garde le contenu
original tel quel plutôt que de casser la requête.
"""
import httpx

OLLAMA_URL = "http://localhost:11434/api/chat"
OLLAMA_MODEL = "hf.co/LiquidAI/LFM2.5-1.2B-Instruct-GGUF"
COMPACT_THRESHOLD_CHARS = 1000  # en dessous, pas besoin de résumer
MAX_INPUT_CHARS = 8000          # tronque avant d'envoyer au modèle local

SUMMARIZE_SYSTEM_PROMPT = (
    "Tu résumes des sorties de commandes techniques (listings de fichiers, "
    "logs, erreurs) en 2-4 phrases denses. Garde impérativement les noms de "
    "fichiers, chemins, et messages d'erreur exacts s'il y en a. Ne reformule "
    "pas le code, ne commente pas, donne uniquement le résumé factuel."
)


async def _summarize_locally(text: str, client: httpx.AsyncClient) -> str | None:
    try:
        resp = await client.post(
            OLLAMA_URL,
            json={
                "model": OLLAMA_MODEL,
                "messages": [
                    {"role": "system", "content": SUMMARIZE_SYSTEM_PROMPT},
                    {"role": "user", "content": text[:MAX_INPUT_CHARS]},
                ],
                "stream": False,
            },
            timeout=15.0,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("message", {}).get("content")
    except Exception as exc:
        print(f"[local_compactor] échec résumé local (fail-open, contenu original conservé): {exc}")
        return None


def _is_function_call_output(item: dict) -> bool:
    return item.get("type") == "function_call_output" or (
        "call_id" in item and "output" in item
    )


async def compact_old_tool_outputs(data: dict) -> dict:
    """
    Résume localement tous les function_call_output volumineux SAUF le
    dernier (le plus récent) de l'historique 'input'.
    """
    input_items = data.get("input")
    if not isinstance(input_items, list):
        return data

    output_indices = [
        i for i, item in enumerate(input_items)
        if isinstance(item, dict) and _is_function_call_output(item)
    ]
    if len(output_indices) <= 1:
        return data  # rien à compacter, un seul output ou moins

    to_compact = output_indices[:-1]  # tous sauf le dernier

    async with httpx.AsyncClient() as client:
        for idx in to_compact:
            item = input_items[idx]
            text = item.get("output")
            if not isinstance(text, str) or len(text) < COMPACT_THRESHOLD_CHARS:
                continue
            summary = await _summarize_locally(text, client)
            if summary:
                item["output"] = (
                    f"[Résumé auto-généré localement -- sortie originale de "
                    f"{len(text)} caractères tronquée pour économiser des tokens]\n{summary}"
                )
            # si échec, on ne touche pas à item["output"] (fail-open)

    return data
