"""Verticale courtage : lecture, rattachement, statut du dossier.

Le modèle est remplacé partout où il intervient. Ces tests portent sur les
**règles** — la cascade de rattachement, la corroboration d'identité, le passage
en validation, le refus de rattacher — et pas sur la qualité d'un LLM, qui ne se
teste pas unitairement et se mesure avec `make inbox-eval`.

Ce qui est vérifié ici est exactement ce qui coûte cher quand ça casse : un
courriel rattaché au mauvais client produit un brouillon crédible et faux, et
personne ne relit un dossier qui a l'air juste.
"""

from __future__ import annotations

import json
from email.message import EmailMessage
from pathlib import Path

import pytest

from agent.core.broker import classify, link, pipeline
from agent.core.broker import message as reader
from agent.core.broker.registry import Client, Contract, Registry

# --- Référentiel de test ------------------------------------------------------


def _registry() -> Registry:
    """Deux clients, dont un à contrats multiples et un homonyme de métier."""
    registry = Registry()

    bativert = Client(
        id="bativert",
        nom="SARL BATIVERT",
        siren="812447903",
        email="s.kervella@bativert.fr",
        domaine="bativert.fr",
    )
    bativert.contracts = {
        "DEC-2024-0117": Contract("DEC-2024-0117", "bativert", "decennale"),
        "MRP-2025-0342": Contract("MRP-2025-0342", "bativert", "mrp"),
    }

    petrin = Client(
        id="petrin",
        nom="SARL LE PÉTRIN D'ORVAULT",
        siren="443918072",
        email="contact@petrin-orvault.fr",
        domaine="petrin-orvault.fr",
    )
    petrin.contracts = {"MRP-2024-0295": Contract("MRP-2024-0295", "petrin", "mrp")}

    registry.clients = {"bativert": bativert, "petrin": petrin}
    for client in registry.clients.values():
        registry.by_email[client.email] = client.id
        registry.by_domain[client.domaine] = client.id
        registry.by_siren[client.siren] = client.id
        registry.contracts.update(client.contracts)
    return registry


def _message(
    *, sender: str = "inconnu@ailleurs.fr", subject: str = "Objet", body: str = "Bonjour."
) -> reader.Message:
    return reader.Message(
        message_id="test", sender=sender, sender_name=None, subject=subject, body=body
    )


# --- Lecture du courriel ------------------------------------------------------


def _eml(corps: str, *, sujet: str = "Test", piece: tuple[str, str] | None = None) -> bytes:
    brut = EmailMessage()
    brut["From"] = "Sandrine Kervella <s.kervella@bativert.fr>"
    brut["To"] = "gestion@rivelaine-courtage.fr"
    brut["Subject"] = sujet
    brut["Date"] = "Mon, 27 Jul 2026 08:00:00 +0000"
    brut["X-Attendu-Client"] = "bativert"
    brut.set_content(corps)
    if piece:
        brut.add_attachment(
            piece[1].encode("utf-8"), maintype="text", subtype="plain", filename=piece[0]
        )
    return brut.as_bytes()


def test_les_citations_sont_retirees_du_corps():
    """La panne la plus discrète d'un tri de boîte mail.

    Un fil à six réponses répète six fois le premier message. Sans découpe, le
    classifieur qualifie l'ANCIENNE demande — et le dossier part sur la mauvaise
    intention sans que rien ne le signale.
    """
    lu = reader.parse(
        _eml(
            "Je relance sur l'attestation.\n\n"
            "Le 20/07/2026, Aurélie Pichon a écrit :\n"
            "> Bonjour, je vous confirme la déclaration de sinistre\n"
            "> dégât des eaux du local social."
        )
    )
    assert "relance" in lu.body
    assert "dégât des eaux" not in lu.body


def test_un_message_entierement_cite_n_est_pas_vide():
    """Garde-fou : une découpe stricte laisserait une chaîne vide au classifieur."""
    lu = reader.parse(
        _eml("Le 20/07/2026, quelqu'un a écrit :\n> le contenu entier du message")
    )
    assert lu.body.strip()


def test_la_verite_terrain_ne_fuit_pas_dans_le_texte_analyse():
    """Les en-têtes `X-Attendu-*` souffleraient la réponse au modèle."""
    lu = reader.parse(_eml("Bonjour."))
    assert lu.ground_truth["client"] == "bativert"
    assert "bativert" not in lu.searchable.lower().replace("s.kervella@bativert.fr", "")


