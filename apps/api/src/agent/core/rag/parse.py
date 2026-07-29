"""Extraction du texte d'un fichier du corpus.

Volontairement modeste. L'extraction documentaire est un puits sans fond — tableaux,
colonnes, en-têtes répétés, PDF scannés qui demandent de l'OCR — et c'est là que
part l'essentiel de l'effort d'un vrai RAG, pas dans la similarité vectorielle.

Ce module traite le cas honnête : du texte structuré en paragraphes. Un PDF dont
`pypdf` ne tire rien (document scanné) est signalé plutôt qu'indexé vide, parce
qu'un document présent mais silencieux est pire qu'un document absent : il donne
l'illusion de la couverture.

**Le front-matter YAML est extrait, pas indexé.** Un document du corpus peut
porter un en-tête `--- ... ---` décrivant ce qu'il est : type, client, référence
de contrat, produit, dates. Ces champs servent à filtrer une recherche
(« uniquement les contrats de ce client ») et à contextualiser un fragment ; les
laisser dans le texte vectorisé serait doublement nuisible — ils diluent le sens
du fragment, et la recherche lexicale se mettrait à répondre sur des clés YAML.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

logger = logging.getLogger("agent.rag.parse")

TEXT_SUFFIXES = frozenset({".md", ".markdown", ".txt", ".html", ".htm"})
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"})
SUPPORTED_SUFFIXES = TEXT_SUFFIXES | IMAGE_SUFFIXES | {".pdf"}

# Champs du front-matter conservés. Une liste blanche, et non « tout ce qui est
# là » : les métadonnées partent en base et servent au filtrage, donc un document
# ne doit pas pouvoir injecter une clé arbitraire dans l'index en étant
# simplement déposé dans le corpus.
METADATA_FIELDS = frozenset(
    {
        "type",
        "reference",
        "client",
        "client_nom",
        "client_email",
        "client_domaine",
        "siren",
        "produit",
        "produit_label",
        "compagnie",
        "compagnie_nom",
        "contrat",
        "sinistre",
        "date",
        "date_effet",
        "date_emission",
        "date_survenance",
        "date_visite",
        "annee",
        "nature",
        "statut",
        "intention",
        "sujet",
        "domaine",
        "prospect",
        "collaborateur",
    }
)


@dataclass
class Parsed:
    """Ce qu'un fichier du corpus rend une fois lu."""

    text: str
    title: str | None
    meta: dict = field(default_factory=dict)


class ParseError(Exception):
    """Le fichier est reconnu mais illisible."""


