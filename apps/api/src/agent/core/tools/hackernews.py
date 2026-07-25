from __future__ import annotations

from typing import Literal

from langchain_core.tools import tool

from agent.infra.http import fetch_json, tool_json


@tool(parse_docstring=True)
async def hacker_news_search(
    query: str, sort_by: Literal["relevance", "date"] = "relevance"
) -> str:
    """Cherche des articles sur Hacker News.

    À utiliser pour l'actualité tech, les sorties de librairies ou d'outils,
    les opinions de la communauté dev.

    Args:
        query: Mots-clés de recherche.
        sort_by: Trier par pertinence ou par date de publication.
    """

    async def run() -> dict:
        endpoint = "search_by_date" if sort_by == "date" else "search"
        data = await fetch_json(
            f"https://hn.algolia.com/api/v1/{endpoint}",
            {"query": query, "tags": "story", "hitsPerPage": 5},
        )
        return {
            "stories": [
                {
                    "title": hit.get("title"),
                    "url": hit.get("url")
                    or f"https://news.ycombinator.com/item?id={hit['objectID']}",
                    "points": hit.get("points"),
                    "comments": hit.get("num_comments"),
                    "author": hit.get("author"),
                    # ISO volontairement : le modèle reformate pour l'humain,
                    # la donnée reste exploitable par une machine.
                    "publishedAt": hit.get("created_at"),
                }
                for hit in data.get("hits", [])
            ]
        }

    return await tool_json(run)
