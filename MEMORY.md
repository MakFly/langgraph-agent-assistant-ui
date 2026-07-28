# MEMORY.md — reprise du projet en 5 minutes

> Fiche de reprise rédigée le **28-07-2026** par lecture seule du dépôt.
> Toute affirmation est traçable en `fichier:ligne`. Ce qui n'est pas dans le dépôt est
> marqué « non déterminé depuis le repo ».
> Dépôt distant : `https://github.com/MakFly/langgraph-agent-assistant-ui.git` (`git remote -v`).
>
> **Mise à jour du 28-07-2026 (même jour) — ajout du RBAC natif et du RAG.**
> Le projet n'est plus « sans auth » : voir la **section 2 bis**, qui décrit les comptes,
> les rôles, les groupes et la recherche documentaire filtrée par permissions. Les
> paragraphes ci-dessous marqués ~~barrés~~ décrivent l'état d'avant et sont conservés
> pour comprendre d'où vient la structure actuelle.

---

## 1. Le produit

**Pitch (3 lignes).**
Un agent **LangGraph (Python)** qui appelle de vrais outils gratuits et sans clé (Wikipédia,
Hacker News, météo Open-Meteo, calculateur AST), dont la sortie est streamée en SSE au
**protocole « AI SDK UI Message Stream »** et rendue par **assistant-ui** dans un front
Vite + React 19. Les conversations sont persistées en PostgreSQL et l'agent est
reconfigurable depuis l'UI sans redémarrage.

**Problème résolu.** Il n'existe pas d'équivalent Python à `@ai-sdk/langchain` : le pont
entre un backend LangGraph et un front assistant-ui doit être écrit à la main
(`README.md:27-36`). Ce dépôt **est** cette implémentation, testée, plus ce qu'il faut
autour pour une vraie app de chat : historique, activation par outil, changement de modèle
à chaud, découverte de serveurs MCP.

**Utilisateurs visés.** Développeurs qui veulent réutiliser le pont `protocol/` — le README
le dit explicitement : « this is a proof of concept, not a maintained product. Fork it,
take the `protocol/` bridge, and go » (`README.md:399-400`).

**Statut réel : POC abouti, mais POC.** Ce n'est ni un prototype bancal ni un produit.
- Le cœur fonctionne et est testé sur le chemin de production (`README.md:367-370`).
- Mais : **2 commits, 1 seul contributeur, dépôt créé le 25-07-2026** (`git log`), donc
  **âge = 3 jours**, bus factor = 1.
- **Aucune CI** (pas de `.github/`, vérifié).
- **Images Docker de dev uniquement** (`apps/api/Dockerfile:26` monte `--reload`,
  `apps/web/Dockerfile:15` lance `bun run dev`).
- ~~**aucune auth**~~ → **corrigé le 28-07-2026** : RBAC natif, cf. section 2 bis.
- ~~**Aucun `uv.lock` versionné**~~ → **corrigé le 28-07-2026** : `apps/api/uv.lock`
  existe (89 paquets épinglés) et les dépendances front sensibles au protocole
  (`@assistant-ui/*`, `ai`, `@ai-sdk/react`) sont en versions exactes
  (`apps/web/package.json:12-16,20`).

---

## 2 bis. RBAC natif + RAG documentaire (ajouté le 28-07-2026)

### Ce que ça change, en une phrase

L'agent dispose d'un **cinquième outil**, `document_search`, qui lit un corpus interne et
**ne rend que les documents auxquels le compte connecté a droit**. Le reste du POC est
inchangé : le pont `protocol/` n'a pas bougé d'une ligne.

### Deux axes d'autorisation, volontairement disjoints

| Axe | Porte sur | Qui l'a | Où c'est décidé |
|---|---|---|---|
| `role` (`admin` / `member`) | la **configuration** : prompt système, modèle, outils, MCP | l'administrateur | `api/settings.py:42` + `dependencies=_ADMIN` sur chaque mutation |
| `groups` (`finance`, `rh`, …) | l'**accès aux documents** | selon le compte | `core/rag/retrieve.py` — `acl && $groups` |

**Être administrateur n'ouvre aucun document.** Un test le verrouille
(`tests/test_auth.py::test_le_role_admin_n_ouvre_aucun_document`). Confondre les deux axes
est la façon habituelle de fabriquer un compte qui voit tout.

### Chaîne de confiance — de la session jusqu'à la requête SQL

```
  navigateur                     apps/api                                  bases
  ──────────                     ────────                                  ─────
  ┌──────────────┐  cookie httpOnly  ┌────────────────────────────┐
  │ LoginScreen  │──POST /api/auth/──▶ api/auth.py                │
  │ AuthProvider │◀──login (Argon2)──│  · decode_token (HS256)    │
  └──────┬───────┘                   │  · current_user / require_ │
         │ POST /api/chat            │    admin  ← SEULE entrée   │
         │ (cookie envoyé seul)      └──────────┬─────────────────┘
         ▼                                      │ User{id, role, groups}
  ┌──────────────────────────────────────────────▼──────────────────┐    ┌──────────────┐
  │ protocol/stream.py — configurable={user_id, user_groups}         │───▶│infra-postgres│
  │            │ LangGraph propage la config aux nœuds               │    │ users        │
  │            ▼                                                     │    │ threads      │
  │ core/graph.py "agent" ──tool_call──▶ ToolNode                    │    │  owner_id ◀──┤
  │                                        │ document_search(query,  │    │ messages     │
  │                                        │   config injecté)       │    │ settings     │
  │                                        ▼                         │    └──────────────┘
  │                          core/tools/rag.py — FERMÉ PAR DÉFAUT    │
  │                            pas de user_groups → refus, pas de    │    ┌──────────────┐
  │                            recherche « sans filtre »             │    │  ragdb       │
  │                                        │                         │    │  (pgvector)  │
  │                                        ▼                         │───▶│ rag_documents│
  │                          core/rag/retrieve.py                    │    │ rag_chunks   │
  │                            dense (<=> cosinus)  ─┐               │    │  embedding   │
  │                            sparse (ts_rank_cd)  ─┼─▶ RRF ─▶ top-k│    │  tsv         │
  │                            WHERE acl && $groups ─┘               │    │  acl[]  GIN  │
  └──────────────────────────────────────────────────────────────────┘    └──────▲───────┘
                                                                                  │
        corpus/<groupe>/fichier.md ──parse──▶ chunk ~800tk ──embed──▶ make ingest ─┘
                    │                                                   (idempotent, sha256)
                    └── le NOM DU DOSSIER est le groupe autorisé
```

