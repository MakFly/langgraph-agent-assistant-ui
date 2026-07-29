"""Brouillon de réponse — proposé au collaborateur, jamais envoyé.

**La limite est la raison d'être du produit, pas une prudence de POC.** Un
intermédiaire d'assurance est tenu à un devoir de conseil dont il répond
personnellement. Un système qui répondrait seul à un client sur l'étendue d'une
garantie engagerait le cabinet sur une position qu'aucun humain n'a validée. Le
brouillon s'arrête donc là où commence la décision :

    ce que fait le brouillon              ce qu'il ne fait jamais
    ─────────────────────────────         ──────────────────────────────────
    accuser réception                     dire si la garantie joue
    rappeler l'état du dossier            annoncer une indemnisation
    réclamer les pièces manquantes        recommander un contrat
    rappeler un délai contractuel         modifier une police
    annoncer la transmission              s'envoyer tout seul

**Le brouillon ne cite que ce qu'on lui a donné.** Le contexte vient de la
recherche documentaire, filtrée par les groupes du collaborateur, et le prompt
interdit d'ajouter un montant ou une référence qui n'y figure pas. C'est la même
règle que pour la recherche : mieux vaut un brouillon incomplet qu'un brouillon
inventé, parce que le premier se complète et que le second se signe sans être
relu.

**Un dossier à valider ne reçoit pas de brouillon de fond.** Quand le
rattachement est incertain, écrire une réponse détaillée serait proposer au
collaborateur un texte plausible construit sur un dossier peut-être faux — le
pire des deux mondes. On produit alors un simple accusé de réception.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from agent.core.broker.classify import Classification
from agent.core.broker.link import Link
from agent.core.broker.message import Message
from agent.core.broker.missing import Checklist
from agent.core.rag import llm, retrieve

logger = logging.getLogger("agent.broker.draft")

_SYSTEM = """Tu rédiges un BROUILLON de réponse pour un collaborateur d'un cabinet
de courtage en assurance IARD professionnelle. Ce brouillon sera relu, corrigé et
envoyé par un humain. Il ne part jamais seul.

Tu écris en français, sur un ton professionnel et direct, en 120 mots maximum.

TU DOIS :
- accuser réception de la demande en la reformulant en une phrase ;
- indiquer ce que le cabinet fait ou va faire (transmettre, monter l'avenant,
  demander l'attestation) ;
- réclamer les pièces manquantes qui te sont fournies, sous forme de liste ;
- rappeler un délai contractuel quand il figure dans le contexte documentaire.

TU NE DOIS JAMAIS :
- dire si une garantie joue, ou si un sinistre est couvert : c'est la position de
  l'assureur, et l'annoncer engagerait le cabinet ;
- annoncer un montant d'indemnisation ;
- recommander un contrat ou une compagnie ;
- citer un montant, une référence, une date ou une franchise qui ne figure PAS
  dans le contexte documentaire fourni. En cas de doute, reste vague ;
- faire référence à un sinistre, un dossier ou un événement ANTÉRIEUR. Tu ne
  disposes d'aucun élément fiable sur l'historique de ce client, et un rappel
  qui viserait le mauvais dossier serait crédible et faux.

N'invente aucun chiffre. Écris le corps du message seul, sans objet, sans en-tête,
et signe par « Le service gestion »."""

_SYSTEM_ACCUSE = """Tu rédiges un BROUILLON d'accusé de réception court (60 mots
maximum) pour un cabinet de courtage en assurance.

Le dossier n'a PAS pu être rattaché avec certitude à un client ou à un contrat.
Tu ne disposes donc d'aucune information fiable sur sa situation.

