"""Socle HTTP partagé par tous les outils.

Deux choses comptent dans un runtime d'agent :
  - un timeout dur, sinon une API amont qui pend bloque tout le graphe ;
  - un vrai User-Agent, exigé par la politique d'API de Wikipédia.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

logger = logging.getLogger("agent.tools")

TIMEOUT = httpx.Timeout(8.0)
USER_AGENT = "langgraph-poc/0.1 (https://github.com/local/langgraph-poc)"

_HEADERS = {"accept": "application/json", "user-agent": USER_AGENT}


async def fetch_json(url: str, params: dict[str, Any] | None = None) -> Any:
    async with httpx.AsyncClient(timeout=TIMEOUT, headers=_HEADERS) as client:
        response = await client.get(url, params=params)
        if response.status_code >= 400:
            raise RuntimeError(f"HTTP {response.status_code} sur {response.url.host}")
        return response.json()


async def tool_json(fn: Callable[[], Awaitable[Any]]) -> str:
    """Résultat d'outil sérialisé en JSON.

    Les exceptions **remontent** volontairement : c'est `ToolNode(handle_tool_errors=…)`
    qui les transforme en message pour le LLM (cf. `agent.tools.tool_error_message`).
    Avant, chaque outil les avalait lui-même — donc chaque nouvel outil devait y penser,
    et une erreur de validation des arguments échappait de toute façon au wrapper.

    Le JSON reste ici plutôt que d'être laissé à LangChain : un `str(dict)` Python
    donnerait des quotes simples, moins bien lues par les modèles.
    """
    return json.dumps(await fn(), ensure_ascii=False)
