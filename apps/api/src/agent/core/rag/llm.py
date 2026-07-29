"""Le petit modèle utilisé PAR la recherche, distinct de celui qui répond.

Reclasser des passages et reformuler une question sont des tâches mécaniques et
courtes. Les confier au modèle de conversation revient à payer un modèle de
raisonnement pour trier une liste — et surtout à ajouter sa latence à chaque
recherche, alors que l'utilisateur attend déjà.

`RAG_LLM_PROVIDER` et `RAG_LLM_MODEL` permettent donc de viser un modèle rapide
et bon marché pour ces tâches, indépendamment de celui du chat. Non renseignés,
on retombe sur la configuration du chat : le réglage est une optimisation, pas
une obligation de câblage.

**Ces appels échouent ouvert.** Un reclasseur en panne doit rendre la recherche
non reclassée, jamais une recherche vide. La règle est inverse de celle des ACL,
et pour une raison simple : dégrader la pertinence est un désagrément, laisser
fuiter un document est un incident.
"""

from __future__ import annotations

import logging
import os

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from agent.core.model import create_model

logger = logging.getLogger("agent.rag.llm")

# Les appels auxiliaires ont lieu à l'intérieur du nœud d'outil LangGraph. Sans
# tag ni rupture explicite des callbacks, `stream_mode="messages"` les confond
# avec le modèle principal et envoie hypothèses HyDE et JSON de reranking à l'UI.
INTERNAL_STREAM_TAG = "rag-internal"

# Une seule instance par (provider, modèle, température) : reconstruire un client
# HTTP à chaque recherche rouvrirait un pool de connexions par requête. HyDE
# échantillonne à 0,7, tandis que reformulation et reclassement restent à zéro.
_cache: dict[tuple[str | None, str | None, float], BaseChatModel] = {}


def _settings() -> tuple[str | None, str | None]:
    provider = os.getenv("RAG_LLM_PROVIDER", "").strip().lower() or None
    model = os.getenv("RAG_LLM_MODEL", "").strip() or None
    return provider, model


def model(*, temperature: float = 0.0) -> BaseChatModel:
    """Modèle des tâches auxiliaires de la recherche.

    Reformulation et reclassement appellent cette fonction à zéro : un
    reclassement qui varie rendrait la mesure illisible. HyDE passe explicitement
    sa température d'échantillonnage, car plusieurs documents hypothétiques
    identiques n'apporteraient aucune estimation de la distribution recherchée.
    """
    provider, name = _settings()
    temperature = min(max(float(temperature), 0.0), 2.0)
    key = provider, name, temperature
    if key not in _cache:
        _cache[key] = create_model(
            provider=provider,
            model=name,
            temperature=temperature,
        )
    return _cache[key]


def describe() -> str:
    provider, name = _settings()
    from agent.core import model as fabrique

    resolu = provider or fabrique.default_provider()
    return f"{name or fabrique.default_model(resolu)} ({resolu})"


async def ask(
    system: str,
    user: str,
    *,
    timeout: float = 20.0,
    temperature: float = 0.0,
) -> str | None:
    """Un aller-retour, ou `None` si quoi que ce soit échoue.

    `None` et non une exception : les deux appelants (reclassement, reformulation)
    ont un repli parfaitement valable — ne rien reclasser, ne rien reformuler — et
    propager l'erreur les obligerait tous les deux à écrire le même `try`.
    """
    import asyncio

    try:
        response = await asyncio.wait_for(
            model(temperature=temperature).ainvoke(
                [SystemMessage(system), HumanMessage(user)],
                config={
                    # Coupe l'héritage des callbacks du run de chat.
                    "callbacks": [],
                    # Défense en profondeur : le protocole ignore aussi tout
                    # message portant ce tag si un provider le propage malgré tout.
                    "tags": [INTERNAL_STREAM_TAG],
                },
            ),
            timeout=timeout,
        )
    except TimeoutError:
        logger.warning("modèle auxiliaire : délai dépassé (%.0f s)", timeout)
        return None
    except Exception as error:  # noqa: BLE001 - chaque SDK a sa propre hiérarchie
        logger.warning("modèle auxiliaire indisponible : %s", error)
        return None

    content = response.content
    if isinstance(content, list):
        # Certains providers rendent une liste de blocs typés plutôt qu'une chaîne.
        content = "".join(
            bloc.get("text", "") if isinstance(bloc, dict) else str(bloc) for bloc in content
        )
    return str(content).strip() or None
