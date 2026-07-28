"""Extraction du texte d'un fichier du corpus.

Volontairement modeste. L'extraction documentaire est un puits sans fond — tableaux,
colonnes, en-têtes répétés, PDF scannés qui demandent de l'OCR — et c'est là que
part l'essentiel de l'effort d'un vrai RAG, pas dans la similarité vectorielle.

Ce module traite le cas honnête : du texte structuré en paragraphes. Un PDF dont
`pypdf` ne tire rien (document scanné) est signalé plutôt qu'indexé vide, parce
qu'un document présent mais silencieux est pire qu'un document absent : il donne
l'illusion de la couverture.
"""

from __future__ import annotations

import logging
import re
from html.parser import HTMLParser
from pathlib import Path

logger = logging.getLogger("agent.rag.parse")

SUPPORTED_SUFFIXES = frozenset({".md", ".markdown", ".txt", ".html", ".htm", ".pdf"})


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


def read_document(path: Path) -> tuple[str, str | None]:
    """Retourne `(texte, titre)`. Lève `ParseError` si le contenu est inexploitable."""
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ParseError(f"Extension non prise en charge : {suffix}")

    if suffix == ".pdf":
        text, title = _from_pdf(path)
    else:
        try:
            raw = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as error:
            raise ParseError(f"Fichier non UTF-8 : {error}") from error

        if suffix in {".html", ".htm"}:
            text, title = _from_html(raw)
        else:
            text, title = raw, _markdown_title(raw)

    text = normalize(text)
    if not text:
        raise ParseError("Document vide après extraction")

    return text, title or path.stem
