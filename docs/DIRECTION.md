# DIRECTION DU PROJET — agents_proxy

Document de cap pour tout agent (humain ou LLM) travaillant sur cette codebase.
À lire APRÈS `CLAUDE.md` (qui fixe les règles d'exécution) : ici on fixe le QUOI
et le POURQUOI ; `CLAUDE.md` fixe le COMMENT.

---

## 1. Vision

Un **proxy/routeur local** placé entre des outils agentiques (Codex CLI d'abord,
d'autres ensuite) et des fournisseurs de LLM, qui :

1. **Traduit et assainit** les requêtes pour rendre compatibles des outils et des
   providers qui ne se parlent pas nativement (Codex parle l'API Responses
   d'OpenAI ; Cerebras/OpenRouter parlent Chat Completions).
2. **Route par nom de modèle** vers le bon backend : Cerebras (dev API),
   OpenRouter, ou des **petits modèles locaux** (famille Liquid LFM2.5 via
   Ollama / llama.cpp).
3. **Réduit les coûts en tokens** en optimisant le contexte localement
   (compaction d'historique, déduplication, optimisation de prompts) avec des
   modèles locaux gratuits.
4. **Remplace les services propriétaires OpenAI** dont Codex dépend
   (`codex-auto-review` en premier) par des équivalents locaux.

Architecture actuelle (fonctionnelle, à préserver) :

```
Codex CLI ──:4000──> proxy/sanitizing_proxy.py ──:4001──> LiteLLM ──> Cerebras
                     (assainissement + compaction)        (bridge Responses→ChatCompletions)
```

Architecture cible :

```
Outil agentique ──> PROXY (port 4000)
                     ├─ pipeline de stages (assainir, compacter, optimiser, hooks)
                     ├─ ROUTEUR par nom de modèle
                     │    ├─ cerebras-*        ──> LiteLLM ──> Cerebras
                     │    ├─ or-* / openrouter ──> LiteLLM ──> OpenRouter
                     │    └─ codex-auto-review,
                     │       local-*           ──> modèle local (Ollama/llama-server)
                     └─ réponses retraduites au format attendu par l'outil (Responses API)
```

---

## 2. Priorités ordonnées

Ne PAS travailler sur une priorité N+1 tant que la priorité N n'est pas
fonctionnelle et testée. En cas de doute sur le périmètre : demander.

### P0 — Le pipeline Cerebras existant reste fonctionnel (invariant permanent)

Tout changement doit préserver le fonctionnement actuel documenté dans
`proxy/README.md`. C'est la seule chose en production chez l'utilisateur.
Toute régression ici annule les bénéfices de n'importe quelle nouveauté.

### P1 — Service local `codex-auto-review`

**Problème.** Codex envoie `POST /v1/responses` avec `model=codex-auto-review`
pour son évaluation automatique d'approbation d'outils. Le proxy relaie
aveuglément vers LiteLLM/Cerebras → 400 « Invalid model name » → Codex affiche
`Automatic approval review denied (risk: high)` et retombe sur l'approbation
manuelle systématique.

**Solution cible.** Le proxy détecte `model == "codex-auto-review"` et sert la
requête via un petit modèle local au lieu de la relayer vers Cerebras.

**Étapes imposées, dans l'ordre :**

1. **M1.1 — Capturer le contrat réel. ✅ FAIT (2026-07-30).** Le contrat
   complet est documenté dans `docs/API_CODEX.md` (détail) et
   `docs/api_codex.summary.json` (résumé machine — à lire en priorité par les
   agents). Reste un trou : le **format de réponse SSE** n'a pas été capturé —
   à compléter lors d'une prochaine session avec le tee de réponse actif
   (`CEREBRAS_PROXY_DEBUG=1`, réponses loggées par `sanitizing_proxy.py`).
2. **M1.2 — Route minimale.** Deux options, à trancher après M1.1 :
   - *Option A (préférée si suffisante)* : ajouter une entrée `model_list`
     dans `litellm_cerebras_config.yaml` avec `model_name: codex-auto-review`
     pointant vers `ollama/<modèle-local>`. Zéro code Python, LiteLLM fait
     déjà le bridge Responses→ChatCompletions. Tester d'abord ça.
   - *Option B (si A ne suffit pas — ex. besoin d'un prompt réécrit ou d'une
     sortie contrainte)* : brancher la décision de routage dans le proxy
     (nouveau module `proxy/router.py`), qui délègue à un backend local.
3. **M1.3 — Contraindre la sortie.** Le verdict d'approbation doit être
   parsable par Codex à tous les coups. Utiliser la sortie structurée
   (format JSON schema d'Ollama, ou grammaire GBNF de llama.cpp) plutôt que
   d'espérer que le modèle réponde bien. Température basse (≤ 0.2).
4. **M1.4 — Exposer le modèle dans `/v1/models`** si Codex le vérifie.

**Choix du modèle local.** L'utilisateur a testé `LFM2.5-1.2B-Thinking` : bon
mais trop lent (reasoning trop long) sur son matériel. Directives :
- Préférer **`LFM2.5-1.2B-Instruct`** (variante non-thinking, déjà utilisée par
  `local_compactor.py` : `hf.co/LiquidAI/LFM2.5-1.2B-Instruct-GGUF`) — la tâche
  « approuver/refuser une commande » est de la classification, pas du
  raisonnement long.
- Si la variante Thinking est retenue malgré tout : plafonner les tokens de
  sortie et/ou couper la phase de réflexion (option du serveur), sinon la
  latence tue l'usage interactif.
- Le backend local est **configurable** (URL + nom de modèle), pas codé en dur.

**Invariant de sécurité (non négociable).** En cas d'échec du modèle local
(Ollama éteint, timeout, sortie non parsable), le service auto-review doit
**échouer vers le refus d'auto-approbation** (Codex redemande à l'humain).
JAMAIS de fail-open qui approuverait une commande par défaut. C'est l'inverse
de la philosophie fail-open du compacteur — et c'est voulu : une compaction
ratée coûte des tokens, une approbation ratée exécute une commande dangereuse.

### P2 — Routeur multi-provider (Cerebras + OpenRouter)

- Routage **par nom de modèle**, piloté par la config, pas par du code :
  ajouter un provider = ajouter des entrées de config, pas des `if`.
- OpenRouter passe déjà par LiteLLM (`openrouter/<model>`) : commencer par des
  entrées `model_list` supplémentaires, même logique que l'option A de P1.
- Le module `proxy/router.py` (s'il a été créé en P1) devient le point unique
  de décision : nom de modèle entrant → backend + transformations à appliquer.
- Prévoir (plus tard, pas tout de suite) : fallback si un provider est down,
  et comptage tokens/coût par provider dans un log local.

### P3 — Optimisation locale du contexte (étend l'existant)

`proxy/local_compactor.py` fait déjà : résumé des vieux `function_call_output`
volumineux (> 1000 chars), sauf le dernier, via Ollama, fail-open.

Extensions par ordre de rendement décroissant :
1. **Cache des résumés** : Codex renvoie tout l'historique à CHAQUE tour, donc
   le même output est re-résumé à chaque requête → gaspillage local. Mettre en
   cache (clé = hash du contenu original) le résumé déjà produit.
2. **Déduplication** : lectures répétées du même fichier, mêmes sorties de
   commandes → remplacer les occurrences anciennes par une référence courte.
3. **Optimisation de prompts** : réécriture locale des instructions verbeuses
   avant envoi au provider payant. ATTENTION : risque de dégrader la qualité
   des réponses du gros modèle. À faire en dernier, derrière un flag
   désactivé par défaut, et mesurable (log avant/après en tokens).

Philosophie inchangée : **fail-open** (une optimisation qui échoue laisse la
requête intacte, elle ne la casse jamais).

### P4 — Hooks / agents sur le proxy

Transformer le pipeline en chaîne de **stages enfichables** (pré-requête et
post-réponse) configurables : redaction de secrets avant envoi au cloud,
métriques, déclenchement d'agents auxiliaires locaux. Ne PAS construire cette
abstraction avant d'avoir au moins deux consommateurs réels (P1 et P3 en
fourniront) — pas d'architecture spéculative.

---

## 3. Contraintes techniques permanentes

- **Matériel modeste** : les modèles locaux sont petits (~1B), les latences
  comptent. Toute étape locale sur le chemin critique d'une requête doit être
  bornée par un timeout court et contournable.
- **Formats d'API** : bien distinguer l'API **Responses** (ce que parle Codex,
  items `function_call`/`function_call_output`, endpoint `/v1/responses`) de
  **Chat Completions** (ce que parlent Cerebras/OpenRouter/Ollama). Toute
  réponse fabriquée localement doit être au format Responses, y compris en
  streaming SSE si Codex le demande. Le bridge existant est fait par LiteLLM —
  s'appuyer dessus avant d'écrire un traducteur maison.
- **Types JSON** : utiliser `proxy/json_types.py` (`JSONValue`/`JSONDict`),
  jamais `Any` (frontière litellm exceptée), jamais `dict` nu.
- **Fail-open pour l'optimisation, fail-safe pour l'approbation** (voir P1).
- **TDD strict Red/Green**, Gauntlet (`ruff` + `mypy` strict + `pytest`),
  imports en paquet `proxy.*` : voir `CLAUDE.md`, qui prime en cas de conflit.
- **Pas de nouvelle dépendance** (pip) sans accord explicite de l'utilisateur.
- **Secrets** : les payloads contiennent le code et les commandes de
  l'utilisateur. Rien ne part vers un service externe autre que le provider
  choisi ; les logs de debug restent locaux et sont signalés comme sensibles.

## 4. Pièges connus (ne pas redécouvrir)

- Les hooks LiteLLM (`async_pre_call_hook`) **ne couvrent pas `/v1/responses`** ;
  c'est la raison d'être de `sanitizing_proxy.py`. Ne pas retenter de tout
  faire dans LiteLLM.
- Cerebras exige l'adjacence stricte `function_call` → `function_call_output` ;
  l'assainissement de `custom_handler.py` est là pour ça, ne pas le contourner.
- Le tier gratuit Cerebras (8K contexte, 5 req/min) est inutilisable en
  agentique ; les tests d'intégration manuels supposent le tier Developer.
- `codex-config.toml` doit pointer sur le port **4000** (proxy), jamais 4001
  (LiteLLM interne).

## 5. Définition de « terminé » pour chaque étape

Une étape est terminée quand : le comportement est spécifié par un test RED
d'abord, l'implémentation le fait passer, le Gauntlet est vert, `proxy/README.md`
et ce document sont mis à jour si le comportement visible a changé, et le
pipeline P0 fonctionne toujours (vérification manuelle avec une vraie session
Codex pour tout changement touchant le chemin des requêtes).
