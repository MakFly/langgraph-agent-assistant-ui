"""Métriques de run, par les callbacks LangChain.

Pourquoi passer par un callback plutôt qu'instrumenter `graph.py` : la latence, les
tokens et la durée des outils sont des préoccupations d'observation, pas de logique
d'agent. Le mécanisme existe dans le framework, il est propagé automatiquement aux
sous-appels, et il laisse `build_graph()` lisible. C'est aussi la seule façon de voir
le **premier token** (`on_llm_new_token`), impossible à mesurer depuis le nœud.

Ce qui sort, sur le canal `agent.metrics` (INFO) :

    tour LLM terminé — duree_ms=812 premier_token_ms=143 tokens_entree=1204 …
    outil terminé — outil=weather_forecast duree_ms=317

Les tokens ne sont journalisés que si le provider les renvoie (`usage_metadata`) :
en streaming, tous ne le font pas.
"""

from __future__ import annotations

import logging
import time
from typing import Any
from uuid import UUID

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.messages import BaseMessage
from langchain_core.outputs import LLMResult

logger = logging.getLogger("agent.metrics")


def _ms(depuis: float) -> int:
    return round((time.perf_counter() - depuis) * 1000)


class RunMetricsHandler(AsyncCallbackHandler):
    """Un handler par run : les chronos sont indexés par `run_id`.

    LangGraph exécute des étapes en parallèle (plusieurs outils dans le même tour),
    donc un unique `t0` d'instance serait faux dès le premier appel d'outil concurrent.
    """

    def __init__(self) -> None:
        self._llm: dict[UUID, float] = {}
        self._premier_token: dict[UUID, int] = {}
        self._outils: dict[UUID, tuple[str, float]] = {}

    async def on_chat_model_start(
        self, serialized: dict[str, Any], messages: list[list[BaseMessage]], **kwargs: Any
    ) -> None:
        run_id = kwargs.get("run_id")
        if run_id is not None:
            self._llm[run_id] = time.perf_counter()

    async def on_llm_new_token(self, token: str, **kwargs: Any) -> None:
        run_id = kwargs.get("run_id")
        debut = self._llm.get(run_id) if run_id is not None else None
        # Le temps jusqu'au premier token, c'est la latence *perçue* : le reste défile.
        if debut is not None and run_id not in self._premier_token:
            self._premier_token[run_id] = _ms(debut)

    async def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        run_id = kwargs.get("run_id")
        debut = self._llm.pop(run_id, None) if run_id is not None else None
        if debut is None:
            return

        contexte: dict[str, Any] = {"duree_ms": _ms(debut)}
        premier = self._premier_token.pop(run_id, None)
        if premier is not None:
            contexte["premier_token_ms"] = premier
        contexte.update(_usage(response))

        logger.info("tour LLM terminé", extra=contexte)

    async def on_llm_error(self, error: BaseException, **kwargs: Any) -> None:
        run_id = kwargs.get("run_id")
        debut = self._llm.pop(run_id, None) if run_id is not None else None
        self._premier_token.pop(run_id, None)
        # `warning` et non `exception` : la trace complète est journalisée une fois pour
        # de bon par `agent.stream` si l'erreur remonte. Ici on veut juste le chrono.
        logger.warning(
            "tour LLM en échec : %s",
            error,
            extra={"duree_ms": _ms(debut) if debut else None},
        )

    async def on_tool_start(
        self, serialized: dict[str, Any], input_str: str, **kwargs: Any
    ) -> None:
        run_id = kwargs.get("run_id")
        if run_id is not None:
            nom = (serialized or {}).get("name") or kwargs.get("name") or "inconnu"
            self._outils[run_id] = (nom, time.perf_counter())

    async def on_tool_end(self, output: Any, **kwargs: Any) -> None:
        self._fin_outil(kwargs.get("run_id"), "outil terminé")

    async def on_tool_error(self, error: BaseException, **kwargs: Any) -> None:
        self._fin_outil(kwargs.get("run_id"), "outil en échec")

    def _fin_outil(self, run_id: UUID | None, message: str) -> None:
        entree = self._outils.pop(run_id, None) if run_id is not None else None
        if entree is None:
            return
        nom, debut = entree
        logger.info(message, extra={"outil": nom, "duree_ms": _ms(debut)})


def _usage(response: LLMResult) -> dict[str, int]:
    """Tokens consommés, si le provider les a renvoyés.

    Deux emplacements selon le provider et le mode : `usage_metadata` sur le message
    (normalisé par langchain) ou `llm_output["token_usage"]` (brut). On tente le
    normalisé, on ne devine rien s'il est absent.
    """
    for generations in response.generations:
        for generation in generations:
            usage = getattr(getattr(generation, "message", None), "usage_metadata", None)
            if usage:
                return {
                    "tokens_entree": usage.get("input_tokens", 0),
                    "tokens_sortie": usage.get("output_tokens", 0),
                    "tokens_total": usage.get("total_tokens", 0),
                }
    return {}
