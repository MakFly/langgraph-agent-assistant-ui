"""Les quatre outils publics sont gratuits et sans clé : aucun compte, aucun quota.

`document_search` est d'une autre nature : il lit le corpus interne, donc des
données privées, et il est le seul à recevoir l'identité de l'appelant pour
filtrer ce qu'il a le droit de rendre (cf. `agent.core.tools.rag`).
"""

import json
import logging

from agent.core.tools.calculator import calculator
from agent.core.tools.hackernews import hacker_news_search
from agent.core.tools.rag import document_search
from agent.core.tools.weather import weather_forecast
from agent.core.tools.wikipedia import wikipedia_search

logger = logging.getLogger("agent.tools")

TOOLS = [
    wikipedia_search,
    hacker_news_search,
    weather_forecast,
    calculator,
    document_search,
]


def tool_error_message(error: Exception) -> str:
    """Ce que le LLM lit quand un outil échoue — passé à `ToolNode`.

    Deux exigences en même temps : l'erreur doit atteindre le modèle pour qu'il se
    rattrape ou l'explique, **et** rester visible dans les logs. Sinon une API amont en
    panne ressemble à un modèle qui hallucine.

    Même forme qu'avant (`{"error": "..."}`) : le comportement du modèle ne change pas.
    """
    logger.warning("outil en échec : %s", error, exc_info=error)
    return json.dumps({"error": str(error)}, ensure_ascii=False)


__all__ = [
    "TOOLS",
    "calculator",
    "document_search",
    "hacker_news_search",
    "tool_error_message",
    "weather_forecast",
    "wikipedia_search",
]
