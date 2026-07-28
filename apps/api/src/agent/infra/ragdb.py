"""Base du RAG : pgvector, séparée de celle des conversations.

Pourquoi une seconde base plutôt qu'un schéma de plus dans la première :

- `infra-postgres`, la base mutualisée de la machine de dev, ne propose pas
  l'extension `vector` — et la changer imposerait un redémarrage aux autres
  projets qui s'en servent ;
- l'index se jette et se reconstruit (`make rag-reset`) sans jamais mettre en
  danger un historique de conversation. Deux durées de vie différentes, deux
  bases.

**La dimension des vecteurs est figée dans le schéma.** `vector(1536)` n'est pas
un détail de configuration : changer de modèle d'embedding pour un modèle d'une
autre dimension impose de tout réindexer. Le module refuse donc de démarrer sur
une table dont la dimension ne correspond pas à `EMBEDDING_DIM`, plutôt que de
laisser cohabiter deux espaces vectoriels incompatibles — ce qui se manifesterait
par des résultats subtilement absurdes, le pire mode de panne possible.
"""

from __future__ import annotations

import logging
import os

import asyncpg

logger = logging.getLogger("agent.ragdb")

# Plafond de pgvector pour un index HNSW sur le type `vector`. Au-delà il faut
# `halfvec`, donc une autre colonne et une autre requête : autant s'arrêter net
# avec un message clair.
MAX_HNSW_DIM = 2000

_pool: asyncpg.Pool | None = None


def database_url() -> str:
    return os.getenv("RAG_DATABASE_URL", "postgresql://rag:rag@ragdb:5432/rag")


def embedding_dim() -> int:
    try:
        dim = int(os.getenv("EMBEDDING_DIM", "1536"))
    except ValueError:
        logger.warning("EMBEDDING_DIM illisible, repli sur 1536")
        return 1536
    if dim < 1:
        raise RuntimeError("EMBEDDING_DIM doit être un entier positif")
    return dim


def schema(dim: int) -> str:
    """DDL, paramétré par la dimension des vecteurs.

    `tsv` est une colonne générée : le vecteur lexical ne peut donc pas diverger
    du texte, contrairement à un trigger qu'on oublie de poser sur les mises à
    jour. La configuration `french` fixe la racinisation et les mots vides ; un
    corpus multilingue demanderait une colonne par langue.
    """
    return f"""
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS rag_documents (
    id           TEXT PRIMARY KEY,
    -- Racine de corpus d'où vient le document. Elle borne la synchronisation :
    -- une ingestion ne peut supprimer que des documents de SA racine. Sans ce
    -- champ, indexer un dossier quelconque effaçait tout le reste de l'index.
    root         TEXT        NOT NULL DEFAULT '',
    source       TEXT        NOT NULL UNIQUE,
    title        TEXT,
    sha256       TEXT        NOT NULL,
    acl          TEXT[]      NOT NULL DEFAULT '{{}}',
    embed_model  TEXT        NOT NULL,
    embed_dim    INTEGER     NOT NULL,
    chunk_count  INTEGER     NOT NULL DEFAULT 0,
    indexed_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS rag_chunks (
    id           BIGSERIAL PRIMARY KEY,
    document_id  TEXT          NOT NULL REFERENCES rag_documents(id) ON DELETE CASCADE,
    ord          INTEGER       NOT NULL,
    text         TEXT          NOT NULL,
    acl          TEXT[]        NOT NULL DEFAULT '{{}}',
    embedding    vector({dim}) NOT NULL,
    tsv          tsvector GENERATED ALWAYS AS (to_tsvector('french', text)) STORED,
    UNIQUE (document_id, ord)
);

-- Recherche dense. `vector_cosine_ops` doit correspondre à l'opérateur employé
-- dans la requête (`<=>`) : avec un autre jeu d'opérateurs, l'index existe mais
-- n'est jamais utilisé, et la lenteur est le seul symptôme.
CREATE INDEX IF NOT EXISTS rag_chunks_embedding_idx
    ON rag_chunks USING hnsw (embedding vector_cosine_ops);

-- Colonne ajoutée après coup : le CREATE TABLE ci-dessus ne s'applique pas à une
-- table déjà existante. `source` reste unique globalement, donc un même chemin
-- relatif appartient à une seule racine — largement suffisant ici, et ça évite
-- une chirurgie de contrainte sur les installations en place.
ALTER TABLE rag_documents ADD COLUMN IF NOT EXISTS root TEXT NOT NULL DEFAULT '';

CREATE INDEX IF NOT EXISTS rag_documents_root_idx ON rag_documents (root);

-- Recherche lexicale et filtrage des permissions.
CREATE INDEX IF NOT EXISTS rag_chunks_tsv_idx ON rag_chunks USING gin (tsv);
CREATE INDEX IF NOT EXISTS rag_chunks_acl_idx ON rag_chunks USING gin (acl);
CREATE INDEX IF NOT EXISTS rag_documents_acl_idx ON rag_documents USING gin (acl);
"""


