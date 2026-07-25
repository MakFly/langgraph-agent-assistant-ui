"""Persistance des conversations sur Postgres.

La base est `infra-postgres`, le service mutualisé de la stack `dev-infra` —
on n'en redéploie pas un pour ce projet.

Le schéma est volontairement minimal et **agnostique du contenu** : le client
assistant-ui encode lui-même chaque message (`format` + `content`), le serveur
ne fait que le stocker et le rendre. Il n'y a donc pas deux représentations de
la conversation à garder synchronisées.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import asyncpg

logger = logging.getLogger("agent.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS threads (
    id          TEXT PRIMARY KEY,
    scope       TEXT        NOT NULL DEFAULT 'default',
    title       TEXT,
    status      TEXT        NOT NULL DEFAULT 'regular',
    custom      JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS messages (
    id          TEXT        NOT NULL,
    thread_id   TEXT        NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
    parent_id   TEXT,
    format      TEXT        NOT NULL,
    content     JSONB       NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (thread_id, id)
);

-- Configuration globale de l'agent. Une ligne par domaine ('agent', 'model',
-- 'tools'), la valeur est un objet JSONB. Pas de colonne par réglage : ça évite
-- une migration à chaque nouveau champ, et il n'y a de toute façon pas d'auth,
-- donc pas de configuration par utilisateur.
CREATE TABLE IF NOT EXISTS settings (
    key         TEXT PRIMARY KEY,
    value       JSONB       NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Serveurs MCP : stockage + validation uniquement à ce stade, le graphe ne les
-- consomme pas encore (cf. docs/settings.md).
CREATE TABLE IF NOT EXISTS mcp_servers (
    id          TEXT PRIMARY KEY,
    name        TEXT        NOT NULL,
    transport   TEXT        NOT NULL,
    url         TEXT,
    command     TEXT,
    args        JSONB       NOT NULL DEFAULT '[]'::jsonb,
    env         JSONB       NOT NULL DEFAULT '{}'::jsonb,
    enabled     BOOLEAN     NOT NULL DEFAULT true,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS threads_scope_updated_idx
    ON threads (scope, updated_at DESC);
CREATE INDEX IF NOT EXISTS messages_thread_created_idx
    ON messages (thread_id, created_at);
CREATE INDEX IF NOT EXISTS mcp_servers_created_idx
    ON mcp_servers (created_at);
"""

_pool: asyncpg.Pool | None = None


def database_url() -> str:
    return os.getenv(
        "DATABASE_URL",
        "postgresql://test:test@infra-postgres:5432/langgraph_poc",
    )


async def connect() -> asyncpg.Pool:
    """Crée le pool et applique le schéma. Appelé au démarrage de l'app."""
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(database_url(), min_size=1, max_size=5)
        async with _pool.acquire() as connection:
            await connection.execute(SCHEMA)
    return _pool


async def disconnect() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Pool non initialisé : la base n'est pas connectée")
    return _pool


async def is_available() -> bool:
    """Le chat doit fonctionner même sans base — seule l'historisation s'éteint."""
    if _pool is None:
        return False
    try:
        async with _pool.acquire() as connection:
            await connection.execute("SELECT 1")
        return True
    except Exception:  # noqa: BLE001
        # En DEBUG seulement : cette sonde est appelée à chaque requête, un warning
        # noierait les logs dès que la base tombe.
        logger.debug("sonde de disponibilité en échec", exc_info=True)
        return False


def row_to_dict(row: asyncpg.Record | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None
