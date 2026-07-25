from __future__ import annotations

import asyncio
from typing import Literal
from urllib.parse import quote

from langchain_core.tools import tool

from agent.infra.http import fetch_json, tool_json


@tool(parse_docstring=True)
async def wikipedia_search(query: str, lang: Literal["fr", "en"] = "fr") -> str:
    """Recherche un sujet sur Wikipédia et renvoie le résumé des meilleurs articles.

    À utiliser pour des faits encyclopédiques : personnes, lieux, concepts,
    événements historiques.

    Args:
        query: Le sujet à rechercher, en langage naturel.
        lang: Édition linguistique de Wikipédia à interroger.
    """

    async def run() -> dict:
        search = await fetch_json(
            f"https://{lang}.wikipedia.org/w/rest.php/v1/search/page",
            {"q": query, "limit": 3},
        )
        pages = search.get("pages", [])
        if not pages:
            return {"results": [], "note": f'Aucun article pour "{query}"'}

        async def summary(page: dict) -> dict:
            data = await fetch_json(
                f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/"
                f"{quote(page['key'], safe='')}"
            )
            return {
                "title": data.get("title"),
                "extract": data.get("extract"),
                "url": data.get("content_urls", {}).get("desktop", {}).get("page"),
            }

        results = await asyncio.gather(*(summary(page) for page in pages[:2]))
        return {
            "results": list(results),
            "otherTitles": [page["title"] for page in pages[2:]],
        }

    return await tool_json(run)
