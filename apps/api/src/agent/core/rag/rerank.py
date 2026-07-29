"""Reclassement des candidats par un modèle qui LIT les passages.

C'est la brique qui manque le plus souvent, et celle qui change le plus de choses.

La fusion RRF ordonne sur des **rangs** : elle sait que le fragment A est premier
en dense et troisième en lexical, mais elle n'a jamais lu ni la question ni le
fragment. Elle ne peut donc pas distinguer « la franchise est de 900 € » — qui
répond — de « les franchises sont détaillées aux conditions particulières » — qui
n'y répond pas, tout en étant plus proche lexicalement.

Le reclassement relit les vingt candidats et les note sur le fond. Deux effets,
et le second est le plus utile :

1. le bon passage remonte en tête, donc il n'est pas noyé dans le contexte ;
2. **les scores deviennent une échelle interprétable**, ce qui rend l'abstention
   possible. La fusion RRF produit des scores comme 0,0163 — sans signification
   absolue, donc impossibles à seuiller. Une note de 0 à 10 attribuée par un
   lecteur, si.

**Reclassement par liste, pas passage par passage.** Un appel unique contenant
tous les candidats coûte vingt fois moins qu'un appel par candidat, et note mieux :
le modèle voit les passages les uns à côté des autres, donc il compare au lieu
d'évaluer dans le vide.

**Échec ouvert.** Modèle indisponible, réponse illisible, identifiant inventé :
on rend l'ordre d'origine. Une recherche non reclassée reste une recherche utile ;
une recherche vide ne l'est pas.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from agent.core.rag import llm

logger = logging.getLogger("agent.rag.rerank")

# Longueur d'extrait soumise au reclasseur. Assez pour juger, assez court pour que
# vingt passages tiennent dans une fenêtre modeste sans faire exploser la latence.
MAX_PASSAGE_CHARS = 700

_SYSTEM = """Tu notes la pertinence de passages documentaires face à une question,
dans le contexte d'un cabinet de courtage en assurance professionnelle.

Note chaque passage de 0 à 10 :
- 10 : le passage contient la réponse explicite à la question posée ;
- 7 à 9 : il contient une partie de la réponse, ou la réponse pour le bon sujet ;
- 4 à 6 : il traite du bon sujet mais ne répond pas à la question ;
- 1 à 3 : même thème général, sujet différent ;
- 0 : hors sujet, OU concerne un AUTRE client, un AUTRE contrat, un AUTRE produit
  que celui visé par la question.

Ce dernier point est le plus important. Un passage qui parle de la bonne notion
mais du mauvais client ne répond PAS à la question : il vaut 0, jamais davantage.
Il est normal et attendu que tous les passages vaillent 0.

Réponds UNIQUEMENT par un tableau JSON, sans texte autour :
[{"id": 1, "score": 8}, {"id": 2, "score": 0}]"""


@dataclass
class Scored:
    index: int
    """Position du passage dans la liste soumise."""
    score: float


def _extract_json(raw: str) -> list | None:
    """Le tableau JSON, même noyé dans du bavardage ou une clôture markdown.

    Les modèles de petite taille encadrent volontiers leur réponse de ```json
    malgré la consigne. Refuser ces réponses reviendrait à désactiver le
    reclassement pour la moitié du catalogue.
    """
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw).strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\[.*\]", raw, re.DOTALL)
        if not match:
            return None
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return parsed if isinstance(parsed, list) else None


def _prompt(question: str, passages: list[str]) -> str:
    blocs = []
    for index, texte in enumerate(passages, start=1):
        extrait = texte[:MAX_PASSAGE_CHARS]
        if len(texte) > MAX_PASSAGE_CHARS:
            extrait = extrait.rsplit(" ", 1)[0] + " […]"
        blocs.append(f"[{index}]\n{extrait}")
    return f"QUESTION : {question}\n\nPASSAGES :\n\n" + "\n\n".join(blocs)


async def score(question: str, passages: list[str]) -> list[Scored] | None:
    """Notes des passages, dans l'ordre soumis. `None` si le reclassement a échoué.

    `None` est distinct d'une liste de zéros : la première dit « on n'a pas pu
    juger », la seconde « on a jugé, rien ne convient ». Confondre les deux ferait
    passer une panne du modèle pour une abstention légitime — et l'abstention
    serait alors mesurée comme un succès.
    """
    if not passages:
        return []

    reponse = await llm.ask(_SYSTEM, _prompt(question, passages))
    if reponse is None:
        return None

    parsed = _extract_json(reponse)
    if parsed is None:
        logger.warning("réponse de reclassement illisible, ordre d'origine conservé")
        return None

    notes: dict[int, float] = {}
    for item in parsed:
        if not isinstance(item, dict):
            continue
        try:
            index = int(item["id"]) - 1
            valeur = float(item["score"])
        except (KeyError, TypeError, ValueError):
            continue
        # Un identifiant hors bornes est une invention du modèle : l'ignorer est
        # plus sûr que de le rabattre sur un passage voisin.
        if 0 <= index < len(passages):
            notes[index] = max(0.0, min(10.0, valeur))

    if not notes:
        logger.warning("aucune note exploitable, ordre d'origine conservé")
        return None

    manquants = len(passages) - len(notes)
    if manquants:
        # Passage non noté : traité comme non pertinent plutôt que comme neutre.
        # Le modèle a vu la liste entière et n'a rien dit de celui-ci ; lui prêter
        # une note moyenne le ferait remonter au-dessus de passages réellement
        # jugés faibles.
        logger.debug("%d passage(s) non noté(s), ramenés à 0", manquants)

    return [Scored(index=index, score=notes.get(index, 0.0)) for index in range(len(passages))]
