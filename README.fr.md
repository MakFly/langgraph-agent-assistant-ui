# LangGraph POC — agent à outils gratuits

> 🇬🇧 **[English version](README.md)** — la version anglaise est le README principal du dépôt.

Un agent **LangGraph (Python)** qui appelle de vrais outils (Wikipédia, Hacker News,
météo, calculatrice), streamé au **protocole AI SDK**, affiché par **assistant-ui** dans
un front **Vite + React + Tailwind 4 + shadcn/ui**. Conversations historisées, agent
configurable depuis l'UI.

Les quatre outils sont **gratuits et sans clé**. Seul le LLM demande un compte — et trois
des quatre providers proposés ont un free tier.

Tout tourne dans Docker, avec hot reload des deux côtés : aucun runtime Python ni Node
n'est requis sur la machine hôte.

---

## Architecture

```
╔══════════════════ apps/web  ─ Vite 8 · React 19 · Tailwind 4 · shadcn · assistant-ui ══════════════╗
║   <Thread/> + <ThreadList/>  ◀── usePersistentChatRuntime  ──▶  POST /api/chat                     ║
║   coque « xulux » : SidebarProvider · SidebarInset · navbar                    /api/threads/*      ║
╚═══════════════════════════════════════════════════════════════════════════════/api/settings/*═════╝
                                    │  SSE — UI Message Stream (x-vercel-ai-ui-message-stream: v1)
                                    ▼
╔══════════════════ apps/api  ─ Python 3.13 · FastAPI · LangGraph 1.x ═══════════════════════════════╗
║  to_lc_messages(UIMessage[])  →  graph.astream([messages, updates])  →  ui_message_stream()        ║
║                                                                                                    ║
║        ┌─────────┐   tool_calls ?   ┌─────────┐                                                    ║
║  START ─▶ agent  ├───── oui ───────▶│  tools  │──── ToolNode ───┐                                  ║
║        │ (LLM)   │◀─────────────────┴─────────┘                 │                                  ║
║        └────┬────┘   boucle ReAct (plafond configurable)        │                                  ║
║             │ non                wikipedia · hacker_news · weather · calculator · outils MCP        ║
║             ▼                                                                                      ║
║            END                                                                                     ║
╚════════════════════════════════════════════════════════════════════════════════════════════════════╝
                                    │
                                    ▼
                  PostgreSQL 16  ─ base langgraph_poc
                  threads · messages · settings · mcp_servers
```

**Qui fait quoi**

| Brique | Rôle |
|---|---|
| `langgraph` | Le graphe : état, nœuds, arêtes conditionnelles, boucle ReAct |
| `langchain-core` (`@tool`) | Définition des outils, schémas dérivés des signatures + docstrings |
| `agent/protocol/stream.py` | Émet le protocole de stream AI SDK (écrit à la main, voir plus bas) |
| `agent/protocol/messages.py` | Reconvertit l'historique `UIMessage[]` en messages LangChain |
| `agent/api/threads.py` | Historisation des conversations |
| `agent/core/settings.py` | Configuration runtime : outils, agent, modèle, serveurs MCP |
| `@assistant-ui/react` | L'UI qui parle ce protocole nativement (texte, appels d'outils, erreurs) |
| `shadcn/ui` | Les primitives visuelles, clonées depuis la maquette `v1-xulux` |

### Les quatre couches d'`apps/api`

```
        api/         ──▶ core/  ──▶ infra/
     (FastAPI)          (agent)     (db, http, log)
          │                ▲
          └──▶ protocol/ ──┘
              (AI SDK)
```

Les dépendances ne pointent que vers l'intérieur, et c'est **vérifiable** :

```bash
grep -rhoE "from agent\.(api|core|protocol|infra)" src/agent/core/   # → core, infra
grep -rhoE "from agent\.(api|core|protocol|infra)" src/agent/infra/  # → rien
```

| Couche | Contenu | Ce que ça garantit |
|---|---|---|
| `api/` | routers FastAPI, corps de requête, codes HTTP | **FastAPI n'existe que là.** `core/` ne l'importe pas une seule fois |
| `protocol/` | conversion et émission du protocole AI SDK | changer de client front ne touche que ce paquet |
| `core/` | graphe, modèle, outils, MCP, configuration | tournerait à l'identique derrière une CLI ou un worker |
| `infra/` | Postgres, client HTTP, journalisation | ne connaît ni l'agent ni le web |

