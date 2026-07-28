"""RAG : découpage, ingestion idempotente, et filtrage des permissions.

Les tests de recherche tapent la vraie base vectorielle (`ragdb`) : c'est du
pgvector réel, avec l'index HNSW, le `tsvector` français et l'intersection de
tableaux qui porte les ACL. Un faux dépôt en mémoire prouverait seulement que le
faux dépôt filtre.

Le fournisseur d'embeddings est `hash` : déterministe, local, sans clé. Il permet
d'exercer toute la mécanique. **Il ne prouve rien sur la pertinence** — ça, c'est
le rôle de `make eval` avec de vrais vecteurs.

Le corpus de test vit dans un `tmp_path` et ses groupes sont préfixés, pour ne
jamais toucher à l'index de démonstration déjà en place.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

from agent.core.graph import build_graph
from agent.core.rag import chunk, ingest, parse, retrieve
from agent.core.tools.rag import document_search
from agent.core.users import User
from agent.infra import ragdb
from agent.protocol.stream import ui_message_stream
from tests.fakes import FakeToolCallingModel, tool_call

PUBLIC = "pytest-public"
PRIVE = "pytest-finance"


# --- Découpage : fonctions pures, aucune base ---------------------------------


def test_le_decoupage_respecte_les_paragraphes():
    text = "\n\n".join(f"Paragraphe numéro {index}." for index in range(20))
    fragments = chunk.chunk_text(text, max_tokens=20, min_tokens=1, overlap_paragraphs=0)

    assert len(fragments) > 1
    # Aucun fragment ne commence ni ne finit au milieu d'un paragraphe.
    for fragment in fragments:
        assert fragment.startswith("Paragraphe")
        assert fragment.endswith(".")


def test_le_recouvrement_reprend_le_paragraphe_precedent():
    text = "\n\n".join(f"Bloc {index} " + "x" * 60 for index in range(6))
    sans = chunk.chunk_text(text, max_tokens=30, min_tokens=1, overlap_paragraphs=0)
    avec = chunk.chunk_text(text, max_tokens=30, min_tokens=1, overlap_paragraphs=1)

    # Le recouvrement duplique du contenu, donc produit au moins autant de
    # fragments — et la fin de l'un se retrouve au début du suivant.
    assert len(avec) >= len(sans)
    assert avec[0].split("\n\n")[-1] == avec[1].split("\n\n")[0]


def test_un_paragraphe_geant_est_coupe_sur_les_phrases():
    paragraph = " ".join(f"Voici la phrase numéro {index}." for index in range(200))
    fragments = chunk.chunk_text(paragraph, max_tokens=50, min_tokens=1)

    assert len(fragments) > 1
    assert all(fragment.strip() for fragment in fragments)


def test_une_queue_trop_courte_est_recollee():
    text = "\n\n".join(["A" * 400, "B" * 400, "court"])
    fragments = chunk.chunk_text(text, max_tokens=100, min_tokens=40, overlap_paragraphs=0)
    assert "court" in fragments[-1]
    assert fragments[-1] != "court"


def test_un_document_vide_est_signale(tmp_path: Path):
    vide = tmp_path / "vide.md"
    vide.write_text("   \n\n  ", encoding="utf-8")
    with pytest.raises(parse.ParseError):
        parse.read_document(vide)


def test_le_titre_markdown_est_extrait(tmp_path: Path):
    document = tmp_path / "note.md"
    document.write_text("# Le vrai titre\n\nDu texte.", encoding="utf-8")
    _, title = parse.read_document(document)
    assert title == "Le vrai titre"


def test_le_html_perd_ses_balises_et_ses_scripts(tmp_path: Path):
    page = tmp_path / "page.html"
    page.write_text(
        "<html><head><title>Ma page</title></head>"
        "<body><p>Texte visible</p><script>var secret = 1;</script></body></html>",
        encoding="utf-8",
    )
    text, title = parse.read_document(page)
    assert title == "Ma page"
    assert "Texte visible" in text
    assert "secret" not in text


# --- Ingestion et recherche : base vectorielle réelle -------------------------


@pytest.fixture
async def corpus(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Un corpus jetable, indexé dans la vraie base, nettoyé après le test."""
    monkeypatch.setenv("EMBEDDING_PROVIDER", "hash")

    (tmp_path / PUBLIC).mkdir()
    (tmp_path / PRIVE).mkdir()
    (tmp_path / PUBLIC / "teletravail.md").write_text(
        "# Télétravail\n\nLe télétravail est ouvert trois jours par semaine.\n\n"
        "Le mardi est un jour de présence commune sur site.",
        encoding="utf-8",
    )
    (tmp_path / PRIVE / "budget.md").write_text(
        "# Budget confidentiel\n\nL'enveloppe outillage est fixée à 185000 euros.\n\n"
        "Toute dépense au-dessus de 5000 euros demande un double visa.",
        encoding="utf-8",
    )

    try:
        await ragdb.connect()
    except Exception as error:  # pragma: no cover
        pytest.skip(f"Base vectorielle injoignable : {error}")

    await _cleanup()
    yield tmp_path
    await _cleanup()
    await ragdb.disconnect()


