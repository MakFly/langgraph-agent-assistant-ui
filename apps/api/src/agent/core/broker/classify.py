"""Qualification d'un courriel entrant : intention, urgence, entités.

**Cinq intentions, et une sixième pour ce qui n'en est pas une.** La tentation,
sur ce genre de produit, est d'ouvrir une taxonomie à vingt catégories parce que
le métier en distingue vingt. C'est l'erreur : un classifieur à vingt classes se
trompe partout, personne ne sait dire où, et la file de travail devient
ingérable. Cinq catégories qui déclenchent cinq traitements différents valent
mieux que vingt qui n'en déclenchent aucun.

`hors_perimetre` n'est pas une catégorie de repli honteuse : une boîte
`gestion@` reçoit des infolettres, des relances de prestataires et des courriels
mal adressés. Les forcer dans une catégorie métier pollue la file et fait perdre
la confiance dans le tri bien plus vite qu'un « non classé » assumé.

**L'urgence est une donnée du métier, pas du ton.** « URGENT » en majuscules dans
l'objet ne veut rien dire — certains clients le mettent toujours. Ce qui compte,
c'est le délai contractuel qui court : vingt-quatre heures pour un incendie, deux
jours ouvrés pour un vol. C'est ce que le prompt demande de regarder.

**Ce module ne décide de rien de réglementé.** Il qualifie une demande et repère
des entités. Il ne dit jamais si une garantie joue — ça, c'est la position de
l'assureur, et un POC qui la prononce expose le cabinet à une mise en cause au
titre du devoir de conseil.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

from agent.core.broker.message import Message
from agent.core.rag import llm

logger = logging.getLogger("agent.broker.classify")

INTENTIONS = (
    "new_quote",
    "contract_amendment",
    "claim",
    "certificate_request",
    "billing_or_payment",
    "coverage_question",
    "administrative",
    "hors_perimetre",
)

URGENCES = ("haute", "moyenne", "basse")

# Immatriculation française depuis 2009 : AA-123-AA.
PLATE = re.compile(r"\b([A-Z]{2}-\d{3}-[A-Z]{2})\b")

_SYSTEM = f"""Tu qualifies un courriel reçu par la boîte de gestion d'un cabinet
de courtage en assurance IARD professionnelle.

**Classe par l'ACTE que le cabinet doit poser, jamais par la forme du message.**
Presque tous les courriels se terminent par une question ; ce n'est donc pas un
critère. La question à se poser est : « qu'est-ce que le gestionnaire doit
FAIRE ? »

Parcours les intentions dans CET ORDRE et retiens la PREMIÈRE qui s'applique :

1. hors_perimetre : infolettre, publicité, message automatique, mal adressé.
2. claim : le message DÉCLARE un sinistre, TRANSMET une pièce pour un dossier
   de sinistre, ou rapporte la MISE EN CAUSE de l'assuré par un tiers.
   Attention : mentionner un sinistre passé pour situer une question ne suffit
   pas. « Suite au sinistre de mars, devons-nous vous fournir telle preuve ? »
   ne demande aucun acte sur le sinistre — ce n'en est pas un.
3. contract_amendment : satisfaire la demande IMPOSE de modifier le contrat —
   ajout ou retrait de véhicule, changement d'adresse ou d'établissement,
   extension d'activité, variation de capitaux ou de plafonds, modification
   d'une période d'exploitation. Y compris quand c'est formulé en question
   (« est-ce qu'on peut… ? », « c'est possible de… ? »).
4. certificate_request : il faut émettre ou obtenir une attestation.
5. new_quote : il faut chiffrer un risque qui n'est pas encore assuré chez nous.
6. billing_or_payment : il y a un PROBLÈME de paiement — prélèvement contesté,
   impayé, quittance erronée. Une simple question sur le montant payé à
   l'occasion d'un renouvellement n'en est pas un.
7. coverage_question : la réponse se trouve ENTIÈREMENT dans le contrat tel
   qu'il est, et aucun acte n'est requis. C'est le cas le plus rare : n'y
   recours que si aucune des lignes précédentes ne s'applique.
8. administrative : gestion courante sans acte identifiable — point de
   portefeuille, préparation d'échéance, échange d'information.

Le piège le plus fréquent est de classer en coverage_question un message qui
demande en réalité un avenant ou qui alimente un sinistre. En cas d'hésitation
entre coverage_question et une ligne au-dessus, choisis la ligne au-dessus.

Évalue l'urgence sur le DÉLAI qui court, jamais sur le ton du message :
- haute : un délai contractuel court (incendie 24 h, vol 2 jours), une activité
  arrêtée, un chantier bloqué, un véhicule qui roule sans garantie
- moyenne : une échéance à quelques jours ou une semaine
- basse : aucune contrainte de temps identifiable

Un objet en majuscules n'est PAS un critère d'urgence.