class _TextExtractor(HTMLParser):
    """Texte visible d'une page : ni script, ni style, ni balises."""

    _IGNORED = frozenset({"script", "style", "noscript", "template"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title: str | None = None
        self._skip_depth = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in self._IGNORED:
            self._skip_depth += 1
        elif tag == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag in self._IGNORED and self._skip_depth:
            self._skip_depth -= 1
        elif tag == "title":
            self._in_title = False
        elif tag in {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "section"}:
            # Sans ça, deux blocs voisins se retrouvent collés en un seul mot.
            self.parts.append("\n\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_title and self.title is None:
            self.title = data.strip() or None
        self.parts.append(data)


def _from_html(raw: str) -> tuple[str, str | None]:
    extractor = _TextExtractor()
    extractor.feed(raw)
    return "".join(extractor.parts), extractor.title


def _from_pdf(path: Path) -> tuple[str, str | None]:
    from pypdf import PdfReader

    try:
        reader = PdfReader(str(path))
        pages = [page.extract_text() or "" for page in reader.pages]
    except Exception as error:  # noqa: BLE001 - pypdf lève des exceptions variées
        raise ParseError(f"PDF illisible : {error}") from error

    text = "\n\n".join(page.strip() for page in pages if page.strip())
    if not text.strip():
        raise ParseError(
            "PDF sans couche texte (document scanné ?) — il faudrait de l'OCR, "
            "que ce projet ne fait pas"
        )

    title = None
    if reader.metadata and reader.metadata.title:
        title = str(reader.metadata.title).strip() or None
    return text, title


async def _from_pdf_with_ocr(path: Path, config) -> tuple[str, str | None]:
    """Conserve la couche texte et n'OCRise que les pages réellement scannées."""
    from pypdf import PdfReader

    from agent.core.rag import ocr

    try:
        reader = PdfReader(str(path))
        native_pages = [page.extract_text() or "" for page in reader.pages]
    except Exception as error:  # noqa: BLE001
        raise ParseError(f"PDF illisible : {error}") from error

    rendered = 0
    pages: list[str] = []
    for index, native in enumerate(native_pages):
        native = normalize(native)
        # Quelques caractères isolés sont souvent un numéro de page ou un artefact,
        # pas une vraie couche texte. Ils ne doivent pas empêcher l'OCR.
        if len(native) >= 20:
            pages.append(native)
            continue
        if not config.enabled:
            pages.append(native)
            continue
        rendered += 1
        if rendered > config.max_pages:
            raise ParseError(
                f"Le document demande l'OCR de plus de {config.max_pages} page(s), "
                "plafond configuré pour cette source"
            )
        try:
            image = ocr.render_pdf_page(path, index, dpi=config.dpi)
            pages.append(
                await ocr.transcribe_image(
                    image,
                    mime_type="image/png",
                    config=config,
                    page=index + 1,
                )
            )
        except ocr.OcrError as error:
            raise ParseError(str(error)) from error

    text = "\n\n".join(page for page in pages if page.strip())
    if not text.strip():
        raise ParseError(
            "PDF sans couche texte (document scanné ?) — activez et configurez "
            "un modèle OCR multimodal sur la source"
        )
    title = None
    if reader.metadata and reader.metadata.title:
        title = str(reader.metadata.title).strip() or None
    return text, title


def _markdown_title(text: str) -> str | None:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip() or None
        if line.strip():
            # Le titre doit être en tête : plus bas, c'est une section.
            break
    return None


def normalize(text: str) -> str:
    """Espaces homogènes et paragraphes conservés.

    Les paragraphes sont la seule structure sur laquelle le découpage s'appuie :
    on écrase les espaces horizontaux mais jamais les lignes vides.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return "\n".join(line.strip() for line in text.split("\n")).strip()


_FRONT_MATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def split_front_matter(raw: str) -> tuple[dict, str]:
    """Sépare l'en-tête YAML du corps. Rend `({}, raw)` s'il n'y en a pas.

    Un en-tête illisible n'interrompt pas l'ingestion : le document reste
    indexable, seulement sans métadonnées. Le contraire — refuser un document
    entier à cause d'une virgule dans son en-tête — rendrait le corpus fragile
    pour un bénéfice nul, puisque le texte, lui, est parfaitement exploitable.
    """
    match = _FRONT_MATTER.match(raw)
    if not match:
        return {}, raw

    import yaml

    try:
        parsed = yaml.safe_load(match.group(1))
    except yaml.YAMLError as error:
        logger.warning("front-matter illisible, métadonnées ignorées : %s", error)
        return {}, raw[match.end() :]

    if not isinstance(parsed, dict):
        return {}, raw[match.end() :]

    meta = {
        key: value
        for key, value in parsed.items()
        if key in METADATA_FIELDS and value not in (None, "")
    }
    ignores = set(parsed) - METADATA_FIELDS
    if ignores:
        logger.debug("champs de front-matter hors liste blanche : %s", sorted(ignores))
    return meta, raw[match.end() :]


def read_document(path: Path) -> Parsed:
    """Lit un fichier du corpus. Lève `ParseError` si le contenu est inexploitable."""
    suffix = path.suffix.lower()
    meta: dict = {}

    if suffix not in SUPPORTED_SUFFIXES:
        raise ParseError(f"Extension non prise en charge : {suffix}")

    if suffix == ".pdf":
        text, title = _from_pdf(path)
    elif suffix in IMAGE_SUFFIXES:
        raise ParseError(
            "Une image demande un modèle OCR — utilisez une source d'ingestion "
            "avec l'OCR activé"
        )
    else:
        try:
            raw = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as error:
            raise ParseError(f"Fichier non UTF-8 : {error}") from error

        if suffix in {".html", ".htm"}:
            text, title = _from_html(raw)
        else:
            # Le front-matter est retiré AVANT la recherche du titre : sinon le
            # `---` d'ouverture serait la première ligne non vide et couperait
            # court à la détection du `# Titre`.
            meta, raw = split_front_matter(raw)
            text, title = raw, _markdown_title(raw)

    text = normalize(text)
    if not text:
        raise ParseError("Document vide après extraction")

    return Parsed(text=text, title=title or path.stem, meta=meta)


async def read_document_async(path: Path, ocr_config: dict | None = None) -> Parsed:
    """Version ingestion : même extraction, avec OCR dynamique si configuré."""
    from agent.core.rag import ocr

    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ParseError(f"Extension non prise en charge : {suffix}")

    try:
        config = ocr.OcrConfig.from_mapping(ocr_config)
    except (ocr.OcrError, TypeError, ValueError) as error:
        raise ParseError(f"Configuration OCR invalide : {error}") from error

    if suffix in TEXT_SUFFIXES:
        return read_document(path)

    if suffix == ".pdf":
        text, title = await _from_pdf_with_ocr(path, config)
        return Parsed(text=normalize(text), title=title or path.stem)

    if not config.enabled:
        return read_document(path)
    try:
        text = await ocr.transcribe_file(path, config)
    except (OSError, ocr.OcrError) as error:
        raise ParseError(str(error)) from error
    text = normalize(text)
    if not text:
        raise ParseError("Document vide après OCR")
    return Parsed(text=text, title=path.stem)
