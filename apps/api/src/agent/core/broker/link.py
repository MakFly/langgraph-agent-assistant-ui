"""Rattachement d'un courriel à un client et à un contrat.

C'est la brique qui décide de tout le reste : mal rattaché, un courriel produit
un brouillon qui cite le contrat d'un autre. En courtage, c'est la faute qui
coûte le client.

**Une cascade, du certain vers le probable**, et l'ordre est le sujet :

    1. référence de contrat citée   → certitude, on s'arrête là
    2. adresse d'expéditeur connue  → quasi-certitude
    3. domaine de messagerie connu  → forte présomption (jamais un domaine grand public)
    4. SIREN reconnu dans le texte  → forte présomption, y compris depuis une pièce jointe
    5. recherche sémantique         → présomption faible, à confirmer
    6. rien de tout ça              → on ne rattache pas

Chaque étape porte une **confiance**, et la confiance n'est pas décorative : c'est
elle qui décide si le dossier part en traitement ou en file de validation. Un
rattachement à 0,45 traité comme un rattachement à 0,98 ferait disparaître toute
la valeur de la cascade.

**On ne rattache jamais faute de mieux.** L'étape 6 existe pour de bon : un
prospect inconnu, une infolettre, un tiers non mandaté doivent ressortir sans
client. C'est le pendant exact de l'abstention côté recherche — et la même erreur
si on l'omet, à savoir répondre quelque chose plutôt que rien.

**Le contrat est traité à part du client.** Rattacher au bon client mais au
mauvais contrat est plus dangereux que ne pas rattacher de contrat du tout :
personne ne relit une référence qui a l'air juste. Quand plusieurs contrats du
client peuvent correspondre, on rend l'ambiguïté au lieu de la trancher.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from agent.core.broker.message import Message
from agent.core.broker.registry import Registry
from agent.core.rag import retrieve

logger = logging.getLogger("agent.broker.link")

# Seuil en dessous duquel un rattachement part en validation humaine plutôt qu'en
# traitement. Calibré sur la boîte de démonstration : la recherche sémantique
# (0,45) doit toujours être relue, le domaine (0,80) non.
CONFIDENCE_REVIEW = 0.60

# Confiance attachée à chaque voie. Ce ne sont pas des probabilités mesurées mais
# un ordre de préférence explicite ; les écrire ici plutôt que de les disperser
# dans le code permet de les discuter, et de les revoir quand on aura des chiffres.
CONFIDENCE = {
    "reference": 0.98,
    "email": 0.95,
    "domaine": 0.80,
    "siren": 0.90,
    "semantique": 0.45,
}

# Mots-clés qui désignent un produit. Le rattachement au contrat s'en sert quand
# le client en a plusieurs. Volontairement grossier : il ne tranche que les cas
# évidents, et laisse l'ambiguïté remonter dans tous les autres.
PRODUCT_HINTS = {
    "flotte": ("flotte", "véhicule", "vehicule", "camion", "porteur", "immatricul",
               "carte grise", "benne", "fourgon", "utilitaire", "dépanneuse"),
    "decennale": ("décennale", "decennale", "chantier", "maître d'ouvrage",
                  "maitre d'ouvrage", "ouvrage", "fissur", "malfaçon"),
    "cyber": ("cyber", "rançongiciel", "rancongiciel", "ransomware", "piratage",
              "chiffr", "données personnelles", "informatique", "sauvegarde"),
    "mrp": ("multirisque", "dégât des eaux", "degat des eaux", "incendie", "vol",
            "effraction", "vitrine", "bris de glace", "local", "chambre froide",
            "terrasse", "marchandise"),
    "rc_pro": ("responsabilité civile", "responsabilite civile", "rc pro",
               "mise en cause", "faute professionnelle", "préjudice", "prejudice",
               "réclamation", "véhicules confiés", "vehicules confies"),
    "dab": ("dommages aux biens", "bris de machine", "machine", "atelier",
            "tempête", "tempete", "catastrophe naturelle"),
}


@dataclass
class Link:
    """Le rattachement proposé, et ce sur quoi il repose."""

    client: str | None = None
    client_nom: str | None = None
    contract: str | None = None
    confidence: float = 0.0
    method: str | None = None
    """Voie retenue : `reference`, `email`, `domaine`, `siren`, `semantique`."""
    evidence: list[str] = field(default_factory=list)
    candidates: list[str] = field(default_factory=list)
    """Contrats du client qui pouvaient correspondre, quand on n'a pas tranché."""
    ambiguous: bool = False

    @property
    def needs_review(self) -> bool:
        """Le dossier doit-il passer par un humain avant tout traitement ?"""
        return (
            self.client is None
            or self.ambiguous
            or self.confidence < CONFIDENCE_REVIEW
        )