**Légende.** Le `configurable` de LangGraph est le seul canal par lequel une identité
atteint un outil : `document_search` reçoit `config` en paramètre injecté, invisible du
modèle (`tests/test_rag.py::test_le_modele_ne_voit_pas_le_parametre_d_identite`). Le LLM
ne connaît que `query` — il ne peut donc pas demander à voir autre chose. `RRF` fusionne
les deux classements par leurs **rangs**, ce qui évite d'avoir à pondérer une distance
cosinus contre un score lexical.

### Carte du code ajouté

| Fichier | Rôle |
|---|---|
| `apps/api/src/agent/infra/auth.py` | Argon2id, JWT HS256, cookie. Aucune notion d'utilisateur. |
| `apps/api/src/agent/core/users.py` | Comptes, rôles, groupes, anti-force-brute, amorçage admin. |
| `apps/api/src/agent/api/auth.py` | `/api/auth/*` + dépendances `current_user` / `require_admin`. |
| `apps/api/src/agent/infra/ragdb.py` | Base pgvector, schéma paramétré par la dimension. |
| `apps/api/src/agent/core/rag/parse.py` | md / txt / html / pdf → texte. |
| `apps/api/src/agent/core/rag/chunk.py` | Découpage par paragraphes, recouvrement. |
| `apps/api/src/agent/core/rag/embed.py` | OpenAI, ou `hash` local déterministe pour les tests. |
| `apps/api/src/agent/core/rag/ingest.py` | Indexation idempotente + garde-fou de coût. |
| `apps/api/src/agent/core/rag/retrieve.py` | Recherche hybride filtrée ACL. |
| `apps/api/src/agent/core/rag/evaluate.py` | rappel@k, MRR, détection de fuite d'ACL. |
| `apps/api/src/agent/core/tools/rag.py` | L'outil exposé au modèle. |
| `apps/api/src/agent/cli.py` | `user *`, `ingest`, `rag stats|reset|eval`. |
| `apps/web/src/components/auth/` | Contexte de session + écran de connexion responsive. |
| `corpus/`, `eval/questions.yaml` | Corpus de démonstration (3 groupes) et jeu d'évaluation. |

### Commandes

```bash
make demo                            # = seed + ingest : la démo complète, prête
make seed                            # 4 comptes de démonstration (DEV, idempotent)
make user-create EMAIL=alice@example.com ROLE=admin GROUPS=finance,rh
make user-list                       # rôle et groupes de chacun
make ingest                          # indexe corpus/ — idempotent, relançable
make ingest-dry                      # le plan, sans rien vectoriser ni payer
make eval                            # rappel@k, MRR, contrôle de fuite d'ACL
make rag-stats / make rag-reset      # état de l'index / le vider
```

Le mot de passe n'est **jamais** un argument : il est demandé en saisie masquée, ou lu sur
l'entrée standard (`echo "…" | make user-create EMAIL=…`).

### Variables d'environnement ajoutées (noms uniquement)

`AUTH_SECRET`, `AUTH_TOKEN_TTL_HOURS`, `AUTH_COOKIE_SECURE`, `ADMIN_EMAIL`,
`ADMIN_PASSWORD`, `RAG_DATABASE_URL`, `EMBEDDING_PROVIDER`, `EMBEDDING_MODEL`,
`EMBEDDING_DIM`, `EMBEDDING_COST_PER_MTOK`, `RAG_MAX_CHUNKS_PER_RUN`, `RAG_TOP_K`,
`RAG_CANDIDATES` — toutes documentées dans `apps/api/.env.example`.

### Démonstration en un clic (développement uniquement)

`make demo` crée quatre comptes (`admin`, `finance`, `rh`, `sans groupe`) et indexe le
corpus. Sur **`http://localhost:4311/`** — et seulement sur cette URL — l'écran de
connexion préremplit le formulaire et propose les quatre comptes en un clic : poser la
même question sur le budget en `finance@demo.local` puis en `rh@demo.local` montre le
filtrage ACL en dix secondes. `/ichat` garde un formulaire vide, pour continuer à
éprouver le vrai parcours de connexion.

**Ces comptes ont un mot de passe écrit dans le dépôt.** Deux verrous indépendants :

1. `make seed` refuse de s'exécuter quand `AUTH_COOKIE_SECURE` est actif
   (`core/seed.py`, testé). Sans seeder, ces identifiants n'ouvrent rien.
2. Le module front qui porte le mot de passe est utilisé **exclusivement** derrière
   `import.meta.env.DEV`, sous une forme que le build peut replier — il disparaît donc du
   bundle de production. Vérifié dans les deux sens :
   - `bun run build` puis `grep -r "demo-motdepasse\|demo\.local" dist/` → **rien** ;
   - `NODE_ENV=development bunx vite build` → présent, donc ce n'est pas du code mort.

   Cette vérification est à **relancer après toute modification** de
   `apps/web/src/components/auth/demo-accounts.tsx` ou de `login-screen.tsx` : une
   première version passant par un `useState` laissait le mot de passe dans `dist/`.

`make seed` est idempotent et ne réinitialise jamais un mot de passe déjà changé
(`tests/test_seed.py`), pour qu'il ne puisse pas rouvrir une porte qu'on vient de fermer.

### Limites assumées, à connaître avant de s'appuyer dessus

1. **Révocation différée.** Le jeton est auto-porteur : désactiver un compte ne prend effet
   qu'à son expiration (`AUTH_TOKEN_TTL_HOURS`, 8 h par défaut). Pour couper immédiatement,
   il faut faire tourner `AUTH_SECRET`, ce qui déconnecte tout le monde.
2. **Aucun seuil de pertinence.** Une recherche dense rend toujours ses plus proches
   voisins, même quand rien ne répond à la question. C'est visible dans
   `tests/test_rag.py::test_l_outil_filtre_sur_les_groupes_de_la_session`.
3. **`EMBEDDING_MODEL` par défaut est à vérifier** contre le catalogue OpenAI en vigueur
   (`core/rag/embed.py:38`) : les noms de modèles d'embedding changent.
4. **La dimension est figée dans le schéma.** En changer impose `make rag-reset && make
   ingest`. Le code refuse de démarrer sur une incohérence plutôt que de mélanger deux
   espaces vectoriels (`infra/ragdb.py`).
5. **L'anti-force-brute est en mémoire** : remis à zéro au redémarrage, inopérant en
   multi-instance. C'est un ralentisseur, pas un rempart.
