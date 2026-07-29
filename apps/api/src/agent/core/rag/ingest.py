"""Indexation du corpus : `corpus/` → `rag_documents` + `rag_chunks`.

**Les permissions viennent de l'arborescence.** Le premier segment du chemin
relatif est le groupe autorisé à lire le document :

    corpus/public/tarifs.md      → acl = ['public']    (tout le monde)
    corpus/finance/budget.pdf    → acl = ['finance']   (groupe `finance` seul)

Aucun fichier de manifeste à tenir à jour, donc aucun manifeste à oublier de
mettre à jour. Un fichier posé à la racine de `corpus/` tombe dans `public` — et
l'ingestion le signale, parce que rendre un document lisible par tous ne doit
jamais être un effet de bord silencieux.

**L'idempotence** repose sur le `sha256` du contenu : réindexer un corpus
inchangé ne produit aucune écriture et n'appelle jamais le fournisseur
d'embeddings. C'est ce qui rend `make ingest` sûr à relancer, et vérifiable.

**Le garde-fou de coût** est appliqué après le découpage et avant la
vectorisation : à ce moment-là on connaît le nombre exact de fragments à payer,
et rien n'a encore été facturé.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from agent.core.rag import chunk as chunker
from agent.core.rag import config as ragconfig
from agent.core.rag import embed, parse
from agent.infra import ragdb

logger = logging.getLogger("agent.rag.ingest")

DEFAULT_MAX_CHUNKS = 2000

# Longueur maximale du préfixe contextuel. Un préfixe qui pèse autant que le
# fragment noie son sens dans le vecteur : on retrouverait alors tous les
# fragments d'un même document sur la seule foi de leur en-tête commun.
MAX_CONTEXT_CHARS = 220

# Groupe attribué à un fichier déposé à la racine du corpus, hors de tout dossier.
FALLBACK_GROUP = "public"


# Proportion de l'index qu'une seule exécution peut supprimer sans confirmation.
# `ingest()` est une SYNCHRONISATION : ce qui a disparu du disque disparaît de
# l'index. C'est le bon comportement quand on vise la racine du corpus, et un
# effacement silencieux quand on se trompe de dossier — pointer `--corpus` sur un
# sous-dossier, ou sur un montage vide, supprimerait tout le reste.
MAX_PRUNE_RATIO = 0.5


class BudgetExceeded(Exception):
    """Le lot dépasse le plafond de fragments autorisé pour une exécution."""


class PruneTooLarge(Exception):
    """La synchronisation supprimerait une part anormale de l'index."""


@dataclass
class DocumentReport:
    source: str
    action: str  # indexé | inchangé | supprimé | erreur
    chunks: int = 0
    groups: list[str] = field(default_factory=list)
    message: str | None = None


@dataclass
class IngestReport:
    documents: list[DocumentReport] = field(default_factory=list)
    tokens: int = 0
    dry_run: bool = False

    def _count(self, action: str) -> int:
        return sum(1 for document in self.documents if document.action == action)

    @property
    def indexed(self) -> int:
        return self._count("indexé")

    @property
    def unchanged(self) -> int:
        return self._count("inchangé")

    @property
    def removed(self) -> int:
        return self._count("supprimé")

    @property
    def failed(self) -> int:
        return self._count("erreur")

    @property
    def chunks(self) -> int:
        return sum(document.chunks for document in self.documents)


@dataclass
class _Pending:
    """Document parsé et découpé, en attente de vectorisation."""

    id: str
    root: str
    source: str
    title: str | None
    sha256: str
    groups: list[str]
    chunks: list[chunker.Chunk]
    meta: dict
    contexts: list[str]
    """Préfixe réellement vectorisé avec chaque fragment. Vide si l'indexation
    contextuelle est désactivée — et alors `chunks[i]` est vectorisé seul."""


