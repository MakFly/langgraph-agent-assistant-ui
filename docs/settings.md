# Module Settings — configuration globale de l'agent

Configuration **globale** de l'agent, persistée sur Postgres et applicable à chaud :
outils actifs, prompt système, plafond de boucle, provider/modèle, serveurs MCP.
Il n'y a pas d'authentification dans ce POC, donc pas de configuration par
utilisateur — un seul jeu de réglages pour l'instance.

## Architecture

```
╔═══════════════════════════════ apps/web ═══════════════════════════════════╗
║  App.tsx ──▶ <SettingsProvider>   un seul useSettings() pour toute l'app   ║
║                      │ contexte partagé                                    ║
║          ┌───────────┴──────────────────────────┐                          ║
║          ▼                                      ▼                          ║
║  settings-menu-item.tsx (sidebar)      thread.tsx (composer)               ║
║          │ open                                 │ affiche le modèle actif  ║
║          ▼                                      ▼                          ║
║  settings-dialog.tsx  (50 % × 85 % vp)     composer-model-picker.tsx       ║
║          │ onglets                              │ modèles du provider      ║
║          ▼                                      │ courant uniquement       ║
║  tools · agent · model · mcp  panels            │                          ║
║          └──────────────────────────┬──────────┘                           ║
║                                     │ patchModel()  ──▶  localStorage      ║
║                                     ▼  (lib/model-preference.ts)           ║
╚═════════════════════════════════════┼══════════════════════════════════════╝
                                      │ fetch JSON  (proxy Vite /api)
╔═════════════════════════════════════▼═══════════════════════════╗
║ apps/api                    settings.py  (router /api/settings) ║
║                                  │                              ║
║        ┌── refresh() ────────────┤                              ║
║        ▼                         ▼ SQL (asyncpg)                ║
║  snapshot mémoire          ┌─────────────┐                      ║
║  + version()  ◀── lit ──┐  │ settings    │  PostgreSQL 16       ║
║        │                │  │ mcp_servers │  (base langgraph_poc)║
║        ▼                │  └─────────────┘                      ║
║   graph.get_graph() ────┘                                       ║
║        │ clé de cache = version()                               ║
║        ▼                                                        ║
║   build_graph() ──▶ outils filtrés · prompt · max_loops · modèle ║
║        ▲                                                        ║
║   stream.py ──▶ /api/chat                                       ║
╚═════════════════════════════════════════════════════════════════╝
```

**Composants**

| Élément | Rôle |
|---|---|
| `apps/api/src/agent/core/settings.py` | Router `/api/settings`, modèles pydantic, snapshot mémoire, compteur `version()` |
| `apps/api/src/agent/infra/db.py` | Tables `settings` (clé/valeur JSONB) et `mcp_servers` |
| `apps/api/src/agent/core/graph.py` | `build_graph(model, config)` applique la config ; `get_graph()` cache invalidé par `version()` — voir [graph.md](graph.md) |
| `apps/api/src/agent/core/mcp.py` | Découverte des outils MCP, cache lu sans I/O par le graphe, état par serveur |
| `apps/api/src/agent/core/model.py` | `create_model(provider, model, temperature, reasoning_effort)`, `has_key()`, catalogue `PROVIDER_MODELS`, capacités `EFFORT_MODELS` |
| `apps/web/src/hooks/use-settings.ts` | Chargement au montage + mutations (dont la mémoire du modèle) |
| `apps/web/src/components/settings/settings-context.tsx` | `<SettingsProvider>` : un seul état pour le panneau **et** le composer |
| `apps/web/src/components/settings/` | Dialog plein écran (mobile) / 50 % large × 85 % haut (`sm`+) + 4 panneaux |
| `apps/web/src/components/settings/model-select.tsx` | Select des modèles du provider courant, partagé panneau + composer |
| `apps/web/src/components/chat/composer-model-picker.tsx` | Sélecteur de modèle du composer, limité au provider courant |
| `apps/web/src/lib/model-preference.ts` | Mémoire localStorage `{ provider: modèle }` |

