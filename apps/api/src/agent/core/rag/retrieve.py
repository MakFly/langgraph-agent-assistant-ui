"""Recherche hybride, filtrée par les permissions de l'appelant.

Deux recherches, une fusion :

- **dense** (`embedding <=> $vecteur`, distance cosinus via pgvector) retrouve les
  reformulations — « combien de jours de télétravail » face à un texte qui parle
  de « rythme hebdomadaire à distance » ;
- **lexicale** (`ts_rank_cd` sur un `tsvector` français) retrouve ce que le dense
  rate systématiquement : un identifiant, un nom propre, un montant, un sigle.

Elles sont fusionnées par **RRF** (*Reciprocal Rank Fusion*) : chaque fragment
marque `1 / (K + rang)` dans chaque liste, et les scores s'additionnent. L'intérêt
est qu'on n'a **rien à calibrer** — additionner une distance cosinus (0 à 2) et un
`ts_rank_cd` (échelle libre, dépendante du corpus) demanderait une pondération
qu'il faudrait réajuster à chaque changement de corpus. RRF ne regarde que les
rangs, donc rien à régler.

**Limite connue : il n'y a aucun seuil de pertinence.** Une recherche dense rend
toujours ses plus proches voisins, même quand aucun document ne répond à la
question — sur un petit corpus, la question « quel est le budget ? » posée par
quelqu'un qui n'a accès qu'à la charte du télétravail lui rendra la charte du
télétravail. Poser un seuil sur la distance cosinus semble être la réponse, mais
la valeur dépend du corpus, du modèle et de la longueur des fragments : c'est un
réglage que personne ne sait calibrer sans mesure. La bonne réponse est
l'évaluation (`make eval`), qui rend le phénomène visible et chiffré ; en
attendant, l'outil formule ses résultats de façon à ce que le modèle ne les
présente pas comme des réponses certaines.

**Le filtre ACL est un paramètre obligatoire de la fonction, pas une option.**
On ne *peut pas* écrire un appel qui oublie les permissions : il n'y a pas de
valeur par défaut, et une liste de groupes vide ne veut pas dire « tout » mais
« rien ». C'est la seule protection qui tienne dans la durée — une convention
qu'on doit penser à respecter finit toujours par être oubliée.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from agent.core.rag import embed
from agent.infra import ragdb

logger = logging.getLogger("agent.rag.retrieve")

# Constante usuelle de la RRF. Elle amortit le poids des toutes premières places :
# avec K petit, le premier résultat d'une liste écraserait tout le reste.
RRF_K = 60

DEFAULT_TOP_K = 5
DEFAULT_CANDIDATES = 20


@dataclass
class Passage:
    """Un fragment retenu, avec de quoi le citer et de quoi expliquer son rang."""

    chunk_id: int
    document_id: str
    source: str
    title: str | None
    ord: int
    text: str
    score: float
    dense_rank: int | None = None
    sparse_rank: int | None = None

    @property
    def citation(self) -> str:
        """Référence vérifiable : le lecteur doit pouvoir retrouver le passage."""
        return f"{self.source}#{self.ord}"


def top_k() -> int:
    return _int_env("RAG_TOP_K", DEFAULT_TOP_K)


def candidates() -> int:
    return _int_env("RAG_CANDIDATES", DEFAULT_CANDIDATES)


def _int_env(name: str, fallback: int) -> int:
    try:
        value = int(os.getenv(name, str(fallback)))
    except ValueError:
        logger.warning("%s illisible, repli sur %d", name, fallback)
        return fallback
    return value if value > 0 else fallback


_DENSE_SQL = """
SELECT c.id, c.document_id, c.ord, c.text, d.source, d.title
FROM rag_chunks c
JOIN rag_documents d ON d.id = c.document_id
WHERE c.acl && $2::text[]
ORDER BY c.embedding <=> $1::vector
LIMIT $3
"""

# `websearch_to_tsquery` accepte une saisie d'utilisateur telle quelle (guillemets,
# `or`, `-mot`) sans jamais lever sur une syntaxe invalide — contrairement à
# `to_tsquery`, qui échouerait sur la moindre apostrophe.
_SPARSE_SQL = """
SELECT c.id, c.document_id, c.ord, c.text, d.source, d.title
FROM rag_chunks c
JOIN rag_documents d ON d.id = c.document_id,
     websearch_to_tsquery('french', $1) AS query
WHERE c.acl && $2::text[] AND c.tsv @@ query
ORDER BY ts_rank_cd(c.tsv, query) DESC
LIMIT $3
"""


async def search(
    query: str,
    groups: list[str],
    *,
    k: int | None = None,
    pool_size: int | None = None,
) -> list[Passage]:
    """Fragments lisibles par `groups`, les plus pertinents d'abord.

    Args:
        query: la question, en langue naturelle.
        groups: groupes de l'appelant. **Obligatoire.** Vide = aucun accès, donc
            aucun résultat — jamais l'inverse.
        k: nombre de fragments rendus.
        pool_size: taille du vivier tiré de chaque recherche avant fusion.
    """
    query = query.strip()
    if not query:
        return []

    if not groups:
        # Fermé par défaut. Ce cas signale un appel sans identité : le tracer est
        # utile, le servir ne l'est pas.
        logger.warning("recherche sans aucun groupe : aucun résultat rendu")
        return []

    k = k or top_k()
    pool_size = pool_size or candidates()

    vector = ragdb.to_vector_literal(await embed.embed_query(query))

    async with ragdb.pool().acquire() as connection:
        dense = await connection.fetch(_DENSE_SQL, vector, groups, pool_size)
        sparse = await connection.fetch(_SPARSE_SQL, query, groups, pool_size)

    return _fuse(dense, sparse)[:k]


def _fuse(dense, sparse) -> list[Passage]:
    """Reciprocal Rank Fusion des deux classements."""
    passages: dict[int, Passage] = {}
    scores: dict[int, float] = {}

    for rank, row in enumerate(dense, start=1):
        passage = _to_passage(row)
        passage.dense_rank = rank
        passages[passage.chunk_id] = passage
        scores[passage.chunk_id] = 1.0 / (RRF_K + rank)

    for rank, row in enumerate(sparse, start=1):
        chunk_id = row["id"]
        if chunk_id in passages:
            passages[chunk_id].sparse_rank = rank
        else:
            passage = _to_passage(row)
            passage.sparse_rank = rank
            passages[chunk_id] = passage
        scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (RRF_K + rank)

    for chunk_id, score in scores.items():
        passages[chunk_id].score = score

    return sorted(passages.values(), key=lambda passage: passage.score, reverse=True)


def _to_passage(row) -> Passage:
    return Passage(
        chunk_id=row["id"],
        document_id=row["document_id"],
        source=row["source"],
        title=row["title"],
        ord=row["ord"],
        text=row["text"],
        score=0.0,
    )