Extrais uniquement ce qui est écrit noir sur blanc. N'invente aucune référence,
aucun montant, aucune date : un champ absent vaut mieux qu'un champ deviné.

Réponds UNIQUEMENT par un objet JSON, sans texte autour :
{{"intention": "...", "urgence": "...", "resume": "une phrase",
  "demandes": ["l'action attendue du cabinet"],
  "references": [], "immatriculations": [], "montants": [], "dates": [],
  "raison_urgence": "en quelques mots"}}

Valeurs acceptées pour `intention` : {", ".join(INTENTIONS)}.
Valeurs acceptées pour `urgence` : {", ".join(URGENCES)}."""


@dataclass
class Classification:
    intention: str = "administrative"
    urgence: str = "basse"
    resume: str = ""
    demandes: list[str] = field(default_factory=list)
    references: list[str] = field(default_factory=list)
    immatriculations: list[str] = field(default_factory=list)
    montants: list[str] = field(default_factory=list)
    dates: list[str] = field(default_factory=list)
    raison_urgence: str = ""
    degraded: bool = False
    """Le modèle n'a pas répondu : la qualification est un repli, pas un jugement.

    Distinguer les deux évite qu'une panne du fournisseur remplisse la file de
    dossiers « administratif / basse » qui ressembleraient à un tri réussi."""


def _extract_json(raw: str) -> dict | None:
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip()).strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            return None
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return parsed if isinstance(parsed, dict) else None


def _strings(value) -> list[str]:
    """Liste de chaînes, quelle que soit la fantaisie du modèle.

    Un modèle rend tantôt une liste, tantôt une chaîne, tantôt une liste
    d'objets. Normaliser ici évite de disperser des `isinstance` dans tout le
    reste du traitement.
    """
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, dict):
        return [str(v) for v in value.values() if str(v).strip()]
    if isinstance(value, list):
        sortie: list[str] = []
        for item in value:
            if isinstance(item, dict):
                sortie.extend(str(v) for v in item.values() if str(v).strip())
            elif str(item).strip():
                sortie.append(str(item))
        return sortie
    return [str(value)]


def _prompt(message: Message) -> str:
    pieces = (
        "\n".join(
            f"- {piece.filename} ({piece.mimetype}"
            + (", lisible" if piece.text else ", non lu — image ou PDF sans couche texte")
            + ")"
            for piece in message.attachments
        )
        or "- aucune"
    )
    extraits = "\n\n".join(
        f"--- {piece.filename} ---\n{piece.text[:800]}"
        for piece in message.attachments
        if piece.text
    )
    return (
        f"EXPÉDITEUR : {message.sender}\n"
        f"OBJET : {message.subject}\n"
        f"DATE : {message.date or 'inconnue'}\n\n"
        f"CORPS :\n{message.body[:4000]}\n\n"
        f"PIÈCES JOINTES :\n{pieces}"
        + (f"\n\nCONTENU DES PIÈCES :\n{extraits[:2000]}" if extraits else "")
    )


async def classify(message: Message) -> Classification:
    """Qualifie un courriel. Ne lève jamais : un message non qualifié reste à traiter."""
    reponse = await llm.ask(_SYSTEM, _prompt(message))
    if reponse is None:
        logger.warning("qualification indisponible pour %s", message.message_id)
        return Classification(
            resume="Qualification indisponible : le modèle n'a pas répondu.",
            degraded=True,
        )

    parsed = _extract_json(reponse)
    if parsed is None:
        logger.warning("réponse de qualification illisible pour %s", message.message_id)
        return Classification(
            resume="Qualification illisible.", degraded=True
        )

    intention = str(parsed.get("intention", "")).strip()
    if intention not in INTENTIONS:
        # Une intention inventée est traitée comme une non-qualification, pas
        # rabattue sur la plus proche : un rabattage silencieux ferait passer une
        # erreur de format pour un jugement.
        logger.info("intention hors nomenclature (%r), repli administratif", intention)
        intention = "administrative"

    urgence = str(parsed.get("urgence", "")).strip().lower()
    if urgence not in URGENCES:
        urgence = "basse"

    # Les immatriculations sont reprises du texte par expression régulière en
    # plus de celles rendues par le modèle : c'est une forme strictement
    # définie, et une regex ne l'oublie ni ne l'invente.
    immatriculations = list(
        dict.fromkeys(
            [*_strings(parsed.get("immatriculations")), *PLATE.findall(message.searchable.upper())]
        )
    )

    return Classification(
        intention=intention,
        urgence=urgence,
        resume=str(parsed.get("resume", "")).strip(),
        demandes=_strings(parsed.get("demandes")),
        references=_strings(parsed.get("references")),
        immatriculations=immatriculations,
        montants=_strings(parsed.get("montants")),
        dates=_strings(parsed.get("dates")),
        raison_urgence=str(parsed.get("raison_urgence", "")).strip(),
    )
