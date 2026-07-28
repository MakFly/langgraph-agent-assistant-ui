"""Assemblage de l'application : rien d'autre.

Ce fichier ne contient aucune logique — il crée l'app, câble le cycle de vie et monte
les routers. Les endpoints vivent dans `agent.api`, l'agent dans `agent.core`, le
protocole dans `agent.protocol`, la technique dans `agent.infra`. Si quelque chose de
métier réapparaît ici, c'est qu'il manque une place ailleurs.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from agent.api.auth import router as auth_router
from agent.api.chat import router as chat_router
from agent.api.settings import router as settings_router
from agent.api.threads import router as threads_router
from agent.core import settings, users
from agent.infra import db, ragdb
from agent.infra.log import setup_logging

logger = logging.getLogger("agent.main")


@asynccontextmanager
async def lifespan(_: FastAPI):
    """La base sert à l'historisation et à la configuration : si elle est absente,
    le chat doit continuer de fonctionner, sans historique et avec les réglages
    par défaut."""
    # En premier : tout ce qui suit doit pouvoir journaliser.
    setup_logging()

    try:
        await db.connect()
    except Exception:
        logger.warning("historisation désactivée : base injoignable", exc_info=True)
    else:
        # Sans base, aucune connexion n'est possible — mais un jeton déjà émis
        # reste valable, puisqu'il se vérifie sans SQL (agent.infra.auth).
        try:
            await users.bootstrap_admin()
        except Exception:
            logger.warning("amorçage du compte administrateur en échec", exc_info=True)

    # Index documentaire : facultatif comme le reste. Sans lui, l'outil de
    # recherche documentaire se déclare indisponible et l'agent continue avec ses
    # autres outils, au lieu de refuser de démarrer.
    try:
        await ragdb.connect()
    except Exception:
        logger.warning("recherche documentaire désactivée : index injoignable", exc_info=True)

    # Un seul chargement de la config au démarrage : ensuite le graphe lit le
    # snapshot en mémoire, et chaque mutation le republie (agent.core.settings).
    config = await settings.refresh()
    logger.info(
        "agent prêt",
        extra={
            "provider": config.model.provider,
            "modele": config.model.model or "défaut du provider",
            "effort": config.model.reasoning_effort,
            "outils": len(settings.enabled_tools()),
        },
    )

    yield
    await db.disconnect()
    await ragdb.disconnect()


app = FastAPI(title="LangGraph POC", version="0.1.0", lifespan=lifespan)

# Le front dev passe par le proxy Vite (same-origin), donc CORS ne sert que si
# vous servez le front depuis une autre origine.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip()
        for origin in os.getenv("CORS_ORIGIN", "http://localhost:4311").split(",")
    ],
    # Le jeton voyage en cookie : sans `allow_credentials`, un front servi depuis
    # une autre origine serait authentifié en local et anonyme en CORS.
    # La liste d'origines est explicite (jamais `*`), ce que cette option impose.
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(chat_router)
app.include_router(threads_router)
app.include_router(settings_router)
