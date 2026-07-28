"""Recherche dans le corpus interne — le seul outil qui touche à des données privées.

Deux points le distinguent des quatre autres outils.

**Il reçoit une identité.** Le paramètre `config` est annoté `RunnableConfig` :
LangChain l'injecte à l'exécution et le retire du schéma présenté au modèle. Le
LLM ne peut donc ni le voir, ni le remplir, ni le falsifier — il ne connaît que
`query`. Les groupes viennent de `agent.protocol.stream`, qui les tient du jeton
de session vérifié par `agent.api.auth`.

**Il échoue fermé.** Sans identité dans la configuration, l'outil refuse de
chercher au lieu de chercher sans filtre. La différence entre ces deux
comportements, c'est un incident de confidentialité.
"""

from __future__ import annotations

import json
import logging

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from agent.core.rag import retrieve
from agent.infra import ragdb

logger = logging.getLogger("agent.tools.rag")

# Longueur au-delà de laquelle un fragment est tronqué dans la réponse à l'outil.
# Cinq fragments entiers peuvent représenter plusieurs milliers de tokens, à
# comparer aux 24 000 de la fenêtre de contexte : sans coupe, une seule recherche
# consomme l'historique de la conversation.
MAX_EXCERPT_CHARS = 1200


def _excerpt(text: str) -> str:
    if len(text) <= MAX_EXCERPT_CHARS:
        return text
    return text[:MAX_EXCERPT_CHARS].rsplit(" ", 1)[0] + " […]"


@tool(parse_docstring=True)
async def document_search(query: str, config: RunnableConfig) -> str:
    """Recherche dans les documents internes de l'organisation.

    À utiliser dès qu'une question porte sur des procédures, des règles
    internes, des budgets ou des politiques propres à l'organisation — tout ce
    qui ne se trouve pas dans une encyclopédie publique.

    Chaque extrait rendu porte une référence `source#fragment` : cite-la.

    Args:
        query: La question ou les termes à rechercher, en langage naturel.
    """
    identity = (config or {}).get("configurable") or {}

    # `None` (clé absente) et `[]` (aucun groupe) sont deux choses différentes :
    # la première est un bug de câblage, la seconde un utilisateur sans droits.
    # Les deux doivent aboutir à zéro résultat, mais pas au même diagnostic.
    if "user_groups" not in identity:
        logger.error("outil documentaire appelé sans identité dans la configuration")
        return json.dumps(
            {"error": "Recherche documentaire indisponible : identité de session absente."},
            ensure_ascii=False,
        )

    groups = identity.get("user_groups") or []

    if not await ragdb.is_available():
        return json.dumps(
            {"error": "Index documentaire injoignable — les autres outils restent utilisables."},
            ensure_ascii=False,
        )

    passages = await retrieve.search(query, groups)

    logger.info(
        "recherche documentaire",
        extra={"groupes": len(groups), "resultats": len(passages)},
    )

    if not passages:
        # Formulation importante : « rien d'accessible » et non « rien n'existe ».
        # Le modèle ne doit pas affirmer qu'un document est inexistant alors qu'il
        # est seulement hors des droits de l'utilisateur.
        return json.dumps(
            {
                "results": [],
                "note": "Aucun document accessible ne correspond à cette recherche.",
            },
            ensure_ascii=False,
        )

    return json.dumps(
        {
            "results": [
                {
                    "citation": passage.citation,
                    "title": passage.title,
                    "source": passage.source,
                    "excerpt": _excerpt(passage.text),
                }
                for passage in passages
            ]
        },
        ensure_ascii=False,
    )
