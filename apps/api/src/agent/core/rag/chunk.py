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
from dataclasses import dataclass

# Ordre de grandeur retenu partout ailleurs dans le projet (cf. la fenêtre de
# contexte du graphe) : un token vaut environ quatre caractères en français.
CHARS_PER_TOKEN = 4

DEFAULT_MAX_TOKENS = 800
DEFAULT_MIN_TOKENS = 40

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")


@dataclass
class Chunk:
    """Un fragment, et la chaîne de titres sous laquelle il se trouve.

    Le chemin de titres est ce qui manque le plus cruellement à un fragment isolé.
    « La franchise est portée à 12 500 € » ne veut rien dire ; la même phrase sous
    « Avenant AV-2026-0018 › Conséquences » en dit assez pour être retrouvée et
    citée. C'est la matière première du préfixage contextuel (cf. `ingest`).
    """

    text: str
    headings: tuple[str, ...] = ()

    @property
    def heading_path(self) -> str:
        return " › ".join(self.headings)


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // CHARS_PER_TOKEN)


def _split_paragraphs(text: str) -> list[str]:
    return [block.strip() for block in re.split(r"\n\s*\n", text) if block.strip()]


def _heading_of(block: str) -> tuple[int, str] | None:
    """`(niveau, texte)` si le bloc est un titre markdown, sinon `None`.

    Seule la PREMIÈRE ligne est examinée : un `#` en milieu de paragraphe est un
    caractère ordinaire (une référence `#42`, un sélecteur CSS), pas un titre.
    """
    match = _HEADING.match(block.split("\n", 1)[0])
    if not match:
        return None
    return len(match.group(1)), match.group(2).strip()


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


def chunk_document(
    text: str,
    *,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    min_tokens: int = DEFAULT_MIN_TOKENS,
    overlap_paragraphs: int = 1,
) -> list[Chunk]:
    """Fragments du document, dans l'ordre de lecture, avec leur chemin de titres.

    Args:
        max_tokens: taille visée d'un fragment.
        min_tokens: en dessous, le dernier fragment est recollé au précédent —
            un fragment de trois mots pollue l'index sans jamais rien apprendre.
        overlap_paragraphs: paragraphes repris du fragment précédent.
    """
    max_chars = max_tokens * CHARS_PER_TOKEN

    # Chaque paragraphe est apparié au chemin de titres en vigueur à cet endroit.
    # Le chemin est maintenu comme une pile : un titre de niveau N remplace tout
    # ce qui était ouvert au niveau N et en dessous.
    paires: list[tuple[str, tuple[str, ...]]] = []
    pile: list[tuple[int, str]] = []

    for block in _split_paragraphs(text):
        heading = _heading_of(block)
        if heading is not None:
            niveau, titre = heading
            pile = [entree for entree in pile if entree[0] < niveau]
            pile.append((niveau, titre))

        chemin = tuple(titre for _, titre in pile)
        if len(block) > max_chars:
            paires.extend((morceau, chemin) for morceau in _split_long_paragraph(block, max_chars))
        else:
            paires.append((block, chemin))

    if not paires:
        return []

    chunks: list[Chunk] = []
    current: list[tuple[str, tuple[str, ...]]] = []
    length = 0

    for paragraphe, chemin in paires:
        size = len(paragraphe)
        if current and length + size > max_chars:
            chunks.append(_assemble(current))
            # Recouvrement : on repart des derniers paragraphes du fragment clos.
            current = current[-overlap_paragraphs:] if overlap_paragraphs else []
            length = sum(len(part) for part, _ in current)
        current.append((paragraphe, chemin))
        length += size

    if current:
        chunks.append(_assemble(current))

    # Une queue trop courte n'est pas un fragment, c'est un reste.
    if len(chunks) > 1 and estimate_tokens(chunks[-1].text) < min_tokens:
        tail = chunks.pop()
        chunks[-1] = Chunk(f"{chunks[-1].text}\n\n{tail.text}", chunks[-1].headings)

    return chunks


def _assemble(paires: list[tuple[str, tuple[str, ...]]]) -> Chunk:
    """Le chemin retenu est celui du PREMIER paragraphe du fragment.

    Un fragment qui chevauche deux sections appartient à celle où il commence :
    c'est ce que dirait un lecteur, et c'est ce qui rend la citation exacte.
    """
    return Chunk("\n\n".join(part for part, _ in paires), paires[0][1])


def chunk_text(
    text: str,
    *,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    min_tokens: int = DEFAULT_MIN_TOKENS,
    overlap_paragraphs: int = 1,
) -> list[str]:
    """Le texte des fragments, sans leur contexte. Confort d'appel et de test."""
    return [
        fragment.text
        for fragment in chunk_document(
            text,
            max_tokens=max_tokens,
            min_tokens=min_tokens,
            overlap_paragraphs=overlap_paragraphs,
        )
    ]