async def _existing_dimension(connection: asyncpg.Connection) -> int | None:
    """Dimension déclarée sur `rag_chunks.embedding`, ou None si la table est neuve.

    pgvector range la dimension dans `atttypmod`, comme la longueur d'un
    `varchar`. C'est la seule façon de la relire depuis le catalogue.
    """
    return await connection.fetchval(
        """
        SELECT a.atttypmod
        FROM pg_attribute a
        WHERE a.attrelid = to_regclass('rag_chunks')
          AND a.attname = 'embedding'
          AND a.attnum > 0
        """
    )


async def connect() -> asyncpg.Pool:
    """Applique le schéma puis ouvre le pool.

    En deux temps volontairement : `CREATE EXTENSION` doit avoir eu lieu avant
    que quoi que ce soit ne manipule le type `vector`.
    """
    global _pool
    if _pool is not None:
        return _pool

    dim = embedding_dim()
    if dim > MAX_HNSW_DIM:
        raise RuntimeError(
            f"EMBEDDING_DIM={dim} dépasse la limite d'un index HNSW sur `vector` "
            f"({MAX_HNSW_DIM}). Réduisez la dimension du modèle d'embedding."
        )

    connection = await asyncpg.connect(database_url())
    try:
        existing = await _existing_dimension(connection)
        if existing is not None and existing > 0 and existing != dim:
            raise RuntimeError(
                f"L'index contient des vecteurs de dimension {existing}, mais "
                f"EMBEDDING_DIM vaut {dim}. Mélanger deux espaces vectoriels donne "
                f"des résultats faux sans erreur visible. Réindexez : `make rag-reset` "
                f"puis `make ingest`."
            )
        await connection.execute(schema(dim))
    finally:
        await connection.close()

    _pool = await asyncpg.create_pool(database_url(), min_size=1, max_size=5)
    logger.info("base du RAG prête", extra={"dimension": dim})
    return _pool


async def disconnect() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Pool RAG non initialisé : la base vectorielle n'est pas connectée")
    return _pool


async def is_available() -> bool:
    """Le chat doit fonctionner sans RAG — seule la recherche documentaire s'éteint."""
    if _pool is None:
        return False
    try:
        async with _pool.acquire() as connection:
            await connection.execute("SELECT 1")
        return True
    except Exception:  # noqa: BLE001
        logger.debug("sonde RAG en échec", exc_info=True)
        return False


async def reset() -> None:
    """Vide l'index. Les documents partent, les conversations restent."""
    async with pool().acquire() as connection:
        await connection.execute("TRUNCATE rag_documents CASCADE")
    logger.warning("index RAG vidé")


def to_vector_literal(values: list[float]) -> str:
    """Représentation textuelle attendue par pgvector : `[0.1,0.2,...]`.

    Le littéral est envoyé en paramètre puis casté (`$n::vector`) plutôt que
    d'enregistrer un codec binaire : un paramètre reste un paramètre, donc aucune
    concaténation dans le SQL, et il n'y a pas de dépendance à l'implémentation
    interne du type.
    """
    return "[" + ",".join(f"{value:.7g}" for value in values) + "]"
