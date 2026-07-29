"""La chaîne de recherche complète, filtrée par les permissions de l'appelant.

    question
       │
       ├─ expansion (facultative) ────────────▶ n formulations
       │
       ├─ HyDE (facultatif) ─────────────────▶ document(s) hypothétique(s)
       │                                         puis vecteur dense moyen
       │
       ├─ pour chacune : dense (pgvector, cosinus)
       │                 lexicale (tsvector français, ts_rank_cd)
       │
       ├─ fusion RRF de TOUTES les listes
       │
       ├─ reclassement (facultatif) ──────────▶ notes 0–10 sur le fond
       │
       ├─ abstention (facultative) ───────────▶ « rien ne répond » plutôt qu'un
       │                                         plus proche voisin sans rapport
       │
       └─ élargissement au voisinage (facultatif) ─▶ fragments recollés

**Ce qui était déjà là, et pourquoi ça compte.** Les deux recherches sont
complémentaires : la dense retrouve les reformulations (« il reste combien à leur
charge » face à « franchise contractuelle »), la lexicale retrouve ce que la dense
rate systématiquement — une référence, une immatriculation, un montant. La fusion
RRF les combine sans rien à calibrer : additionner une distance cosinus (0 à 2) et
un `ts_rank_cd` (échelle libre, dépendante du corpus) demanderait une pondération
à réajuster à chaque corpus. RRF ne regarde que les rangs.

**Ce qui manquait, et qui est le vrai sujet.** La fusion RRF n'a jamais lu la
question. Elle ne peut donc ni distinguer un passage qui répond d'un passage qui
évoque, ni dire qu'aucun ne convient. Un RAG sans seuil rend **toujours** ses plus
proches voisins : demandez la franchise cyber d'un client qui n'a pas de contrat
cyber, il rendra le contrat cyber d'un autre client, avec le même aplomb. C'est la
panne la plus coûteuse en courtage, et elle est invisible tant qu'on ne mesure que
le rappel. Le reclassement produit une échelle interprétable, et c'est cette
échelle qui rend l'abstention possible.

**Le filtre ACL est un paramètre obligatoire, pas une option.** Il n'y a pas de
valeur par défaut, et une liste de groupes vide ne veut pas dire « tout » mais
« rien ». C'est la seule protection qui tienne dans la durée — une convention
qu'on doit penser à respecter finit toujours par être oubliée. Le filtre métier
(`filters`), lui, n'est PAS une protection : il restreint, il ne garde rien.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field

from agent.core.rag import embed, hyde, rerank
from agent.core.rag import query as query_expansion
from agent.core.rag.config import RagConfig, from_env
from agent.infra import ragdb

logger = logging.getLogger("agent.rag.retrieve")

# Constante usuelle de la RRF. Elle amortit le poids des toutes premières places :
# avec K petit, le premier résultat d'une liste écraserait tout le reste.
RRF_K = 60


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
    similarity: float | None = None
    """Similarité cosinus au meilleur vecteur de requête, quand la recherche dense
    a vu ce fragment. `None` s'il ne vient que de la branche lexicale."""
    rerank_score: float | None = None
    meta: dict = field(default_factory=dict)
    expanded: bool = False
    """Le texte a été recollé avec ses voisins (*small-to-big*)."""

    @property
    def citation(self) -> str:
        """Référence vérifiable : le lecteur doit pouvoir retrouver le passage."""
        return f"{self.source}#{self.ord}"


@dataclass
class SearchResult:
    """Résultat d'une recherche, **et la raison de ce résultat**.

    Distinguer « aucun document accessible » de « des documents existaient mais
    aucun ne répondait » n'est pas cosmétique : le premier cas se corrige avec des
    droits, le second avec du corpus ou un seuil. Confondus dans une liste vide,
    les deux sont indiscernables — et l'abstention devient impossible à mesurer.
    """

    passages: list[Passage] = field(default_factory=list)
    abstained: bool = False
    reason: str | None = None
    candidates_seen: int = 0
    queries: list[str] = field(default_factory=list)
    reranked: bool = False
    hyde_used: bool = False
    hypotheses_generated: int = 0

    def __bool__(self) -> bool:
        return bool(self.passages)


