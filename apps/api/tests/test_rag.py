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
from agent.core.rag import chunk, hyde, ingest, llm, parse, query, retrieve
from agent.core.rag import config as ragconfig
from agent.core.rag.config import RagConfig
from agent.core.tools.rag import document_search
from agent.core.users import User
from agent.infra import ragdb
from agent.protocol.stream import ui_message_stream
from tests.fakes import FakeToolCallingModel, tool_call

PUBLIC = "pytest-public"
PRIVE = "pytest-finance"


# --- Découpage : fonctions pures, aucune base ---------------------------------


async def test_le_llm_auxiliaire_ne_fuite_pas_dans_le_stream(monkeypatch):
    class FakeAuxiliaryModel:
        config = None

        async def ainvoke(self, _messages, config=None):
            self.config = config
            return AIMessage(content="résultat interne")

    fake = FakeAuxiliaryModel()
    monkeypatch.setattr(llm, "model", lambda **_kwargs: fake)

    assert await llm.ask("système", "question") == "résultat interne"
    assert fake.config["callbacks"] == []
    assert llm.INTERNAL_STREAM_TAG in fake.config["tags"]


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
    assert parse.read_document(document).title == "Le vrai titre"


def test_le_html_perd_ses_balises_et_ses_scripts(tmp_path: Path):
    page = tmp_path / "page.html"
    page.write_text(
        "<html><head><title>Ma page</title></head>"
        "<body><p>Texte visible</p><script>var secret = 1;</script></body></html>",
        encoding="utf-8",
    )
    document = parse.read_document(page)
    assert document.title == "Ma page"
    assert "Texte visible" in document.text
    assert "secret" not in document.text


# --- Front-matter : métadonnées extraites, jamais vectorisées -----------------


def test_le_front_matter_est_retire_du_texte_indexe(tmp_path: Path):
    """Le piège : un en-tête laissé dans le texte se retrouve dans le vecteur.

    La recherche lexicale se mettrait alors à répondre sur des clés YAML, et le
    fragment dilué perdrait en pertinence — deux dégradations pour zéro gain.
    """
    document = tmp_path / "contrat.md"
    document.write_text(
        '---\ntype: "conditions_particulieres"\nclient: "bativert"\n'
        'reference: "MRP-2025-0342"\n---\n\n'
        "# Conditions particulières\n\nLa franchise est de 900 euros.",
        encoding="utf-8",
    )
    lu = parse.read_document(document)

    assert lu.meta == {
        "type": "conditions_particulieres",
        "client": "bativert",
        "reference": "MRP-2025-0342",
    }
    assert "conditions_particulieres" not in lu.text
    assert "bativert" not in lu.text
    # Le titre reste détecté : le front-matter est retiré AVANT sa recherche.
    assert lu.title == "Conditions particulières"


def test_un_champ_hors_liste_blanche_n_entre_pas_dans_l_index(tmp_path: Path):
    """Déposer un fichier ne doit pas suffire à injecter une clé arbitraire."""
    document = tmp_path / "note.md"
    document.write_text(
        '---\ntype: "note"\nacl: "direction"\nsecret_interne: "oui"\n---\n\nDu texte.',
        encoding="utf-8",
    )
    meta = parse.read_document(document).meta

    assert meta == {"type": "note"}
    # `acl` en particulier : les permissions viennent de l'arborescence, jamais
    # d'un champ que l'auteur du document contrôle.
    assert "acl" not in meta


def test_un_front_matter_illisible_n_empeche_pas_l_indexation(tmp_path: Path):
    """Le texte reste parfaitement exploitable : refuser le document coûterait
    plus que de perdre ses métadonnées."""
    document = tmp_path / "casse.md"
    document.write_text(
        "---\ntype: [ceci n'est pas: du YAML valide\n---\n\nDu texte lisible.",
        encoding="utf-8",
    )
    lu = parse.read_document(document)

    assert lu.meta == {}
    assert "Du texte lisible." in lu.text


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
    assert "courtage ou d'assurance" in document_search.description
    assert "EN PREMIER" in document_search.description


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


# --- Métadonnées, filtrage métier et profil d'index ---------------------------


