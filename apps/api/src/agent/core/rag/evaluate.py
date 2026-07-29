"""Mesure de la recherche : rappel, MRR, abstention, et fuites d'ACL.

Sans ce module, « le RAG marche bien » est une opinion, et tout réglage — taille
des fragments, modèle d'embedding, reclassement, seuil — se choisit au doigt
mouillé. Le jour où quelqu'un ajoute un reclasseur « parce que c'est mieux »,
personne ne sait dire si la qualité a monté ou baissé. C'est ainsi que meurent les
RAG : pas d'un bug, mais d'une dérive que personne ne mesure.

**Cinq chiffres, et le troisième est celui qu'on oublie partout.**

- **rappel@k** — dans quelle proportion des questions le bon document est-il dans
  les `k` résultats ? C'est le plafond de tout ce que le modèle pourra répondre :
  un document absent du top-k n'existe pas pour lui.
- **MRR** — à quel rang arrive-t-il ? Un document en cinquième position sera
  souvent noyé par les quatre autres dans le contexte.
- **abstention correcte** — sur les questions dont la réponse n'est nulle part,
  quelle proportion le système refuse-t-il de traiter ? Un RAG sans seuil obtient
  **0 %** ici tout en affichant un rappel flatteur : il rend toujours ses plus
  proches voisins. Mesurer le rappel seul, c'est ne pas voir cette panne.
- **abstention abusive** — la contrepartie. Un seuil trop haut fait taire le
  système sur des questions qui avaient pourtant une réponse. Les deux chiffres se
  lisent ENSEMBLE : améliorer l'un en dégradant l'autre n'est pas un progrès.
- **fuites** — un document restreint est-il remonté à quelqu'un qui n'y a pas
  droit ? Cette valeur doit rester à zéro. **Une seule fuite invalide tout le
  reste** : mieux vaut un RAG médiocre qu'un RAG indiscret.

**L'ablation est la raison d'être du module.** Mesurer une configuration ne dit
rien ; mesurer la même chose avec et sans une brique dit ce que cette brique
apporte. `run_ablation()` exécute le jeu sous plusieurs configurations qui ne
diffèrent que d'un élément, et c'est le seul moyen honnête de répondre à « est-ce
que le reclassement sert ? » — sur CE corpus, avec CES questions.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from agent.core.rag import retrieve
from agent.core.rag.config import RagConfig, ablations, from_env

logger = logging.getLogger("agent.rag.eval")

# Groupe de référence pour le contrôle de fuite : ce que voit quelqu'un qui n'a
# aucun droit particulier.
BASELINE_GROUP = "public"

# Recherches menées de front. Le plafond protège les quotas des fournisseurs :
# reclassement et expansion appellent un LLM par question, et un free tier tombe
# en 429 bien avant que la base ne bronche.
DEFAULT_CONCURRENCY = 4


@dataclass
class Case:
    question: str
    groups: list[str]
    expect: list[str]
    abstain: bool = False
    note: str | None = None
    fact: str | None = None
    """Le fait que la réponse doit contenir, tel qu'écrit dans le document."""


@dataclass
class CaseResult:
    case: Case
    found_rank: int | None
    sources: list[str]
    abstained: bool
    reason: str | None = None
    fact_covered: bool | None = None
    """Le TEXTE rendu contenait-il le fait attendu ? `None` si le cas n'en
    déclare pas.

    C'est la métrique qui manquait. Rappel et MRR portent sur les *sources*
    retrouvées : ils sont aveugles à tout ce qui agit sur le texte plutôt que
    sur le classement — l'élargissement au voisinage en premier lieu. Retrouver
    le bon document et rendre le fragment d'à côté donne un rappel parfait et
    une réponse impossible ; seule la couverture du fait le voit."""

    @property
    def hit(self) -> bool:
        return self.found_rank is not None

    @property
    def reciprocal_rank(self) -> float:
        return 1.0 / self.found_rank if self.found_rank else 0.0

    @property
    def answerable(self) -> bool:
        """Le passage rendu permet-il RÉELLEMENT de répondre ?

        Plus exigeant que `hit` : il faut le bon document ET le bon fragment.
        Quand le cas ne déclare pas de fait, on retombe sur `hit`, faute de
        pouvoir en dire davantage.
        """
        if self.case.abstain:
            return self.abstained
        if self.fact_covered is None:
            return self.hit
        return self.fact_covered

    @property
    def correct(self) -> bool:
        """Le système a-t-il fait ce qu'il fallait sur ce cas ?

        Pour un négatif, réussir c'est se taire. Pour un positif, réussir c'est
        trouver — et s'abstenir est un échec au même titre que se tromper.
        """
        return self.abstained if self.case.abstain else self.hit