## Endpoints

| Méthode | Chemin | Effet |
|---|---|---|
| `GET` | `/api/settings` | État complet : `persisted`, `agent`, `model` (+ `providers[]`, chacun avec son catalogue `models`), `tools`, `mcp_servers` |
| `PATCH` | `/api/settings/agent` | `system_prompt` / `max_tool_loops` (1..20) / `temperature` (0..2) — patch partiel |
| `PATCH` | `/api/settings/model` | `provider` (groq\|google\|ollama\|openai) / `model` / `reasoning_effort` (default\|low\|medium\|high) |
| `PATCH` | `/api/settings/tools/{name}` | `{ "enabled": bool }` — 404 si l'outil n'existe pas |
| `GET` | `/api/settings/mcp` | Liste des serveurs MCP |
| `POST` | `/api/settings/mcp` | Création (id généré serveur) |
| `PATCH` | `/api/settings/mcp/{id}` | Mise à jour partielle, revalidée en entier |
| `DELETE` | `/api/settings/mcp/{id}` | Suppression |

Les mutations MCP (`POST`, `PATCH`, `DELETE`) appellent aussi `refresh()` : c'est ce
qui redécouvre les outils et fait reconstruire le graphe.

Les trois `PATCH` de configuration renvoient **l'état complet**, pas seulement le
domaine touché : le front remplace son state par la réponse, il ne peut donc pas
diverger de ce qui est réellement enregistré.

### Conventions

- **Effacer une surcharge** : envoyer une chaîne vide (`{"system_prompt": ""}`,
  `{"model": ""}`) remet la valeur par défaut. En JSON on ne distingue pas
  « champ absent » de « champ nul » côté client, d'où ce marqueur explicite.
- **Clés API** : jamais renvoyées, même tronquées. `model.providers[]` expose
  seulement `requires_key` et `has_key`.
- **Bornes** : `agent.max_tool_loops_range` et `agent.temperature_range` sont
  renvoyées par l'API pour que le front affiche exactement ce qu'elle valide.

## Sélecteur de modèle du composer

Le composer affiche le modèle réellement actif et permet d'en changer sans ouvrir le
panneau. Il ne propose que les modèles du **provider courant** : c'est le provider qui
décide des modèles disponibles, et il reste réglé dans l'onglet « Modèle ».

- **Catalogue** : `agent.core.model.PROVIDER_MODELS`, exposé par `model.providers[].models`,
  défaut du provider en tête (`DEFAULT_MODELS` en est dérivé). Liste curatée à la main
  et **jamais imposée** : `PATCH /model` accepte n'importe quel nom, et la surcharge
  libre du panneau reste l'échappatoire — indispensable pour Ollama, où seuls les tags
  effectivement `ollama pull` en local existent.
- **Effet réel** : le choix part en `PATCH /api/settings/model`, donc il reconstruit le
  graphe. Pas de sélecteur « vitrine » qui afficherait un modèle sans que l'agent suive.
- **Mémoire localStorage** (`model-by-provider`, `{ "<provider>": "<modèle>" }`) : la
  configuration serveur étant globale, cette mémoire n'est pas une source de vérité.
  Elle sert à une seule chose — quand on change de provider, réappliquer le dernier
  modèle qu'on y avait choisi. Un seul point d'écriture : `patchModel()`.
- **Changement de provider** : `patchModel({ provider })` envoie *toujours* un modèle
  (le mémorisé, sinon `""` qui efface la surcharge). Sans ça, l'ancienne surcharge
  survivrait à la bascule et un modèle Groq partirait chez Gemini.
- **Sans base** : le sélecteur passe en lecture seule, comme le panneau
  (`persisted: false`), et n'affiche rien tant que la configuration n'est pas lue — le
  chat, lui, n'attend jamais la configuration.

## Effort de raisonnement