async def _cleanup() -> None:
    await ragdb.pool().execute(
        "DELETE FROM rag_documents WHERE acl && $1::text[]", [PUBLIC, PRIVE]
    )


async def test_ingerer_deux_fois_ne_reindexe_rien(corpus: Path):
    """Le critère du lot : `make ingest` est sûr à relancer.

    Zéro token à la seconde passe — donc zéro appel facturé au fournisseur
    d'embeddings.
    """
    first = await ingest.ingest(corpus)
    assert first.indexed == 2
    assert first.chunks >= 2
    assert first.tokens > 0

    second = await ingest.ingest(corpus)
    assert second.indexed == 0
    assert second.unchanged == 2
    assert second.tokens == 0


async def test_un_document_modifie_est_reindexe_sans_doublon(corpus: Path):
    await ingest.ingest(corpus)
    document = corpus / PUBLIC / "teletravail.md"
    document.write_text(
        "# Télétravail\n\nLe télétravail passe à quatre jours par semaine.",
        encoding="utf-8",
    )

    report = await ingest.ingest(corpus)
    assert report.indexed == 1

    rows = await ragdb.pool().fetch(
        "SELECT count(*) AS total FROM rag_documents WHERE source = $1",
        f"{PUBLIC}/teletravail.md",
    )
    assert rows[0]["total"] == 1

    passages = await retrieve.search("télétravail par semaine", [PUBLIC])
    assert passages
    assert all("trois jours" not in passage.text for passage in passages)


async def test_un_document_supprime_disparait_de_l_index(corpus: Path):
    await ingest.ingest(corpus)
    (corpus / PRIVE / "budget.md").unlink()

    report = await ingest.ingest(corpus)
    assert report.removed == 1
    assert await retrieve.search("enveloppe outillage", [PRIVE]) == []


async def test_indexer_un_autre_corpus_ne_touche_pas_au_premier(corpus: Path):
    """La régression qui a vidé l'index de démonstration.

    `ingest()` synchronise : ce qui a disparu du disque disparaît de l'index. Sans
    périmètre, indexer un second dossier faisait passer TOUS les documents du
    premier pour supprimés — c'est ainsi que les tests effaçaient le corpus de
    démonstration à chaque exécution.
    """
    await ingest.ingest(corpus)

    autre = corpus.parent / "autre-corpus"
    (autre / PUBLIC).mkdir(parents=True, exist_ok=True)
    (autre / PUBLIC / "autre-note.md").write_text(
        "# Autre note\n\nUn document venu d'un corpus totalement différent.",
        encoding="utf-8",
    )

    report = await ingest.ingest(autre)
    assert report.indexed == 1
    assert report.removed == 0, "un autre corpus a été considéré comme supprimé"

    # Le premier corpus est intact.
    assert await retrieve.search("télétravail présence commune", [PUBLIC])
    assert await retrieve.search("enveloppe outillage", [PRIVE])


