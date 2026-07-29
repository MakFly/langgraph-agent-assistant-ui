"""Expansion de requête : poser la même question de plusieurs façons.

Le problème que ça résout est concret. Un collaborateur écrit « il reste combien
à leur charge ? » ; le contrat écrit « franchise contractuelle par sinistre ». Ces
deux formulations ne partagent aucun mot, et le vecteur de la première ne tombe
pas forcément près de celui de la seconde — surtout sur un corpus où trente
documents parlent de franchise pour trente clients différents.

Générer trois reformulations et fusionner les résultats donne trois chances de
tomber juste plutôt qu'une. C'est la technique la plus simple du lot, et souvent
la plus rentable sur un corpus métier où le vocabulaire des utilisateurs ne
ressemble pas à celui des documents.

**Ce qu'elle coûte.** Un appel de modèle *avant* la recherche, donc de la latence
sur le chemin critique, et N fois plus de requêtes SQL. Sur un corpus dont le
vocabulaire est déjà proche de celui des questions, elle n'apporte rien et se
contente de coûter. D'où l'ablation : c'est une hypothèse à vérifier, pas un
acquis.
"""

from __future__ import annotations

import logging
import re

from agent.core.rag import llm

logger = logging.getLogger("agent.rag.query")

_SYSTEM = """Tu reformules une question posée à une base documentaire d'un cabinet
de courtage en assurance professionnelle.

Produis exactement {count} reformulations de la question, une par ligne, sans
numérotation, sans puce, sans commentaire.

Règles :
- garde STRICTEMENT le même sens ; ne réponds pas à la question, ne l'élargis pas ;
- conserve tels quels les noms propres, raisons sociales, références de contrat,
  immatriculations et montants — ce sont eux qui retrouvent le bon document ;
- varie le vocabulaire : emploie les termes techniques du métier là où la question
  est familière, et l'inverse là où elle est technique ;
- une reformulation par ligne, rien d'autre."""

# Bornes du nombre de reformulations. En dessous de 1 la technique n'existe pas ;
# au-delà de 5 la latence et le nombre de requêtes montent bien plus vite que le
# rappel — chaque reformulation supplémentaire ressemble aux précédentes.
MAX_VARIANTS = 5


def _clean(ligne: str) -> str:
    """Retire les décorations que le modèle ajoute malgré la consigne."""
    ligne = ligne.strip()
    ligne = re.sub(r"^\s*(?:\d+[.)]|[-*•])\s*", "", ligne)
    return ligne.strip(" \"'").strip()


async def expand(question: str, count: int) -> list[str]:
    """`[question, *reformulations]`. Toujours au moins la question d'origine.

    La question d'origine est **toujours** en tête, et jamais remplacée. Une
    reformulation reste une supposition du modèle : si elle dérive, la question
    telle qu'elle a été posée reste dans le lot et rattrape le coup.
    """
    question = question.strip()
    if not question or count <= 0:
        return [question] if question else []

    count = min(count, MAX_VARIANTS)
    reponse = await llm.ask(_SYSTEM.format(count=count), question)
    if reponse is None:
        logger.info("expansion indisponible, recherche sur la question seule")
        return [question]

    variantes: list[str] = []
    vus = {question.lower()}
    for ligne in reponse.splitlines():
        candidate = _clean(ligne)
        # Une « reformulation » de trois mots est presque toujours un fragment de
        # préambule du modèle (« Voici :  »), pas une question.
        if len(candidate) < 8 or candidate.lower() in vus:
            continue
        vus.add(candidate.lower())
        variantes.append(candidate)
        if len(variantes) >= count:
            break

    logger.debug("requête étendue", extra={"variantes": len(variantes)})
    return [question, *variantes]