# --- SQL ----------------------------------------------------------------------
#
# `$1` vecteur · `$2` groupes · `$3` limite · `$4` filtre métier (jsonb)
#
# Le filtre métier est appliqué DANS la requête et non après coup : filtrer en
# Python sur un top-30 déjà tronqué ne rendrait presque rien dès que le filtre est
# sélectif. `meta @> '{}'::jsonb` est vrai pour tout le monde, donc l'absence de
# filtre ne demande pas une seconde version de la requête.

_DENSE_SQL = """
SELECT c.id, c.document_id, c.ord, c.text, c.meta, d.source, d.title,
       1 - (c.embedding <=> $1::vector) AS similarity
FROM rag_chunks c
JOIN rag_documents d ON d.id = c.document_id
WHERE c.acl && $2::text[] AND c.meta @> $4::jsonb
ORDER BY c.embedding <=> $1::vector
LIMIT $3
"""

# `websearch_to_tsquery` accepte une saisie d'utilisateur telle quelle (guillemets,
# `or`, `-mot`) sans jamais lever sur une syntaxe invalide — contrairement à
# `to_tsquery`, qui échouerait sur la moindre apostrophe.
#
# **Mais il conjugue les termes par ET**, et c'est un piège coûteux. Sur la
# question « il reste combien à la charge de Bativert en cas de pépin sur la
# multirisque ? », il produit
#
#     'rest' & 'combien' & 'charg' & 'bativert' & 'cas' & 'pépin' & 'multirisqu'
#
# qui exige les sept lexèmes dans le MÊME fragment : zéro résultat. Mesuré sur ce
# corpus, la branche lexicale ainsi écrite plafonnait à 1 % de rappel — autrement
# dit elle ne contribuait à rien, et la recherche prétendument hybride était en
# réalité une recherche dense avec du code mort à côté. Le symptôme est
# silencieux : aucune erreur, juste une moitié du système qui ne sert jamais.
#
# On bascule donc l'opérateur en OU sur la forme textuelle de la tsquery. Le
# découpage, la racinisation et l'échappement restent l'œuvre de
# `websearch_to_tsquery` — on ne touche qu'au connecteur. Les opérateurs de
# phrase (`<->`) et de négation (`!`) traversent la substitution intacts, `&`
# n'apparaissant jamais à l'intérieur d'un lexème produit par l'analyseur
# français. Limite assumée : sur une saisie du type `-mot`, la négation se
# retrouve reliée en OU et perd donc son effet de filtre ; c'est un opérateur que
# personne n'emploie en posant une question en langue naturelle, et le classement
# par `ts_rank_cd` fait le tri que le ET faisait brutalement.
_SPARSE_SQL = """
WITH q AS (
    SELECT replace(websearch_to_tsquery('french', $1)::text, '&', '|')::tsquery AS query
)
SELECT c.id, c.document_id, c.ord, c.text, c.meta, d.source, d.title,
       NULL::float8 AS similarity
FROM rag_chunks c
JOIN rag_documents d ON d.id = c.document_id,
     q
WHERE c.acl && $2::text[] AND c.meta @> $4::jsonb AND c.tsv @@ q.query
ORDER BY ts_rank_cd(c.tsv, q.query) DESC
LIMIT $3
"""

_NEIGHBOURS_SQL = """
SELECT c.document_id, c.ord, c.text
FROM rag_chunks c
WHERE c.document_id = $1 AND c.acl && $2::text[]
  AND c.ord BETWEEN $3 AND $4
ORDER BY c.ord
"""


# --- Compatibilité ------------------------------------------------------------


