"""Lecture d'un courriel `.eml` : en-têtes, corps, pièces jointes.

Le format des courriels est un marécage — encodages multiples, corps HTML et
texte concurrents, pièces jointes imbriquées, en-têtes encodés en RFC 2047. Le
module `email` de la bibliothèque standard sait tout ça ; la seule chose à
décider ici, c'est ce qu'on garde.

**Ce qu'on garde du corps.** La partie `text/plain` quand elle existe, la partie
HTML dépouillée sinon. Un courriel professionnel contient presque toujours les
deux, et le texte brut est plus propre à analyser que du HTML de client Outlook.

**Ce qu'on jette : les citations.** Un fil de discussion à six réponses répète six
fois le premier message. Le donner tel quel à un classifieur, c'est lui faire
qualifier l'ancienne demande plutôt que la nouvelle — la panne la plus fréquente
et la plus discrète d'un tri automatique de boîte mail.

**Les en-têtes `X-Attendu-*` sont retirés du corps d'analyse mais conservés à
part.** Ils portent la vérité terrain du jeu de démonstration. Les laisser passer
au modèle reviendrait à lui souffler la réponse, et la mesure ne vaudrait rien.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from email import message_from_bytes, policy
from email.message import EmailMessage
from email.utils import getaddresses, parsedate_to_datetime
from pathlib import Path

logger = logging.getLogger("agent.broker.message")

# Préfixe des en-têtes de vérité terrain. Ils n'existent que dans la boîte de
# démonstration ; un courriel réel n'en a jamais.
GROUND_TRUTH_PREFIX = "X-Attendu-"

# Marqueurs de début de citation. Ce qui suit appartient à un message antérieur.
_QUOTE_MARKERS = (
    re.compile(r"^\s*-{2,}\s*Message d'origine\s*-{2,}", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*-{2,}\s*Original Message\s*-{2,}", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*Le .{5,60} a écrit\s*:", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*On .{5,60} wrote\s*:", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*De\s*:.*\n\s*Envoyé\s*:", re.IGNORECASE | re.MULTILINE),
)

_SIGNATURE = re.compile(r"^\s*--\s*$", re.MULTILINE)


@dataclass
class Attachment:
    filename: str
    mimetype: str
    size: int
    text: str | None = None
    """Contenu, quand il est lisible sans OCR. `None` pour une image ou un PDF
    scanné : ce POC ne prétend pas les lire, et un texte vide serait pris pour
    un document sans information plutôt que pour un document non lu."""


@dataclass
class Message:
    """Un courriel entrant, réduit à ce dont le traitement a besoin."""

    message_id: str
    sender: str
    """Adresse seule, en minuscules. Le nom d'affichage est dans `sender_name`."""
    sender_name: str | None
    subject: str
    body: str
    date: str | None = None
    attachments: list[Attachment] = field(default_factory=list)
    ground_truth: dict = field(default_factory=dict)
    path: Path | None = None

    @property
    def domain(self) -> str:
        _, _, domaine = self.sender.partition("@")
        return domaine.lower()

    @property
    def searchable(self) -> str:
        """Tout le texte exploitable : sujet, corps, pièces jointes lisibles.

        Les pièces jointes en font partie parce que c'est souvent là que se
        trouve l'accroche — un SIREN sur un Kbis, une immatriculation sur une
        carte grise. Les ignorer réduirait le rattachement au seul corps du
        message, qui est justement la partie la moins précise.
        """
        morceaux = [self.subject, self.body]
        morceaux.extend(
            piece.text for piece in self.attachments if piece.text
        )
        return "\n\n".join(part for part in morceaux if part)


def strip_quotes(body: str) -> str:
    """Retire les citations et la signature. Rend au moins le premier paragraphe.

    Le garde-fou compte : sur un message intégralement cité — une transmission
    sans commentaire — une découpe stricte ne laisserait rien du tout, et le
    classifieur recevrait une chaîne vide. Mieux vaut alors le message entier.
    """
    coupe = len(body)
    for marqueur in _QUOTE_MARKERS:
        trouve = marqueur.search(body)
        if trouve and trouve.start() < coupe:
            coupe = trouve.start()

    tete = body[:coupe]

    # Lignes préfixées de « > », qui peuvent précéder tout marqueur explicite.
    lignes = tete.split("\n")
    while lignes and lignes[-1].lstrip().startswith(">"):
        lignes.pop()
    tete = "\n".join(lignes)

    signature = _SIGNATURE.search(tete)
    if signature:
        tete = tete[: signature.start()]

    tete = tete.strip()
    return tete or body.strip()


def _text_from_html(raw: str) -> str:
    from agent.core.rag.parse import _from_html

    texte, _ = _from_html(raw)
    return texte


def _body_of(message: EmailMessage) -> str:
    partie = message.get_body(preferencelist=("plain", "html"))
    if partie is None:
        return ""
    contenu = partie.get_content()
    if partie.get_content_type() == "text/html":
        contenu = _text_from_html(contenu)
    return contenu


def _attachments_of(message: EmailMessage) -> list[Attachment]:
    pieces: list[Attachment] = []
    for partie in message.iter_attachments():
        nom = partie.get_filename() or "sans-nom"
        charge = partie.get_payload(decode=True) or b""
        texte: str | None = None
        if partie.get_content_maintype() == "text":
            try:
                texte = charge.decode(partie.get_content_charset() or "utf-8", "replace")
            except (LookupError, UnicodeDecodeError):
                texte = None
        pieces.append(
            Attachment(
                filename=nom,
                mimetype=partie.get_content_type(),
                size=len(charge),
                text=texte,
            )
        )
    return pieces


def parse(raw: bytes, *, path: Path | None = None) -> Message:
    """Lit un courriel au format RFC 5322."""
    brut = message_from_bytes(raw, policy=policy.default)

    adresses = getaddresses([brut.get("From", "")])
    nom, adresse = adresses[0] if adresses else ("", "")

    date = None
    if brut.get("Date"):
        try:
            date = parsedate_to_datetime(brut["Date"]).isoformat()
        except (TypeError, ValueError):
            logger.debug("date de courriel illisible : %r", brut.get("Date"))

    verite = {
        cle[len(GROUND_TRUTH_PREFIX) :].lower(): str(valeur)
        for cle, valeur in brut.items()
        if cle.startswith(GROUND_TRUTH_PREFIX)
    }

    return Message(
        message_id=str(brut.get("Message-ID", "")).strip("<>"),
        sender=adresse.lower(),
        sender_name=nom or None,
        subject=str(brut.get("Subject", "")),
        body=strip_quotes(_body_of(brut)),
        date=date,
        attachments=_attachments_of(brut),
        ground_truth=verite,
        path=path,
    )


def read(path: Path) -> Message:
    return parse(path.read_bytes(), path=path)


def read_mailbox(root: Path) -> list[Message]:
    """Tous les `.eml` d'un dossier, triés — l'ordre rend les exécutions comparables."""
    if not root.is_dir():
        raise FileNotFoundError(f"Boîte introuvable : {root}")
    return [read(chemin) for chemin in sorted(root.glob("*.eml"))]