def _fold(texte: str) -> str:
    """Espaces normalisés pour comparer un montant à son écriture dans un texte.

    « 1 500 € » peut être coupé par une espace insécable, une fin de ligne ou un
    double espace selon l'endroit du document. Comparer les chaînes telles
    quelles compterait un échec là où le lecteur voit le bon chiffre.
    """
    return " ".join(texte.replace("\u00a0", " ").replace("\u202f", " ").split())


@dataclass
class Leak:
    question: str
    source: str
    seen_by: list[str]


@dataclass
class Report:
    k: int
    config: RagConfig | None = None
    results: list[CaseResult] = field(default_factory=list)
    leaks: list[Leak] = field(default_factory=list)

    @property
    def positives(self) -> list[CaseResult]:
        return [result for result in self.results if not result.case.abstain]

    @property
    def negatives(self) -> list[CaseResult]:
        return [result for result in self.results if result.case.abstain]

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def hits(self) -> int:
        return sum(1 for result in self.positives if result.hit)

    @property
    def recall(self) -> float:
        """Rappel sur les seuls cas positifs.

        Les négatifs en sont exclus : ils n'ont aucun document attendu, les
        inclure ferait mécaniquement chuter le rappel d'un système par ailleurs
        parfait. C'est l'erreur qui rend un tableau d'ablation illisible.
        """
        positifs = self.positives
        return self.hits / len(positifs) if positifs else 0.0

    @property
    def mrr(self) -> float:
        positifs = self.positives
        if not positifs:
            return 0.0
        return sum(result.reciprocal_rank for result in positifs) / len(positifs)

    @property
    def abstention_correcte(self) -> float:
        """Part des négatifs difficiles sur lesquels le système s'est tu."""
        negatifs = self.negatives
        if not negatifs:
            return 0.0
        return sum(1 for result in negatifs if result.abstained) / len(negatifs)

    @property
    def abstention_abusive(self) -> float:
        """Part des cas positifs sur lesquels le système s'est tu à tort."""
        positifs = self.positives
        if not positifs:
            return 0.0
        return sum(1 for result in positifs if result.abstained) / len(positifs)

    @property
    def fact_coverage(self) -> float:
        """Part des cas à fait déclaré dont le TEXTE rendu contient la réponse.

        À lire avec le rappel : l'écart entre les deux est exactement ce que le
        rappel surestime. Un rappel de 90 % et une couverture de 70 % veut dire
        qu'une réponse sur cinq est impossible malgré le bon document.
        """
        mesurables = [
            result for result in self.positives if result.fact_covered is not None
        ]
        if not mesurables:
            return 0.0
        return sum(1 for result in mesurables if result.fact_covered) / len(mesurables)

    @property
    def fact_cases(self) -> int:
        return sum(
            1 for result in self.positives if result.fact_covered is not None
        )

    @property
    def exactitude(self) -> float:
        """Part des cas, tous types confondus, traités correctement.

        Le seul chiffre qui ne peut pas être amélioré en dégradant l'autre moitié
        du jeu : monter le seuil fait grimper l'abstention correcte et chuter le
        rappel, et cette valeur-ci en rend la somme.
        """
        return (
            sum(1 for result in self.results if result.correct) / self.total
            if self.total
            else 0.0
        )