def test_les_pieces_jointes_lisibles_entrent_dans_le_texte_cherchable():
    """L'accroche est souvent dans la pièce jointe, pas dans le corps : un SIREN
    sur un Kbis, une immatriculation sur une carte grise."""
    lu = reader.parse(
        _eml("Voir pièce jointe.", piece=("kbis.txt", "SIREN 812 447 903"))
    )
    assert lu.attachments[0].filename == "kbis.txt"
    assert "812 447 903" in lu.searchable


# --- Cascade de rattachement --------------------------------------------------


async def test_une_reference_citee_l_emporte_sur_tout():
    registry = _registry()
    lien = await link.link_message(
        _message(sender="inconnu@ailleurs.fr", body="Au sujet du contrat MRP-2025-0342."),
        registry,
    )
    assert lien.client == "bativert"
    assert lien.contract == "MRP-2025-0342"
    assert lien.method == "reference"
    assert not lien.needs_review


async def test_une_reference_bien_formee_mais_inconnue_ne_rattache_rien():
    """Le négatif difficile : « le contrat MRP-2025-0999 ».

    Retenir une référence inexistante rattacherait le dossier à un contrat qui
    n'est pas au portefeuille — une erreur qu'aucune relecture ne rattrape,
    puisque la référence a l'air juste.
    """
    lien = await link.link_message(
        _message(body="Question sur le contrat MRP-2025-0999."), _registry()
    )
    assert lien.client is None
    assert lien.needs_review


async def test_l_adresse_connue_rattache_le_client():
    lien = await link.link_message(
        _message(sender="s.kervella@bativert.fr", body="Bonjour, une question."),
        _registry(),
    )
    assert lien.client == "bativert"
    assert lien.method == "email"


async def test_un_domaine_grand_public_ne_rattache_jamais():
    """Sinon le premier client qui écrit de chez lui s'approprie tout `orange.fr`."""
    registry = _registry()
    registry.by_domain["orange.fr"] = "bativert"  # câblage fautif, volontaire
    lien = await link.link_message(
        _message(sender="quelqu-un@orange.fr", body="Bonjour."), registry
    )
    assert lien.client is None


async def test_un_siren_reconnu_rattache_le_client():
    lien = await link.link_message(
        _message(
            sender="comptabilite@prestataire-externe.fr",
            body="L'entreprise concernée est immatriculée sous le SIREN 812 447 903.",
        ),
        _registry(),
    )
    assert lien.client == "bativert"
    assert lien.method == "siren"


async def test_un_nombre_a_neuf_chiffres_inconnu_ne_rattache_rien():
    """Neuf chiffres, c'est une forme trop banale pour valoir une identité.

    Un numéro de commande, un montant en centimes, un identifiant client d'un
    autre système : seuls les SIREN reconnus du portefeuille comptent.
    """
    lien = await link.link_message(
        _message(body="Notre référence de commande est le 123 456 789, dossier 447903812."),
        _registry(),
    )
    assert lien.client is None


async def test_sans_aucun_indice_le_message_n_est_pas_rattache():
    """L'étape 6 de la cascade existe pour de bon.

    C'est le pendant exact de l'abstention côté recherche : rattacher faute de
    mieux est la même faute que répondre faute de mieux.
    """
    lien = await link.link_message(
        _message(
            sender="direction@charcuterie-blanchard.fr",
            body="Nous cherchons une multirisque pour notre charcuterie à Ancenis.",
        ),
        _registry(),
    )
    assert lien.client is None
    assert lien.needs_review
    assert any("aucun élément" in preuve for preuve in lien.evidence)


# --- Corroboration d'identité -------------------------------------------------


def test_les_fragments_d_identite_ignorent_la_forme_juridique():
    tokens = link._identity_tokens("SARL LE PÉTRIN D'ORVAULT", "petrin-orvault.fr")
    assert "petrin" in tokens
    assert "orvault" in tokens
    assert "sarl" not in tokens


def test_les_fragments_d_identite_tolerent_accents_et_separateurs():
    """« La Baule » dans un courriel et « labaule » dans un domaine, c'est pareil."""
    tokens = link._identity_tokens("SARL LE SABLIER", "lesablier-labaule.fr")
    assert "labaule" in tokens
    assert "labaule" in link._fold("le restaurant de La Baule")