def _hint_products(texte: str) -> set[str]:
    minuscule = texte.lower()
    return {
        produit
        for produit, marqueurs in PRODUCT_HINTS.items()
        if any(marqueur in minuscule for marqueur in marqueurs)
    }


def _pick_contract(registry: Registry, client_id: str, texte: str, link: Link) -> None:
    """Choisit le contrat parmi ceux du client, ou signale l'ambiguïté.

    Trois issues, et la troisième est celle qui protège :

    - un seul contrat au portefeuille → c'est celui-là, sans discussion ;
    - les indices de produit en désignent exactement un → on le retient ;
    - zéro ou plusieurs → on rend la liste et on marque le dossier ambigu.

    Ne jamais « prendre le plus probable » dans le dernier cas. Un contrat proposé
    au collaborateur a toutes les chances d'être validé sans être vérifié ; une
    ambiguïté affichée, elle, sera tranchée.
    """
    client = registry.clients.get(client_id)
    if client is None or not client.contracts:
        return

    references = list(client.contracts)
    if len(references) == 1:
        link.contract = references[0]
        return

    indices = _hint_products(texte)
    correspondants = [
        reference
        for reference, contrat in client.contracts.items()
        if contrat.produit in indices
    ]

    if len(correspondants) == 1:
        link.contract = correspondants[0]
        produit = client.contracts[link.contract].produit
        link.evidence.append(f"produit déduit du contenu : {produit}")
        return

    link.candidates = sorted(correspondants or references)
    link.ambiguous = True
    link.evidence.append(
        f"{len(link.candidates)} contrats possibles pour ce client, aucun ne se détache"
    )


async def link_message(
    message: Message,
    registry: Registry,
    *,
    groups: list[str] | None = None,
) -> Link:
    """Rattache un courriel, ou déclare qu'il n'est pas rattachable.

    Args:
        message: le courriel entrant.
        registry: le portefeuille.
        groups: groupes utilisés pour la recherche sémantique de dernier recours.
            Sans eux, cette étape est simplement sautée — jamais exécutée sans
            filtre. C'est la même règle que partout ailleurs : l'absence
            d'identité ferme la porte, elle ne l'ouvre pas.
    """
    texte = message.searchable
    lien = Link()

    # 1 — Une référence de contrat citée. Rien ne bat ça.
    references = registry.find_references(texte)
    if references:
        contrat = registry.contracts[references[0]]
        lien.client = contrat.client
        lien.contract = contrat.reference
        lien.method = "reference"
        lien.confidence = CONFIDENCE["reference"]
        lien.evidence.append(f"référence {contrat.reference} citée dans le message")
        if len(references) > 1:
            lien.candidates = references
            lien.ambiguous = True
            lien.evidence.append(f"{len(references)} références citées, la première retenue")
        _finalise(lien, registry)
        return lien

    # 2 — L'adresse d'expéditeur est celle d'un contact connu.
    client_id = registry.by_email.get(message.sender)
    if client_id:
        lien.method, lien.confidence = "email", CONFIDENCE["email"]
        lien.evidence.append(f"adresse {message.sender} connue au portefeuille")

    # 3 — À défaut, le domaine de messagerie de l'entreprise.
    if not client_id:
        client_id = registry.client_of_domain(message.domain)
        if client_id:
            lien.method, lien.confidence = "domaine", CONFIDENCE["domaine"]
            lien.evidence.append(f"domaine {message.domain} rattaché au portefeuille")

    # 4 — Un SIREN reconnu, y compris dans une pièce jointe.
    if not client_id:
        sirens = registry.find_sirens(texte)
        if sirens:
            client_id = registry.by_siren[sirens[0]]
            lien.method, lien.confidence = "siren", CONFIDENCE["siren"]
            lien.evidence.append(f"SIREN {sirens[0]} reconnu dans le message")

    # 5 — Dernier recours : ce que dit le contenu.
    if not client_id and groups:
        client_id = await _semantic_client(message, registry, groups)
        if client_id:
            lien.method, lien.confidence = "semantique", CONFIDENCE["semantique"]
            lien.evidence.append("rattachement déduit du contenu seul — à confirmer")

    if not client_id:
        lien.evidence.append(
            "aucun élément ne rattache ce message au portefeuille "
            "(ni référence, ni adresse, ni SIREN, ni contenu concordant)"
        )
        return lien

    lien.client = client_id
    _pick_contract(registry, client_id, texte, lien)
    _finalise(lien, registry)
    return lien