@pytest.fixture
async def corpus_meta(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Deux contrats de deux clients, même produit — le piège du corpus réel.

    C'est la configuration où un RAG sans discernement se trompe : les deux
    documents disent la même chose, dans les mêmes termes, pour deux clients
    différents. Rien dans le texte ne permet de trancher, tout dans les
    métadonnées le permet.
    """
    monkeypatch.setenv("EMBEDDING_PROVIDER", "hash")
    (tmp_path / PUBLIC).mkdir()

    for client, franchise, reference in (
        ("bativert", "900", "MRP-2025-0342"),
        ("tourneix", "1500", "MRP-2023-0090"),
    ):
        # La référence figure DANS le corps, comme dans un vrai contrat, et pas
        # seulement en front-matter : c'est justement ce que la branche lexicale
        # doit savoir retrouver et que la recherche dense rate.
        (tmp_path / PUBLIC / f"contrat-{client}.md").write_text(
            f'---\ntype: "conditions_particulieres"\nclient: "{client}"\n'
            f'produit: "mrp"\nreference: "{reference}"\n---\n\n'
            f"# Conditions particulières {reference}\n\n"
            f"La franchise contractuelle est de {franchise} euros par sinistre.\n\n"
            f"Elle s'applique quelle que soit la garantie mise en jeu.",
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


async def test_les_metadonnees_du_front_matter_arrivent_sur_les_fragments(corpus_meta: Path):
    """Recopiées sur CHAQUE fragment, pas seulement sur le document.

    C'est ce qui permet de filtrer dans la même requête que la recherche
    vectorielle ; une jointure empêcherait l'index HNSW de servir.
    """
    await ingest.ingest(corpus_meta)
    rows = await ragdb.pool().fetch(
        "SELECT meta FROM rag_chunks WHERE acl && $1::text[]", [PUBLIC]
    )
    assert rows
    for row in rows:
        meta = json.loads(row["meta"]) if isinstance(row["meta"], str) else row["meta"]
        assert meta["type"] == "conditions_particulieres"
        assert meta["client"] in {"bativert", "tourneix"}


async def test_le_filtre_metier_isole_le_bon_client(corpus_meta: Path):
    """Le cas que le texte seul ne peut pas trancher."""
    await ingest.ingest(corpus_meta)

    sans_filtre = await retrieve.search("franchise contractuelle par sinistre", [PUBLIC])
    assert len({p.meta["client"] for p in sans_filtre}) == 2, "les deux clients remontent"

    filtre = await retrieve.search(
        "franchise contractuelle par sinistre", [PUBLIC], filters={"client": "bativert"}
    )
    assert filtre
    assert all(passage.meta["client"] == "bativert" for passage in filtre)
    assert all("1500" not in passage.text for passage in filtre)


async def test_le_filtre_metier_ne_contourne_jamais_les_acl(corpus_meta: Path):
    """Le filtre restreint, il n'autorise rien.

    Le confondre avec une protection serait le début d'une fuite : on finirait
    par croire qu'un filtre bien posé suffit, et par relâcher les groupes.
    """
    await ingest.ingest(corpus_meta)
    resultats = await retrieve.search(
        "franchise", ["groupe-qui-n-a-aucun-droit"], filters={"client": "bativert"}
    )
    assert resultats == []


async def test_changer_la_taille_des_fragments_reindexe(corpus_meta: Path):
    """Le profil d'index fait partie de l'identité des vecteurs.

    Sans ce contrôle, deux découpages cohabiteraient dans le même espace
    vectoriel — et la comparaison de deux réglages mesurerait un mélange des
    deux au lieu de l'un ou de l'autre.
    """
    premier = await ingest.ingest(corpus_meta)
    assert premier.indexed == 2

    # Même corpus, même modèle : sans changement de profil, rien à refaire.
    assert (await ingest.ingest(corpus_meta)).indexed == 0

    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("RAG_CHUNK_TOKENS", "12")
        refait = await ingest.ingest(corpus_meta)

    assert refait.indexed == 2, "le changement de découpage n'a pas déclenché de réindexation"
    assert refait.chunks > premier.chunks


async def test_l_indexation_contextuelle_ne_pollue_pas_le_texte_restitue(corpus_meta: Path):
    """Le contexte part dans le VECTEUR, jamais dans l'extrait cité.

    Un extrait servi avec son en-tête technique collé devant serait illisible
    dans une réponse, et le lecteur ne saurait pas ce qui vient du document.
    """
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("RAG_CONTEXTUAL", "1")
        await ingest.ingest(corpus_meta)

    rows = await ragdb.pool().fetch(
        "SELECT text, context FROM rag_chunks WHERE acl && $1::text[]", [PUBLIC]
    )
    assert rows
    assert any(row["context"] for row in rows), "aucun contexte n'a été construit"
    for row in rows:
        assert "conditions particulieres" not in row["text"].lower()
        assert row["context"] not in row["text"]

    passages = await retrieve.search("franchise", [PUBLIC])
    assert passages
    assert all("client:" not in passage.text for passage in passages)


# --- Voisinage (small-to-big) -------------------------------------------------


@pytest.fixture
async def corpus_long(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Un document dont la réponse est coupée en deux par le découpage."""
    monkeypatch.setenv("EMBEDDING_PROVIDER", "hash")
    monkeypatch.setenv("RAG_CHUNK_TOKENS", "12")
    (tmp_path / PUBLIC).mkdir()
    (tmp_path / PUBLIC / "avenant.md").write_text(
        "# Avenant\n\n"
        "Le present avenant modifie la garantie vol du contrat.\n\n"
        "La franchise applicable est ramenee a 750 euros.\n\n"
        "Cette disposition prend effet au premier aout.",
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


async def test_les_voisins_recollent_une_reponse_coupee(corpus_long: Path):
    await ingest.ingest(corpus_long)
    total = await ragdb.pool().fetchval(
        "SELECT count(*) FROM rag_chunks WHERE acl && $1::text[]", [PUBLIC]
    )
    assert total > 2, "le corpus de test doit produire plusieurs fragments"

    seul = await retrieve.search("avenant garantie vol", [PUBLIC], config=RagConfig(top_k=1))
    elargi = await retrieve.search(
        "avenant garantie vol", [PUBLIC], config=RagConfig(top_k=1, neighbours=1)
    )

    assert seul and elargi
    assert not seul[0].expanded
    assert elargi[0].expanded
    assert len(elargi[0].text) > len(seul[0].text)
    # La citation ne bouge pas : c'est toujours le fragment retrouvé qui est cité.
    assert elargi[0].citation == seul[0].citation


# --- Reclassement et abstention -----------------------------------------------
#
# Le modèle auxiliaire est remplacé par une fonction : ces tests portent sur le
# CÂBLAGE — le seuil est-il appliqué, la panne est-elle absorbée, l'outil
# distingue-t-il les trois issues — et pas sur la qualité d'un LLM, qui ne se
# teste pas unitairement et se mesure avec `rag eval`.


def _faux_reclasseur(notes: dict[int, float]):
    """Rend un reclasseur qui attribue `notes` par position (1-indexée)."""

    async def _ask(system: str, user: str, **kwargs) -> str:
        import re as _re

        positions = [int(n) for n in _re.findall(r"^\[(\d+)\]$", user, _re.MULTILINE)]
        return json.dumps(
            [{"id": p, "score": notes.get(p, 0.0)} for p in positions]
        )

    return _ask


async def test_le_seuil_de_reclassement_declenche_une_abstention(
    corpus: Path, monkeypatch: pytest.MonkeyPatch
):
    """La panne que le rappel ne voit pas.

    Sans seuil, une question sans réponse rend quand même les plus proches
    voisins. Avec seuil, elle rend une abstention — et le motif, pour que
    l'utilisateur sache que la recherche a bien eu lieu.
    """
    await ingest.ingest(corpus)
    monkeypatch.setattr(llm, "ask", _faux_reclasseur({}))  # tout à 0

    resultat = await retrieve.search_detailed(
        "télétravail",
        [PUBLIC],
        config=RagConfig(rerank="llm", min_rerank_score=4.0),
    )

    assert resultat.abstained
    assert resultat.passages == []
    assert resultat.candidates_seen > 0, "des candidats existaient bien"
    assert "seuil" in (resultat.reason or "")


async def test_sans_seuil_la_recherche_repond_toujours(
    corpus: Path, monkeypatch: pytest.MonkeyPatch
):
    """Le comportement de référence, celui qu'on cherche justement à corriger."""
    await ingest.ingest(corpus)
    monkeypatch.setattr(llm, "ask", _faux_reclasseur({}))

    resultat = await retrieve.search_detailed(
        "télétravail", [PUBLIC], config=RagConfig(rerank="llm")
    )

    assert not resultat.abstained
    assert resultat.passages, "sans seuil, les plus proches voisins sortent quoi qu'il arrive"


async def test_le_reclassement_remonte_le_passage_le_mieux_note(
    corpus_meta: Path, monkeypatch: pytest.MonkeyPatch
):
    """Deux fragments également plausibles pour la fusion, départagés au fond.

    C'est précisément ce que la RRF ne sait pas faire : elle ordonne sur des
    rangs, sans avoir lu ni la question ni les passages.
    """
    await ingest.ingest(corpus_meta)
    # Le deuxième candidat de la fusion devient le premier après reclassement.
    monkeypatch.setattr(llm, "ask", _faux_reclasseur({1: 2.0, 2: 9.0}))

    avant = await retrieve.search("franchise contractuelle par sinistre", [PUBLIC])
    apres = await retrieve.search_detailed(
        "franchise contractuelle par sinistre", [PUBLIC], config=RagConfig(rerank="llm")
    )

    assert len(avant) >= 2, "il faut au moins deux candidats pour que l'ordre change"
    assert apres.reranked
    assert apres.passages[0].chunk_id == avant[1].chunk_id
    assert apres.passages[0].rerank_score == 9.0


async def test_un_reclasseur_en_panne_conserve_la_recherche(
    corpus: Path, monkeypatch: pytest.MonkeyPatch
):
    """Échec OUVERT, à l'inverse de la règle des ACL.

    Dégrader la pertinence est un désagrément ; rendre une liste vide parce
    qu'un modèle auxiliaire ne répond pas est une panne. Et le drapeau
    `reranked` reste faux, pour qu'un seuil ne s'applique pas à des notes qui
    n'existent pas.
    """
    await ingest.ingest(corpus)

    async def _muet(system: str, user: str, **kwargs) -> None:
        return None

    monkeypatch.setattr(llm, "ask", _muet)

    resultat = await retrieve.search_detailed(
        "télétravail",
        [PUBLIC],
        config=RagConfig(rerank="llm", min_rerank_score=9.0),
    )

    assert not resultat.reranked
    assert not resultat.abstained
    assert resultat.passages


async def test_l_outil_distingue_abstention_et_absence_de_droits(
    corpus: Path, monkeypatch: pytest.MonkeyPatch
):
    """Trois issues, trois messages. C'est ce que le modèle doit pouvoir lire.

    Confondre « aucun droit » et « rien ne répond » conduirait le modèle à
    affirmer qu'un document n'existe pas alors qu'il est seulement hors de
    portée de l'utilisateur.
    """
    await ingest.ingest(corpus)
    identite = {"configurable": {"user_groups": [PUBLIC]}}

    monkeypatch.setattr(llm, "ask", _faux_reclasseur({}))
    monkeypatch.setenv("RAG_RERANK", "llm")
    monkeypatch.setenv("RAG_MIN_RERANK_SCORE", "4")
    abstention = json.loads(
        await document_search.ainvoke({"query": "télétravail"}, config=identite)
    )
    assert abstention["results"] == []
    assert "aucun ne répond" in abstention["note"]
    assert abstention["diagnostic"]

    monkeypatch.delenv("RAG_RERANK")
    monkeypatch.delenv("RAG_MIN_RERANK_SCORE")
    sans_droit = json.loads(
        await document_search.ainvoke(
            {"query": "télétravail"},
            config={"configurable": {"user_groups": ["groupe-vide"]}},
        )
    )
    assert sans_droit["results"] == []
    assert "accessible" in sans_droit["note"]
    assert "diagnostic" not in sans_droit


# --- Expansion de requête -----------------------------------------------------


async def test_l_expansion_garde_toujours_la_question_d_origine(
    monkeypatch: pytest.MonkeyPatch,
):
    """Une reformulation reste une supposition du modèle.

    Si elle dérive, la question telle qu'elle a été posée doit rester dans le
    lot pour rattraper le coup — donc jamais de remplacement, seulement un ajout.
    """

    async def _ask(system: str, user: str, **kwargs) -> str:
        return "1. Quel est le montant de la franchise ?\n- Reste à charge assuré\n\n"

    monkeypatch.setattr(llm, "ask", _ask)
    formulations = await query.expand("il reste combien à leur charge ?", 3)

    assert formulations[0] == "il reste combien à leur charge ?"
    assert "Quel est le montant de la franchise ?" in formulations
    # Numérotation et puces retirées : elles pollueraient la recherche lexicale.
    assert not any(f.startswith(("1.", "-")) for f in formulations)


async def test_l_expansion_indisponible_ne_bloque_pas_la_recherche(
    monkeypatch: pytest.MonkeyPatch,
):
    async def _muet(system: str, user: str, **kwargs) -> None:
        return None

    monkeypatch.setattr(llm, "ask", _muet)
    assert await query.expand("ma question", 3) == ["ma question"]


# --- HyDE : document hypothétique → embedding, jamais une source --------------


async def test_hyde_genere_des_documents_distincts_a_temperature_echantillonnee(
    monkeypatch: pytest.MonkeyPatch,
):
    reponses = iter(
        (
            "La franchise contractuelle applicable au contrat professionnel est "
            "définie dans les conditions particulières et reste à la charge de l'assuré.",
            "Les conditions particulières précisent le montant restant à la charge "
            "du souscripteur pour chaque sinistre garanti par le contrat.",
        )
    )
    temperatures: list[float] = []

    async def _ask(system: str, user: str, **kwargs) -> str:
        assert "DOCUMENT HYPOTHÉTIQUE" in system
        assert "franchise" in user
        temperatures.append(kwargs["temperature"])
        return next(reponses)

    monkeypatch.setattr(llm, "ask", _ask)
    documents = await hyde.generate("Quelle est la franchise ?", 2)

    assert len(documents) == 2
    assert temperatures == [0.7, 0.7]


def test_hyde_moyenne_et_normalise_les_vecteurs():
    vector = hyde.average([[1.0, 0.0], [0.0, 1.0]])

    assert vector == pytest.approx([2**-0.5, 2**-0.5])


async def test_hyde_reste_un_pivot_invisible_et_revient_a_la_question_en_cas_de_panne(
    corpus: Path, monkeypatch: pytest.MonkeyPatch
):
    await ingest.ingest(corpus)
    hypothese = (
        "MARQUEUR-HYDE-SECRET. Le document interne sur le télétravail précise "
        "les jours de présence et les modalités applicables aux collaborateurs."
    )

    async def _document(system: str, user: str, **kwargs) -> str:
        return hypothese

    monkeypatch.setattr(llm, "ask", _document)
    avec_hyde = await retrieve.search_detailed(
        "organisation du travail à distance",
        [PUBLIC],
        config=RagConfig(dense=True, sparse=True, hyde_documents=2),
    )

    assert avec_hyde.hyde_used
    # Deux générations identiques ne forment qu'un document utile.
    assert avec_hyde.hypotheses_generated == 1
    assert avec_hyde.queries == ["organisation du travail à distance"]
    assert avec_hyde.passages
    assert all("MARQUEUR-HYDE-SECRET" not in passage.text for passage in avec_hyde.passages)
    assert all("MARQUEUR-HYDE-SECRET" not in passage.citation for passage in avec_hyde.passages)

    async def _muet(system: str, user: str, **kwargs) -> None:
        return None

    monkeypatch.setattr(llm, "ask", _muet)
    repli = await retrieve.search_detailed(
        "télétravail",
        [PUBLIC],
        config=RagConfig(dense=True, sparse=True, hyde_documents=1),
    )
    assert not repli.hyde_used
    assert repli.hypotheses_generated == 0
    assert repli.passages


def test_la_configuration_hyde_est_bornee_et_lisible(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("RAG_PROFILE", ragconfig.CUSTOM_PROFILE)
    monkeypatch.setenv("RAG_HYDE_DOCUMENTS", "99")
    monkeypatch.setenv("RAG_HYDE_INCLUDE_QUERY", "0")
    monkeypatch.setenv("RAG_HYDE_TEMPERATURE", "3.5")

    config = ragconfig.from_env()

    assert config.hyde_documents == ragconfig.MAX_HYDE_DOCUMENTS
    assert not config.hyde_include_query
    assert config.hyde_temperature == 2.0
    assert f"hyde{ragconfig.MAX_HYDE_DOCUMENTS}" in config.label()


def test_le_profil_moderne_est_atomique_et_versionne(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("RAG_PROFILE", ragconfig.MODERN_HYDE_PROFILE)
    # Ces restes d'un ancien `.env` ne doivent pas créer un demi-profil.
    monkeypatch.setenv("RAG_MULTI_QUERY", "0")
    monkeypatch.setenv("RAG_HYDE_DOCUMENTS", "0")
    monkeypatch.setenv("RAG_RERANK", "none")
    monkeypatch.setenv("RAG_MIN_RERANK_SCORE", "")

    config = ragconfig.from_env()

    assert config.profile == ragconfig.MODERN_HYDE_PROFILE
    assert config.multi_query == 3
    assert config.hyde_documents == 1
    assert config.rerank == "llm"
    assert config.min_rerank_score == 4.5
    assert config.neighbours == 1
    assert config.label().startswith(f"{ragconfig.MODERN_HYDE_PROFILE}[")


async def test_desactiver_les_deux_recherches_est_une_erreur_de_programmation(
    corpus: Path,
):
    """Un garde-fou, pas un repli : une config sans aucune recherche est un bug
    d'appelant, et rendre silencieusement une liste vide le masquerait."""
    await ingest.ingest(corpus)
    with pytest.raises(ValueError, match="aucune recherche"):
        await retrieve.search("télétravail", [PUBLIC], config=RagConfig(dense=False, sparse=False))


# --- Branche lexicale : le piège du ET implicite ------------------------------


async def test_la_recherche_lexicale_ne_conjugue_pas_tous_les_termes(corpus_meta: Path):
    """Non-régression sur le bug qui rendait la moitié du système inutile.

    `websearch_to_tsquery` relie les termes par ET. Sur une question en langue
    naturelle — sept ou huit lexèmes après racinisation — cela exige que TOUS
    figurent dans le même fragment, ce qui n'arrive jamais. La branche lexicale
    ne remontait donc rien, sans la moindre erreur : la recherche « hybride »
    était une recherche dense avec du code mort à côté.

    Mesuré sur le corpus de démonstration, ce bug coûtait le rappel de la
    branche lexicale entière (1 % au lieu de plusieurs dizaines de pour cent).
    """
    await ingest.ingest(corpus_meta)
    question = "il reste combien à la charge du client en cas de sinistre sur sa multirisque"

    lexical = await retrieve.search(
        question, [PUBLIC], config=RagConfig(dense=False, sparse=True)
    )
    assert lexical, "aucun terme commun ne suffisait : la branche lexicale est muette"


async def test_la_branche_lexicale_retrouve_une_reference_exacte(corpus_meta: Path):
    """Ce que la recherche dense rate systématiquement : un identifiant.

    C'est la seule raison d'être de la branche lexicale. Si elle ne sait pas
    faire ça, l'hybridation ne sert à rien et autant assumer un RAG dense.
    """
    await ingest.ingest(corpus_meta)
    passages = await retrieve.search(
        "MRP-2025-0342", [PUBLIC], config=RagConfig(dense=False, sparse=True)
    )
    assert passages
    assert passages[0].meta["client"] == "bativert"


# --- Schéma : ne pas verrouiller la base à chaque démarrage -------------------


async def test_le_ddl_n_est_pas_rejoue_quand_le_schema_est_a_jour(corpus: Path):
    """Un `connect()` sur base déjà à jour ne doit prendre AUCUN verrou exclusif.

    `ALTER TABLE` et `CREATE INDEX`, même en `IF NOT EXISTS`, prennent un
    `AccessExclusiveLock`. Rejoués à chaque démarrage de processus, ils font
    interblocage avec tout lecteur en cours — constaté pour de bon, une
    évaluation de vingt minutes tuée par un `DeadlockDetectedError` parce
    qu'une suite de tests démarrait à côté.
    """
    connection = await ragdb.pool().acquire()
    try:
        assert await ragdb._schema_is_current(connection), (
            "la fixture a déjà appliqué le schéma : il doit être reconnu comme à jour"
        )
    finally:
        await ragdb.pool().release(connection)


async def test_un_schema_incomplet_est_bien_detecte(
    corpus: Path, monkeypatch: pytest.MonkeyPatch
):
    """Le garde-fou ne doit pas devenir un « on n'applique jamais rien »."""
    monkeypatch.setattr(
        ragdb, "_SCHEMA_MARKERS", (("rag_chunks", "colonne_qui_n_existe_pas"),)
    )
    connection = await ragdb.pool().acquire()
    try:
        assert not await ragdb._schema_is_current(connection)
    finally:
        await ragdb.pool().release(connection)