async def test_la_ressemblance_seule_ne_suffit_pas_a_rattacher(monkeypatch):
    """Le bug mesuré : un prospect charcutier rattaché à une boulangerie.

    Même métier de bouche, même vocabulaire, mêmes laboratoires : la boulangerie
    dominait largement le vote sémantique. Rien dans le message ne la désignait
    pourtant — autre nom, autre ville, autre domaine. Le rattachement était
    confiant et faux, c'est-à-dire la pire des sorties possibles.

    La similarité sémantique dit de quoi on parle, pas à qui on parle.
    """
    from agent.core.rag import retrieve as moteur

    async def _faux_search(query, groups, **kwargs):
        return [
            moteur.Passage(
                chunk_id=index,
                document_id="d",
                source="gestion/contrats/mrp-2024-0295-petrin.md",
                title=None,
                ord=0,
                text="laboratoire, chambres froides, denrées",
                score=1.0,
                meta={"client": "petrin"},
            )
            for index in range(4)
        ]

    monkeypatch.setattr(moteur, "search", _faux_search)

    lien = await link.link_message(
        _message(
            sender="direction@charcuterie-blanchard.fr",
            subject="Demande de devis multirisque",
            body="Charcuterie artisanale de 8 salariés à Ancenis, deux laboratoires.",
        ),
        _registry(),
        groups=["gestion", "public"],
    )
    assert lien.client is None, "la ressemblance de métier a été prise pour une identité"


async def test_le_client_nomme_dans_le_message_est_bien_rattache(monkeypatch):
    """Le pendant du test précédent : la corroboration ne doit pas tout bloquer."""
    from agent.core.rag import retrieve as moteur

    async def _faux_search(query, groups, **kwargs):
        return [
            moteur.Passage(
                chunk_id=index,
                document_id="d",
                source="gestion/contrats/mrp-2024-0295-petrin.md",
                title=None,
                ord=0,
                text="laboratoire",
                score=1.0,
                meta={"client": "petrin"},
            )
            for index in range(4)
        ]

    monkeypatch.setattr(moteur, "search", _faux_search)

    lien = await link.link_message(
        _message(
            sender="yannis@telephone-perso.fr",
            subject="Question",
            body="C'est pour la boulangerie Le Pétrin d'Orvault.",
        ),
        _registry(),
        groups=["gestion", "public"],
    )
    assert lien.client == "petrin"
    assert lien.method == "semantique"
    # Voie faible : le dossier passe quand même par un humain.
    assert lien.needs_review


# --- Choix du contrat ---------------------------------------------------------


async def test_un_client_a_contrat_unique_ne_pose_pas_de_question():
    lien = await link.link_message(
        _message(sender="contact@petrin-orvault.fr", body="Une question."), _registry()
    )
    assert lien.contract == "MRP-2024-0295"
    assert not lien.ambiguous


async def test_le_produit_se_deduit_du_contenu():
    lien = await link.link_message(
        _message(
            sender="s.kervella@bativert.fr",
            body="Le maître d'ouvrage réclame une attestation avant l'ouverture du chantier.",
        ),
        _registry(),
    )
    assert lien.contract == "DEC-2024-0117"


async def test_plusieurs_contrats_possibles_ne_se_tranchent_pas():
    """Ne jamais « prendre le plus probable ».

    Un contrat proposé au collaborateur a toutes les chances d'être validé sans
    être vérifié ; une ambiguïté affichée, elle, sera tranchée.
    """
    lien = await link.link_message(
        _message(sender="s.kervella@bativert.fr", body="Nous voulons augmenter nos plafonds."),
        _registry(),
    )
    assert lien.contract is None
    assert lien.ambiguous
    assert len(lien.candidates) == 2
    assert lien.needs_review, "être sûr du client ne rend pas sûr du contrat"


async def test_l_ambiguite_plafonne_la_confiance():
    lien = await link.link_message(
        _message(sender="s.kervella@bativert.fr", body="Nous voulons augmenter nos plafonds."),
        _registry(),
    )
    assert lien.confidence < link.CONFIDENCE_REVIEW
    assert lien.confidence < link.CONFIDENCE["email"]


# --- Statut du dossier --------------------------------------------------------


def test_un_message_hors_perimetre_n_est_pas_un_dossier_a_valider():
    """Une infolettre non rattachée est un succès du tri, pas un cas douteux."""
    statut = pipeline._status(
        classify.Classification(intention="hors_perimetre"), link.Link()
    )
    assert statut == "hors_perimetre"