async def test_une_suppression_massive_demande_une_confirmation(corpus: Path):
    """Le cas « --corpus pointe au mauvais endroit », ou montage vide.

    Vider l'index est une opération légitime, mais jamais un effet de bord : elle
    doit être demandée.
    """
    await ingest.ingest(corpus)
    for document in corpus.rglob("*.md"):
        document.unlink()

    with pytest.raises(ingest.PruneTooLarge):
        await ingest.ingest(corpus)

    # Rien n'a été supprimé tant que ce n'est pas confirmé.
    assert await retrieve.search("télétravail", [PUBLIC])

    report = await ingest.ingest(corpus, force=True)
    assert report.removed == 2
    assert await retrieve.search("télétravail", [PUBLIC]) == []


async def test_le_plafond_de_fragments_bloque_avant_toute_vectorisation(corpus: Path):
    with pytest.raises(ingest.BudgetExceeded):
        await ingest.ingest(corpus, max_chunks=1)

    # Rien n'a été écrit : le garde-fou tombe avant l'appel au fournisseur.
    total = await ragdb.pool().fetchval(
        "SELECT count(*) FROM rag_documents WHERE acl && $1::text[]", [PUBLIC, PRIVE]
    )
    assert total == 0


async def test_les_permissions_viennent_de_l_arborescence(corpus: Path):
    await ingest.ingest(corpus)
    rows = await ragdb.pool().fetch(
        "SELECT source, acl FROM rag_documents WHERE acl && $1::text[] ORDER BY source",
        [PUBLIC, PRIVE],
    )
    assert [(row["source"], list(row["acl"])) for row in rows] == [
        (f"{PRIVE}/budget.md", [PRIVE]),
        (f"{PUBLIC}/teletravail.md", [PUBLIC]),
    ]


# --- Le critère du lot 3 : jamais un fragment hors des droits -----------------


async def test_un_document_hors_groupes_n_est_jamais_rendu(corpus: Path):
    await ingest.ingest(corpus)

    # La question porte explicitement sur le document confidentiel.
    passages = await retrieve.search("enveloppe outillage 185000 euros", [PUBLIC])
    assert all(PRIVE not in passage.source for passage in passages)
    assert all("185000" not in passage.text for passage in passages)

    # Le même utilisateur, avec le bon groupe, le trouve.
    autorise = await retrieve.search("enveloppe outillage 185000 euros", [PRIVE])
    assert autorise
    assert any("185000" in passage.text for passage in autorise)


async def test_sans_aucun_groupe_la_recherche_ne_rend_rien(corpus: Path):
    await ingest.ingest(corpus)
    assert await retrieve.search("télétravail", []) == []


async def test_la_recherche_hybride_trouve_un_montant_exact(corpus: Path):
    """Ce que la recherche dense seule raterait : un nombre.

    Le vectoriseur `hash` ne sait rien de la sémantique, donc ce test mesure
    surtout que la branche lexicale est bien câblée et fusionnée.
    """
    await ingest.ingest(corpus)
    passages = await retrieve.search("185000", [PRIVE])
    assert passages
    assert passages[0].sparse_rank is not None


async def test_la_citation_est_verifiable(corpus: Path):
    await ingest.ingest(corpus)
    passages = await retrieve.search("présence commune", [PUBLIC])
    assert passages
    assert passages[0].citation.startswith(f"{PUBLIC}/teletravail.md#")


# --- L'outil : le LLM ne choisit pas ses propres droits -----------------------


async def test_l_outil_refuse_de_chercher_sans_identite(corpus: Path):
    """Fermé par défaut : pas d'identité câblée = pas de recherche du tout."""
    await ingest.ingest(corpus)
    result = json.loads(await document_search.ainvoke({"query": "télétravail"}))
    assert "error" in result