def build_context(meta: dict, title: str | None, fragment: chunker.Chunk) -> str:
    """Préfixe situant un fragment dans son document.

    **Contextualisation structurelle, pas générée.** L'approche popularisée par
    Anthropic fait rédiger ce préfixe par un LLM, fragment par fragment : c'est
    plus riche, et c'est un appel de modèle par fragment à chaque ingestion —
    donc un coût qui croît avec le corpus et se repaie à chaque réindexation.
    Ici le préfixe est assemblé à partir de ce qu'on sait déjà : le front-matter
    et le chemin de titres. C'est gratuit, déterministe, et ça règle le problème
    qui compte — « la franchise est portée à 12 500 € » ne dit ni de quel client
    ni de quel contrat il s'agit, et devient donc introuvable par ces termes.

    Le gain reste à MESURER, pas à supposer : c'est ce que fait la comparaison
    de deux index (cf. `rag eval --ablation`, section indexation).
    """
    morceaux: list[str] = []

    libelle = meta.get("type")
    if libelle:
        morceaux.append(str(libelle).replace("_", " "))
    for cle in ("client_nom", "prospect"):
        if meta.get(cle):
            morceaux.append(str(meta[cle]))
            break
    for cle in ("reference", "contrat"):
        if meta.get(cle):
            morceaux.append(str(meta[cle]))
            break
    if meta.get("produit_label"):
        morceaux.append(str(meta["produit_label"]))

    entete = " — ".join(dict.fromkeys(morceaux))
    chemin = fragment.heading_path or (title or "")

    contexte = " · ".join(part for part in (entete, chemin) if part)
    if len(contexte) > MAX_CONTEXT_CHARS:
        contexte = contexte[:MAX_CONTEXT_CHARS].rsplit(" ", 1)[0] + "…"
    return contexte


def max_chunks_per_run() -> int:
    try:
        return int(os.getenv("RAG_MAX_CHUNKS_PER_RUN", str(DEFAULT_MAX_CHUNKS)))
    except ValueError:
        logger.warning("RAG_MAX_CHUNKS_PER_RUN illisible, repli sur %d", DEFAULT_MAX_CHUNKS)
        return DEFAULT_MAX_CHUNKS


def _document_id(root: str, source: str) -> str:
    """Identifiant stable, dérivé du CHEMIN et non du contenu.

    Ainsi un document modifié garde son identité : on remplace ses fragments au
    lieu d'accumuler un doublon à chaque révision. La racine entre dans le calcul
    pour que deux corpus distincts ne se disputent pas le même identifiant.
    """
    return hashlib.sha256(f"{root}\0{source}".encode()).hexdigest()[:32]


def _groups_for(relative: Path) -> tuple[list[str], str | None]:
    if len(relative.parts) > 1:
        return [relative.parts[0]], None
    return (
        [FALLBACK_GROUP],
        f"déposé à la racine du corpus → lisible par tous ({FALLBACK_GROUP})",
    )


def scan(root: Path) -> list[Path]:
    """Fichiers indexables, triés — l'ordre rend les exécutions comparables."""
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file()
        and path.suffix.lower() in parse.SUPPORTED_SUFFIXES
        and not path.name.startswith(".")
    )


async def _existing(connection, root: str) -> dict[str, dict]:
    """Documents déjà indexés **pour cette racine**, et eux seuls.

    Le périmètre est essentiel : c'est ce dictionnaire qui sert à décider ce qui
    a disparu. Non filtré, indexer un dossier quelconque considérait tout le
    reste de l'index comme supprimé.
    """
    rows = await connection.fetch(
        "SELECT id, source, sha256, embed_model, embed_dim, index_profile "
        "FROM rag_documents WHERE root = $1",
        root,
    )
    return {row["source"]: dict(row) for row in rows}


