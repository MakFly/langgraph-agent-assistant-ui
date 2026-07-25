"""Serveurs MCP déclarés dans la configuration → outils réellement bindés au modèle.

Jusqu'ici les serveurs étaient **stockés et validés, mais jamais branchés** : le panneau
laissait en ajouter et il ne se passait rien. C'est le seul écart promesse/réalité
visible par l'utilisateur, d'où ce module.

La contrainte structurante : `graph.build_graph()` est **synchrone** et appelé dans le
chemin du chat, alors que découvrir les outils d'un serveur MCP demande des I/O (spawn de
process ou requête HTTP). On ne peut donc pas les charger à la construction du graphe.

    settings.refresh()  ── async, au démarrage et après chaque mutation
        │
        ├──▶ mcp.refresh(servers)  ── I/O, timeout, ne lève jamais
        │        └──▶ cache module : _tools + _status
        │
        └──▶ version()++ ──▶ get_graph() reconstruit ──▶ mcp.tools() (lecture sync)

Conséquences assumées :
  - un serveur injoignable **ne casse ni le démarrage ni le chat** : il est absent des
    outils et son état est exposé au front (`status`) au lieu d'être silencieux ;
  - les outils ne changent qu'à un `refresh()`, pas à chaud pendant un run — c'est ce
    qui garde le chemin du chat sans I/O.

Le cycle de vie des process `stdio` n'est pas géré ici : `langchain-mcp-adapters` ouvre
une session par appel (spawn, appel, fermeture). Coût en latence par appel, mais aucun
process orphelin à surveiller — le bon compromis pour ce POC.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient

from agent.core.tools import TOOLS

logger = logging.getLogger("agent.mcp")

# Spawn d'un process `uvx` + `list_tools` peut prendre plusieurs secondes au premier
# appel (téléchargement du paquet). Au-delà, on considère le serveur injoignable.
TIMEOUT = 20.0

# Nos transports (contrainte de la table `mcp_servers`) → ceux de l'adaptateur.
_TRANSPORTS = {"stdio": "stdio", "http": "streamable_http", "sse": "sse"}

# Cache lu **synchroniquement** par le graphe. Deux variables plutôt qu'un objet : le
# module est déjà le singleton.
_tools: list[BaseTool] = []
_status: dict[str, dict[str, Any]] = {}


def tools() -> list[BaseTool]:
    """Outils MCP disponibles, sans I/O. Vide si aucun serveur ne répond."""
    return list(_tools)


def status() -> dict[str, dict[str, Any]]:
    """État par serveur (clé = id), tel que l'expose `GET /api/settings`."""
    return {server_id: dict(state) for server_id, state in _status.items()}


def _cause(error: BaseException, profondeur: int = 0) -> str:
    """Message exploitable, extrait d'un éventuel `ExceptionGroup`.

    Le SDK MCP travaille en `TaskGroup` : une connexion refusée arrive emballée en
    « unhandled errors in a TaskGroup (1 sub-exception) », inutile à afficher dans le
    panneau. On descend chercher la vraie cause.
    """
    sous = getattr(error, "exceptions", None)
    if sous and profondeur < 5:
        return _cause(sous[0], profondeur + 1)
    return f"{type(error).__name__}: {error}" if str(error) else type(error).__name__


def _connection(server: dict[str, Any]) -> dict[str, Any] | None:
    """Traduit une ligne `mcp_servers` en connexion pour l'adaptateur."""
    transport = _TRANSPORTS.get(server["transport"])
    if transport is None:  # pragma: no cover - le Literal de l'API l'empêche
        return None

    if transport == "stdio":
        if not server.get("command"):
            return None
        return {
            "transport": "stdio",
            "command": server["command"],
            "args": server.get("args") or [],
            "env": server.get("env") or None,
        }

    if not server.get("url"):
        return None
    return {"transport": transport, "url": server["url"]}


async def refresh(servers: list[dict[str, Any]]) -> None:
    """Redécouvre les outils des serveurs activés. **Ne lève jamais.**

    Appelé depuis `settings.refresh()`, donc dans le démarrage et dans chaque mutation
    de configuration : une exception ici empêcherait le service de démarrer ou ferait
    échouer un PATCH sans rapport.
    """
    global _tools, _status

    actifs = {
        server["id"]: connection
        for server in servers
        if server.get("enabled") and (connection := _connection(server)) is not None
    }
    noms = {server["id"]: server.get("name") or server["id"] for server in servers}

    if not actifs:
        _tools, _status = [], {}
        return

    reserves = {tool.name for tool in TOOLS}
    collectes: list[BaseTool] = []
    etats: dict[str, dict[str, Any]] = {}

    client = MultiServerMCPClient(actifs)  # type: ignore[arg-type]

    for server_id in actifs:
        try:
            decouverts = await asyncio.wait_for(
                client.get_tools(server_name=server_id), timeout=TIMEOUT
            )
        except Exception as error:  # noqa: BLE001 - un serveur tiers casse comme il veut
            cause = _cause(error)
            # Tronqué : certains serveurs recrachent une trace entière.
            etats[server_id] = {"state": "error", "tools": 0, "error": cause[:300]}
            logger.warning(
                "serveur MCP injoignable : %s",
                cause,
                extra={"serveur": noms[server_id], "transport": actifs[server_id]["transport"]},
            )
            continue

        gardes: list[BaseTool] = []
        for tool in decouverts:
            # Un serveur qui exposerait `calculator` masquerait le nôtre — ou ferait
            # échouer `bind_tools` sur un nom dupliqué. On garde le nôtre et on le dit.
            if tool.name in reserves:
                logger.warning(
                    "outil MCP ignoré : nom déjà pris",
                    extra={"serveur": noms[server_id], "outil": tool.name},
                )
                continue
            reserves.add(tool.name)
            gardes.append(tool)

        collectes += gardes
        etats[server_id] = {"state": "ready", "tools": len(gardes), "error": None}
        logger.info(
            "serveur MCP prêt",
            extra={"serveur": noms[server_id], "outils": len(gardes)},
        )

    _tools, _status = collectes, etats