def _finalise(lien: Link, registry: Registry) -> None:
    if lien.client:
        client = registry.clients.get(lien.client)
        lien.client_nom = client.nom if client else lien.client
    if lien.ambiguous:
        # L'ambiguïté plafonne la confiance quelle que soit la voie empruntée :
        # être certain du client ne rend pas certain le contrat.
        lien.confidence = min(lien.confidence, CONFIDENCE_REVIEW - 0.01)


def _fold(texte: str) -> str:
    """Minuscules, sans accents, sans séparateurs — pour comparer des identités.

    « La Baule » dans un courriel et « labaule » dans un nom de domaine désignent
    le même endroit ; les traiter comme deux chaînes distinctes ferait rater le
    seul indice d'identité disponible.
    """
    import unicodedata

    sans_accent = "".join(
        c
        for c in unicodedata.normalize("NFD", texte.lower())
        if unicodedata.category(c) != "Mn"
    )
    return re.sub(r"[^a-z0-9]", "", sans_accent)


# Mots à ignorer dans une raison sociale : ils ne désignent personne.
_STOPWORDS_RAISON = frozenset(
    {"sarl", "sas", "sasu", "eurl", "selarl", "sci", "scop", "société", "societe",
     "entreprise", "groupe", "cabinet", "agence", "etablissements", "ets"}
)


def _identity_tokens(nom: str, domaine: str | None) -> set[str]:
    """Fragments qui identifient un client : mots de sa raison sociale, son domaine.

    On écarte la forme juridique et les mots trop courts : « SAS » et « LE » ne
    distinguent personne, et les retenir ferait correspondre n'importe qui.
    """
    tokens = {
        _fold(mot)
        for mot in re.split(r"[\s'’\-]+", nom)
        if len(mot) > 3 and mot.lower() not in _STOPWORDS_RAISON
    }
    if domaine:
        racine = domaine.split(".")[0]
        tokens.update(_fold(part) for part in racine.split("-") if len(part) > 3)
    return {token for token in tokens if len(token) > 3}


async def _semantic_client(
    message: Message, registry: Registry, groups: list[str]
) -> str | None:
    """Le client dont les documents ressemblent au message — **et qui est nommé**.

    Un vote pondéré par le rang désigne le candidat, mais la ressemblance seule
    ne suffit pas à conclure, et c'est le point important : **la similarité
    sémantique dit de quoi on parle, pas à qui on parle.**

    Le cas qui l'a démontré : un prospect charcutier écrit pour un devis
    multirisque. Ses documents ressemblent à ceux d'une boulangerie du
    portefeuille — même métier de bouche, mêmes laboratoires, même vocabulaire —
    et la boulangerie l'emportait largement au vote. Le message était pourtant
    signé d'un autre nom, d'une autre ville, d'un autre domaine : rien n'y
    désignait la boulangerie. Le rattachement était confiant et faux, c'est-à-dire
    la pire des sorties possibles.

    On exige donc une **corroboration d'identité** : au moins un fragment du nom
    ou du domaine du candidat doit apparaître dans le message. Le vote propose,
    l'identité confirme. Sans elle, on ne rattache pas.
    """
    requete = f"{message.subject}\n\n{message.body}"[:1500]
    passages = await retrieve.search(requete, groups, k=8)

    comptes: dict[str, float] = {}
    for rang, passage in enumerate(passages, start=1):
        client_id = passage.meta.get("client")
        if client_id and client_id in registry.clients:
            # Pondération par le rang : un passage en tête pèse plus qu'un
            # huitième, sinon la queue de liste décide.
            comptes[client_id] = comptes.get(client_id, 0.0) + 1.0 / rang

    if not comptes:
        return None

    classement = sorted(comptes.items(), key=lambda item: item[1], reverse=True)
    meilleur, score = classement[0]
    second = classement[1][1] if len(classement) > 1 else 0.0

    # Il faut un écart net. Deux clients au coude-à-coude, c'est une ambiguïté
    # qu'on n'a pas le droit de trancher sur un dixième de point.
    if score < 2 * second:
        logger.debug("rattachement sémantique écarté : pas d'écart net")
        return None

    client = registry.clients[meilleur]
    empreinte = _fold(f"{message.subject} {message.body} {message.sender}")
    tokens = _identity_tokens(client.nom, client.domaine)
    if not any(token in empreinte for token in tokens):
        logger.info(
            "rattachement sémantique écarté : aucun élément d'identité",
            extra={"candidat": meilleur},
        )
        return None
    return meilleur


def normalise_reference(brut: str) -> str:
    return re.sub(r"\s+", "", brut).upper()