Accuse réception, indique que la demande est prise en charge, et demande
UNIQUEMENT la précision qui manque pour avancer. N'affirme rien sur les contrats,
les garanties ou les montants. Signe par « Le service gestion »."""


@dataclass
class Draft:
    body: str = ""
    sources: list[str] = field(default_factory=list)
    """Citations du contexte réellement fourni au modèle, pour que le
    collaborateur puisse vérifier chaque affirmation."""
    kind: str = "traitement"
    """`traitement` ou `accuse_reception` — ce dernier quand le dossier est à valider."""
    degraded: bool = False


def _subject(message: Message) -> str:
    sujet = message.subject.strip()
    return sujet if sujet.lower().startswith("re") else f"Re : {sujet}"


# Types de documents qui décrivent un DOSSIER daté et clos, par opposition aux
# documents qui décrivent le contrat ou la règle. Ce sont eux qui contaminent un
# brouillon : ils parlent d'un autre événement du même client.
_DOSSIERS_ANTERIEURS = frozenset(
    {"declaration_sinistre", "rapport_expertise", "fil_email"}
)


def _pertinent(passage: retrieve.Passage, link: Link) -> bool:
    """Ce passage parle-t-il du dossier COURANT, ou d'un autre ?

    **Le bon client ne suffit pas.** Observé sur un nouveau dégât des eaux : la
    recherche a remonté la déclaration d'un sinistre antérieur du même client —
    une panne de chambre froide, trois semaines plus tôt — et le brouillon a
    repris sa date de transmission à la compagnie. Rien n'était inventé, tout
    était faux.

    La règle retenue est volontairement brutale : les documents qui décrivent un
    **dossier daté** (déclaration de sinistre, rapport d'expertise, fil de
    courriels antérieur) sont écartés du contexte d'un brouillon. Les contrats,
    avenants, attestations, conditions générales et procédures restent — ce sont
    eux qui portent les franchises, les délais et les règles, c'est-à-dire tout
    ce que le brouillon a le droit de rappeler.

    On perd la capacité de dire « comme lors de votre précédent sinistre ». C'est
    un renoncement assumé : rattacher un courriel au bon *dossier* est un travail
    en soi, et tant qu'il n'est pas fait, mieux vaut un brouillon qui n'en parle
    pas qu'un brouillon qui parle du mauvais.
    """
    if passage.meta.get("type") in _DOSSIERS_ANTERIEURS:
        return False
    # Un document rattaché à un AUTRE contrat du même client n'a pas sa place non
    # plus : la franchise citée serait celle d'une autre police.
    if link.contract:
        porte = passage.meta.get("reference") or passage.meta.get("contrat")
        if porte and porte != link.contract:
            return False
    return True


async def _context(
    message: Message, classification: Classification, link: Link, groups: list[str]
) -> list[retrieve.Passage]:
    """Contexte documentaire du brouillon : le bon client, le bon contrat, et
    aucun dossier antérieur.

    Le filtre par client n'est pas un confort : sans lui, la recherche remonte les
    contrats homonymes des autres clients, et le brouillon citerait leur
    franchise. C'est exactement la faute que la verticale doit éviter.
    """
    requete = f"{message.subject} {classification.resume}".strip() or message.subject
    filtres = {"client": link.client} if link.client else None

    # On tire large avant de filtrer : écarter les dossiers antérieurs après coup
    # sur un top-4 ne laisserait presque rien.
    bruts = await retrieve.search(requete, groups, k=10, filters=filtres)
    passages = [passage for passage in bruts if _pertinent(passage, link)][:4]

    if not passages:
        # Le client existe mais rien de contractuel ne correspond : on élargit aux
        # documents généraux (conditions générales, procédures), qui restent
        # utiles pour rappeler un délai sans rien affirmer sur son dossier.
        generaux = await retrieve.search(requete, groups, k=6)
        passages = [
            passage
            for passage in generaux
            if passage.meta.get("type") not in _DOSSIERS_ANTERIEURS
            and not passage.meta.get("client")
        ][:3]
    return passages


async def compose(
    message: Message,
    classification: Classification,
    link: Link,
    checklist: Checklist,
    groups: list[str],
) -> Draft:
    """Rédige le brouillon. Ne lève jamais : un dossier sans brouillon reste traitable."""
    if link.needs_review:
        precision = (
            "le contrat concerné parmi ceux du client"
            if link.ambiguous
            else "l'identité de l'entreprise concernée"
        )
        corps = await llm.ask(
            _SYSTEM_ACCUSE,
            f"OBJET : {message.subject}\n\nCOURRIEL :\n{message.body[:1500]}\n\n"
            f"PRÉCISION MANQUANTE : {precision}",
        )
        if corps is None:
            return Draft(kind="accuse_reception", degraded=True)
        return Draft(body=corps.strip(), kind="accuse_reception")

    passages = await _context(message, classification, link, groups)
    contexte = (
        "\n\n".join(f"[{p.citation}]\n{p.text[:900]}" for p in passages)
        or "(aucun document accessible ne documente ce dossier)"
    )
    manquantes = (
        "\n".join(f"- {piece.piece}" for piece in checklist.manquantes) or "(aucune)"
    )

    corps = await llm.ask(
        _SYSTEM,
        f"CLIENT : {link.client_nom or 'non identifié'}\n"
        f"CONTRAT : {link.contract or 'non déterminé'}\n"
        f"TYPE DE DEMANDE : {classification.intention}\n"
        f"URGENCE : {classification.urgence}\n\n"
        f"COURRIEL REÇU :\n{message.body[:2000]}\n\n"
        f"PIÈCES MANQUANTES À RÉCLAMER :\n{manquantes}\n\n"
        f"CONTEXTE DOCUMENTAIRE (seule source autorisée pour les chiffres) :\n{contexte}",
    )
    if corps is None:
        logger.warning("brouillon indisponible pour %s", message.message_id)
        return Draft(degraded=True, sources=[p.citation for p in passages])

    return Draft(body=corps.strip(), sources=[p.citation for p in passages])
