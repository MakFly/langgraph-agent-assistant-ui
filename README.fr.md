# LangChain — RAG moderne et agent à outils

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

Accessible depuis la sidebar (Configuration), cinq onglets :

- **Outils** — activer/désactiver chaque outil. Un outil désactivé n'est ni déclaré au
  modèle ni exécutable : il disparaît du `bind_tools` **et** du `ToolNode`.
- **Agent** — prompt système (remplace intégralement le défaut), plafond de boucle (1..20),
  température (0..2).
- **Modèle** — provider et modèle. Les clés ne sortent jamais de l'API.
- **MCP** — CRUD des serveurs, et leurs outils sont **découverts et bindés au modèle**
  (`agent/core/mcp.py`). Un serveur injoignable est signalé dans le panneau et ignoré, sans
  bloquer le démarrage ni le chat. Détails : [docs/settings.md](docs/settings.md#serveurs-mcp).
- **Sources** *(administrateurs)* — dépôt de fichiers, ACL, OCR dynamique, simulation,
  synchronisation et historique d'exécution.

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
        ingestion.py          sources, uploads et jobs d'indexation administrés
      core/                 ── le domaine : l'agent
        graph.py              StateGraph + fenêtre de contexte + reprise + cache
        model.py              fabrique de modèle + catalogue + capacités d'effort
        settings.py           configuration : modèles, snapshot, version, lecture/écriture
        mcp.py                découverte des outils des serveurs MCP
        callbacks.py          métriques de run (latence, tokens, durée des outils)
        ingestion.py          persistance des sources, fichiers et exécutions
        rag/ocr.py             rendu des pages et OCR provider/modèle dynamique
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
make test        # 212 tests dans le conteneur
make test-unit   # tests sans les appels réseau
```

Les tests du graphe passent par `ui_message_stream()`, c'est-à-dire **le chemin exact de
l'endpoint** : seul l'appel réseau au LLM est remplacé par un faux modèle qui streame de
vrais `tool_call_chunks`. Les outils, eux, s'exécutent réellement, et les tests
d'historisation et de configuration tapent la vraie base.

C'est une leçon de la version TypeScript : les tests y reconstruisaient leur propre stream
au lieu d'appeler le code de production, et sont restés verts pendant que le multi-tours
était cassé. Un test qui n'exerce pas le chemin de production ne teste rien.

---

## Recherche documentaire et verticale courtage

Le corpus de démonstration est celui d'un **cabinet de courtage IARD** : 156 documents
générés de façon déterministe (conditions générales, contrats, avenants, attestations,
sinistres, fils de courriels, procédures internes), et une boîte de 15 courriels entrants
à traiter.

```bash
make corpus     # régénère corpus/, eval/questions.yaml et mailbox/
make ingest     # indexe (idempotent : rien à repayer si rien n'a changé)
make eval       # rappel@k, couverture du fait, MRR, abstention, fuites d'ACL
make ablation   # ce que chaque technique de recherche apporte, une par une
make calibrate  # DÉDUIT le seuil d'abstention d'un critère écrit
make inbox      # courriel → dossier préparé : la file de travail
make inbox-eval # mesure la verticale sur 5 exécutions (moyenne + étendue)
```

### Ingestion administrée et OCR dynamique

Un administrateur peut maintenant tout piloter dans **Configuration → Sources** :
créer une source, déclarer ses groupes ACL, déposer des fichiers, simuler le lot,
l'indexer et relire l'historique durable des exécutions.

```text
╔═══════════ OUTIL ADMIN ═══════════╗
║ ┌─────────┐  HTTPS/multipart      ║
║ │ Sources │ ────────────────────▶ ║
║ └─────────┘                       ║
╚═══════════════════════════════════╝
                  │ config + fichiers
                  ▼
╔════════════════ API D'INGESTION ════════════════╗
║ ┌──────────────┐  job durable  ┌─────────────┐ ║
║ │ Source + ACL │ ─────────────▶ │ Parse/OCR   │ ║
║ └──────────────┘                └─────────────┘ ║
╚═════════════════════════════════════════════════╝
                  │ pages scannées : image + prompt
                  ▼
       ┌───────────────────────────┐
       │ Provider/modèle dynamique │
       └───────────────────────────┘
                  │ texte extrait
                  ▼
       ┌───────────────────────────┐
       │ Index hybride pgvector    │
       └───────────────────────────┘
```

Légende : les doubles cadres sont les surfaces produit ; les cadres simples sont
les composants d'exécution. Composants : UI admin, API d'ingestion, volume
`ingestion_data`, journal Postgres, parseur natif, OCR multimodal, index RAG.

Le provider, le nom exact du modèle, le prompt, la résolution et le plafond de
pages OCR sont enregistrés **par source**. Le modèle est un champ libre : le
catalogue n'est qu'une aide et ne bloque pas un modèle privé ou un tag Ollama.
Les clés restent dans `apps/api/.env`; l'UI ne reçoit qu'un booléen
« configurée/absente ». Les PDF textuels ne partent pas au LLM : seules les pages
sans couche texte sont rendues en image. Les images PNG/JPEG/WebP/TIFF suivent le
même chemin.

Les fichiers déposés vivent dans le volume `ingestion_data`, distinct du corpus
Git. Un job `running` interrompu par un redémarrage est remis en file au prochain
démarrage. La simulation ne vectorise pas et n'écrit pas l'index, mais un OCR
activé appelle quand même le modèle — le plafond de pages est donc le garde-fou
de coût en amont.

Deux documents détaillent le raisonnement, les mesures et les limites :

- [docs/rag-moderne.md](docs/rag-moderne.md) — la chaîne de recherche, le tableau
  d'ablation, et les deux bugs que la mesure a trouvés (une branche lexicale qui ne
  servait à rien, une fusion RRF qui dégradait ce qu'elle était censée améliorer) ;
- [docs/verticale-courtage.md](docs/verticale-courtage.md) — la cascade de rattachement,
  ce que le système refuse de décider, et pourquoi.

L'essentiel en deux phrases : **le jeu d'évaluation compte 93 cas positifs et 35
négatifs difficiles**, parce qu'un RAG se juge autant sur ce qu'il refuse de répondre
que sur ce qu'il trouve. Et **38 de ces cas déclarent le fait exact que la réponse doit
contenir**, parce que retrouver le bon document ne prouve pas qu'on a rendu le bon
fragment — deux angles morts du rappel, mesurés séparément.

---

## Limites connues

- **Le corpus est synthétique.** C'est la limite qu'aucun code ne lève : elle ne tombera
  qu'avec un corpus réel. Les écarts *relatifs* entre configurations restent bien plus
  fiables que les valeurs absolues, et c'est sur eux que reposent toutes les décisions.
- **La qualité RÉDACTIONNELLE des réponses n'est pas mesurée.** La couverture du fait
  vérifie que le texte rendu contient la réponse, pas que le modèle la reformule bien.
  Côté verticale en revanche, le brouillon est contrôlé : aucune référence étrangère au
  dossier n'y est tolérée, et le contrôle est déterministe.
- **La précision des pièces reste perfectible** : 2 à 6 pièces sur 27 sont réclamées
  alors qu'elles étaient déjà au dossier. Un aller-retour inutile, pas une erreur de
  fond — et le biais va dans le bon sens.
- **Un modèle à température zéro n'est pas déterministe.** `make inbox-eval` mesure donc
  sur cinq exécutions et rend l'étendue : sur quinze courriels, un cas vaut sept points.
  Les lignes de `make ablation` qui appellent un LLM portent la même variance — les six
  premières, elles, sont déterministes et reproductibles à l'identique.
- **Les outils MCP ne sont pas filtrables un par un** : seul l'interrupteur du serveur
  existe, là où les outils du projet se désactivent individuellement.
- **Pas de checkpointer LangGraph** : un run interrompu n'est pas repris, et la
  validation humaine avant appel d'outil (`interrupt()`) est hors de portée.
  Voir [docs/graph.md](docs/graph.md).
- **L'authentification et le RBAC sont natifs**, mais l'administration des comptes
  reste en CLI : il n'existe pas encore d'écran de gestion des utilisateurs.
- **Images de dev uniquement** : le Dockerfile de l'API embarque les outils de test et
  monte le code ; une image de prod demanderait un multi-stage sans `--reload`.
- **Les jobs d'ingestion sont persistants mais exécutés dans le processus API.**
  Le claim SQL empêche un doublon et un redémarrage reprend la file ; une charge
  importante demanderait un worker dédié et une vraie file de messages.
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
