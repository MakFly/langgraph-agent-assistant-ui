"""Référentiel du portefeuille, reconstruit depuis les métadonnées de l'index.

**Pourquoi pas une base dédiée.** Le référentiel dont le rattachement a besoin —
qui sont les clients, quels contrats ils détiennent, quelles adresses leur
appartiennent — existe déjà : il est écrit dans le front-matter des conditions
particulières, et il est parti en base avec les fragments. Le dupliquer dans une
seconde table créerait exactement le problème qu'un POC ne survit pas : deux
sources de vérité qui divergent, et personne pour savoir laquelle a raison.

La conséquence assumée est qu'**un client sans contrat indexé n'existe pas** pour
le rattachement. C'est correct ici : le cabinet n'a pas de client sans contrat.
Sur un vrai déploiement, le référentiel viendrait du logiciel de courtage, et ce
module deviendrait un adaptateur — la forme du `Registry` ne changerait pas.

**Le référentiel n'est pas filtré par les ACL, et c'est délibéré.** Il ne
contient aucun contenu documentaire : des raisons sociales, des références, des
domaines de messagerie. Savoir qu'un contrat existe n'est pas savoir ce qu'il
contient — la lecture, elle, repasse par `retrieve.search` avec les groupes de
l'appelant. Confondre les deux reviendrait à interdire au tri du courrier de
reconnaître un expéditeur.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

from agent.infra import ragdb

logger = logging.getLogger("agent.broker.registry")

# Forme d'une référence de contrat du cabinet : trois lettres, l'année, un rang.
CONTRACT_REF = re.compile(r"\b([A-Z]{3})-(\d{4})-(\d{4})\b")

# SIREN (9 chiffres) et SIRET (14), éventuellement espacés ou pointés.
SIREN_LIKE = re.compile(r"\b(\d[\d .]{8,17}\d)\b")

# Domaines de messagerie qui n'appartiennent jamais à une entreprise en propre.
# Un client qui écrit depuis son adresse personnelle ne doit pas rattacher tout
# `gmail.com` à son dossier.
PUBLIC_DOMAINS = frozenset(
    {
        "gmail.com", "orange.fr", "wanadoo.fr", "free.fr", "sfr.fr", "laposte.net",
        "hotmail.com", "hotmail.fr", "outlook.com", "outlook.fr", "yahoo.fr",
        "yahoo.com", "icloud.com", "me.com", "bbox.fr", "numericable.fr",
    }
)


@dataclass
class Contract:
    reference: str
    client: str
    produit: str | None = None
    produit_label: str | None = None
    compagnie: str | None = None


@dataclass
class Client:
    id: str
    nom: str
    siren: str | None = None
    email: str | None = None
    domaine: str | None = None
    contracts: dict[str, Contract] = field(default_factory=dict)

    @property
    def products(self) -> set[str]:
        return {c.produit for c in self.contracts.values() if c.produit}


@dataclass
class Registry:
    clients: dict[str, Client] = field(default_factory=dict)
    contracts: dict[str, Contract] = field(default_factory=dict)
    by_siren: dict[str, str] = field(default_factory=dict)
    by_email: dict[str, str] = field(default_factory=dict)
    by_domain: dict[str, str] = field(default_factory=dict)

    def client_of_domain(self, domaine: str) -> str | None:
        """`None` sur un domaine grand public, même s'il a été vu au portefeuille.

        C'est la protection qui évite d'attribuer tout `orange.fr` au premier
        client qui a écrit depuis chez lui.
        """
        domaine = domaine.lower()
        if domaine in PUBLIC_DOMAINS:
            return None
        return self.by_domain.get(domaine)

    def normalise_siren(self, brut: str) -> str:
        return re.sub(r"\D", "", brut)[:9]

    def find_sirens(self, texte: str) -> list[str]:
        """SIREN connus du portefeuille présents dans le texte.

        On ne rend que ceux qu'on reconnaît. Un numéro à neuf chiffres est une
        forme trop banale — un montant, une référence, un numéro de téléphone —
        pour qu'un inconnu vaille la peine d'être signalé.
        """
        trouves: list[str] = []
        for brut in SIREN_LIKE.findall(texte):
            numero = self.normalise_siren(brut)
            if len(numero) == 9 and numero in self.by_siren and numero not in trouves:
                trouves.append(numero)
        return trouves

    def find_references(self, texte: str) -> list[str]:
        """Références de contrat du portefeuille citées dans le texte.

        Une référence bien formée mais inconnue est écartée : elle relève du
        négatif difficile (« le contrat MRP-2025-0999 »), et la retenir
        produirait un rattachement à un contrat qui n'existe pas.
        """
        vues: list[str] = []
        for lettres, annee, rang in CONTRACT_REF.findall(texte.upper()):
            reference = f"{lettres}-{annee}-{rang}"
            if reference in self.contracts and reference not in vues:
                vues.append(reference)
        return vues


async def load() -> Registry:
    """Construit le référentiel depuis `rag_documents.meta`.

    Reconstruit à chaque appel plutôt que mémorisé : le portefeuille change à
    chaque ingestion, et un cache qui survit à un `make ingest` rattacherait des
    courriels à un portefeuille périmé sans que rien ne le signale.
    """
    registry = Registry()

    async with ragdb.pool().acquire() as connection:
        rows = await connection.fetch(
            """
            SELECT DISTINCT meta
            FROM rag_documents
            WHERE meta ? 'client'
            """
        )

    for row in rows:
        meta = row["meta"]
        if isinstance(meta, str):
            meta = json.loads(meta)

        client_id = meta.get("client")
        if not client_id:
            continue

        client = registry.clients.get(client_id)
        if client is None:
            client = Client(id=client_id, nom=meta.get("client_nom") or client_id)
            registry.clients[client_id] = client

        # Les champs ne sont renseignés qu'une fois : plusieurs documents parlent
        # du même client, et le premier qui porte l'information fait foi. Les
        # écraser à chaque passage rendrait le résultat dépendant de l'ordre des
        # lignes, donc instable d'une ingestion à l'autre.
        client.nom = client.nom or meta.get("client_nom") or client_id
        client.siren = client.siren or meta.get("siren")
        client.email = client.email or meta.get("client_email")
        client.domaine = client.domaine or meta.get("client_domaine")

        if client.siren:
            registry.by_siren.setdefault(registry.normalise_siren(client.siren), client_id)
        if client.email:
            registry.by_email.setdefault(client.email.lower(), client_id)
        if client.domaine and client.domaine.lower() not in PUBLIC_DOMAINS:
            registry.by_domain.setdefault(client.domaine.lower(), client_id)

        reference = meta.get("reference")
        # Seules les conditions particulières définissent un contrat. Une
        # attestation ou un avenant PORTENT une référence sans la créer, et les
        # laisser en créer inventerait des contrats à partir de leur propre
        # numéro de pièce (`ATT-2026-0003` n'est pas un contrat).
        if meta.get("type") == "conditions_particulieres" and reference:
            contrat = Contract(
                reference=reference,
                client=client_id,
                produit=meta.get("produit"),
                produit_label=meta.get("produit_label"),
                compagnie=meta.get("compagnie"),
            )
            registry.contracts[reference] = contrat
            client.contracts[reference] = contrat

    logger.info(
        "référentiel chargé",
        extra={
            "clients": len(registry.clients),
            "contrats": len(registry.contracts),
            "domaines": len(registry.by_domain),
        },
    )
    return registry
