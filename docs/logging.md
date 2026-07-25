# Journalisation — canaux, niveaux, contexte

Tout part sur **stdout** : `make logs-api`, ou n'importe quel visualiseur qui lit le flux
Docker. Pas de fichier : le conteneur est éphémère, un fichier disparaîtrait au
`docker compose down` et resterait invisible du collecteur.

## Ce qui a motivé ce module : « 0 log conteneur »

Un provider qui refuse une requête (400 sur un paramètre, quota, clé invalide) ne
laissait **aucune trace**. La raison :

```
POST /api/chat ──▶ StreamingResponse (HTTP 200, corps SSE)
                        │
                        ▼
        stream.py : except Exception ──▶ yield {"type":"error"}  ──▶ UI
                                              │
                                              └──▶ (rien) ✗  stdout / logs Docker
```

Le statut HTTP est 200 — l'échec arrive *après* les en-têtes, au milieu du flux. Donc
ni `uvicorn.access` ni le handler d'exception de FastAPI ne voient quoi que ce soit, et
`except: yield` n'écrivait nulle part. Cinq autres `except` étaient dans le même cas.

Les erreurs partent maintenant **dans le flux ET dans les logs**, avec le contexte du
modèle — sans lui, un `400 invalid_request_error` n'est pas diagnosticable :

```
15:21:24 ERROR agent.protocol.stream : run interrompu : Error code: 400 - {...} —
    provider=openai modele=gpt-5.6-luna effort=high outils=4 messages=1
Traceback (most recent call last): ...
```

## On ne réécrit pas Monolog

Le module `logging` de la stdlib **est** l'équivalent de Monolog : canaux
hiérarchiques, niveaux, handlers, formatters, contexte par message. `agent/infra/log.py` ne
fait donc que le configurer une fois au démarrage. Une couche maison par-dessus
n'ajouterait que du code à maintenir et casserait l'intégration avec uvicorn, langchain,
httpx et asyncpg, qui journalisent déjà via `logging`.

| Monolog (PHP) | ici (Python) |
|---|---|
| `new Logger('db')` | `logging.getLogger("agent.db")` |
| `pushHandler(new StreamHandler)` | un handler, sur stdout |
| processors / `$context` | `extra={...}` à l'appel, rendu par le formatter |
| `LineFormatter` / `JsonFormatter` | `LOG_FORMAT=text` \| `json` |
| niveau par canal | `LOG_LEVELS="agent.protocol.stream=DEBUG,agent.db=ERROR"` |
| `$log->error($msg, $ctx)` | `logger.error(msg, extra=ctx)` / `.exception()` |

## Réglages (`apps/api/.env`)

| Variable | Défaut | Effet |
|---|---|---|
| `LOG_LEVEL` | `INFO` | Niveau du canal `agent`. Une valeur inconnue retombe sur `INFO` **et le signale** — elle n'empêche pas le démarrage. |
| `LOG_FORMAT` | `text` | `text` = humain, contexte en `clé=valeur` ; `json` = une ligne JSON par message (`jq`, collecteurs de logs) |
| `LOG_LEVELS` | — | Niveaux par canal, pour ne bavarder que là où on enquête |
| `LOG_ACCESS` | `on` | `off` coupe la ligne par requête d'uvicorn, bruyante quand on suit un run |

La **racine reste à `WARNING`** quoi qu'il arrive : sinon httpx, langchain et asyncpg
noient l'application dès qu'on passe en `DEBUG`. Seul le canal `agent` suit `LOG_LEVEL`.

## Canaux

| Canal | Ce qu'on y trouve |
|---|---|
| `agent.main` | démarrage : provider, modèle, effort, nombre d'outils actifs |
| `agent.log` | niveau et format appliqués, `LOG_LEVEL` inconnu |
| `agent.protocol.stream` | **échec d'un run** (trace + contexte du modèle) |
| `agent.core.model` | modèle instancié (provider, modèle, température, effort), palier ignoré |
| `agent.core.graph` | reconstruction du graphe et version de configuration associée |
| `agent.core.settings` | réglages enregistrés, réglages illisibles, palier neutralisé |
| `agent.core.tools` | outil en échec — l'erreur va au LLM, mais reste visible |
| `agent.db` | base injoignable (sonde en `DEBUG` : elle tourne à chaque requête) |
| `agent.metrics` | latence d'un tour, temps jusqu'au premier token, tokens, durée de chaque outil |
| `agent.core.mcp` | serveur MCP prêt (nombre d'outils) ou injoignable (cause), outil ignoré pour collision |

## Exemples

```bash
# Suivre un run en détail sans le bruit des requêtes HTTP
LOG_LEVEL=DEBUG LOG_ACCESS=off docker compose up api

# N'ouvrir qu'un canal
LOG_LEVELS=agent.protocol.stream=DEBUG docker compose up api

# Sortie machine, filtrée
LOG_FORMAT=json make logs-api | jq 'select(.level=="ERROR")'
```