def load(path: Path) -> list[Case]:
    """Lit le jeu de questions. Lève `ValueError` si un cas est mal formé.

    La validation est stricte : un cas silencieusement ignoré ferait bouger le
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
        abstain = bool(item.get("abstain", False))
        if not question:
            raise ValueError(f"{path} : cas #{index} sans question")
        # Les deux moitiés du contrat, vérifiées dans les deux sens : un négatif
        # avec des sources attendues et un positif sans en attendre sont l'un et
        # l'autre des erreurs de rédaction, et tous deux fausseraient la mesure
        # en silence.
        if abstain and expect:
            raise ValueError(
                f"{path} : cas #{index} est marqué `abstain` mais attend des sources"
            )
        if not abstain and not expect:
            raise ValueError(
                f"{path} : cas #{index} sans `expect` — ajoutez `abstain: true` si "
                "l'absence de réponse est le comportement attendu"
            )
        fact = item.get("fact")
        if abstain and fact:
            raise ValueError(
                f"{path} : cas #{index} est marqué `abstain` mais déclare un fait"
            )
        cases.append(
            Case(
                question,
                list(groups),
                list(expect),
                abstain,
                item.get("note"),
                str(fact) if fact else None,
            )
        )
    return cases


async def run(
    cases: list[Case],
    *,
    k: int | None = None,
    config: RagConfig | None = None,
    concurrency: int = DEFAULT_CONCURRENCY,
) -> Report:
    """Exécute le jeu et mesure. Aucune écriture : l'index n'est pas modifié."""
    config = config or from_env()
    k = k or config.top_k
    report = Report(k=k, config=config)

    limite = asyncio.Semaphore(max(1, concurrency))

    async def traiter(case: Case) -> tuple[CaseResult, list[Leak]]:
        async with limite:
            return await _one(case, k, config)

    # `gather` conserve l'ordre des cas quel que soit l'ordre d'achèvement : un
    # rapport dont les lignes changent de place à chaque exécution serait
    # impossible à comparer d'une mesure à l'autre.
    for resultat, fuites in await asyncio.gather(*(traiter(case) for case in cases)):
        report.results.append(resultat)
        report.leaks.extend(fuites)

    logger.info(
        "évaluation terminée",
        extra={
            "configuration": config.label(),
            "cas": report.total,
            "rappel": round(report.recall, 3),
            "mrr": round(report.mrr, 3),
            "abstention_correcte": round(report.abstention_correcte, 3),
            "fuites": len(report.leaks),
        },
    )
    return report


async def _one(case: Case, k: int, config: RagConfig) -> tuple[CaseResult, list[Leak]]:
    resultat = await retrieve.search_detailed(case.question, case.groups, k=k, config=config)
    sources = [passage.source for passage in resultat.passages]

    rang = next(
        (
            position
            for position, source in enumerate(sources, start=1)
            if source in case.expect
        ),
        None,
    )
    # La couverture se mesure sur le texte RÉELLEMENT rendu, recollage des
    # voisins compris — c'est précisément ce qu'on cherche à voir.
    couverture = None
    if case.fact and not case.abstain:
        rendu = "\n".join(passage.text for passage in resultat.passages)
        couverture = _fold(case.fact) in _fold(rendu)

    mesure = CaseResult(
        case, rang, sources, resultat.abstained, resultat.reason, couverture
    )

    # Contrôle de fuite : la même question, posée par quelqu'un qui n'a que le
    # groupe de base. Aucun document attendu hors de ce groupe ne doit apparaître.
    restreints = [
        source for source in case.expect if not source.startswith(f"{BASELINE_GROUP}/")
    ]
    if not restreints:
        return mesure, []

    # Le contrôle repasse par la recherche BRUTE, sans reclassement, sans
    # expansion et sans seuil. Deux raisons, et la seconde est la principale :
    # ces briques coûtent un appel de modèle par question et doubleraient le prix
    # d'une évaluation ; surtout, un seuil qui écarte un document fuité le
    # masquerait au contrôle. On veut savoir ce que le filtre SQL laisse sortir,
    # pas ce qu'un reclasseur veut bien montrer.
    baseline = await retrieve.search_detailed(
        case.question,
        [BASELINE_GROUP],
        k=k,
        config=config.with_(
            multi_query=0,
            hyde_documents=0,
            rerank="none",
            min_rerank_score=None,
            min_similarity=None,
        ),
    )
    fuites = [
        Leak(case.question, passage.source, [BASELINE_GROUP])
        for passage in baseline.passages
        if passage.source in restreints
    ]
    return mesure, fuites


# --- Ablation -----------------------------------------------------------------


@dataclass
class AblationRow:
    label: str
    config: RagConfig
    report: Report

    def delta(self, reference: Report) -> dict[str, float]:
        return {
            "rappel": self.report.recall - reference.recall,
            "mrr": self.report.mrr - reference.mrr,
            "abstention": self.report.abstention_correcte - reference.abstention_correcte,
            "exactitude": self.report.exactitude - reference.exactitude,
        }


# --- Calibration du seuil -----------------------------------------------------


@dataclass
class Sweep:
    """Un point de la courbe de seuil."""

    seuil: float
    recall: float
    abstention_correcte: float
    abstention_abusive: float
    exactitude: float


@dataclass
class Calibration:
    points: list[Sweep] = field(default_factory=list)
    critere: str = ""

    def best(self) -> Sweep:
        """Le seuil retenu par le critère annoncé.

        En cas d'égalité d'exactitude, le seuil le plus BAS l'emporte : à
        performance égale, moins seuiller c'est moins d'abstentions abusives à
        venir sur des questions qu'on n'a pas encore vues. Le critère doit être
        écrit, sinon « optimal » ne veut rien dire.
        """
        return min(self.points, key=lambda point: (-point.exactitude, point.seuil))


async def calibrate(
    cases: list[Case],
    *,
    k: int | None = None,
    base: RagConfig | None = None,
    seuils: tuple[float, ...] = tuple(x / 2 for x in range(0, 21)),
    concurrency: int = DEFAULT_CONCURRENCY,
) -> Calibration:
    """Déduit le seuil d'abstention au lieu de le choisir.

    **Une seule passe de reclassement pour toute la courbe.** Le seuil n'agit
    qu'après le reclassement : il ne change ni la recherche, ni la fusion, ni les
    notes attribuées. On exécute donc le jeu UNE fois sans seuil, on garde les
    notes obtenues, et on rejoue chaque seuil sur ces notes — ce qui donne
    exactement le même résultat que vingt-et-une exécutions complètes, pour un
    vingt-et-unième du coût et sans la variance qu'introduiraient vingt-et-un
    appels de modèle différents sur les mêmes questions.
    """
    base = (base or from_env()).with_(rerank="llm", min_rerank_score=None)
    k = k or base.top_k
    limite = asyncio.Semaphore(max(1, concurrency))

    async def observer(case: Case) -> tuple[Case, list[tuple[float, str]]]:
        async with limite:
            resultat = await retrieve.search_detailed(
                case.question, case.groups, k=k, config=base
            )
        return case, [
            (passage.rerank_score or 0.0, passage.source) for passage in resultat.passages
        ]

    observations = await asyncio.gather(*(observer(case) for case in cases))

    points: list[Sweep] = []
    for seuil in seuils:
        hits = abst_ok = abst_abus = corrects = 0
        positifs = negatifs = 0
        for case, notes in observations:
            gardes = [source for note, source in notes if note >= seuil]
            abstenu = not gardes
            if case.abstain:
                negatifs += 1
                abst_ok += abstenu
                corrects += abstenu
            else:
                positifs += 1
                trouve = any(source in case.expect for source in gardes)
                hits += trouve
                abst_abus += abstenu
                corrects += trouve

        points.append(
            Sweep(
                seuil=seuil,
                recall=hits / positifs if positifs else 0.0,
                abstention_correcte=abst_ok / negatifs if negatifs else 0.0,
                abstention_abusive=abst_abus / positifs if positifs else 0.0,
                exactitude=corrects / len(observations) if observations else 0.0,
            )
        )

    return Calibration(
        points=points,
        critere=(
            "exactitude globale maximale sur l'ensemble du jeu ; à égalité, "
            "le seuil le plus bas"
        ),
    )


async def run_ablation(
    cases: list[Case],
    *,
    k: int | None = None,
    base: RagConfig | None = None,
    concurrency: int = DEFAULT_CONCURRENCY,
) -> list[AblationRow]:
    """Le même jeu, sous plusieurs configurations. Séquentiel **à dessein**.

    Les configurations ne sont pas parallélisées entre elles : celles qui
    appellent un LLM satureraient le quota du fournisseur, et une mesure de
    latence prise pendant qu'une autre configuration tape la même API ne voudrait
    rien dire. C'est plus long, et c'est la seule façon d'obtenir des chiffres
    comparables entre eux.
    """
    lignes: list[AblationRow] = []
    for label, config in ablations(base):
        logger.info("ablation : %s", label)
        report = await run(cases, k=k, config=config, concurrency=concurrency)
        lignes.append(AblationRow(label=label, config=config, report=report))
    return lignes