def top_k() -> int:
    return from_env().top_k


def candidates() -> int:
    return from_env().candidates


# --- Recherche ----------------------------------------------------------------


async def search(
    query: str,
    groups: list[str],
    *,
    k: int | None = None,
    pool_size: int | None = None,
    config: RagConfig | None = None,
    filters: dict | None = None,
) -> list[Passage]:
    """Fragments lisibles par `groups`, les plus pertinents d'abord.

    Conserve la signature historique — une liste, éventuellement vide — pour tout
    le code qui n'a pas besoin de savoir *pourquoi* elle est vide. Voir
    `search_detailed` quand la distinction compte.
    """
    return (
        await search_detailed(
            query, groups, k=k, pool_size=pool_size, config=config, filters=filters
        )
    ).passages


async def search_detailed(
    query: str,
    groups: list[str],
    *,
    k: int | None = None,
    pool_size: int | None = None,
    config: RagConfig | None = None,
    filters: dict | None = None,
) -> SearchResult:
    """La recherche complète, avec le détail de ce qui s'est passé.

    Args:
        query: la question, en langue naturelle.
        groups: groupes de l'appelant. **Obligatoire.** Vide = aucun accès, donc
            aucun résultat — jamais l'inverse.
        k: nombre de fragments rendus. Surcharge `config.top_k`.
        pool_size: vivier tiré de chaque recherche. Surcharge `config.candidates`.
        config: configuration de la chaîne. Par défaut celle de l'environnement.
        filters: restriction métier sur les métadonnées, par confinement JSON —
            `{"client": "bativert"}`. **Ce n'est pas une protection** : il
            restreint le périmètre, il ne remplace jamais `groups`.
    """
    config = config or from_env()
    query = query.strip()
    if not query:
        return SearchResult(reason="question vide")

    if not groups:
        # Fermé par défaut. Ce cas signale un appel sans identité : le tracer est
        # utile, le servir ne l'est pas.
        logger.warning("recherche sans aucun groupe : aucun résultat rendu")
        return SearchResult(reason="aucun groupe : accès fermé par défaut")

    if not config.dense and not config.sparse:
        raise ValueError(
            "config.dense et config.sparse sont tous deux désactivés : il ne reste "
            "aucune recherche à exécuter."
        )

    k = k or config.top_k
    pool_size = pool_size or config.candidates
    filtre = json.dumps(filters or {}, ensure_ascii=False)

    formulations, hypotheses = await asyncio.gather(
        query_expansion.expand(query, config.multi_query),
        hyde.generate(
            query,
            config.hyde_documents if config.dense else 0,
            temperature=config.hyde_temperature,
        ),
    )
    fusionnes, hyde_used = await _collect(
        formulations,
        hypotheses,
        groups,
        pool_size,
        filtre,
        config,
    )

    if not fusionnes:
        return SearchResult(
            reason="aucun fragment accessible ne correspond",
            queries=formulations,
            hyde_used=hyde_used,
            hypotheses_generated=len(hypotheses),
        )

    resultat = SearchResult(
        candidates_seen=len(fusionnes),
        queries=formulations,
        hyde_used=hyde_used,
        hypotheses_generated=len(hypotheses),
    )

    if config.rerank == "llm":
        fusionnes, resultat.reranked = await _apply_rerank(query, fusionnes, config)

    retenus, abstention = _apply_threshold(fusionnes, config, resultat.reranked)
    if abstention:
        resultat.abstained = True
        resultat.reason = abstention
        return resultat

    retenus = retenus[:k]
    if config.neighbours:
        await _expand_neighbours(retenus, groups, config.neighbours)

    resultat.passages = retenus
    return resultat


