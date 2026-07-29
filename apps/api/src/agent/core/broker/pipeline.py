"""Courriel → dossier : l'enchaînement complet, et ce qu'il refuse de faire.

    courriel .eml
        │
        ├─ lecture           en-têtes, corps décité, pièces jointes
        ├─ qualification     intention · urgence · entités          (modèle)
        ├─ rattachement      client · contrat · confiance           (cascade)
        ├─ pièces manquantes référentiel du corpus vs. pièces jointes (RAG + modèle)
        ├─ brouillon         réponse proposée, sources citées        (RAG + modèle)
        └─ dossier           à traiter · à valider · hors périmètre

**Un dossier n'est jamais « traité ».** Il est *préparé*. La sortie de ce module
est une file de travail, pas une file d'envois. Le collaborateur garde la
décision, et c'est ce qui permet de vendre l'outil à un métier réglementé sans
prendre à sa place un risque qu'il porte personnellement.

**L'ordre des étapes n'est pas négociable.** Le rattachement précède la recherche
de pièces et le brouillon, parce que les deux dépendent du client : chercher les
pièces avant de savoir de qui l'on parle reviendrait à interroger tout le
portefeuille. Et la qualification précède le rattachement parce que l'intention
oriente le choix du contrat quand le client en a plusieurs.

**Les groupes de l'appelant traversent toute la chaîne.** Chaque recherche est
filtrée par eux. Un traitement automatique n'est pas une raison de lire ce que
l'utilisateur n'a pas le droit de lire — c'est même le moment où la règle est le
plus facile à oublier, puisque personne ne regarde.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path

from agent.core.broker import classify as classifier
from agent.core.broker import draft as drafter
from agent.core.broker import link as linker
from agent.core.broker import message as reader
from agent.core.broker import missing as checker
from agent.core.broker import registry as referentiel

logger = logging.getLogger("agent.broker.pipeline")

# Traitements menés de front. Chaque dossier consomme trois appels de modèle ;
# au-delà, un free tier répond 429 et la mesure devient du bruit.
DEFAULT_CONCURRENCY = 3

STATUSES = ("a_traiter", "a_valider", "hors_perimetre")


@dataclass
class Case:
    """Un dossier préparé, prêt pour la file de travail."""

    message: reader.Message
    classification: classifier.Classification
    link: linker.Link
    checklist: checker.Checklist
    draft: drafter.Draft
    status: str = "a_traiter"

    @property
    def title(self) -> str:
        return self.message.subject or "(sans objet)"

    @property
    def blocked(self) -> bool:
        return bool(self.checklist.manquantes)

    @property
    def badge(self) -> str:
        """Pastille de la file de travail, par ordre de priorité décroissante."""
        if self.status == "hors_perimetre":
            return "⚪"
        if self.status == "a_valider":
            return "🟡"
        if self.classification.urgence == "haute":
            return "🔴"
        if self.blocked:
            return "🟠"
        return "🟢"


@dataclass
class Batch:
    cases: list[Case] = field(default_factory=list)

    def by_status(self, status: str) -> list[Case]:
        return [case for case in self.cases if case.status == status]

    @property
    def urgent(self) -> list[Case]:
        return [
            case
            for case in self.cases
            if case.status == "a_traiter" and case.classification.urgence == "haute"
        ]


def _status(classification: classifier.Classification, link: linker.Link) -> str:
    """Où le dossier atterrit dans la file.

    L'ordre des tests compte : un message hors périmètre n'a pas à passer en
    validation humaine sous prétexte qu'il n'est rattaché à personne. Une
    infolettre non rattachée est un succès du tri, pas un cas douteux.
    """
    if classification.intention == "hors_perimetre":
        return "hors_perimetre"
    if link.needs_review:
        return "a_valider"
    return "a_traiter"


async def process(
    message: reader.Message,
    registry: referentiel.Registry,
    groups: list[str],
) -> Case:
    """Prépare un dossier à partir d'un courriel."""
    classification = await classifier.classify(message)

    if classification.intention == "hors_perimetre":
        # On s'arrête net : rattacher, chercher des pièces et rédiger un brouillon
        # pour une infolettre, ce sont trois appels de modèle dépensés à décorer
        # un message qui part à la corbeille.
        return Case(
            message=message,
            classification=classification,
            link=linker.Link(evidence=["message hors périmètre : aucun rattachement tenté"]),
            checklist=checker.Checklist(reason="message hors périmètre"),
            draft=drafter.Draft(kind="aucun"),
            status="hors_perimetre",
        )

    lien = await linker.link_message(message, registry, groups=groups)
    # La voie de rattachement est transmise : elle établit si l'expéditeur est
    # l'assuré, ce que le corps du message ne dit presque jamais.
    liste = await checker.checklist(
        message, classification, groups, link_method=lien.method
    )
    brouillon = await drafter.compose(message, classification, lien, liste, groups)

    return Case(
        message=message,
        classification=classification,
        link=lien,
        checklist=liste,
        draft=brouillon,
        status=_status(classification, lien),
    )


async def process_mailbox(
    root: Path,
    groups: list[str],
    *,
    concurrency: int = DEFAULT_CONCURRENCY,
    limit: int | None = None,
) -> Batch:
    """Traite une boîte entière. L'ordre de sortie suit celui des fichiers.

    Le référentiel est chargé une seule fois pour tout le lot : il ne bouge pas
    pendant un traitement, et le recharger par courriel ferait une requête par
    message pour un résultat identique.
    """
    if not groups:
        raise ValueError(
            "Aucun groupe fourni : le traitement lirait des documents sans filtre "
            "de permissions. Passez les groupes du collaborateur."
        )

    messages = reader.read_mailbox(root)
    if limit:
        messages = messages[:limit]

    registre = await referentiel.load()
    if not registre.clients:
        raise RuntimeError(
            "Référentiel vide : aucun contrat n'est indexé. Lancez `make ingest` "
            "avant de traiter une boîte."
        )

    limite = asyncio.Semaphore(max(1, concurrency))

    async def traiter(message: reader.Message) -> Case:
        async with limite:
            return await process(message, registre, groups)

    lot = Batch(cases=list(await asyncio.gather(*(traiter(m) for m in messages))))

    logger.info(
        "boîte traitée",
        extra={
            "courriels": len(lot.cases),
            "a_traiter": len(lot.by_status("a_traiter")),
            "a_valider": len(lot.by_status("a_valider")),
            "hors_perimetre": len(lot.by_status("hors_perimetre")),
        },
    )
    return lot