6. **Les conversations antérieures à l'authentification** ont `owner_id = NULL` et sont donc
   invisibles. Voulu : on ne peut pas deviner à qui elles appartenaient.
7. **Le score d'évaluation actuel (100 %) ne veut presque rien dire** : 4 documents d'un
   fragment chacun, mesurés avec le vectoriseur `hash`. Il prouve que le harnais tourne,
   pas que le RAG est bon.

---

## 2 ter. Jauge de contexte — mesure réelle plutôt qu'estimation

Le compteur « % restant » du composer s'appuyait sur une estimation locale
(`ceil(caractères/4) + 3`, copie de `count_tokens_approximately`). Elle **sous-évaluait
massivement** : elle ignorait le prompt système, les schémas d'outils envoyés par
`bind_tools()` et les images. Mesuré sur cette installation : **9 tokens estimés contre
679 réels** pour un simple « Réponds juste: ok ».

La source de vérité existait déjà dans le processus — `usage_metadata`, renvoyé par le
provider et extrait par `core/callbacks.py:110-126` — mais elle finissait dans une ligne
de log. Elle est maintenant transmise à l'interface :

```
provider ──usage_metadata──▶ protocol/stream.py  (dernier tour de modèle)
                                    │
     data: {"type":"message-metadata","messageMetadata":{"usage":{…}}}   ← avant `finish`
                                    ▼
        useThreadTokenUsage()  (@assistant-ui/react-ai-sdk)
                                    ▼
        composer-context-meter.tsx  →  mesuré, sinon estimation en repli
```

- **Clés imposées** par le hook (`react-ai-sdk/dist/usage.js`) : `inputTokens`,
  `outputTokens`, `totalTokens`, `cachedInputTokens`, `reasoningTokens`. Toute autre clé
  est ignorée **en silence** — d'où un test qui verrouille la correspondance.
- **Le dernier tour, pas la somme** : `input_tokens` du dernier appel est la taille du
  prompt réellement envoyé. Sommer les tours d'une boucle ReAct dépasserait la fenêtre,
  puisque chaque tour renvoie tout l'historique. Ce n'est donc **pas** le coût du run.
- **Émis avant `finish`** : le client applique la métadonnée au message en cours.
  Après, elle n'a plus de message auquel s'attacher — sans la moindre erreur visible.