async def test_l_outil_filtre_sur_les_groupes_de_la_session(corpus: Path):
    """La question vise le document confidentiel ; l'utilisateur ne l'a pas.

    Ce qu'on vérifie est bien l'absence de fuite, **pas** l'absence de résultat :
    une recherche dense rend toujours ses plus proches voisins, pertinents ou
    non. Ici l'utilisateur récupère donc le document public, ce qui est correct
    — et ce qui rappelle qu'il n'y a aucun seuil de pertinence (cf. le module
    `retrieve`, et l'évaluation qui sert justement à le mesurer).
    """
    await ingest.ingest(corpus)

    interdit = json.loads(
        await document_search.ainvoke(
            {"query": "enveloppe outillage 185000 euros"},
            config={"configurable": {"user_groups": [PUBLIC]}},
        )
    )
    citations = [result["citation"] for result in interdit["results"]]
    assert all(not citation.startswith(PRIVE) for citation in citations)
    assert "185000" not in json.dumps(interdit, ensure_ascii=False)

    autorise = json.loads(
        await document_search.ainvoke(
            {"query": "enveloppe outillage 185000 euros"},
            config={"configurable": {"user_groups": [PRIVE]}},
        )
    )
    assert autorise["results"]
    assert autorise["results"][0]["citation"].startswith(PRIVE)


async def test_aucun_document_accessible_donne_une_note_prudente(corpus: Path):
    """Un utilisateur sans aucun droit : la note ne doit pas nier l'existence."""
    await ingest.ingest(corpus)
    vide = json.loads(
        await document_search.ainvoke(
            {"query": "enveloppe outillage"},
            config={"configurable": {"user_groups": ["groupe-sans-aucun-document"]}},
        )
    )
    assert vide["results"] == []
    # « rien d'accessible », jamais « rien n'existe ».
    assert "accessible" in vide["note"]


async def test_le_modele_ne_voit_pas_le_parametre_d_identite():
    """`config` est injecté, pas exposé : le LLM ne peut pas le remplir."""
    schema = document_search.args_schema.model_json_schema()
    assert "query" in schema["properties"]
    assert "config" not in schema["properties"]


# --- Le chemin complet : session → graphe → ToolNode → outil ------------------


async def _run_chat(user: User, question: str) -> list[dict]:
    """Exécute le vrai chemin de `/api/chat`, seul le LLM étant remplacé."""
    model = FakeToolCallingModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[tool_call("document_search", {"query": question}, "d1")],
            ),
            AIMessage(content="Voici ce que j'ai trouvé."),
        ]
    )

    chunks: list[dict] = []
    stream = ui_message_stream(
        [{"id": "u1", "role": "user", "parts": [{"type": "text", "text": question}]}],
        graph=build_graph(model),
        user=user,
    )
    async for line in stream:
        payload = line.removeprefix("data: ").strip()
        if payload and payload != "[DONE]":
            chunks.append(json.loads(payload))
    return chunks


def _tool_output(chunks: list[dict]) -> str:
    return next(
        chunk["output"] for chunk in chunks if chunk["type"] == "tool-output-available"
    )


async def test_l_identite_traverse_le_graphe_jusqu_a_l_outil(corpus: Path):
    """Le test qui compte : le filtrage tient sur le chemin réel, pas seulement
    quand on appelle l'outil à la main.

    Entre la session et la requête SQL il y a `ui_message_stream`, le
    `configurable` de LangGraph, le nœud agent et le `ToolNode`. Un maillon qui
    perd les groupes en route ne se verrait dans aucun des tests précédents — et
    se traduirait par une recherche sans filtre.
    """
    await ingest.ingest(corpus)
    question = "Quelle est l'enveloppe outillage de 185000 euros ?"

    sans_droit = User(id="u1", email="a@b.c", groups=[PUBLIC])
    sortie = _tool_output(await _run_chat(sans_droit, question))
    assert PRIVE not in sortie
    assert "185000" not in sortie

    avec_droit = User(id="u2", email="d@e.f", groups=[PRIVE])
    sortie = _tool_output(await _run_chat(avec_droit, question))
    assert PRIVE in sortie


async def test_un_run_sans_utilisateur_ne_lit_aucun_document(corpus: Path):
    """Fermé par défaut jusqu'au bout : `user=None` ne veut pas dire « tout voir »."""
    await ingest.ingest(corpus)
    sortie = _tool_output(await _run_chat(None, "enveloppe outillage"))
    assert PRIVE not in sortie
    assert PUBLIC not in sortie
