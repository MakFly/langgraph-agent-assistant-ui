"""Pièces manquantes : ce qui bloque le dossier avant même de le traiter.

C'est la fonction qui fait gagner du temps pour de bon. Un dossier qui part chez
la compagnie sans le relevé de sinistralité revient huit jours plus tard, et le
délai perçu par le client double. Détecter le manque au premier courriel, c'est
un aller-retour économisé sur chaque demande.

**Énumérer, puis cocher — jamais générer.** La première version demandait au
modèle de rédiger la liste des pièces manquantes à partir d'un extrait du
référentiel. Résultat mesuré : 65 % de rappel et **19 pièces réclamées à tort sur
15 courriels**. Le défaut n'était pas le modèle, c'était la tâche : rédiger une
liste, c'est en inventer les éléments.

La chaîne actuelle inverse la charge :

    1. le référentiel est chargé ENTIER depuis l'index (filtre sur ses
       métadonnées, pas une recherche sémantique qui pourrait le rater) ;
    2. il est découpé en sections énumérées, une par type de demande ;
    3. le modèle CHOISIT une section et coche ses pièces, une par une ;
    4. tout ce qui n'est pas un intitulé exact du référentiel est **jeté**.

L'étape 4 est la garantie : quoi que réponde le modèle, la sortie est un
sous-ensemble du référentiel. Réclamer une pièce inexistante devient impossible,
pas improbable.

**Le référentiel vit dans le corpus, pas dans le code.** La liste des
justificatifs exigés est un document du cabinet
(`public/procedures/pieces-par-demande.md`) : c'est le collaborateur, pas le
développeur, qui sait qu'une compagnie réclame désormais une pièce de plus. Sa
lecture repasse par les ACL de l'appelant comme n'importe quel autre document.

**Ce module ne réclame que des pièces, jamais une décision.** Il ne dit pas si la
garantie joue, il dit ce qui manque pour instruire.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from dataclasses import dataclass, field

from agent.core.broker.classify import Classification
from agent.core.broker.message import Message
from agent.core.rag import llm
from agent.infra import ragdb

logger = logging.getLogger("agent.broker.missing")

# Intentions pour lesquelles il n'y a rien à réunir. Aller chercher un
# référentiel pour une infolettre coûterait un appel de modèle pour rien.
NO_DOCUMENTS = frozenset({"hors_perimetre", "administrative", "coverage_question"})

# Le document qui fait foi, désigné par ses métadonnées et non par une recherche.
# Un référentiel qu'on retrouve « la plupart du temps » est un référentiel qui
# manquera le jour où le corpus grossit.
REFERENTIEL_FILTER = {"type": "procedure_interne", "domaine": "pieces-par-demande"}

_SECTION = re.compile(r"^##\s+(.+?)\s+—\s+(.+)$", re.MULTILINE)
_PUCE = re.compile(r"^-\s+(.+?)\s*$", re.MULTILINE)


@dataclass
class Section:
    key: str
    """Clé de la demande, par exemple `claim / dégât des eaux`."""
    title: str
    items: list[str] = field(default_factory=list)

    @property
    def intention(self) -> str:
        return self.key.split("/")[0].strip()


@dataclass
class Piece:
    piece: str
    pourquoi: str = ""


@dataclass
class Checklist:
    manquantes: list[Piece] = field(default_factory=list)
    fournies: list[str] = field(default_factory=list)
    section: str | None = None
    """Section du référentiel retenue. Mesurable, donc vérifiable."""
    referentiel_items: list[str] = field(default_factory=list)
    """Toutes les pièces de la section retenue, cochées ou non.

    Conservé pour que la mesure puisse distinguer une pièce réclamée à tort —
    elle est au référentiel, elle était simplement déjà fournie — d'une pièce
    inventée, qui n'y figure pas du tout. Sans cette liste, les deux fautes
    seraient indiscernables alors qu'elles ne se corrigent pas pareil."""
    sources: list[str] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)
    """Intitulés proposés par le modèle et écartés faute de figurer au
    référentiel. Vide en régime normal ; non vide, c'est le signal que le prompt
    ou le référentiel a dérivé."""
    evaluated: bool = True
    """Faux quand rien n'a pu être évalué (référentiel introuvable, modèle muet).
    Une liste vide « rien ne manque » et une liste vide « on n'a pas regardé »
    n'appellent pas du tout la même suite."""
    reason: str | None = None


# --- Lecture du référentiel ---------------------------------------------------


def parse_referentiel(texte: str) -> list[Section]:
    """Découpe le document en sections énumérées.

    Le format attendu est `## <clé> — <titre>` suivi d'une liste à puces. Une
    section sans puce est ignorée : elle ne permettrait pas de cocher, donc elle
    n'a rien à faire dans le référentiel énumérable.
    """
    sections: list[Section] = []
    marques = list(_SECTION.finditer(texte))
    for index, marque in enumerate(marques):
        fin = marques[index + 1].start() if index + 1 < len(marques) else len(texte)
        corps = texte[marque.end() : fin]
        items = [item.strip() for item in _PUCE.findall(corps) if item.strip()]
        if items:
            sections.append(Section(marque.group(1).strip(), marque.group(2).strip(), items))
    return sections