- **Repli conservé** : avant la première réponse, et pour les providers qui ne
  rapportent pas les tokens en streaming ([langchain#30429](https://github.com/langchain-ai/langchain/issues/30429)),
  l'estimation reprend la main. L'infobulle distingue « mesuré » de « estimé ».

Vérifié en réel sur `openai` : les tokens remontent en streaming **sans** avoir à régler
`stream_usage`. Non vérifié : la survie de la métadonnée à un rechargement de
conversation (elle dépend de l'encodage d'assistant-ui) — en cas d'absence, le repli
s'applique, il n'y a pas de régression.

### La fenêtre est un réglage, plus une constante

`MAX_CONTEXT_TOKENS = 24_000` était **la même valeur pour tous les modèles**, dimensionnée
sur le plus petit du catalogue. Sur un modèle à grande fenêtre, on tronquait des
conversations pour rien. C'est devenu `AgentConfig.max_context_tokens` (défaut 24 000,
bornes 2 000 – 1 000 000), réglable par un **admin** depuis le panneau Agent.

Choix assumé : un réglage plutôt qu'un catalogue « fenêtre par modèle ». Ce catalogue
vivrait à côté de `PROVIDER_MODELS`, déjà identifié comme le point de rot du projet ;
l'opérateur, lui, connaît le modèle qu'il a choisi.

**Ce qu'on a décidé de NE PAS faire, mesures à l'appui :** calibrer le comptage avec
`count_tokens_approximately(use_usage_metadata_scaling=True)`.

- Ce serait un **no-op** : le paramètre exige des `AIMessage` porteurs de `usage_metadata`
  et de `response_metadata["model_provider"]`, or l'historique est reconstruit depuis le
  client par `to_lc_messages()` qui produit des `AIMessage(content=text)` nus
  (`protocol/messages.py:65,70`).
- Et ça irait dans le mauvais sens : mesuré, `approx(prompt + schémas d'outils) = 906`
  contre **675 réels**. Sur du JSON, la règle des 4 caractères par token **surestime** de
  35 %. Calibrer rendrait le rognage plus agressif, donc le produit pire.
- La marge suffit : la surcharge fixe réelle est d'environ 670 tokens, contre 8 000 de
  marge sur le plus petit modèle visé.

Enfin, un `context_length_exceeded` n'est plus rendu brut : `stream.py:friendly_error()`
le traduit en instruction (« démarrez une nouvelle conversation, ou réduisez la fenêtre
dans les réglages »). La détection se fait sur le texte de l'erreur — fragile par nature,
faute de code distinctif chez les providers — et **toute erreur non reconnue est rendue
telle quelle**, jamais réécrite.

---

## 2. Architecture

```
╔══════════ apps/web — Vite 8 · React 19 · Tailwind 4 · shadcn · assistant-ui ══════════════════╗
║  main.tsx:6 ─▶ App.tsx:52                                                                     ║
║      │                                                                                        ║
║      ├─▶ usePersistentChatRuntime (use-persistent-chat-runtime.ts:23)                         ║
║      │        └─ AssistantChatTransport { api:"/api/chat" }        (App.tsx:59)                ║
║      ├─▶ useChatRoute (use-chat-route.ts:17) ── URL /ichat, /ichat/c/:id ── source de vérité   ║
║      └─▶ SettingsProvider (settings-context.tsx) ── panneau Outils/Agent/Modèle/MCP            ║
╚═══════════════════════════════════════════════════════════════════════════════════════════════╝
        │ HTTP même origine — proxy Vite /api ─▶ http://api:4310   (vite.config.ts:22)
        ▼
╔══════════ apps/api — Python 3.13 · FastAPI · LangGraph 1.x ═══════════════════════════════════╗
║  main.py:58 FastAPI(lifespan) ── monte 3 routers (main.py:72-74)                               ║
║     ├── api/chat.py:38    POST /api/chat      ─▶ SSE  text/event-stream                        ║
║     ├── api/chat.py:26    GET  /api/health                                                     ║
║     ├── api/threads.py    CRUD /api/threads/*   (contrat RemoteThreadListAdapter)              ║
║     └── api/settings.py   GET/PATCH /api/settings/* + CRUD /api/settings/mcp                   ║
║                                                                                                ║
║  Chemin du chat :                                                                              ║
║    UIMessage[] ──▶ to_lc_messages (protocol/messages.py) ──▶ get_graph() (core/graph.py:231)   ║
║                                                                                                ║
║        ┌──────────────┐  tool_calls ? oui   ┌──────────────┐                                   ║
║  START ─▶  agent      ├────────────────────▶│    tools     │  ToolNode(handle_tool_errors)     ║
║        │  (LLM astream)│◀───────────────────┤ wikipedia · hacker_news · weather · calculator   ║
║        └──────┬───────┘  boucle ReAct        └──────────────┘  + outils MCP (core/mcp.py:55)   ║
║               │ non (graph.py:196 should_continue, plafond `loops`)                            ║
║               ▼                                                                                ║
║              END ──▶ ui_message_stream (protocol/stream.py:69) ──▶ chunks SSE typés            ║
╚═══════════════════════════════════════════════════════════════════════════════════════════════╝
        │ asyncpg (infra/db.py:86 connect)                    │ HTTPS — httpx timeout 8 s
        ▼                                                     ▼   (infra/http.py:19)
┌───────────────────────────────┐         ┌──────────────────────────────────────────────┐
│  PostgreSQL 16                │         │  APIs externes sans clé                      │
│  db langgraph_poc             │         │  Wikipedia REST · Algolia HN · Open-Meteo     │
│  threads · messages           │         └──────────────────────────────────────────────┘
│  settings · mcp_servers       │         ┌──────────────────────────────────────────────┐
│  (schéma: infra/db.py:22)     │         │  LLM : groq | google | ollama | openai        │
└───────────────────────────────┘         │  (core/model.py:186 create_model)             │
                                          └──────────────────────────────────────────────┘
```

**Légende.**
`╔═╗` = un service déployé (conteneur). `┌─┐` = dépendance externe.
`──▶` = appel sortant, protocole indiqué sur la flèche.
La boucle `agent ⇄ tools` est la boucle ReAct, bornée par `loops` dans l'état
(`graph.py:196-200`), plafond réglable 1..20 (`core/settings.py:58`).

**Composants.**

| Composant | Rôle | Point d'entrée |
|---|---|---|
| `apps/web` | UI de chat, historique, panneau de configuration | `apps/web/src/main.tsx:6` |
| `apps/api` | agent + surface HTTP + SSE | `apps/api/src/agent/main.py:58` |
| `protocol/` | pont bidirectionnel AI SDK ⇄ LangChain | `protocol/stream.py:69`, `protocol/messages.py` |
| `core/` | graphe, modèle, outils, MCP, configuration | `core/graph.py:138` |
| `infra/` | Postgres, client HTTP, logs | `infra/db.py:86` |
| `db` (compose) | PostgreSQL 16 embarqué | `docker-compose.yml:45` |

**Règle d'architecture (revendiquée et vérifiable).** Les dépendances pointent vers
l'intérieur : `api/ → core/ → infra/`, et **FastAPI n'est importé que dans `api/`**
(`README.md:91-92` donne la commande `grep` de vérification).

---

## 3. Stack & versions

| Couche | Techno | Version déclarée | Source |
|---|---|---|---|
| Runtime API | Python | `requires-python = ">=3.12"` ; image `python:3.13-slim` | `apps/api/pyproject.toml:5`, `apps/api/Dockerfile:4` |
| Serveur | FastAPI + uvicorn[standard] | `>=0.140.0`, `>=0.51.0` | `apps/api/pyproject.toml:8-9` |
| Agent | LangGraph / langchain-core | `>=1.2.9` / `>=1.5.1` | `apps/api/pyproject.toml:10-11` |
| LLM providers | langchain-groq, -google-genai, -ollama, -openai | `>=1.1.3`, `>=4.3.1`, `>=1.1.0`, `>=1.4.1` | `apps/api/pyproject.toml:16-18` |
| MCP | langchain-mcp-adapters | `>=0.1.9` | `apps/api/pyproject.toml:20` |
| DB driver | asyncpg | `>=0.30.0` | `apps/api/pyproject.toml:13` |
| HTTP | httpx (jamais axios) | `>=0.28.1` | `apps/api/pyproject.toml:12` |
| Tests / lint API | pytest, pytest-asyncio, asgi-lifespan, ruff | `>=9.1.1`, `>=1.4.0`, `>=2.1.0`, `>=0.14.0` | `apps/api/pyproject.toml:24-28` |
| Front | React 19 / Vite 8 / TypeScript ~6.0 | `^19.2.7` / `^8.1.1` / `~6.0.2` | `apps/web/package.json:26,44,43` |
| UI chat | @assistant-ui/react + @assistant-ui/react-ai-sdk + ai | `^0.14.27`, `^1.3.41`, `^7.0.37` | `apps/web/package.json:15,16,21` |
| Style | Tailwind 4 + shadcn + radix-ui | `^4.3.3`, `^4.14.1`, `^1.6.7` | `apps/web/package.json:32,29,25` |
| Lint front | oxlint | `^1.71.0` | `apps/web/package.json:42` |
| Package manager front | bun (`oven/bun:1.3-alpine`) | lock présent (`apps/web/bun.lock`) | `apps/web/Dockerfile:3` |
| DB | PostgreSQL | `postgres:16-alpine` | `docker-compose.yml:46` |
| Infra | Docker Compose, projet `langgraph-poc` | — | `docker-compose.yml:3` |

**Modèles LLM par défaut** (catalogue curaté à la main, `core/model.py:46-75`) :
groq `openai/gpt-oss-120b`, google `gemini-3.6-flash`, ollama `qwen3:8b`,
openai `gpt-5.6-luna`. Vérifié par l'auteur le **25-07-2026** (`core/model.py:32`).

---

## 4. Carte du code

```
langgraph-poc/
├── Makefile                     tout passe par docker compose (cibles : Makefile:20,25,57,60,63,66,69,72)
├── docker-compose.yml           3 services : api:6, web:29, db:45 — ports 4310 / 4311
├── docker-compose.override.yml  NON VERSIONNÉ (.gitignore) : bascule sur infra-postgres / dev-shared-net
├── .env                         racine : POSTGRES_PASSWORD uniquement (clé lue, valeur jamais)
├── README.md / README.fr.md     doc principale, ~400 lignes chacune, à jour à 2 détails près (§8)
├── docs/
│   ├── graph.md                 fenêtre de contexte, retry 429, erreurs d'outils, MCP, métriques
│   ├── logging.md               canaux, niveaux, LOG_* (docs/logging.md:51)
│   └── settings.md              config globale, endpoints, piège du cache de graphe, MCP
└── apps/
    ├── api/
    │   ├── Dockerfile           image de DEV : uvicorn --reload (apps/api/Dockerfile:26)
    │   ├── pyproject.toml       deps + config pytest/ruff — PAS de uv.lock versionné
    │   ├── .env.example         modèle de config (aucune valeur secrète)
    │   ├── src/agent/
    │   │   ├── main.py          ★ POINT D'ENTRÉE : app FastAPI (main.py:58), lifespan (main.py:28)
    │   │   ├── api/
    │   │   │   ├── chat.py      POST /api/chat (chat.py:38) · GET /api/health (chat.py:26)
    │   │   │   ├── threads.py   7 routes CRUD (threads.py:60,75,90,102,128,138,162)
    │   │   │   └── settings.py  GET/PATCH config + CRUD MCP (settings.py:107→225) ; 503 si DB HS (settings.py:91)
    │   │   ├── core/
    │   │   │   ├── graph.py     ★ build_graph (graph.py:138) · should_continue (graph.py:196) · get_graph (graph.py:231)
    │   │   │   ├── model.py     ★ create_model (model.py:186) · catalogue (model.py:46) · efforts (model.py:124)
    │   │   │   ├── settings.py  snapshot mémoire + version (core/settings.py:165,173,184)
    │   │   │   ├── mcp.py       découverte des outils MCP (mcp.py:99) · lecture sync (mcp.py:55)
    │   │   │   ├── callbacks.py métriques de run (latence, 1er token, tokens, durée outil)
    │   │   │   └── tools/       4 outils, registre TOOLS (tools/__init__.py:13)
    │   │   ├── protocol/
    │   │   │   ├── stream.py    ★ ui_message_stream (stream.py:69) — émission SSE AI SDK
    │   │   │   └── messages.py  UIMessage[] → messages LangChain (COMPLETED_STATES à messages.py:35)
    │   │   └── infra/
    │   │       ├── db.py        pool asyncpg + schéma SQL inline (db.py:22, db.py:86)
    │   │       ├── http.py      fetch_json + timeout 8 s (http.py:19)
    │   │       └── log.py       setup_logging, LOG_FORMAT/LEVEL/LEVELS/ACCESS (log.py:123-175)
    │   └── tests/               10 fichiers, 67 fonctions `def test_` + 5 `@parametrize`
    └── web/
        ├── Dockerfile           image de DEV : bun run dev (apps/web/Dockerfile:15)
        ├── vite.config.ts       port 4311, proxy /api (vite.config.ts:22)
        └── src/
            ├── main.tsx         ★ POINT D'ENTRÉE front (main.tsx:6)
            ├── App.tsx          ★ composition : runtime + shell + Thread (App.tsx:52,59,62)
            ├── components/
            │   ├── assistant-ui/   composants du registre shadcn, vendorisés (thread.tsx 589 LOC)
            │   ├── chat/           runtime persistant, adaptateur thread-list, picker modèle, jauge contexte
            │   ├── settings/       4 onglets : outils / agent / modèle / MCP
            │   ├── ui/             primitives shadcn (sidebar.tsx = 704 LOC, le plus gros fichier du repo)
            │   └── xulux/          coque visuelle clonée d'une maquette `v1-xulux`
            ├── hooks/              use-chat-route, use-health, use-settings, use-theme, use-mobile
            └── lib/                chat-route.ts:15 (CHAT_BASE="/ichat"), context-usage, model-preference
```

★ = point d'entrée ou fichier à lire en premier.

---

## 5. Flux principal — un message de bout en bout

1. **Saisie** — l'utilisateur écrit dans `<Thread/>`. Le runtime est
   `usePersistentChatRuntime` (`use-persistent-chat-runtime.ts:23`), qui combine
   `useRemoteThreadListRuntime` (liste des conversations) et `useChatRuntime`.
2. **POST `/api/chat`** — via `AssistantChatTransport { api: "/api/chat" }`
   (`App.tsx:59`). Même origine : le proxy Vite renvoie vers `http://api:4310`
   (`vite.config.ts:22`, `docker-compose.yml:35`). Le corps contient **tout l'historique**
   sous forme d'`UIMessage[]`.
3. **FastAPI** — `chat()` (`api/chat.py:38`) valide qu'il y a des messages puis rend un
   `StreamingResponse(ui_message_stream(...))` avec les en-têtes SSE, dont
   `x-vercel-ai-ui-message-stream: v1` (`protocol/stream.py:41-48`).
4. **Conversion** — `to_lc_messages()` découpe chaque `UIMessage` assistant en
   `AIMessage(tool_calls)` → `ToolMessage` → `AIMessage(texte)`. Un appel d'outil sans
   résultat est **jeté** (`protocol/messages.py:35`) sinon l'API amont refuse la requête.
5. **Graphe** — `get_graph()` (`core/graph.py:231`) rend le graphe compilé, reconstruit
   uniquement si `settings.version()` a changé. `build_graph()` (`core/graph.py:138`)
   filtre les outils désactivés, ajoute les outils MCP en cache (`core/mcp.py:55`),
   instancie le modèle et binde les outils.
6. **Nœud `agent`** — consomme `model.astream()` et non `ainvoke` (`core/graph.py:188`),
   sinon aucun token intermédiaire ne sortirait. L'historique est d'abord rogné à
   24 000 tokens (`core/settings.py:69`, appliqué par `_windowed` `core/graph.py:108`).
   Reprise automatique sur 429/5xx via `RetryPolicy` (`core/graph.py:32-39`) ;
   **un 400 n'est jamais rejoué** (`core/graph.py:50`).
7. **Arête conditionnelle** — `should_continue` (`core/graph.py:196`) : s'il y a des
   `tool_calls` et que `loops <= max_loops`, on va au nœud `tools`, sinon `END`.
8. **Nœud `tools`** — `ToolNode(tools, handle_tool_errors=tool_error_message)`
   (`core/graph.py:215`) : une erreur d'outil devient un JSON `{"error": ...}` lu par le
   LLM **et** loggé (`core/tools/__init__.py:16`). Retour au nœud `agent`.
9. **Émission SSE** — `graph.astream(stream_mode=["messages","updates"])`
   (`protocol/stream.py:88-94`). `messages` donne les tokens (`text-delta`,
   `tool-input-delta`), `updates` donne les arguments d'outil **parsés**
   (`tool-input-available`) et les résultats (`tool-output-available` /
   `tool-output-error`). Fin : `finish` puis `data: [DONE]`.
10. **Erreur pendant le run** — capturée (`protocol/stream.py:187`), loggée **avec le
    contexte modèle**, et renvoyée au client en chunk `{"type":"error"}`. Sans ça un
    échec provider serait invisible : la réponse HTTP est un 200 qui streame.
11. **Persistance** — le client (et non le serveur) pousse chaque message via
    `POST /api/threads/{id}/messages` (`api/threads.py:162`). Le serveur ne comprend pas
    le contenu, il stocke `(format, content)` en JSONB (`infra/db.py:33-41`).
12. **Métriques** — un `RunMetricsHandler` par run (`protocol/stream.py:93`) journalise
    latence, temps jusqu'au premier token, tokens et durée d'outil sur le canal
    `agent.metrics`.

**Flux de configuration (parallèle).** `PATCH /api/settings/*` → `settings.save()` en base
→ `settings.refresh()` (`core/settings.py:184`) qui relit la base, redécouvre les outils
MCP puis **incrémente `_version`** → au prochain message, `get_graph()` détecte le
changement et reconstruit le graphe. C'est le mécanisme qui rend les réglages effectifs
sans redémarrage (`docs/settings.md:165`).

---

## 6. Config & secrets (noms uniquement)

Fichier attendu : **`apps/api/.env`**, créé par `make install` depuis
`apps/api/.env.example` (`Makefile:20-22`). Contenu du modèle (`apps/api/.env.example`) :

| Variable | Rôle | Défaut au code |
|---|---|---|
| `LLM_PROVIDER` | `groq` \| `google` \| `ollama` \| `openai` | `groq` (`core/model.py:159`) |
| `GROQ_API_KEY` | clé Groq (gratuit, sans CB) | — |
| `GOOGLE_API_KEY` | clé Google AI Studio (gratuit) | — |
| `OPENAI_API_KEY` | clé OpenAI (payant) | — |
| `LLM_MODEL` | surcharge du nom de modèle | tête du catalogue provider (`core/model.py:78`) |
| `LLM_TEMPERATURE` | température, bornée 0..2 | `0` (`core/model.py:166`) |
| `OLLAMA_BASE_URL` | Ollama sur l'hôte | `http://host.docker.internal:11434` (`core/model.py:277`) |
| `LOG_LEVEL` | niveau du canal `agent` | `INFO` |
| `LOG_FORMAT` | `text` \| `json` | `text` (`infra/log.py:123`) |
| `LOG_LEVELS` | niveaux par canal, ex. `agent.stream=DEBUG` | vide |
| `LOG_ACCESS` | `on` \| `off` (ligne d'accès uvicorn) | `on` (`infra/log.py:163`) |
| `CORS_ORIGIN` | origines autorisées | `http://localhost:4311` (`main.py:66`) |

Injectées par compose, hors `.env.example` :

| Variable | Où | Valeur |
|---|---|---|
| `DATABASE_URL` | `docker-compose.yml:14` | `postgresql://agent:***@db:5432/langgraph_poc` |
| `POSTGRES_PASSWORD` | `.env` racine (clé seule lue) | mot de passe du Postgres |
| `VITE_API_TARGET` | `docker-compose.yml:35` | `http://api:4310` |

Règles observées dans le code : l'API **n'expose jamais la valeur** d'une clé, seulement un
booléen `has_key` par provider (`core/model.py:145-154`, `core/settings.py:243`).
**Une seule clé LLM suffit** pour démarrer ; Ollama n'en demande aucune.

---

## 7. Comment lancer / tester

Toutes les commandes viennent du `Makefile` et du `README.md`. **Rien n'a été exécuté**
pendant cet audit (contrainte lecture seule).

```bash
make install     # crée apps/api/.env depuis .env.example puis build les images   (Makefile:20)
#  → renseigner UNE clé LLM dans apps/api/.env
make dev         # docker compose up -d — hot reload des deux côtés               (Makefile:25)
#  → API   http://localhost:4310
#  → Front http://localhost:4311   (la racine redirige vers /ichat)
make logs        # suit les logs des deux services                                (Makefile:33)
make test        # pytest -v dans le conteneur (réseau requis : vraies APIs)      (Makefile:57)
make test-unit   # idem, sans TestApisExternes                                    (Makefile:60)
make lint        # ruff check src tests                                           (Makefile:63)
make typecheck   # cd apps/web && bunx tsc -b                                     (Makefile:66)
make build       # cd apps/web && bun run build                                   (Makefile:69)
make check       # test + lint + typecheck + build                                (Makefile:72)
make down        # arrête et supprime les conteneurs                              (Makefile:45)
make clean       # + volumes et artefacts                                         (Makefile:82)
```

**Piège documenté** : après modification d'une clé dans `apps/api/.env`, un
`docker compose restart` ne suffit pas — il faut `make down && make dev`
(`README.md:166`).

**Piège documenté** : après un `bun add`, il faut recréer le volume
`langgraph-poc_web_node_modules` (`README.md:184`).

**Attention `make typecheck` / `make build`** : ces deux cibles s'exécutent **sur l'hôte**
(`cd apps/web && bunx …`), contrairement au reste. Elles supposent donc `bun` installé
localement, alors que le README promet « no Python or Node runtime is required on the host »
(`README.md:21-22`). Incohérence mineure mais réelle.

---

## 8. État des lieux honnête

### Ce qui est fait et solide
- **Le pont AI SDK ⇄ LangGraph** : émission (`protocol/stream.py`) et re-conversion
  (`protocol/messages.py`), avec le bug le plus coûteux de la version TS (arguments
  d'outil jamais émis → `400 INVALID_TOOL_RESULTS` au 2e tour) verrouillé par des tests
  (`README.md:332-340`).
- **Les tests passent par le chemin de production** : `ui_message_stream()` est exercé tel
  quel, seul l'appel réseau au LLM est remplacé par un faux modèle qui streame de vrais
  `tool_call_chunks` (`README.md:367-370`, `apps/api/tests/fakes.py`).
- **Dégradation sans base assumée et implémentée** : le chat marche sans Postgres, seule
  l'historisation s'éteint (`main.py:36-39`, `infra/db.py:109`, `api/settings.py:91`).
- **Garde-fous coût/quota présents** : plafond de boucle ReAct (`core/graph.py:196`),
  fenêtre de contexte 24 k tokens (`core/settings.py:69`), retry sélectif qui ne rejoue
  **jamais** un 400 (`core/graph.py:50`), timeout HTTP 8 s (`infra/http.py:19`).
- **Calculateur sûr par construction** : évaluateur AST à liste blanche plutôt qu'`eval`
  durci, 7 tentatives d'évasion couvertes par des tests (`README.md:213-224`).
- **Observabilité réelle** : canaux de log, format JSON optionnel, métriques par run
  (`infra/log.py`, `core/callbacks.py`, `docs/logging.md`).

### Ce qui est partiel
- **MCP** : les serveurs sont découverts et bindés (`core/mcp.py:99`), mais les outils MCP
  **ne sont pas filtrables individuellement** — seulement l'interrupteur par serveur
  (`README.md:379-380`).
- **Pièces jointes** : le bouton existe côté assistant-ui, le backend ne les traite pas
  (`README.md:388-389`).
- **Front non testé** : aucun fichier `*.test.*` ni `*.spec.*` sous `apps/web/src`
  (vérifié). Seul `oxlint` + `tsc -b` protègent le front.

### Ce qui manque
- **Aucune CI** : pas de `.github/` (vérifié). `make check` existe mais n'est déclenché par
  rien.
- **Aucun `uv.lock` versionné** → les dépendances Python sont résolues au build, sur des
  contraintes `>=` uniquement (`apps/api/pyproject.toml:7-28`). **Deux builds à deux dates
  différentes ne donnent pas le même environnement.** C'est la dette la plus concrète du
  dépôt. Côté front, `apps/web/bun.lock` existe : l'asymétrie est probablement un oubli.
- **Aucune authentification** : toutes les routes sont ouvertes et la configuration est
  globale (`README.md:383-384`).
- **Aucune image de production** : les deux Dockerfiles sont des images de dev
  (`apps/api/Dockerfile:26`, `apps/web/Dockerfile:15`).
- **Pas de checkpointer LangGraph** — choix explicite et argumenté (`docs/graph.md:99-105`) :
  conséquence assumée, pas de reprise de run ni de validation humaine avant outil.
- **Pas de propagation multi-process** : le snapshot de configuration vit dans le
  processus ; avec plusieurs workers uvicorn un `PATCH` ne toucherait qu'un worker
  (`docs/settings.md:241-244`).

### Dette / incohérences repérées (précises)
| # | Constat | Preuve |
|---|---|---|
| 1 | **Doc obsolète** : le README décrit `safe_tool()` qui n'existe plus (remplacé par `tool_error_message` + `ToolNode(handle_tool_errors=…)`) | `README.md:343`, `README.fr.md:330` vs `core/tools/__init__.py:16` et `core/graph.py:215` |
| 2 | **Commentaire SQL obsolète** : « le graphe ne les consomme pas encore » à propos des serveurs MCP — faux depuis `core/mcp.py` | `infra/db.py:54` contredit par `core/graph.py:156` |
| 3 | **Écart de comptage de tests** : le README annonce 89 / 85 tests ; le dépôt contient 67 fonctions `def test_` + 5 `@parametrize` (le total collecté n'a pas été vérifié, aucun run autorisé) | `README.md:363-364` vs comptage `grep` |
| 4 | **Le Makefile parle de 63 tests**, le README de 89 : deux chiffres différents dans deux fichiers | `Makefile:55` vs `README.md:363` |
| 5 | Deux wrappers front marqués `@deprecated` (API legacy d'assistant-ui) | `apps/web/src/components/assistant-ui/reasoning.tsx:345`, `tool-group.tsx:221` |
| 6 | `components/ui/` et `components/xulux/` sont **vendorisés et patchés à la main** : relancer `shadcn add` écrase les patches | `README.md:346-351` |
| 7 | Aucun marqueur `TODO`/`FIXME`/`HACK`/`XXX` dans `apps/*/src` (vérifié) — la dette est structurelle, pas dispersée | — |

---

## 9. Risques & pré-mortem

> **Nous sommes le 28-01-2027. Ce projet est mort. Racontons pourquoi.**

| P | Cause de mort (au passé) | Signal d'alerte observable **en premier** | Parade |
|---|---|---|---|
| **P0** | **Le catalogue de modèles a pourri et le POC ne démarrait plus.** Groq a éteint `llama-3.3-70b-versatile` et `llama-3.1-8b-instant` le 16-08-2026 — l'auteur l'avait lui-même noté (`core/model.py:36-38`). Personne n'a maintenu `PROVIDER_MODELS`. Le premier visiteur a cloné, lancé, obtenu un 400 du provider, et est parti. | Une réponse en erreur immédiate dans l'UI au premier message avec le modèle par défaut ; ou un chunk `{"type":"error"}` visible dans les logs `agent.stream` (`protocol/stream.py:204`). | Un test « smoke » hebdomadaire qui liste les modèles chez chaque provider et compare à `PROVIDER_MODELS`. Coût : ~2 h. **Inférieur au dégât** (un POC vitrine qui ne démarre pas ne sert à rien). |
| **P0** | **Le build n'était plus reproductible.** Sans `uv.lock`, un `uv sync` de janvier a tiré un LangGraph 1.x ou un `langchain-core` avec un breaking change (les deux sont en `>=`, `pyproject.toml:10-11`). L'image ne buildait plus, ou pire : buildait et cassait le streaming silencieusement. | `make dev` qui échoue au build, ou des tests verts localement et rouges sur une machine vierge. **Aucune CI ne l'aurait détecté** — il n'y en a pas. | `uv lock` puis versionner `apps/api/uv.lock`. Coût : ~15 min. C'est la parade au meilleur ratio du dépôt. |
| **P1** | **Bus factor = 1 et personne n'a repris.** 2 commits, 1 auteur, 0 issue, 0 PR (`git shortlog`). Le projet est un artefact personnel : dès que l'auteur est passé à autre chose, plus rien n'a bougé. Un fork intéressé par `protocol/` a copié les 340 lignes utiles et a ignoré le reste. | Aucun commit pendant 90 jours ; ou une issue ouverte sans réponse pendant 30 jours. | **Aucune parade technique.** Assumer : le README le dit déjà (`README.md:399`). La vraie parade est éditoriale — extraire `protocol/` en paquet publiable si l'objectif est la réutilisation. |
| **P1** | **Le pont a divergé du protocole.** `@assistant-ui/react` est en `^0.14` et `ai` en `^7` (`package.json:15,21`) : une lib pré-1.0 change de format. Le stream émis par `stream.py` a cessé d'être compris par le front après un `bun update`, et il n'y avait **aucun test front** ni CI pour l'attraper. | Le chat affiche des bulles vides ou aucun token, alors que les tests Python restent verts (ils testent l'émission, pas la consommation). | Épingler `@assistant-ui/*` et `ai` en versions exactes, ou ajouter un test de bout en bout (Playwright) sur un message + un appel d'outil. Coût : ~4 h pour le E2E. À arbitrer : sur un POC, l'épinglage seul (~10 min) couvre 80 % du risque. |
| **P1** | **Le projet n'aurait pas dû exister — obligatoire.** Le besoin réel était « brancher assistant-ui sur du Python » ; entre-temps `@assistant-ui` a publié un adaptateur Python officiel, ou LangGraph Platform a livré un mode « AI SDK protocol ». Les 12 000 LOC du dépôt sont devenus 20 lignes d'import. **Argument sérieux** : ~80 % du volume du dépôt est du front vendorisé (shadcn + xulux, `sidebar.tsx` seul = 704 LOC) qui n'apporte rien à la thèse du projet ; le cœur défendable tient dans `protocol/` (~340 LOC) + `core/graph.py` (243 LOC). **Contre-argument** : cet adaptateur n'existe pas au 28-07-2026 (`README.md:29-30`), et le front vendorisé est ce qui rend la démo cliquable en une commande. **Conclusion : la thèse tient encore, mais sa durée de vie est celle de l'absence d'un adaptateur officiel.** | L'annonce d'un adaptateur Python officiel côté assistant-ui / Vercel AI SDK. | Ne rien construire de plus sur le front. Si l'adaptateur sort → archiver et pointer vers lui. |
| **P2** | **Exposé, puis compromis.** Quelqu'un a mis le POC derrière une URL publique « juste pour montrer ». Sans auth (`README.md:383`), n'importe qui a pu changer le prompt système, ajouter un serveur MCP arbitraire (`api/settings.py:152`) et faire exécuter du code tiers via un transport `stdio` (`core/mcp.py:84-92`), et brûler la clé LLM. | Un pic de requêtes `/api/chat` dans les logs uvicorn ; une ligne « réglages enregistrés » (`core/settings.py:214`) que personne n'a déclenchée ; une facture ou un quota épuisé. | Ne jamais exposer. Si exposition nécessaire : auth en amont (reverse proxy) **avant** tout le reste, et désactiver la création de serveurs MCP `stdio`. |
| **P2** | **Coût silencieux sur provider payant.** Un utilisateur bascule sur `openai` depuis l'UI (`api/settings.py:125`), le graphe utilise l'API Responses avec raisonnement par défaut (`core/model.py:263-269`), et l'historique complet est renvoyé à chaque tour. Aucun compteur de coût n'existe dans le dépôt. | Les métriques `agent.metrics` montrent `tokens_entree` qui croît linéairement par tour — mais **personne ne les regarde**, il n'y a pas de tableau de bord. | La fenêtre 24 k tokens (`core/settings.py:69`) borne déjà le pire cas. Suffisant pour un POC. **Ne pas construire de suivi de coût** : la parade coûterait plus que le dégât. |

**Le risque sans signal précoce** est le P1 « divergence du protocole » : rien dans le
dépôt ne le détecte automatiquement (pas de CI, pas de test front). Il se découvre par un
humain qui essaie l'app — donc potentiellement des mois après. Cela **amplifie son impact**.

---

## 10. Verdict & décision

**VERDICT — Continuer, mais en mode « geler le périmètre et verrouiller la
reproductibilité ». N'ajoutez aucune fonctionnalité avant d'avoir versionné `uv.lock` et
épinglé les dépendances front.**  *(confiance : haute)*

**Pourquoi.**
1. La thèse du projet est valide et non résolue ailleurs : il n'y a pas d'équivalent Python
   à `@ai-sdk/langchain` (`README.md:29-30`), et le pont est écrit, testé sur le chemin de
   production (`README.md:367-370`).
2. La qualité d'ingénierie est au-dessus de la moyenne d'un POC : couches vérifiables
   (`README.md:91-92`), garde-fous coût/quota déjà en place (`core/graph.py:32-39,196`),
   dégradation sans base (`main.py:36-39`). Ce n'est pas un squelette.
3. Mais la surface de pourrissement est disproportionnée par rapport à l'effort de
   maintenance disponible (1 contributeur, 2 commits) : catalogue de modèles daté du
   25-07-2026 avec des extinctions annoncées au 16-08-2026 (`core/model.py:36-38`), et
   **aucun lockfile Python**.

**Ce que ça coûte si je me trompe / retour arrière.** Si « continuer » est le mauvais choix,
le coût est de ~1 h de travail (lock + épinglage) sur un dépôt qu'on archivera de toute
façon. Décision **totalement réversible** : archiver reste possible à tout moment, sans
migration ni engagement externe. C'est une porte battante.

**Critères d'abandon (observables).** Archiver le dépôt si **l'un** survient :
- un adaptateur Python officiel du protocole AI SDK est publié par assistant-ui ou Vercel ;
- aucun commit pendant 120 jours **et** aucun fork/star/issue entrant ;
- `make dev` sur une machine vierge échoue et la remise en marche demande > 1 journée.

**Revue le 17-08-2026** — soit le lendemain de l'extinction annoncée des modèles Groq
(`core/model.py:37`). Vérifier ce jour-là que `make dev` + un message par défaut fonctionne
encore.

**Contre-argument le plus fort (red team).** « Ce dépôt devrait être archivé maintenant :
c'est un POC déclaré non maintenu par son propre README (`README.md:399`), avec 1
contributeur, 0 CI et 3 jours d'existence. Tout investissement de maintenance est du
sunk-cost sur un artefact dont la valeur est déjà entièrement extractible par copier-coller
de `protocol/`. » — **Réponse** : cet argument est bon, et il gagnerait si la parade coûtait
cher. Elle coûte ~1 h (un `uv lock`, un épinglage), pour un dépôt public qui sert de vitrine
et dont le premier échec possible est « ça ne démarre pas ». Le ratio tranche en faveur de
« continuer, périmètre gelé ». S'il fallait 2 semaines de durcissement, j'archiverais.

**Prochaine action concrète.**
```bash
cd apps/api && uv lock          # puis versionner apps/api/uv.lock
```
Puis, dans `apps/web/package.json`, remplacer `^` par des versions exactes sur
`@assistant-ui/react`, `@assistant-ui/react-ai-sdk`, `@assistant-ui/react-markdown` et `ai`
(lignes 15, 16, 17, 21). Enfin corriger les 2 références obsolètes à `safe_tool()`
(`README.md:343`, `README.fr.md:330`) et le commentaire MCP de `infra/db.py:54`.
