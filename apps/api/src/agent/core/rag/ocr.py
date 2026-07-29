"""OCR visuel piloté par un modèle multimodal configurable.

Le provider et le modèle ne viennent jamais d'une constante de ce module : ils
sont enregistrés sur la source d'ingestion puis passés à ``create_model``. Le
même pipeline sait donc employer OpenAI, Gemini, Groq ou Ollama, sous réserve que
le modèle choisi accepte les images.
"""

from __future__ import annotations

import base64
import mimetypes
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage

from agent.core.model import PROVIDER_KEYS, create_model

DEFAULT_PROMPT = (
    "Transcris fidèlement cette page en texte structuré. Préserve les titres, "
    "listes, tableaux, montants, dates et références. N'invente rien et ne "
    "commente pas le document. Si une zone est illisible, écris [illisible]."
)

IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"})


class OcrError(Exception):
    """Configuration invalide ou réponse OCR inexploitable."""


@dataclass(frozen=True)
class OcrConfig:
    enabled: bool = False
    provider: str = "openai"
    model: str = ""
    prompt: str = DEFAULT_PROMPT
    max_pages: int = 40
    dpi: int = 160

    @classmethod
    def from_mapping(cls, value: dict[str, Any] | None) -> OcrConfig:
        raw = value or {}
        config = cls(
            enabled=bool(raw.get("enabled", False)),
            provider=str(raw.get("provider", "openai")).strip().lower(),
            model=str(raw.get("model", "")).strip(),
            prompt=str(raw.get("prompt", DEFAULT_PROMPT)).strip() or DEFAULT_PROMPT,
            max_pages=int(raw.get("max_pages", 40)),
            dpi=int(raw.get("dpi", 160)),
        )
        config.validate()
        return config

    def validate(self) -> None:
        if self.provider not in PROVIDER_KEYS:
            raise OcrError(f"Provider OCR inconnu : {self.provider}")
        if self.enabled and not self.model:
            raise OcrError("Un modèle OCR est requis quand l'OCR est activé")
        if not 1 <= self.max_pages <= 200:
            raise OcrError("max_pages doit être compris entre 1 et 200")
        if not 72 <= self.dpi <= 300:
            raise OcrError("dpi doit être compris entre 72 et 300")
        if len(self.prompt) > 6000:
            raise OcrError("Le prompt OCR ne peut pas dépasser 6000 caractères")

    def json(self) -> dict[str, Any]:
        return asdict(self)


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts).strip()
    return str(content).strip()


async def transcribe_image(
    image: bytes,
    *,
    mime_type: str,
    config: OcrConfig,
    page: int | None = None,
) -> str:
    """Envoie une image au couple provider/modèle choisi pour la source."""
    if not config.enabled:
        raise OcrError("OCR désactivé pour cette source")

    model = create_model(
        provider=config.provider,
        model=config.model,
        temperature=0,
        reasoning_effort="default",
    )
    page_hint = f"\nPage {page} du document." if page is not None else ""
    data_url = f"data:{mime_type};base64,{base64.b64encode(image).decode('ascii')}"
    message = HumanMessage(
        content=[
            {"type": "text", "text": f"{config.prompt}{page_hint}"},
            {"type": "image_url", "image_url": {"url": data_url}},
        ]
    )
    try:
        response = await model.ainvoke([message])
    except Exception as error:  # noqa: BLE001 - les providers ont leurs exceptions
        raise OcrError(
            f"OCR {config.provider}/{config.model} en échec"
            f"{f' à la page {page}' if page else ''} : {error}"
        ) from error

    text = _message_text(response.content)
    if not text:
        raise OcrError(
            f"OCR {config.provider}/{config.model} : réponse vide"
            f"{f' à la page {page}' if page else ''}"
        )
    return text


async def transcribe_file(path: Path, config: OcrConfig) -> str:
    mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return await transcribe_image(path.read_bytes(), mime_type=mime_type, config=config)


def render_pdf_page(path: Path, page_index: int, *, dpi: int) -> bytes:
    """Rend une page PDF en PNG sans conserver tout le document en mémoire."""
    import fitz

    try:
        with fitz.open(path) as document:
            page = document.load_page(page_index)
            scale = dpi / 72
            pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
            return pixmap.tobytes("png")
    except Exception as error:  # noqa: BLE001 - PyMuPDF expose plusieurs familles
        raise OcrError(f"Impossible de rendre la page {page_index + 1} : {error}") from error
