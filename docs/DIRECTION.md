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
                     (assainissement + pré-filtre)        (bridge Responses→ChatCompletions)
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
   agents). Le **format de réponse SSE** est également établi (`API_CODEX.md`
   §6), non par capture mais par lecture de la source de Codex : deux
   événements suffisent à fabriquer une réponse valide, `response.completed`
   étant le seul obligatoire. Plus aucune inconnue ne bloque M1.2.
2. **M1.2 — Route dans le proxy avec réécriture du prompt (Option B
   imposée).** L'option « alias LiteLLM → ollama qui relaie la requête telle
   quelle » est **écartée** par les mesures de performance (voir encadré
   ci-dessous) : le prompt Guardian complet (~8 000+ tokens) prendrait ~11
   minutes à ingérer sur le matériel cible. Le proxy doit donc intercepter
   `model == "codex-auto-review"` (nouveau module `proxy/router.py`) et
   **construire une requête locale radicalement réduite** :
   - politique de sécurité distillée en un prompt système court et FIXE
     (≤ ~300 tokens) placé en préfixe pour profiter du cache de prompt du
     serveur local (`llama-server --cache-prompt` / Ollama `keep_alive`) —
     le préfixe fixe n'est alors ingéré qu'une fois par session ;
   - partie variable minimale : l'objet `Planned action JSON` (~100-200
     tokens), le `cwd`, et au plus les derniers messages user du transcript ;
   - PAS le prompt Guardian de 18 k chars, PAS les AGENTS.md complets,
     PAS le transcript intégral, PAS les tools (réponse directe forcée).

   **Fait (2026-07-30)** — `guardian.compact_review_request` : 48 942 chars
   ramenés à 884 (~220 tokens) sur une requête réaliste.

   **Décision (2026-07-30) — la zone grise part chez Cerebras, pas au modèle
   local.** Même réduite à 220 tokens, la requête demande ~19 s d'ingestion à
   11,65 tok/s, au-delà du délai d'inactivité de Codex ; et un 1.2B juge mal
   une question de sécurité. L'escalade est donc routée vers
   `cerebras-review` — le modèle déjà en service, `reasoning_effort: low`
   (plancher de gpt-oss-120b, qui refuse `none`), pour ~0,0001 $ par verdict.

   Conséquence à assumer : **la commande jugée quitte la machine**, ce qui
   entame l'objectif n°4 (remplacer les services OpenAI par des équivalents
   locaux). L'exposition reste bornée — le même trafic Codex passe déjà par
   Cerebras, et le pré-filtre (M1.3) tranche localement la majorité des cas
   sans aucun appel. Revenir au local suppose un GPU, pas un réglage.
3. **M1.3 — Pré-filtre déterministe AVANT le LLM.** La plupart des actions
   évaluées sont banales (`git add`, `ls`, lectures de fichiers...). Une
   liste de règles locales (allowlist de préfixes de commandes sûrs,
   denylist de motifs destructeurs évidents) tranche instantanément les cas
   clairs ; le modèle local n'est consulté que pour la zone grise. Les
   règles sont de la config, pas du code en dur. Un cas non couvert par les
   règles ET par un verdict LLM dans le délai imparti → erreur remontée
   (= refus d'auto-approbation côté Codex, voir invariant).

   **Décision (2026-07-30) — liste noire ET liste blanche, combinables.**
   Le proxy tourne en local, sur la machine de l'utilisateur : la liste noire
   est donc un choix acceptable, là où elle serait insuffisante pour un
   service exposé. Les deux mécanismes coexistent et sont configurables :

   | Niveau | Liste noire | Liste blanche |
   |---|---|---|
   | Caractères | métacaractères shell rejetés (défaut, permissif) | jeu de caractères autorisés, tout le reste rejeté (mode strict, optionnel) |
   | Commandes | motifs destructeurs → jamais approuvés | préfixes sûrs → approuvés immédiatement |

   **L'ordre d'évaluation est une propriété de sécurité, pas une préférence :**
   1. liste noire (caractères, puis motifs) — un refus l'emporte toujours ;
   2. liste blanche de préfixes — approbation immédiate ;
   3. sinon, zone grise → `escalate` vers le modèle local.

   Une liste blanche ne doit **jamais** pouvoir annuler une entrée de liste
   noire. Inverser ces deux étapes recréerait exactement la faille corrigée
   par le rejet des enchaînements shell : un préfixe sûr suivi d'une commande
   arbitraire.
4. **M1.4 — Contraindre la sortie.** Le verdict doit être parsable à tous
   les coups : sortie structurée (format JSON schema d'Ollama, ou grammaire
   GBNF de llama.cpp) conforme à `codex_output_schema` (voir
   `docs/API_CODEX.md` §5.4), température ≤ 0.2, sortie plafonnée à
   quelques dizaines de tokens. Ne jamais parser du texte libre.
5. **M1.5 — Exposer le modèle dans `/v1/models`** si Codex le vérifie.

**Performance mesurée du modèle local (2026-07-30, matériel de
l'utilisateur, LFM2.5-1.2B-Instruct via llama-cli, CPU).**

| Métrique | Mesure | Conséquence |
|---|---|---|
| Ingestion du prompt | **11,65 tok/s** (85,8 ms/token) | 8 000 tokens ≈ 11,5 min ; 300 tokens ≈ 26 s ; le budget de prompt NON caché doit rester ≤ ~300 tokens |
| Génération | **6,11 tok/s** (163,8 ms/token) | verdict JSON complet (~40 tokens) ≈ 7 s ; `{"outcome":"allow"}` ≈ 1,5 s |
| Chargement modèle | 0,33 s | négligeable si le serveur reste résident |

Directives qui en découlent :
- **Serveur résident obligatoire** (llama-server ou Ollama avec
  `keep_alive`), jamais un lancement de CLI par requête, et cache de prompt
  activé pour amortir le préfixe fixe.
- Préférer **`LFM2.5-1.2B-Instruct`** (non-thinking). La variante Thinking testée par l'utilisateur
  génère trop de tokens de raisonnement : à ~6 tok/s, chaque token de
  réflexion coûte 164 ms — proscrite pour ce service.
- Objectif de latence de bout en bout pour un verdict : **< 30 s** dans la
  zone grise, **< 1 s** pour les cas tranchés par le pré-filtre (M1.3).
- Le backend local est **configurable** (URL + nom de modèle + budget de
  tokens), pas codé en dur. Si le matériel évolue (GPU), seuls les budgets
  changent, pas l'architecture.

**Invariant de sécurité (non négociable).** En cas d'échec du backend de
review (injoignable, timeout, sortie non parsable), le service auto-review doit
**échouer vers le refus d'auto-approbation** (Codex redemande à l'humain).
JAMAIS de fail-open qui approuverait une commande par défaut : une compaction
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

### P3 — Optimisation locale du contexte (à reprendre de zéro)

Une première version (`proxy/local_compactor.py`, retirée le 2026-07-30)
résumait les vieux `function_call_output` volumineux via Ollama, fail-open.
Retirée parce qu'elle plaçait **un appel HTTP séquentiel par output, timeout
15 s chacun, sur le chemin critique de chaque requête** — pour un résultat nul
dès qu'Ollama ne répond pas, seules les traces d'échec restant visibles. Toute
reprise doit être asynchrone ou mise en cache, jamais bloquante.

Pistes par ordre de rendement décroissant :
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