async def load_referentiel(groups: list[str]) -> tuple[list[Section], str | None]:
    """Charge le référentiel ENTIER, dans l'ordre, filtré par les ACL.

    Chargé en entier et non par recherche : on veut *toutes* les sections pour
    que le modèle choisisse la bonne, et une recherche sémantique n'en
    ramènerait que les plus proches de la question — c'est-à-dire qu'elle
    déciderait à sa place, en amont, sans rien en savoir.

    Les groupes restent appliqués : le référentiel est un document comme un
    autre, et le fait qu'il soit chargé par un traitement automatique ne
    justifie pas de le lire sans droits.
    """
    if not groups:
        return [], "aucun groupe : lecture du référentiel fermée"

    filtre = json.dumps(REFERENTIEL_FILTER, ensure_ascii=False)
    async with ragdb.pool().acquire() as connection:
        rows = await connection.fetch(
            """
            SELECT d.source, c.ord, c.text
            FROM rag_chunks c
            JOIN rag_documents d ON d.id = c.document_id
            WHERE c.acl && $1::text[] AND c.meta @> $2::jsonb
            ORDER BY d.source, c.ord
            """,
            groups,
            filtre,
        )

    if not rows:
        return [], "référentiel des pièces absent de l'index accessible"

    # Les fragments sont recollés dans l'ordre : une section coupée en deux par
    # le découpage perdrait la moitié de ses puces si on les traitait isolément.
    texte = "\n\n".join(row["text"] for row in rows)
    sections = parse_referentiel(texte)
    if not sections:
        return [], "référentiel trouvé mais aucune section énumérable"
    return sections, rows[0]["source"]


# --- Cochage ------------------------------------------------------------------

_SYSTEM = """Tu prépares un dossier dans un cabinet de courtage en assurance.

On te donne le RÉFÉRENTIEL des pièces exigibles, découpé en sections numérotées,
puis un courriel de client et la liste des fichiers qu'il a joints.

Deux décisions, dans cet ordre :

1. choisis LA section du référentiel qui correspond à la demande — une seule.
   Cite d'abord, dans `element`, LE passage exact du courriel qui détermine ce
   choix : la nature du dommage, l'acte demandé, le bien concerné. Une section
   choisie sans pouvoir désigner ce passage est une section choisie au hasard ;
2. pour CHAQUE pièce de cette section, dis si elle est déjà au dossier ou non.

Le champ QUALITÉ DE L'EXPÉDITEUR t'est donné : il n'est pas à deviner. Quand
plusieurs sections ne diffèrent que par la personne qui demande — l'assuré
lui-même ou un tiers — c'est LUI qui tranche, jamais le fait qu'un tiers soit
mentionné dans le corps. Un assuré qui écrit « mon client me réclame une
attestation » reste un assuré qui demande.

Une pièce est « fournie » UNIQUEMENT si elle est jointe à CE message, ou si son
contenu est explicitement écrit dans CE corps.

Tout le reste est manquant, sans exception :
- une pièce que le client annonce sans la joindre ;
- une pièce qu'il a pu envoyer dans un échange antérieur. Tu ne vois que ce
  message : tu ne peux pas savoir, et supposer qu'elle est arrivée fait sortir
  le dossier incomplet. C'est au gestionnaire de rayer ce qu'il a déjà.

Un message qui répond à un précédent (« Re : ») ne prouve rien sur ce qui a été
transmis avant.

Tu ne peux citer que des numéros de pièces de la section choisie. N'écris aucun
intitulé nouveau : tu coches une liste, tu ne la rédiges pas.

Réponds UNIQUEMENT par un objet JSON :
{"element": "<le passage du courriel qui détermine la section>",
 "section": <numéro de section>,
 "manquantes": [<numéros>], "fournies": [<numéros>]}"""


def _fold(texte: str) -> str:
    sans_accent = "".join(
        c
        for c in unicodedata.normalize("NFD", texte.lower())
        if unicodedata.category(c) != "Mn"
    )
    return " ".join(sans_accent.replace("'", " ").split())


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


def _candidates(sections: list[Section], intention: str) -> list[Section]:
    """Sections plausibles pour cette intention.

    Restreindre en amont fait deux choses : le modèle ne peut pas choisir la
    section d'une autre intention (une erreur qu'il commet volontiers quand un
    sinistre évoque un véhicule), et le prompt reste court.
    """
    retenues = [section for section in sections if section.intention == intention]
    return retenues or sections


def _numbers(value) -> list[int]:
    """Les entiers d'une réponse, quelle que soit la forme rendue."""
    if value is None:
        return []
    if isinstance(value, int):
        return [value]
    if not isinstance(value, list):
        return []
    sortie: list[int] = []
    for item in value:
        try:
            sortie.append(int(item))
        except (TypeError, ValueError):
            continue
    return sortie


