"""Découpage d'un document en fragments indexables.

Le découpage est le réglage qui pèse le plus sur la qualité d'un RAG, et le plus
souvent choisi au hasard. Deux règles tiennent lieu de doctrine ici :

1. **Ne jamais couper au milieu d'un paragraphe** quand on peut l'éviter. Un
   fragment qui commence en plein milieu d'une phrase se retrouve cité tel quel
   dans la réponse, et le lecteur ne peut pas vérifier.
2. **Recouvrir d'un paragraphe.** Une information à cheval sur deux fragments est
   sinon introuvable : chaque moitié, seule, ne répond à rien.

La taille est mesurée en tokens *estimés* (≈ 4 caractères par token). Une mesure
exacte demanderait le tokeniseur du modèle d'embedding, donc une dépendance de
plus et un couplage au fournisseur, pour un gain nul : la cible est un ordre de
grandeur, pas une limite dure.
"""

from __future__ import annotations

import re

# Ordre de grandeur retenu partout ailleurs dans le projet (cf. la fenêtre de
# contexte du graphe) : un token vaut environ quatre caractères en français.
CHARS_PER_TOKEN = 4

DEFAULT_MAX_TOKENS = 800
DEFAULT_MIN_TOKENS = 40


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // CHARS_PER_TOKEN)


def _split_paragraphs(text: str) -> list[str]:
    return [block.strip() for block in re.split(r"\n\s*\n", text) if block.strip()]


def _split_long_paragraph(paragraph: str, max_chars: int) -> list[str]:
    """Dernier recours : un paragraphe plus long qu'un fragment entier.

    On coupe alors sur les fins de phrase, et seulement à défaut sur les
    caractères — un tableau ou un bloc de code n'a pas de phrases.
    """
    sentences = re.split(r"(?<=[.!?…])\s+", paragraph)
    pieces: list[str] = []
    current = ""

    for sentence in sentences:
        while len(sentence) > max_chars:
            # Phrase seule plus longue que la limite : découpe brutale assumée.
            pieces.append(sentence[:max_chars])
            sentence = sentence[max_chars:]
        if not sentence:
            continue
        candidate = f"{current} {sentence}".strip()
        if len(candidate) > max_chars and current:
            pieces.append(current)
            current = sentence
        else:
            current = candidate

    if current:
        pieces.append(current)
    return pieces


def chunk_text(
    text: str,
    *,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    min_tokens: int = DEFAULT_MIN_TOKENS,
    overlap_paragraphs: int = 1,
) -> list[str]:
    """Fragments du document, dans l'ordre de lecture.

    Args:
        max_tokens: taille visée d'un fragment.
        min_tokens: en dessous, le dernier fragment est recollé au précédent —
            un fragment de trois mots pollue l'index sans jamais rien apprendre.
        overlap_paragraphs: paragraphes repris du fragment précédent.
    """
    max_chars = max_tokens * CHARS_PER_TOKEN
    paragraphs: list[str] = []
    for block in _split_paragraphs(text):
        if len(block) > max_chars:
            paragraphs.extend(_split_long_paragraph(block, max_chars))
        else:
            paragraphs.append(block)

    if not paragraphs:
        return []

    chunks: list[str] = []
    current: list[str] = []
    length = 0

    for paragraph in paragraphs:
        size = len(paragraph)
        if current and length + size > max_chars:
            chunks.append("\n\n".join(current))
            # Recouvrement : on repart des derniers paragraphes du fragment clos.
            current = current[-overlap_paragraphs:] if overlap_paragraphs else []
            length = sum(len(part) for part in current)
        current.append(paragraph)
        length += size

    if current:
        chunks.append("\n\n".join(current))

    # Une queue trop courte n'est pas un fragment, c'est un reste.
    if len(chunks) > 1 and estimate_tokens(chunks[-1]) < min_tokens:
        tail = chunks.pop()
        chunks[-1] = f"{chunks[-1]}\n\n{tail}"

    return chunks
