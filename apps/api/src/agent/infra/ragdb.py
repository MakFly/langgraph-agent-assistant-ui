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

-- Métadonnées métier venues du front-matter (type, client, produit, référence…).
-- En JSONB et non en colonnes : le corpus d'un courtier n'a pas les mêmes champs
-- que celui d'un cabinet comptable, et figer un schéma ici obligerait à une
-- migration à chaque nouveau type de document. Le filtrage se fait par
-- confinement (`@>`), qui est indexable — contrairement à un LIKE sur du texte.
ALTER TABLE rag_documents ADD COLUMN IF NOT EXISTS meta JSONB NOT NULL DEFAULT '{{}}'::jsonb;
ALTER TABLE rag_chunks    ADD COLUMN IF NOT EXISTS meta JSONB NOT NULL DEFAULT '{{}}'::jsonb;

-- Préfixe contextuel réellement vectorisé avec le fragment, quand l'indexation
-- contextuelle est active. Stocké séparément du texte pour deux raisons : ce
-- qu'on RESTITUE à l'utilisateur reste le texte d'origine, et on peut vérifier
-- après coup ce qui a été embarqué dans le vecteur — sans quoi « l'indexation
-- contextuelle a-t-elle servi ? » resterait une question sans réponse.
ALTER TABLE rag_chunks ADD COLUMN IF NOT EXISTS context TEXT NOT NULL DEFAULT '';

-- Signature des réglages qui ont produit les vecteurs de ce document : taille de
-- fragment, recouvrement, préfixage contextuel. Fait partie de l'IDENTITÉ de
-- l'index au même titre que le modèle et la dimension — changer l'un d'eux doit
-- déclencher une réindexation, pas laisser cohabiter deux façons de vectoriser.
-- Une chaîne et non une colonne par réglage : ajouter un paramètre d'indexation
-- ne doit pas coûter une migration, sinon plus personne n'en essaie.
ALTER TABLE rag_documents
    ADD COLUMN IF NOT EXISTS index_profile TEXT NOT NULL DEFAULT '';

CREATE INDEX IF NOT EXISTS rag_documents_root_idx ON rag_documents (root);

-- Deux sources gérées peuvent contenir le même chemin relatif (par exemple
-- `public/conditions.pdf`). L'identité documentaire est donc le couple
-- racine/chemin, pas le chemin seul.
ALTER TABLE rag_documents DROP CONSTRAINT IF EXISTS rag_documents_source_key;
CREATE UNIQUE INDEX IF NOT EXISTS rag_documents_root_source_idx
    ON rag_documents (root, source);

-- Recherche lexicale et filtrage des permissions.
CREATE INDEX IF NOT EXISTS rag_chunks_tsv_idx ON rag_chunks USING gin (tsv);
CREATE INDEX IF NOT EXISTS rag_chunks_acl_idx ON rag_chunks USING gin (acl);
CREATE INDEX IF NOT EXISTS rag_documents_acl_idx ON rag_documents USING gin (acl);

-- `jsonb_path_ops` plutôt que l'opérateur par défaut : l'index est plus compact
-- et plus rapide, au prix de ne servir que `@>` — qui est précisément et
-- uniquement ce que le filtrage métier utilise.
CREATE INDEX IF NOT EXISTS rag_chunks_meta_idx
    ON rag_chunks USING gin (meta jsonb_path_ops);
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


# Colonnes dont la présence atteste que le schéma courant est déjà appliqué.
# Sert à ne PAS rejouer le DDL à chaque démarrage de processus — voir `connect`.
_SCHEMA_MARKERS = (
    ("rag_chunks", "meta"),
    ("rag_chunks", "context"),
    ("rag_documents", "index_profile"),
)


async def _schema_is_current(connection: asyncpg.Connection) -> bool:
    manquantes = await connection.fetchval(
        """
        SELECT count(*)
        FROM (VALUES {}) AS attendu(table_name, column_name)
        WHERE NOT EXISTS (
            SELECT 1 FROM information_schema.columns c
            WHERE c.table_name = attendu.table_name
              AND c.column_name = attendu.column_name
        )
        """.format(", ".join(f"('{t}', '{c}')" for t, c in _SCHEMA_MARKERS))
    )
    root_source_index = await connection.fetchval(
        """
        SELECT EXISTS (
            SELECT 1 FROM pg_indexes
            WHERE tablename = 'rag_documents'
              AND indexname = 'rag_documents_root_source_idx'
        )
        """
    )
    return manquantes == 0 and root_source_index


async def connect() -> asyncpg.Pool:
    """Applique le schéma **si nécessaire**, puis ouvre le pool.

    En deux temps volontairement : `CREATE EXTENSION` doit avoir eu lieu avant
    que quoi que ce soit ne manipule le type `vector`.

    **Le DDL n'est rejoué que s'il manque quelque chose.** `ALTER TABLE` et
    `CREATE INDEX`, même en `IF NOT EXISTS`, prennent un `AccessExclusiveLock`
    sur la table : un processus qui démarre pendant qu'un autre lit peut donc
    provoquer un interblocage. Constaté pour de bon — une exécution
    d'évaluation de vingt minutes tuée par un `DeadlockDetectedError` parce
    qu'une suite de tests démarrait à côté. Vérifier avant d'écrire coûte une
    requête sur le catalogue et supprime le verrou dans le cas courant, qui est
    de très loin le plus fréquent : le schéma est déjà là.

    Ce n'est pas un système de migration, et ça n'en tient pas lieu. C'est le
    strict nécessaire pour qu'ouvrir une connexion cesse d'être une opération
    qui peut bloquer les autres.
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
        if await _schema_is_current(connection):
            logger.debug("schéma déjà à jour, DDL non rejoué")
        else:
            # Plutôt attendre puis échouer clairement que rester bloqué sans fin
            # derrière un long lecteur.
            await connection.execute("SET lock_timeout = '15s'")
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