Réglage `model.reasoning_effort` : `default` (rien n'est transmis, le modèle garde son
comportement) ou l'un des paliers `low` / `medium` / `high`. Rendu dans l'onglet
« Modèle », sous le choix du modèle.

**Le piège : l'effort dépend du modèle, pas du provider.** Transmis à un modèle qui ne
le connaît pas, le paramètre fait échouer la requête. État vérifié le 25-07-2026 :

| Provider / modèle | Accepté |
|---|---|
| OpenAI GPT-5.x (sol/terra/luna, nano) | `none, minimal, low, medium, high, xhigh, max` — mais **refusé avec des tools sur `/v1/chat/completions`** |
| Google Gemini (normalisé par langchain-google-genai) | `minimal, low, medium, high` |
| Groq **GPT-OSS 120b / 20b** | `low, medium, high` |
| Groq `qwen/qwen3.6-27b` | `none, default` — pas de paliers |
| Groq Llama 3.x | aucun raisonnement |
| Ollama | `think` : booléen ou palier **selon le modèle pullé** |

D'où trois garde-fous :

- `agent.core.model.EFFORT_MODELS` déclare qui accepte quoi (`None` = toute la gamme du
  provider, `frozenset()` = personne) et `effort_levels(provider, model)` arbitre. On
  n'expose que l'intersection `low|medium|high`, sinon le réglage ne voudrait pas dire
  la même chose d'un provider à l'autre. Ollama est volontairement hors périmètre : la
  capacité dépend de l'installation locale, pas d'une table.
- `create_model()` **ignore** un palier refusé au lieu de le transmettre : mieux vaut un
  réglage sans effet qu'un chat cassé.
- **Chez OpenAI, la contrainte n'est ni le modèle ni le réglage, c'est le transport.**
  `/v1/chat/completions` refuse les tools sur un modèle qui raisonne — *« use
  /v1/responses or set reasoning_effort to 'none' »* — et ce refus **ne dépend pas de ce
  qu'on envoie** : les GPT-5.x raisonnent par défaut côté serveur, donc l'erreur tombe
  aussi avec le réglage sur « défaut du modèle ». `create_model()` utilise donc
  **toujours** `use_responses_api=True` pour OpenAI. L'alternative (`reasoning_effort:
  "none"`) reviendrait à éteindre le raisonnement pour contourner un problème de
  transport.

  Vérifié en réel sur `gpt-5.6-luna` avec les 4 outils bindés : `effort=default` répond,
  `effort=high` répond, et un vrai appel d'outil traverse correctement l'API Responses
  (`tool-input-start` → `tool-input-delta` → `tool-input-available` →
  `tool-output-available`). C'est ce dernier point qui manquait : le protocole de stream
  n'avait jamais été exercé sur ce transport.
- `GET /api/settings` renvoie `model.effort_levels` pour le modèle **actif** ; vide, le
  front désactive le contrôle et affiche pourquoi.

`_resolve()` neutralise un palier devenu incompatible. Comme les mutations repartent du
snapshot, cette neutralisation finit en base : **basculer vers un modèle sans paliers
perd le choix**, il faut le refaire. Assumé — le conserver imposerait de relire la ligne
brute à chaque patch.

## Le piège : le cache du graphe

`get_graph()` était mis en cache par `@lru_cache(maxsize=1)`. Comme la fonction ne
prend aucun argument, le graphe restait figé sur la configuration du démarrage :
n'importe quel réglage n'aurait eu d'effet qu'après un redémarrage du conteneur.

Le cache est désormais un couple `(version, graphe)` :

1. chaque mutation appelle `settings.refresh()`, qui republie le snapshot et
   incrémente `settings.version()` ;
2. `get_graph()` compare `version()` à la clé mémorisée et reconstruit si besoin.

Un échec de construction (clé API manquante) n'est pas mémorisé — la tentative
suivante réessaie.

## Dégradation sans base

Contrainte structurante : **le chat ne dépend jamais de la disponibilité des
réglages**.

- Le nœud agent lit un snapshot **en mémoire** (`settings.current()`), jamais la
  base : le chemin du chat reste synchrone et sans I/O.
- `refresh()` retombe silencieusement sur les valeurs par défaut (issues de
  l'environnement : `LLM_PROVIDER`, `LLM_MODEL`, `LLM_TEMPERATURE`) si
  `db.is_available()` est faux.
- `GET /api/settings` répond 200 avec `persisted: false`.
- Les **écritures** répondent `503` : mieux vaut refuser franchement qu'accepter un
  réglage qui disparaîtra au redémarrage.

## Serveurs MCP

Les serveurs activés sont **découverts et bindés au modèle** (`agent/core/mcp.py`). La
contrainte qui a dicté la forme : `graph.build_graph()` est **synchrone** et appelé dans le
chemin du chat, alors que découvrir les outils d'un serveur demande des I/O (spawn de
process ou requête HTTP).

```
settings.refresh()  ── async : au démarrage et après CHAQUE mutation
    │
    ├──▶ mcp.refresh(servers) ── I/O, timeout 20 s, ne lève jamais
    │        └──▶ cache module : outils + état par serveur
    │
    └──▶ version()++ ──▶ get_graph() reconstruit ──▶ mcp.tools() (lecture sync)