**Pourquoi FastAPI, alors ?** LangGraph est une bibliothèque de graphe, pas un serveur :
il faut bien quelque chose pour exposer `/api/chat` en SSE, l'historisation et le CRUD de
configuration. L'alternative serait le serveur de LangGraph Platform (`langgraph dev`),
qui apporte son propre protocole HTTP et son propre stockage — or ici c'est le front
(assistant-ui) qui impose le protocole AI SDK, et les conversations vivent dans nos
tables. FastAPI est donc le choix qui garde la maîtrise du fil, confiné à une couche de
~480 lignes.

---

## Démarrage

```bash
make install          # crée apps/api/.env et construit les images
# puis renseigner UNE clé dans apps/api/.env (voir ci-dessous)

make dev              # démarre la stack en arrière-plan
```

Ouvrir <http://localhost:4311> — la racine redirige vers `/ichat`. `make` seul liste
toutes les cibles.

### Routes

| URL | Écran |
|---|---|
| `/ichat` | nouvelle conversation |
| `/ichat/c/:id` | conversation existante (lien profond, rechargeable, partageable) |
| toute autre URL | `replaceState` vers `/ichat` |

**Pas de routeur** : à deux routes, `history.pushState` + `popstate` suffisent
(`lib/chat-route.ts` pour les URLs, `hooks/use-chat-route.ts` pour l'état). L'URL est
l'unique source de vérité de la conversation ouverte — elle alimente le `threadId`
contrôlé de `useRemoteThreadListRuntime`, dont le `onThreadIdChange` la met à jour en
retour. Précédent/suivant du navigateur fonctionnent donc sans code dédié. Le jour où il
faut des routes imbriquées, des loaders ou du code splitting par route, prendre un vrai
routeur — pas avant.

En dev, le repli SPA est celui de Vite (`appType: 'spa'`, par défaut) : `/ichat/c/x`
renvoie `index.html`. **Un déploiement statique devra fournir la même réécriture**
(`try_files $uri /index.html;` chez nginx), sinon un rechargement sur une conversation
donnera un 404.

| Cible | Effet |
|---|---|
| `make dev` | démarre la stack, hot reload actif des deux côtés |
| `make logs` | suit les logs des deux services — réglages : [docs/logging.md](docs/logging.md) |
| `make stop` / `make down` | arrête / supprime les conteneurs |
| `make test` | les tests de l'API, dans le conteneur |
| `make test-unit` | idem sans les tests qui tapent des APIs externes |
| `make check` | tests + lint + types du front + build |
| `make clean` | conteneurs, volumes et artefacts |

### Choisir un LLM

| `LLM_PROVIDER` | Clé | Coût | Où l'obtenir |
|---|---|---|---|
| `groq` *(défaut)* | `GROQ_API_KEY` | **gratuit**, sans CB | <https://console.groq.com/keys> |
| `google` | `GOOGLE_API_KEY` | **gratuit**, quota quotidien | <https://aistudio.google.com/apikey> |
| `ollama` | — | **gratuit**, 100 % local | <https://ollama.com> |
| `openai` | `OPENAI_API_KEY` | payant à l'usage | <https://platform.openai.com/api-keys> |

Le provider et le modèle se changent aussi **depuis l'UI** (Configuration → Modèle), sans
redémarrage. Les clés, elles, restent dans `apps/api/.env` : l'API n'expose jamais leur
valeur, seulement un booléen `has_key` par provider.

`docker compose` lit `apps/api/.env` **à la création du conteneur** : après avoir changé
une clé, `make down && make dev` (un simple `restart` ne la relira pas).

---

## Docker

Trois services, autonomes : `git clone` puis `make dev` suffit, aucune infrastructure
externe n'est supposée.

| Service | Image | Rechargement |
|---|---|---|
| `api` | `python:3.13-slim` + `uv` | `uvicorn --reload` surveille `/app/src`, monté depuis `apps/api/src` |
| `web` | `oven/bun:1.3-alpine` | HMR Vite, `apps/web` monté, `host: true` pour écouter hors loopback |
| `db` | `postgres:16-alpine` | volume `db_data` ; aucun port publié, seule l'API l'atteint |

`node_modules` du front vit dans un volume nommé : sans lui, le bind mount de `apps/web`
masquerait celui installé à la construction de l'image et Vite refuserait de démarrer.
Corollaire : après un `bun add`, il faut recréer ce volume
(`docker compose down && docker volume rm langgraph-poc_web_node_modules`).

L'API attend que la base soit `service_healthy` avant de démarrer, donc le premier
`make dev` ne joue pas au plus rapide avec la création du schéma.

**Brancher une base existante.** `docker-compose.override.yml` est gitignoré et chargé
automatiquement par compose : y redéfinir `DATABASE_URL` suffit à viser un Postgres
mutualisé, et `db: profiles: ["disabled"]` neutralise celui du projet (l'API a alors
besoin de `depends_on: !reset []`). Le dépôt reste autonome pour tout le monde.

---

## Les outils

Tous keyless, tous appelés en HTTP direct, tous avec timeout de 8 s.

| Outil | Source | Pourquoi celle-là |
|---|---|---|
| `wikipedia_search` | API REST Wikipédia | stable depuis des années, pas de quota |
| `hacker_news_search` | API Algolia HN | sans clé, sans rate limit gênant |
| `weather_forecast` | Open-Meteo (geocoding + forecast) | gratuit en usage non commercial |
| `calculator` | évaluateur AST local | zéro réseau, zéro dépendance |

Choix assumé : **pas de scraping DuckDuckGo**. C'est ce qu'on voit dans la plupart des
démos LangChain, et c'est ce qui casse en premier — HTML qui bouge, rate limiting, IP
bloquées. Quatre APIs publiques documentées valent mieux qu'un scraper qui marche
aujourd'hui.

### Le calculateur

L'expression vient du LLM, lui-même orienté par l'entrée utilisateur et par ce que les
outils web ramènent : **c'est de la donnée non fiable**. Plutôt que de durcir un `eval()`
(jeu perdu d'avance), `tools/calculator.py` parse l'expression et n'exécute que des nœuds
AST explicitement autorisés. Sont refusés par construction : accès aux attributs,
indexation, comprehensions, lambdas, noms libres, et tout appel hors liste blanche.
Sept tentatives d'évasion sont couvertes par les tests, dont `__import__('os').system`,
`().__class__.__bases__` et l'épuisement CPU par `2 ** 10 ** 9`.

Différence avec la version TypeScript : mathjs gérait les conversions d'unités
(`90 km/h to m/s`), pas cet évaluateur. La docstring de l'outil indique au modèle de
convertir lui-même (`90 / 3.6`), ce qu'il fait sans difficulté.

---

## Historisation

Le serveur **ne comprend pas** le contenu des messages : le client assistant-ui encode
lui-même chaque message (`format` + `content`) et le serveur ne fait que le stocker et le
rendre. Il n'y a donc pas deux représentations de la conversation à garder synchronisées —
c'est ce qui rend la persistance sûre ici, alors qu'un checkpointer LangGraph côté serveur
aurait créé une seconde source de vérité (et les bugs de restart / régénération / édition
qui vont avec).

Le contrat REST est imposé par le `RemoteThreadListAdapter` d'assistant-ui :

```
GET    /api/threads?scope=          POST   /api/threads
GET    /api/threads/{id}            PATCH  /api/threads/{id}      DELETE /api/threads/{id}
GET    /api/threads/{id}/messages   POST   /api/threads/{id}/messages
```

Le titre est généré côté client à partir du premier message utilisateur. Suppression en
cascade, scopes étanches, upsert sur réémission d'un message (édition, régénération).

---

## Configuration

Accessible depuis la sidebar (Configuration), quatre onglets :

- **Outils** — activer/désactiver chaque outil. Un outil désactivé n'est ni déclaré au
  modèle ni exécutable : il disparaît du `bind_tools` **et** du `ToolNode`.
- **Agent** — prompt système (remplace intégralement le défaut), plafond de boucle (1..20),
  température (0..2).
- **Modèle** — provider et modèle. Les clés ne sortent jamais de l'API.
- **MCP** — CRUD des serveurs, et leurs outils sont **découverts et bindés au modèle**
  (`agent/core/mcp.py`). Un serveur injoignable est signalé dans le panneau et ignoré, sans
  bloquer le démarrage ni le chat. Détails : [docs/settings.md](docs/settings.md#serveurs-mcp).

Les réglages s'appliquent **sans redémarrage**. C'est le piège de cette fonctionnalité :
le graphe est mis en cache, et un cache non invalidé aurait rendu tous les réglages
inopérants jusqu'au redémarrage du conteneur. `get_graph()` compare donc une version de
configuration et reconstruit dès qu'elle change ; un test le vérifie explicitement.

Si Postgres est indisponible, l'API sert les valeurs par défaut et le chat continue de
fonctionner — seule l'historisation s'éteint (`/api/health` expose `history: false`).

---

## Structure

```
apps/
  api/                      Python — 4 couches, dépendances vers l'intérieur
    src/agent/
      main.py               assemblage : app FastAPI, lifespan, montage des routers
      api/                  ── surface HTTP (la SEULE couche qui importe FastAPI)
        chat.py               POST /api/chat (SSE) · GET /api/health
        threads.py            historisation des conversations
        settings.py           router /api/settings : corps de requête, 503, codes
      core/                 ── le domaine : l'agent
        graph.py              StateGraph + fenêtre de contexte + reprise + cache
        model.py              fabrique de modèle + catalogue + capacités d'effort
        settings.py           configuration : modèles, snapshot, version, lecture/écriture
        mcp.py                découverte des outils des serveurs MCP
        callbacks.py          métriques de run (latence, tokens, durée des outils)
        tools/                un fichier par outil
      protocol/             ── le protocole AI SDK, dans les deux sens
        messages.py           UIMessage[] -> messages LangChain
        stream.py             émission du « UI Message Stream »
      infra/                ── briques techniques, sans métier
        db.py                 pool asyncpg + schéma
        http.py               client HTTP avec timeout + sérialisation des résultats
        log.py                configuration de la journalisation
    tests/                  89 tests
    Dockerfile
  web/
    src/
      components/ui/        primitives clonées depuis la maquette v1-xulux
      components/xulux/     coque : sidebar, navbar, logo, nav-user, thème
      components/settings/  panneau de configuration
      components/chat/      routage /ichat, sélecteur de modèle, jauge de contexte
      components/assistant-ui/  généré par le registre shadcn, puis vendored
      lib/                  routes, mémoire du modèle, estimation du contexte
docker-compose.yml
Makefile
```

---

## Détails d'implémentation qui méritent une note

Le graphe lui-même (fenêtre de contexte, reprise sur 429, erreurs d'outils, outils MCP,
métriques) a sa propre note : [docs/graph.md](docs/graph.md).

**Le pont vers l'UI est écrit à la main.** Côté TypeScript, `@ai-sdk/langchain` faisait la
traduction. Il n'a pas d'équivalent Python : `stream.py` et `messages.py` implémentent le
protocole directement. Le format n'a pas été deviné — il a été **capturé sur le fil** de
l'implémentation TypeScript avant migration (en-têtes, types de chunks, terminateur
`[DONE]`, forme exacte du corps de requête), puis recoupé avec le type `UIMessageChunk` du
paquet `ai`. Les charges utiles des tests de `messages.py` sont ces captures.

**Deux modes de stream LangGraph en parallèle**, chacun pour ce qu'il fait le mieux :
`messages` donne les tokens au fil de l'eau (effet de frappe, arguments d'outil qui se
construisent), `updates` donne l'état consolidé en sortie de nœud — c'est là qu'on lit les
arguments **parsés** et les résultats d'outils, sans réassembler du JSON partiel.

**L'agent consomme `astream`, pas `ainvoke`.** Un `ainvoke` ne produit aucun token
intermédiaire : le `stream_mode="messages"` n'aurait rien à transmettre et l'UI resterait
figée jusqu'à la réponse complète.

**Les arguments d'outil doivent atteindre le client.** C'est le bug qui a coûté le plus
cher dans la version TypeScript : l'adaptateur n'émettait que l'id de run et le nom de
l'outil. Le client stockait donc un appel sans `input` ; au tour suivant, l'historique
reconverti contenait un `tool_call` sans `function.arguments` et l'API répondait
`400 INVALID_TOOL_RESULTS` — premier message OK, deuxième en échec. Deux tests verrouillent
ça, côté émission et côté relecture.

**Un message assistant côté UI porte l'appel ET la réponse.** Les APIs de chat exigent la
séquence `AIMessage(tool_calls)` → `ToolMessage` → `AIMessage(texte)`. `messages.py`
découpe donc un `UIMessage` en plusieurs messages LangChain, dans l'ordre des parts. Un
appel d'outil sans résultat est ignoré : sans `ToolMessage` correspondant, la requête
amont échoue.

**Un outil ne lève jamais d'exception.** `safe_tool()` sérialise l'erreur en JSON pour que
le LLM puisse la lire et se rattraper, au lieu d'avorter le run.

**Composants vendored.** `components/ui/` et `components/xulux/` sont clonés de la maquette
`ux-ui-unified/v1-xulux`, `components/assistant-ui/` vient du registre shadcn. Écarts
assumés, commentés à l'endroit concerné : pas de routeur ici (donc `usePathname` remplacé
par une prop, `next/link` par une ancre), `next-themes` remplacé par un hook local, la
recherche cmdk remplacée par l'action « nouvelle conversation », et le trigger de sidebar
de l'en-tête retiré parce qu'il faisait doublon avec celui de la navbar. Ajouts locaux : le
variant `pointer-coarse:` pour atteindre 44 px de cible sur appareil tactile sans grossir
l'UI à la souris, et l'utilitaire `no-scrollbar`. Regénérer via `shadcn add` écrasera ces
patchs.