def test_un_rattachement_incertain_part_en_validation():
    statut = pipeline._status(
        classify.Classification(intention="claim"),
        link.Link(client="bativert", confidence=0.45, method="semantique"),
    )
    assert statut == "a_valider"


def test_un_rattachement_sur_va_directement_au_traitement():
    statut = pipeline._status(
        classify.Classification(intention="claim"),
        link.Link(client="bativert", contract="MRP-2025-0342", confidence=0.95, method="email"),
    )
    assert statut == "a_traiter"


def test_la_pastille_priorise_l_urgence_sur_le_manque_de_pieces():
    """Un sinistre urgent doit sortir en tête même si son dossier est complet."""
    from agent.core.broker import draft, missing

    def _case(urgence: str, manquantes: list[str], statut: str = "a_traiter"):
        return pipeline.Case(
            message=_message(),
            classification=classify.Classification(intention="claim", urgence=urgence),
            link=link.Link(client="bativert", confidence=0.95),
            checklist=missing.Checklist(
                manquantes=[missing.Piece(p) for p in manquantes]
            ),
            draft=draft.Draft(),
            status=statut,
        )

    assert _case("haute", []).badge == "🔴"
    assert _case("haute", ["constat"]).badge == "🔴"
    assert _case("basse", ["constat"]).badge == "🟠"
    assert _case("basse", []).badge == "🟢"
    assert _case("haute", [], "a_valider").badge == "🟡"


# --- Refus de traiter sans identité -------------------------------------------


async def test_traiter_une_boite_sans_groupes_est_refuse(tmp_path: Path):
    """Un traitement automatique n'est pas une raison de lire sans filtre.

    C'est même le moment où la règle est le plus facile à oublier, puisque
    personne ne regarde le résultat au moment où il est produit.
    """
    with pytest.raises(ValueError, match="Aucun groupe"):
        await pipeline.process_mailbox(tmp_path, [])


# --- Pièces manquantes : cocher un référentiel, jamais en rédiger un ----------


_REFERENTIEL = """# Pièces exigibles selon le type de demande

Texte d'introduction sans puce, qui ne doit pas devenir une section.

## claim / vol — Déclaration de vol ou de vandalisme

- dépôt de plainte
- photographies des points d'effraction

## new_quote — Nouveau devis entreprise

- extrait Kbis de moins de trois mois
- dernier bilan clos
"""


def test_le_referentiel_se_decoupe_en_sections_enumerees():
    from agent.core.broker import missing

    sections = missing.parse_referentiel(_REFERENTIEL)

    assert [section.key for section in sections] == ["claim / vol", "new_quote"]
    assert sections[0].items == ["dépôt de plainte", "photographies des points d'effraction"]
    assert sections[0].intention == "claim"


def test_une_section_sans_puce_est_ignoree():
    """Elle ne permettrait pas de cocher, donc elle n'a rien à faire là."""
    from agent.core.broker import missing

    sections = missing.parse_referentiel(
        "## claim / vol — Vol\n\nDu texte en prose, sans liste.\n"
    )
    assert sections == []


async def test_une_piece_hors_referentiel_est_impossible(monkeypatch):
    """**La garantie du module.** Quoi que réponde le modèle, la sortie est un
    sous-ensemble du référentiel.

    La version précédente laissait le modèle RÉDIGER la liste : 19 pièces
    réclamées à tort sur 15 courriels. Ici il ne fournit que des indices, et les
    intitulés sont relus depuis le référentiel — inventer devient impossible, pas
    improbable.
    """
    from agent.core.broker import missing
    from agent.core.rag import llm as rag_llm

    async def _delirant(system: str, user: str, **kwargs) -> str:
        # Le modèle réclame une pièce imaginaire et des indices hors bornes.
        return json.dumps(
            {
                "section": 1,
                "manquantes": [1, 99, -3],
                "fournies": [2],
                "piece_inventee": "attestation de bonne humeur",
            }
        )

    async def _faux_referentiel(groups):
        return missing.parse_referentiel(_REFERENTIEL), "public/procedures/pieces.md"

    monkeypatch.setattr(rag_llm, "ask", _delirant)
    monkeypatch.setattr(missing, "load_referentiel", _faux_referentiel)

    liste = await missing.checklist(
        _message(body="On nous a cambriolés."),
        classify.Classification(intention="claim"),
        ["gestion"],
    )

    intitules = [piece.piece for piece in liste.manquantes]
    assert intitules == ["dépôt de plainte"]
    assert all(piece in _REFERENTIEL for piece in intitules)
    # Les indices hors bornes sont tracés, pas silencieusement rabattus.
    assert len(liste.rejected) == 2


