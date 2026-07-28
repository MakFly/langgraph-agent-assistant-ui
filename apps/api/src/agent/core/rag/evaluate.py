"""Mesure de la recherche documentaire : rappel, MRR, et fuites d'ACL.

Sans ce module, « le RAG marche bien » est une opinion, et tout réglage — taille
des fragments, recouvrement, modèle d'embedding, taille du vivier — se choisit au
doigt mouillé. Le jour où quelqu'un double la taille des fragments « pour voir »,
personne ne sait dire si la qualité a monté ou baissé. C'est ainsi que meurent
les RAG : pas d'un bug, mais d'une dérive que personne ne mesure.

Trois chiffres sont rendus :

- **rappel@k** — dans quelle proportion des questions le bon document est-il
  dans les `k` résultats ? C'est le plafond de tout ce que le modèle pourra
  répondre : un document absent du top-k n'existe pas pour lui.
- **MRR** — à quel rang arrive-t-il ? Un document en cinquième position sera
  souvent noyé par les quatre autres dans le contexte.
- **fuites** — un document restreint est-il remonté à quelqu'un qui n'y a pas
  droit ? Cette valeur doit rester à zéro. **Une seule fuite invalide tout le
  reste** : mieux vaut un RAG médiocre qu'un RAG indiscret.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from agent.core.rag import retrieve

logger = logging.getLogger("agent.rag.eval")

# Groupe de référence pour le contrôle de fuite : ce que voit quelqu'un qui n'a
# aucun droit particulier.
BASELINE_GROUP = "public"


@dataclass
class Case:
    question: str
    groups: list[str]
    expect: list[str]


@dataclass
class CaseResult:
    case: Case
    found_rank: int | None
    sources: list[str]

    @property
    def hit(self) -> bool:
        return self.found_rank is not None

    @property
    def reciprocal_rank(self) -> float:
        return 1.0 / self.found_rank if self.found_rank else 0.0


@dataclass
class Leak:
    question: str
    source: str
    seen_by: list[str]


@dataclass
class Report:
    k: int
    results: list[CaseResult] = field(default_factory=list)
    leaks: list[Leak] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def hits(self) -> int:
        return sum(1 for result in self.results if result.hit)

    @property
    def recall(self) -> float:
        return self.hits / self.total if self.total else 0.0

    @property
    def mrr(self) -> float:
        if not self.total:
            return 0.0
        return sum(result.reciprocal_rank for result in self.results) / self.total


def load(path: Path) -> list[Case]:
    """Lit le jeu de questions. Lève `ValueError` si un cas est mal formé.

    La validation est stricte : un cas silencieusement ignoré ferait monter le
    score sans que personne ne comprenne pourquoi.
    """
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list) or not raw:
        raise ValueError(f"{path} : une liste de cas est attendue")

    cases: list[Case] = []
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"{path} : cas #{index} n'est pas un objet")
        question = str(item.get("question", "")).strip()
        expect = item.get("expect") or []
        groups = item.get("groups") or [BASELINE_GROUP]
        if not question:
            raise ValueError(f"{path} : cas #{index} sans question")
        if not expect:
            raise ValueError(f"{path} : cas #{index} sans `expect`")
        cases.append(Case(question, list(groups), list(expect)))
    return cases


async def run(cases: list[Case], *, k: int | None = None) -> Report:
    """Exécute le jeu et mesure. Aucune écriture : l'index n'est pas modifié."""
    k = k or retrieve.top_k()
    report = Report(k=k)

    for case in cases:
        passages = await retrieve.search(case.question, case.groups, k=k)
        sources = [passage.source for passage in passages]

        rank = next(
            (
                position
                for position, source in enumerate(sources, start=1)
                if source in case.expect
            ),
            None,
        )
        report.results.append(CaseResult(case, rank, sources))

        # Contrôle de fuite : la même question, posée par quelqu'un qui n'a que
        # le groupe de base. Aucun document attendu hors de ce groupe ne doit
        # apparaître.
        restricted = [
            source for source in case.expect if not source.startswith(f"{BASELINE_GROUP}/")
        ]
        if not restricted:
            continue

        baseline = await retrieve.search(case.question, [BASELINE_GROUP], k=k)
        for passage in baseline:
            if passage.source in restricted:
                report.leaks.append(
                    Leak(case.question, passage.source, [BASELINE_GROUP])
                )

    logger.info(
        "évaluation terminée",
        extra={
            "cas": report.total,
            "rappel": round(report.recall, 3),
            "mrr": round(report.mrr, 3),
            "fuites": len(report.leaks),
        },
    )
    return report