async def _collect(
    formulations: list[str],
    hypotheses: list[str],
    groups: list[str],
    pool_size: int,
    filtre: str,
    config: RagConfig,
) -> tuple[list[Passage], bool]:
    """Exécute toutes les recherches et fusionne le tout en une seule RRF.

    Une seule fusion pour l'ensemble, et non une fusion par formulation puis une
    fusion des fusions : un fragment trouvé par trois reformulations doit cumuler
    trois contributions. C'est exactement ce qui fait l'intérêt de l'expansion —
    le consensus entre formulations est un signal, et l'écraser en fusionnant par
    étapes le détruirait.
    """
    vecteurs: list[str] = []
    hyde_used = False
    if config.dense:
        # Les vectorisations partent ensemble : sur trois reformulations, les
        # faire en série ajoute deux allers-retours réseau au chemin critique.
        bruts, hypotheses_vectors = await asyncio.gather(
            asyncio.gather(*(embed.embed_query(texte) for texte in formulations)),
            embed.embed_documents(hypotheses),
        )

        # HyDE REMPLACE le vecteur de la question originale. L'ajouter comme une
        # liste RRF supplémentaire surpondérerait artificiellement la branche
        # dense ; remplacer conserve exactement une liste dense par formulation.
        if hypotheses_vectors:
            composants = list(hypotheses_vectors)
            if config.hyde_include_query:
                composants.append(bruts[0])
            try:
                bruts[0] = hyde.average(composants)
                hyde_used = True
            except ValueError as error:
                logger.warning("vecteur HyDE inutilisable, repli sur la question : %s", error)

        vecteurs = [ragdb.to_vector_literal(vecteur) for vecteur in bruts]

    passages: dict[int, Passage] = {}
    scores: dict[int, float] = {}

    async with ragdb.pool().acquire() as connection:
        for index, formulation in enumerate(formulations):
            if config.dense:
                lignes = await connection.fetch(
                    _DENSE_SQL, vecteurs[index], groups, pool_size, filtre
                )
                _fuse_into(passages, scores, lignes, "dense", 1.0)
            if config.sparse:
                lignes = await connection.fetch(
                    _SPARSE_SQL, formulation, groups, pool_size, filtre
                )
                _fuse_into(passages, scores, lignes, "sparse", config.sparse_weight)

    for chunk_id, valeur in scores.items():
        passages[chunk_id].score = valeur

    return (
        sorted(passages.values(), key=lambda passage: passage.score, reverse=True),
        hyde_used,
    )


def _fuse_into(
    passages: dict[int, Passage],
    scores: dict[int, float],
    rows,
    branche: str,
    poids: float,
) -> None:
    """Ajoute une liste classée à la fusion RRF en cours.

    `poids` corrige le défaut de la RRF à poids égal : sans lui, une liste
    faible mais toujours pleine — ce qu'est une recherche lexicale en OU, qui
    trouve quelque chose pour n'importe quelle question — fait descendre les
    résultats d'une liste forte. Le poids ne touche qu'aux scores, jamais aux
    rangs conservés : `dense_rank` et `sparse_rank` restent l'information brute,
    et servent à expliquer un classement après coup.
    """
    for rang, row in enumerate(rows, start=1):
        chunk_id = row["id"]
        passage = passages.get(chunk_id)
        if passage is None:
            passage = _to_passage(row)
            passages[chunk_id] = passage

        if branche == "dense":
            # Meilleur rang conservé : un fragment premier sur une reformulation et
            # dixième sur une autre vaut son meilleur classement, pas le dernier vu.
            if passage.dense_rank is None or rang < passage.dense_rank:
                passage.dense_rank = rang
            similarite = row["similarity"]
            if similarite is not None and (
                passage.similarity is None or similarite > passage.similarity
            ):
                passage.similarity = float(similarite)
        elif passage.sparse_rank is None or rang < passage.sparse_rank:
            passage.sparse_rank = rang

        scores[chunk_id] = scores.get(chunk_id, 0.0) + poids / (RRF_K + rang)