async def ingest(
    root: Path,
    *,
    dry_run: bool = False,
    max_chunks: int | None = None,
    prune: bool = True,
    force: bool = False,
    ocr_config: dict | None = None,
) -> IngestReport:
    """Synchronise l'index sur `root`, en ne payant que ce qui a changé.

    Args:
        prune: supprimer de l'index les documents absents du disque. C'est la
            sémantique d'une synchronisation, et c'est ce qu'on veut quand `root`
            est bien la racine du corpus. Le passer à `False` rend l'opération
            purement additive — c'est ce que font les tests, qui indexent un
            corpus temporaire sans avoir à toucher au reste de l'index.
        force: passe outre le garde-fou de suppression.

    Raises:
        BudgetExceeded: le lot dépasse le plafond de fragments.
        PruneTooLarge: la synchronisation supprimerait plus de la moitié de
            l'index. Dans les deux cas, rien n'a été vectorisé ni écrit.
    """
    report = IngestReport(dry_run=dry_run)
    cap = max_chunks if max_chunks is not None else max_chunks_per_run()
    model, dim = embed.model_name(), embed.dimension()
    reglages = ragconfig.from_env()
    profil = reglages.index_profile()
    if ocr_config:
        # Modifier le modèle/prompt OCR doit invalider les documents, même si le
        # fichier source n'a pas changé. Seule l'empreinte entre dans le profil :
        # le prompt complet n'a rien à faire dans l'index documentaire.
        ocr_signature = hashlib.sha256(
            json.dumps(ocr_config, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()[:12]
        profil = f"{profil};ocr={ocr_signature}"

    if not root.is_dir():
        raise FileNotFoundError(f"Corpus introuvable : {root}")

    # Chemin absolu résolu : deux invocations avec « corpus » et « ./corpus »
    # doivent viser le même périmètre, sinon chacune croirait l'autre disparue.
    scope = str(root.resolve())

    async with ragdb.pool().acquire() as connection:
        known = await _existing(connection, scope)

    seen: set[str] = set()
    pending: list[_Pending] = []

    for path in scan(root):
        relative = path.relative_to(root)
        source = relative.as_posix()
        seen.add(source)
        groups, note = _groups_for(relative)

        try:
            raw = path.read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
        except OSError as error:
            report.documents.append(DocumentReport(source, "erreur", message=str(error)))
            continue

        previous = known.get(source)
        # Le modèle, la dimension ET le profil d'indexation font partie de
        # l'identité de l'index : un document inchangé mais vectorisé autrement
        # doit être refait, sinon deux façons de découper et de contextualiser
        # cohabitent dans le même espace vectoriel sans que rien ne le signale.
        if (
            previous
            and previous["sha256"] == digest
            and previous["embed_model"] == model
            and previous["embed_dim"] == dim
            and previous["index_profile"] == profil
        ):
            report.documents.append(DocumentReport(source, "inchangé", groups=groups))
            continue

        try:
            document = await parse.read_document_async(path, ocr_config)
        except parse.ParseError as error:
            report.documents.append(DocumentReport(source, "erreur", message=str(error)))
            continue

        fragments = chunker.chunk_document(
            document.text,
            max_tokens=reglages.chunk_tokens,
            overlap_paragraphs=reglages.chunk_overlap,
        )
        if not fragments:
            report.documents.append(
                DocumentReport(source, "erreur", message="aucun fragment produit")
            )
            continue

        contexts = (
            [build_context(document.meta, document.title, fragment) for fragment in fragments]
            if reglages.contextual
            else [""] * len(fragments)
        )

        pending.append(
            _Pending(
                _document_id(scope, source),
                scope,
                source,
                document.title,
                digest,
                groups,
                fragments,
                document.meta,
                contexts,
            )
        )
        report.documents.append(
            DocumentReport(source, "indexé", len(fragments), groups, note)
        )

    total_chunks = sum(len(item.chunks) for item in pending)
    # Le décompte porte sur ce qui part RÉELLEMENT au fournisseur, préfixe compris :
    # l'indexation contextuelle est facturée, et l'annoncer après coup serait une
    # mauvaise surprise sur un gros corpus.
    report.tokens = embed.estimate(
        [texte for item in pending for texte in _to_embed(item)]
    )

    # Le plafond s'applique ici : le découpage est gratuit, la vectorisation non.
    if total_chunks > cap:
        raise BudgetExceeded(
            f"{total_chunks} fragments à vectoriser pour un plafond de {cap}. "
            f"Relevez RAG_MAX_CHUNKS_PER_RUN si c'est voulu, ou réduisez le corpus."
        )

    # Documents disparus du disque : leur index doit disparaître aussi, sinon la
    # recherche cite des sources qui n'existent plus.
    obsolete = [source for source in known if source not in seen] if prune else []

    # Garde-fou de rayon d'action, appliqué avant toute écriture. Supprimer la
    # quasi-totalité de l'index n'est presque jamais l'intention : c'est le
    # symptôme d'un `--corpus` qui pointe au mauvais endroit, ou d'un montage
    # vide. Le cas légitime existe (on a vraiment vidé le corpus) — il demande
    # alors `--force`, c'est-à-dire une seconde de réflexion.
    if obsolete and known and not force:
        ratio = len(obsolete) / len(known)
        if ratio > MAX_PRUNE_RATIO:
            raise PruneTooLarge(
                f"cette synchronisation supprimerait {len(obsolete)} document(s) sur "
                f"{len(known)} ({ratio:.0%} de l'index). Vérifiez que --corpus pointe "
                f"bien sur la racine du corpus ({root}). Si c'est voulu : --force."
            )

    for source in obsolete:
        report.documents.append(DocumentReport(source, "supprimé"))

    if dry_run:
        return report

    for item in pending:
        vectors = await embed.embed_documents(_to_embed(item))
        await _write(item, vectors, model, dim, profil)

    if obsolete:
        async with ragdb.pool().acquire() as connection:
            await connection.execute(
                "DELETE FROM rag_documents WHERE root = $1 AND source = ANY($2::text[])",
                scope,
                obsolete,
            )

    logger.info(
        "ingestion terminée",
        extra={
            "indexés": report.indexed,
            "inchangés": report.unchanged,
            "supprimés": report.removed,
            "erreurs": report.failed,
            "fragments": report.chunks,
        },
    )
    return report


def _to_embed(item: _Pending) -> list[str]:
    """Ce qui part réellement au vectoriseur : le fragment, précédé de son contexte.

    Le contexte est joint au texte pour la SEULE vectorisation. Ce qui est stocké
    dans `rag_chunks.text`, et donc ce qui sera cité à l'utilisateur, reste le
    fragment d'origine — un extrait servi avec son en-tête technique collé devant
    serait illisible dans une réponse.
    """
    return [
        f"{contexte}\n\n{fragment.text}" if contexte else fragment.text
        for fragment, contexte in zip(item.chunks, item.contexts, strict=True)
    ]


async def _write(
    item: _Pending,
    vectors: list[list[float]],
    model: str,
    dim: int,
    profil: str,
) -> None:
    """Remplace un document et ses fragments, en une transaction.

    Suppression puis réinsertion plutôt que mise à jour fragment par fragment :
    un document réécrit n'a pas le même nombre de fragments, et une réécriture
    partielle laisserait la queue de l'ancienne version dans l'index.
    """
    # Les métadonnées du document sont recopiées sur chaque fragment. C'est de la
    # dénormalisation assumée : le filtrage métier s'applique dans la MÊME requête
    # que la recherche vectorielle, et une jointure sur `rag_documents` empêcherait
    # l'index HNSW de servir — la recherche deviendrait un parcours complet.
    meta_json = json.dumps(item.meta, ensure_ascii=False)

    async with ragdb.pool().acquire() as connection, connection.transaction():
        await connection.execute("DELETE FROM rag_documents WHERE id = $1", item.id)
        await connection.execute(
            """
            INSERT INTO rag_documents
                (id, root, source, title, sha256, acl, embed_model, embed_dim,
                 chunk_count, meta, index_profile)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb, $11)
            """,
            item.id,
            item.root,
            item.source,
            item.title,
            item.sha256,
            item.groups,
            model,
            dim,
            len(item.chunks),
            meta_json,
            profil,
        )
        await connection.executemany(
            """
            INSERT INTO rag_chunks (document_id, ord, text, acl, embedding, meta, context)
            VALUES ($1, $2, $3, $4, $5::vector, $6::jsonb, $7)
            """,
            [
                (
                    item.id,
                    index,
                    fragment.text,
                    item.groups,
                    ragdb.to_vector_literal(vector),
                    meta_json,
                    contexte,
                )
                for index, (fragment, vector, contexte) in enumerate(
                    zip(item.chunks, vectors, item.contexts, strict=True)
                )
            ],
        )