```

- **Un serveur injoignable ne casse ni le démarrage ni le chat** : il est absent des
  outils, son état part dans `mcp_servers[].status` et le panneau l'affiche (« injoignable
  — ConnectError: … ») au lieu de rester silencieux. La cause est extraite du
  `TaskGroup` du SDK MCP, sinon on n'obtient que « unhandled errors in a TaskGroup ».
- **Les mutations MCP appellent `refresh()`** (création, patch, suppression). Elles ne le
  faisaient pas : sans conséquence tant que MCP n'était pas branché, bloquant ensuite — un
  serveur ajouté restait inerte jusqu'au redémarrage, un serveur supprimé gardait ses
  outils dans le graphe.
- **Un outil MCP homonyme d'un outil du projet est ignoré** (et journalisé) : il ferait
  doublon dans `bind_tools` ou masquerait le nôtre.
- **Pas de gestion de cycle de vie des process `stdio`** : `langchain-mcp-adapters` ouvre
  une session par appel (spawn, appel, fermeture). Coût en latence par appel, aucun process
  orphelin à surveiller.
- La découverte est **synchrone dans la requête** : ajouter un serveur `stdio` dont le
  paquet n'est pas encore en cache peut bloquer le POST plusieurs secondes (borné par le
  timeout). Assumé — c'est plus honnête qu'un statut « unknown » qui se résoudrait plus
  tard sans que le front le sache.

Vérifié en réel avec `uvx mcp-server-time` (le conteneur a `uvx`) :

```bash
curl -X POST localhost:4310/api/settings/mcp -H 'content-type: application/json' \
  -d '{"name":"Horloge","transport":"stdio","command":"uvx",
       "args":["mcp-server-time","--local-timezone=Europe/Paris"],"enabled":true}'
```

→ 2 outils découverts, `get_current_time` réellement appelé par le modèle dans une
conversation.

## Hors périmètre (assumé)
- **Pas de configuration par utilisateur** : il n'y a pas d'auth.
- **Pas de propagation multi-process** : le snapshot vit dans le processus. Avec
  plusieurs workers uvicorn, un `PATCH` ne toucherait que le worker qui l'a reçu.
  Le service tourne en worker unique ; passer à plusieurs workers exigerait un
  bus (LISTEN/NOTIFY Postgres, Redis pub/sub) ou une relecture par requête.
- **Les valeurs d'environnement ne sont pas modifiables** depuis l'API (clés API,
  `OLLAMA_BASE_URL`, `CORS_ORIGIN`) : elles restent dans `apps/api/.env`.