async def _apply_rerank(
    question: str, passages: list[Passage], config: RagConfig
) -> tuple[list[Passage], bool]:
    """Reclasse le début de la liste. Rend `(passages, reclassement_effectif)`.

    Seuls les `rerank_pool` premiers sont soumis : au-delà, le modèle paie une
    fenêtre plus large pour trier des candidats que la fusion a déjà jugés
    marginaux. La queue non soumise est **conservée derrière**, pas jetée — la
    tronquer ici retirerait des résultats qu'aucun reclasseur n'a pourtant écartés.
    """
    tete = passages[: config.rerank_pool]
    queue = passages[config.rerank_pool :]

    notes = await rerank.score(question, [passage.text for passage in tete])
    if notes is None:
        # Échec ouvert : l'ordre RRF reste parfaitement utilisable.
        return passages, False

    for note in notes:
        tete[note.index].rerank_score = note.score

    tete.sort(key=lambda passage: (passage.rerank_score or 0.0), reverse=True)
    return [*tete, *queue], True


def _apply_threshold(
    passages: list[Passage], config: RagConfig, reranked: bool
) -> tuple[list[Passage], str | None]:
    """Écarte ce qui est trop faible, et dit quand il ne reste rien.

    Deux seuils, jamais les deux à la fois : celui du reclasseur quand il a
    tourné, celui de la similarité sinon. Les cumuler n'aurait pas de sens — ils
    ne mesurent pas la même chose et ne vivent pas sur la même échelle.
    """
    if reranked and config.min_rerank_score is not None:
        gardes = [
            passage
            for passage in passages
            if (passage.rerank_score or 0.0) >= config.min_rerank_score
        ]
        if not gardes:
            meilleur = max((p.rerank_score or 0.0) for p in passages)
            return [], (
                f"aucun passage au-dessus du seuil de pertinence "
                f"({meilleur:.1f} < {config.min_rerank_score:g} sur 10)"
            )
        return gardes, None

    if not reranked and config.min_similarity is not None:
        similarites = [p.similarity for p in passages if p.similarity is not None]
        if similarites and max(similarites) < config.min_similarity:
            return [], (
                f"similarité maximale {max(similarites):.3f} sous le seuil "
                f"{config.min_similarity:g}"
            )

    return passages, None


async def _expand_neighbours(passages: list[Passage], groups: list[str], span: int) -> None:
    """Recolle chaque fragment retenu avec ses voisins immédiats (*small-to-big*).

    Le découpage coupe parfois une réponse en deux : le fragment retrouvé contient
    la question, le suivant contient le montant. Recoller les voisins **après** le
    classement plutôt qu'indexer des fragments plus gros permet de garder une
    recherche fine et une restitution large — c'est tout l'intérêt du procédé.

    Les voisins repassent par le filtre ACL. Ce n'est pas de la prudence
    décorative : un document est indexé avec une ACL uniforme aujourd'hui, mais
    élargir un fragment sans revérifier serait exactement le raccourci qui fait
    fuiter le jour où une ACL devient plus fine que le document.
    """
    async with ragdb.pool().acquire() as connection:
        for passage in passages:
            lignes = await connection.fetch(
                _NEIGHBOURS_SQL,
                passage.document_id,
                groups,
                max(0, passage.ord - span),
                passage.ord + span,
            )
            if len(lignes) <= 1:
                continue
            passage.text = "\n\n".join(ligne["text"] for ligne in lignes)
            passage.expanded = True


def _to_passage(row) -> Passage:
    meta = row["meta"]
    if isinstance(meta, str):
        # asyncpg rend le JSONB en texte tant qu'aucun codec n'est enregistré.
        meta = json.loads(meta)
    return Passage(
        chunk_id=row["id"],
        document_id=row["document_id"],
        source=row["source"],
        title=row["title"],
        ord=row["ord"],
        text=row["text"],
        score=0.0,
        similarity=float(row["similarity"]) if row["similarity"] is not None else None,
        meta=meta or {},
    )
