# Le graphe — ce que LangGraph fait vraiment ici

Le graphe est une boucle ReAct à un nœud de modèle et un `ToolNode`, écrite à la main
plutôt qu'avec `create_react_agent` : elle doit être **reconstruite selon la
configuration** et accepter un modèle injectable pour les tests. Le prebuilt aurait
masqué les deux.

```
START ──▶ agent ──tool_calls?──▶ tools ──▶ agent ──▶ END
           │  ▲                   │
           │  └───────────────────┘
           │
           ├── fenêtre de contexte (trim_messages)      ← coût / context_length
           ├── retry_policy (429, 5xx, timeouts)        ← free tiers
           ├── outils projet + outils MCP (cache)       ← agent/core/mcp.py
           └── callbacks de métriques                   ← agent/core/callbacks.py
```

## Fenêtre de contexte

Le client est la source de vérité de l'historique et le renvoie **en entier** à chaque
tour. Sans plafond, le coût d'une conversation croît en O(N²) et elle finit sur un
`context_length_exceeded`.

`_windowed()` (`core/graph.py`) applique `trim_messages` avec un plafond de **24 000
tokens**, choisi pour tenir dans le plus petit modèle du catalogue (`qwen3:8b`, 32k) en
laissant la place à la réponse et aux résultats d'outils.

La valeur vit dans `core/settings.MAX_CONTEXT_TOKENS`, pas dans le graphe : c'est d'abord
une limite de configuration (le graphe la *lit*), et la placer dans `graph.py` créerait un
cycle d'import. Elle est exposée au front (`agent.context_window_tokens`) pour que le
composer affiche le contexte restant **avec exactement le même plafond** — pas une
constante dupliquée côté client. Ça reste une constante et non un réglage : un curseur de
plus n'aiderait personne tant qu'aucun usage réel ne montre que 24k est le mauvais chiffre.

Deux détails non cosmétiques :

- `start_on="human"` — une fenêtre qui commencerait par un `ToolMessage` orphelin (son
  `AIMessage` porteur des `tool_call_id` étant tombé hors fenêtre) est **refusée par les
  providers**. Un test le verrouille.
- `include_system=False` — le prompt système n'est pas dans l'état, il est ajouté à
  l'appel : il n'est donc jamais rognable.

Le rognage est journalisé (`agent.core.graph`) avec le nombre de messages reçus/envoyés.

## Reprise sur erreur transitoire

`add_node("agent", agent, retry_policy=AGENT_RETRY)` : 3 tentatives, backoff
exponentiel avec jitter (0,5 s → 8 s). C'est LangGraph qui rejoue l'étape, `agent()` ne
sait pas qu'il est rejoué.

**Le prédicat compte plus que la politique.** `_is_transient()` ne rejoue que les statuts
408/409/425/429/500/502/503/504, avec un repli par nom de classe (`RateLimit`, `Timeout`,
`APIConnection`…) parce que chaque SDK a sa propre hiérarchie d'exceptions. Un **400 n'est
jamais rejoué** : la requête est invalide, la refaire trois fois ne fait que tripler la
latence et la facture — vécu sur ce projet avec `reasoning_effort` sur
`/v1/chat/completions`. Deux tests couvrent les deux branches.

## Erreurs d'outils

`ToolNode(tools, handle_tool_errors=tool_error_message)`. Avant, chaque outil enveloppait
lui-même ses exceptions (`safe_tool`) : chaque nouvel outil devait y penser, et une erreur
de **validation des arguments** produits par le LLM échappait de toute façon au wrapper.

Conséquence visible : un outil en échec produit un `ToolMessage` avec `status="error"`,
donc `stream.py` émet `tool-output-error` au lieu d'un succès contenant `{"error": ...}`.
L'UI distingue enfin un outil en panne d'un outil qui a répondu. Le modèle, lui, reçoit le
même message qu'avant et peut toujours se rattraper.

`agent.infra.http.tool_json()` ne fait plus que sérialiser (le JSON reste préférable à un
`str(dict)` Python, moins bien lu par les modèles).

## Outils MCP

Voir [settings.md](settings.md#serveurs-mcp) pour le cycle de vie. Côté graphe :
`tools = outils du projet activés + mcp.tools()`, ce dernier étant une **lecture de cache
sans I/O** — le chemin du chat reste synchrone.

Un outil MCP homonyme d'un outil du projet est **ignoré** (et journalisé) : il ferait
doublon dans `bind_tools`, ou masquerait silencieusement le nôtre.

## Métriques

`agent/core/callbacks.py` fournit un `RunMetricsHandler`, passé à `graph.astream(config=…)`.
Un callback plutôt qu'une instrumentation dans `graph.py` parce que la latence et les
tokens sont une préoccupation d'observation, pas de logique d'agent — et parce que c'est
la seule façon de mesurer le **temps jusqu'au premier token**, invisible depuis le nœud.

```
agent.metrics : tour LLM terminé — duree_ms=1723 premier_token_ms=606
                tokens_entree=708 tokens_sortie=36 tokens_total=744
agent.metrics : outil terminé — outil=get_current_time duree_ms=554
```

Les chronos sont indexés par `run_id` : LangGraph exécute plusieurs outils du même tour en
parallèle, un `t0` d'instance serait faux. Les tokens n'apparaissent que si le provider
les renvoie (`usage_metadata`).

## Ce qui n'est volontairement pas là

- **Pas de checkpointer.** Le client possède l'historique, ce qui fait marcher le
  protocole assistant-ui. Coût assumé et définitif : pas de reprise d'un run interrompu,
  pas d'`interrupt()` donc **pas de validation humaine avant un appel d'outil**, pas de
  time-travel. Le jour où l'un des trois devient nécessaire, il faut
  `langgraph-checkpoint-postgres` — pas avant.
- **Pas de sous-graphes, de `Send`/map-reduce, de superviseur multi-agents, de `store` de
  mémoire long terme, ni de RAG.** Rien de tout ça ne sert cette application. « Utiliser
  LangGraph à fond » n'est pas un objectif : un ReAct à un nœud qui marche vaut mieux
  qu'une architecture à cinq nœuds qui impressionne.
- **`recursion_limit` n'est pas configuré** : le plafond de boucle ReAct est porté par le
  compteur `loops` de l'état, réglable dans l'UI (1..20). Sémantique différente, garde-fou
  volontairement explicite.