async def test_une_section_hors_bornes_ne_produit_aucune_piece(monkeypatch):
    from agent.core.broker import missing
    from agent.core.rag import llm as rag_llm

    async def _hors_bornes(system: str, user: str, **kwargs) -> str:
        return json.dumps({"section": 42, "manquantes": [1]})

    async def _faux_referentiel(groups):
        return missing.parse_referentiel(_REFERENTIEL), "public/procedures/pieces.md"

    monkeypatch.setattr(rag_llm, "ask", _hors_bornes)
    monkeypatch.setattr(missing, "load_referentiel", _faux_referentiel)

    liste = await missing.checklist(
        _message(), classify.Classification(intention="claim"), ["gestion"]
    )
    assert liste.manquantes == []
    assert not liste.evaluated, "une section absurde n'est pas « rien ne manque »"


def test_la_qualite_de_l_expediteur_se_deduit_du_rattachement():
    """Le fait existait, il n'était pas transmis.

    Sans lui, « attestation demandée par l'assuré » et « demandée par un tiers »
    étaient systématiquement inversées : rien dans le corps ne les distingue,
    seul le rattachement le sait.
    """
    from agent.core.broker import missing

    assert "EST l'assuré" in missing.sender_quality("email")
    assert "EST l'assuré" in missing.sender_quality("domaine")
    assert "PAS identifié comme l'assuré" in missing.sender_quality("siren")
    assert "pas un assuré connu" in missing.sender_quality(None)


# --- Brouillon : ne jamais parler d'un autre dossier --------------------------


def test_un_dossier_anterieur_est_ecarte_du_contexte_du_brouillon():
    """La faute observée : sur un NOUVEAU dégât des eaux, le brouillon reprenait
    la date de transmission d'un sinistre antérieur du même client.

    Rien n'était inventé, tout était faux — et la référence, bien formée et du
    bon client, passait la relecture.
    """
    from agent.core.broker import draft
    from agent.core.rag import retrieve as moteur

    def _passage(type_: str, reference: str | None = None):
        return moteur.Passage(
            chunk_id=1,
            document_id="d",
            source="s.md",
            title=None,
            ord=0,
            text="…",
            score=1.0,
            meta={"type": type_, **({"reference": reference} if reference else {})},
        )

    lien = link.Link(client="petrin", contract="MRP-2024-0295")

    assert not draft._pertinent(_passage("declaration_sinistre"), lien)
    assert not draft._pertinent(_passage("rapport_expertise"), lien)
    assert not draft._pertinent(_passage("fil_email"), lien)
    # Le contrat rattaché et les documents de règle restent.
    assert draft._pertinent(_passage("conditions_particulieres", "MRP-2024-0295"), lien)
    assert draft._pertinent(_passage("conditions_generales"), lien)
    assert draft._pertinent(_passage("procedure_interne"), lien)
    # Un AUTRE contrat du même client porterait une autre franchise.
    assert not draft._pertinent(_passage("conditions_particulieres", "DAB-2024-0296"), lien)


def test_une_reference_etrangere_au_courriel_est_detectee():
    """Le contrôle est déterministe : c'est ce qui en fait une garantie."""
    from agent.core.broker import draft, missing
    from agent.core.broker import evaluate as broker_eval

    def _case(corps_brouillon: str, corps_courriel: str, contrat: str | None):
        return pipeline.Case(
            message=_message(body=corps_courriel),
            classification=classify.Classification(intention="claim"),
            link=link.Link(client="petrin", contract=contrat),
            checklist=missing.Checklist(),
            draft=draft.Draft(body=corps_brouillon),
        )

    # Référence d'un dossier dont le courriel ne parle pas.
    assert broker_eval._contamination(
        _case("Comme lors du sinistre SIN-2026-0103…", "Bonjour.", "MRP-2024-0295")
    ) == ["SIN-2026-0103"]

    # La même référence, citée par le client : légitime.
    assert (
        broker_eval._contamination(
            _case("Votre dossier SIN-2026-0103 suit son cours.", "Voir SIN-2026-0103", None)
        )
        == []
    )

    # Le contrat rattaché est toujours citable.
    assert (
        broker_eval._contamination(
            _case("Sur votre contrat MRP-2024-0295…", "Bonjour.", "MRP-2024-0295")
        )
        == []
    )