**Pas de CORS en dev.** Vite proxifie `/api` vers le service `api` par son hostname sur le
réseau Docker, donc le front appelle une URL same-origin.

---

## Tests

```bash
make test        # 89 tests dans le conteneur
make test-unit   # 85 tests, sans les appels réseau
```

Les tests du graphe passent par `ui_message_stream()`, c'est-à-dire **le chemin exact de
l'endpoint** : seul l'appel réseau au LLM est remplacé par un faux modèle qui streame de
vrais `tool_call_chunks`. Les outils, eux, s'exécutent réellement, et les tests
d'historisation et de configuration tapent la vraie base.

C'est une leçon de la version TypeScript : les tests y reconstruisaient leur propre stream
au lieu d'appeler le code de production, et sont restés verts pendant que le multi-tours
était cassé. Un test qui n'exerce pas le chemin de production ne teste rien.

---

## Limites connues

- **Les outils MCP ne sont pas filtrables un par un** : seul l'interrupteur du serveur
  existe, là où les outils du projet se désactivent individuellement.
- **Pas de checkpointer LangGraph** : un run interrompu n'est pas repris, et la
  validation humaine avant appel d'outil (`interrupt()`) est hors de portée.
  Voir [docs/graph.md](docs/graph.md).
- **Pas d'authentification** : toutes les routes sont ouvertes, et la configuration est
  globale. Suffisant en local, inacceptable exposé.
- **Images de dev uniquement** : le Dockerfile de l'API embarque les outils de test et
  monte le code ; une image de prod demanderait un multi-stage sans `--reload`.
- **Le bouton pièce jointe** du composer est affiché par assistant-ui mais le backend ne
  traite pas les pièces jointes.
- **Prompt injection** : une page Wikipédia ou un titre HN peut contenir des instructions
  que le modèle suivra. Sans effet de bord ici (tous les outils sont en lecture seule),
  mais à traiter avant d'ajouter le moindre outil qui écrit. **Le sujet devient réel avec
  MCP** : un serveur tiers apporte des outils dont on ne contrôle ni le code ni les
  effets de bord.

---

## Licence

[MIT](LICENSE) — c'est une preuve de concept, pas un produit maintenu. Forkez, prenez le
pont `protocol/`, et allez-y.
