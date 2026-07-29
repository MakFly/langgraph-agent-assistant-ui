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
-- Comptes locaux. `role` gouverne la CONFIGURATION (qui peut réécrire le prompt
-- système, changer de modèle, ingérer du corpus) ; `groups` gouverne l'ACCÈS AUX
-- DONNÉES (quels documents le RAG a le droit de retourner). Les deux sont
-- volontairement séparés : un administrateur n'a aucune raison de lire les
-- documents RH, et confondre les deux axes est la façon la plus courante de se
-- retrouver avec un « super-utilisateur » qui voit tout.
CREATE TABLE IF NOT EXISTS users (
    id            TEXT PRIMARY KEY,
    email         TEXT        NOT NULL UNIQUE,
    password_hash TEXT        NOT NULL,
    display_name  TEXT,
    role          TEXT        NOT NULL DEFAULT 'member',
    groups        TEXT[]      NOT NULL DEFAULT '{}',
    disabled      BOOLEAN     NOT NULL DEFAULT false,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

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

-- Sessions de rafraîchissement : la couche RÉVOCABLE au-dessus du JWT d'accès.
-- Le jeton d'accès reste auto-porteur et se vérifie SANS SQL — seule cette table
-- (donc uniquement login / refresh / logout) touche la base. C'est ce qui préserve
-- la propriété du POC : le chat survit à une panne Postgres jusqu'à l'expiration du
-- jeton d'accès (court, 15 min par défaut).
--
-- On stocke le HACHÉ (sha256) du jeton, jamais le jeton : une fuite de la base ne
-- livre aucune session utilisable. `previous_hash` porte le jeton juste pivoté —
-- le présenter à nouveau ne peut venir que d'un rejeu (vol), et déclenche alors la
-- révocation de TOUTES les sessions du compte.
CREATE TABLE IF NOT EXISTS sessions (
    id            TEXT PRIMARY KEY,
    user_id       TEXT        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash    TEXT        NOT NULL,
    previous_hash TEXT,
    expires_at    TIMESTAMPTZ NOT NULL,
    revoked_at    TIMESTAMPTZ,
    user_agent    TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    rotated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Sources documentaires administrées depuis l'outil. Les fichiers eux-mêmes
-- vivent dans un volume persistant ; la base conserve le contrat de traitement,
-- notamment le provider et le modèle OCR choisis pour CETTE source.
CREATE TABLE IF NOT EXISTS ingestion_sources (
    id          TEXT PRIMARY KEY,
    name        TEXT        NOT NULL,
    kind        TEXT        NOT NULL DEFAULT 'upload',
    groups      TEXT[]      NOT NULL DEFAULT '{public}',
    enabled     BOOLEAN     NOT NULL DEFAULT true,
    ocr         JSONB       NOT NULL DEFAULT '{}'::jsonb,
    options     JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Journal durable : un redémarrage ne transforme pas un traitement en opération
-- fantôme. Les exécutions interrompues sont remises en file au démarrage.
CREATE TABLE IF NOT EXISTS ingestion_runs (
    id          TEXT PRIMARY KEY,
    source_id   TEXT        NOT NULL REFERENCES ingestion_sources(id) ON DELETE CASCADE,
    mode        TEXT        NOT NULL DEFAULT 'sync',
    status      TEXT        NOT NULL DEFAULT 'queued',
    report      JSONB,
    error       TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at  TIMESTAMPTZ,
    finished_at TIMESTAMPTZ
);

-- Propriétaire d'une conversation. En ALTER plutôt que dans le CREATE ci-dessus :
-- la table existe déjà sur les installations en cours, et le CREATE TABLE IF NOT
-- EXISTS ne l'aurait donc jamais modifiée.
--
-- Les lignes antérieures à l'authentification gardent `owner_id` à NULL et
-- deviennent invisibles — c'est voulu : une conversation sans propriétaire
-- identifiable ne peut être rattachée à personne sans deviner.
ALTER TABLE threads
    ADD COLUMN IF NOT EXISTS owner_id TEXT REFERENCES users(id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS threads_scope_updated_idx
    ON threads (scope, updated_at DESC);
CREATE INDEX IF NOT EXISTS threads_owner_updated_idx
    ON threads (owner_id, scope, updated_at DESC);
CREATE INDEX IF NOT EXISTS messages_thread_created_idx
    ON messages (thread_id, created_at);
CREATE INDEX IF NOT EXISTS mcp_servers_created_idx
    ON mcp_servers (created_at);

-- Un jeton est unique : l'index unique fait aussi office de garde-fou. La lecture
-- par `previous_hash` (détection de rejeu) et le balayage par compte (révocation
-- globale, élagage) sont les deux seuls autres accès.
CREATE UNIQUE INDEX IF NOT EXISTS sessions_token_idx ON sessions (token_hash);
CREATE INDEX IF NOT EXISTS sessions_previous_idx ON sessions (previous_hash);
CREATE INDEX IF NOT EXISTS sessions_user_idx ON sessions (user_id);
CREATE INDEX IF NOT EXISTS ingestion_sources_updated_idx
    ON ingestion_sources (updated_at DESC);
CREATE INDEX IF NOT EXISTS ingestion_runs_source_created_idx
    ON ingestion_runs (source_id, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS ingestion_runs_one_active_idx
    ON ingestion_runs (source_id) WHERE status IN ('queued', 'running');
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