def _prompt(
    message: Message,
    classification: Classification,
    sections: list[Section],
    link_method: str | None = None,
) -> str:
    blocs = []
    for index, section in enumerate(sections, start=1):
        puces = "\n".join(
            f"  {position}. {item}" for position, item in enumerate(section.items, start=1)
        )
        blocs.append(f"SECTION {index} — {section.title} ({section.key})\n{puces}")

    joints = (
        "\n".join(
            f"- {piece.filename}"
            + (f"\n  contenu : {piece.text[:400]}" if piece.text else " (non lu)")
            for piece in message.attachments
        )
        or "- aucune"
    )

    return (
        "RÉFÉRENTIEL :\n\n" + "\n\n".join(blocs) + "\n\n"
        f"DEMANDE : {classification.intention}\n"
        f"RÉSUMÉ : {classification.resume}\n"
        f"QUALITÉ DE L'EXPÉDITEUR : {sender_quality(link_method)}\n\n"
        f"OBJET : {message.subject}\n"
        f"COURRIEL :\n{message.body[:2500]}\n\n"
        f"FICHIERS JOINTS :\n{joints}"
    )


# Voies de rattachement qui établissent que l'expéditeur EST l'assuré : son
# adresse est celle du contact enregistré, ou son domaine est celui de
# l'entreprise. Les autres voies (SIREN cité, référence citée, contenu) peuvent
# tout aussi bien venir d'un tiers mandaté.
_VOIES_ASSURE = frozenset({"email", "domaine"})


def sender_quality(link_method: str | None) -> str:
    """Qualité de l'expéditeur, DÉDUITE du rattachement.

    Le rattachement connaît déjà la réponse et ne la transmettait pas : c'est ce
    qui faisait confondre « attestation demandée par l'assuré » et « demandée
    par un tiers », les deux seules sections que rien ne distingue dans le
    corps du message. La faire remonter coûte une ligne et supprime la classe
    d'erreur entière — un fait connu vaut mieux qu'une inférence.
    """
    if link_method in _VOIES_ASSURE:
        return "l'expéditeur EST l'assuré (son adresse ou son domaine est au portefeuille)"
    if link_method is None:
        return "expéditeur non rattaché au portefeuille : ce n'est pas un assuré connu"
    return (
        "l'expéditeur n'est PAS identifié comme l'assuré : il cite le dossier "
        "sans écrire depuis une adresse du portefeuille (tiers probable)"
    )


async def checklist(
    message: Message,
    classification: Classification,
    groups: list[str],
    *,
    link_method: str | None = None,
) -> Checklist:
    """Pièces manquantes pour cette demande, d'après le référentiel du cabinet."""
    if classification.intention in NO_DOCUMENTS:
        return Checklist(reason=f"aucune pièce attendue pour « {classification.intention} »")

    sections, source = await load_referentiel(groups)
    if not sections:
        # Sans référentiel, on ne devine pas : réclamer des pièces sorties de
        # nulle part ferait perdre du temps au client et discréditerait l'outil.
        return Checklist(evaluated=False, reason=source or "référentiel introuvable")

    candidates = _candidates(sections, classification.intention)
    reponse = await llm.ask(
        _SYSTEM, _prompt(message, classification, candidates, link_method)
    )
    if reponse is None:
        return Checklist(evaluated=False, reason="modèle indisponible")

    parsed = _extract_json(reponse)
    if parsed is None:
        return Checklist(evaluated=False, reason="réponse illisible")

    numeros = _numbers(parsed.get("section"))
    if not numeros or not 1 <= numeros[0] <= len(candidates):
        return Checklist(
            evaluated=False, reason=f"section hors bornes : {parsed.get('section')!r}"
        )
    section = candidates[numeros[0] - 1]

    # **La garantie.** La sortie est construite depuis le référentiel, à partir
    # d'indices ; le modèle ne fournit jamais d'intitulé. Une pièce inventée est
    # donc impossible, et non simplement rare.
    def _items(cle: str) -> tuple[list[str], list[str]]:
        gardes, ecartes = [], []
        for numero in _numbers(parsed.get(cle)):
            if 1 <= numero <= len(section.items):
                gardes.append(section.items[numero - 1])
            else:
                ecartes.append(f"{cle}[{numero}] hors bornes")
        return gardes, ecartes

    manquantes, rejets_m = _items("manquantes")
    fournies, rejets_f = _items("fournies")

    # Une pièce citée des deux côtés est une contradiction du modèle. On la
    # traite comme manquante : réclamer une pièce déjà là fait perdre un
    # aller-retour, ne pas réclamer une pièce absente bloque le dossier.
    fournies = [item for item in fournies if item not in set(manquantes)]

    if rejets_m or rejets_f:
        logger.info("indices hors bornes écartés : %s", rejets_m + rejets_f)

    return Checklist(
        manquantes=[Piece(item) for item in manquantes],
        fournies=fournies,
        section=section.key,
        referentiel_items=list(section.items),
        sources=[f"{source}#{index}" for index in range(1)] if source else [],
        rejected=rejets_m + rejets_f,
    )
