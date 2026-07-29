"""Mesure du traitement « courriel → dossier », contre la vérité terrain.

Même logique que pour la recherche : sans chiffres, « le rattachement marche
bien » est une impression formée sur les trois courriels qu'on a regardés.

**Trois issues, et elles ne se valent pas du tout :**

- **exact** — le bon client, ou une abstention là où il n'y avait rien à rattacher ;
- **manqué** — aucun rattachement alors qu'il y en avait un. Coûteux : le
  collaborateur fait le travail à la main, comme avant. Mais rien n'est cassé.
- **erroné** — un rattachement au MAUVAIS client. C'est la faute grave, et la
  seule qui produise un dommage : le brouillon citera le contrat, la franchise et
  le sinistre de quelqu'un d'autre, et il aura l'air parfaitement crédible.

Les additionner dans un « taux de réussite » unique masquerait la seule chose
qu'on a besoin de savoir. Le tableau les sépare donc, et le chiffre à surveiller
n'est pas le taux d'exactitude : c'est le nombre d'erronés, qui doit rester nul.

**Le passage en validation n'est pas un échec.** Un dossier ambigu correctement
envoyé à un humain est un succès du système — c'est ce qu'on lui demande. Il est
donc compté à part, et un rattachement juste mais signalé « à valider » n'est pas
puni comme une erreur.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field

from agent.core.broker.pipeline import Batch, Case

# Toute référence de dossier du cabinet : contrat, sinistre, expertise,
# avenant, attestation, proposition, fil de discussion.
_REFERENCE = re.compile(
    r"\b(?:RCP|MRP|FLA|DEC|CYB|DAB|SIN|EXP|AV|ATT|DEV|THR)-\d{4}-\d{4}\b"
)

logger = logging.getLogger("agent.broker.eval")


def _fold(texte: str) -> str:
    """Minuscules sans accents ni ponctuation, pour comparer des intitulés.

    Le référentiel dit « relevé de sinistralité des cinq derniers exercices », le
    modèle écrit « relevé de sinistralité ». Comparer ces deux chaînes à
    l'identique compterait un échec là où le collaborateur verrait un succès.
    """
    sans_accent = "".join(
        c
        for c in unicodedata.normalize("NFD", texte.lower())
        if unicodedata.category(c) != "Mn"
    )
    return " ".join(sans_accent.replace("'", " ").split())


def _split(brut: str) -> list[str]:
    return [part.strip() for part in (brut or "").split("|") if part.strip()]


# La comparaison est **exacte** (aux accents et espaces près), et c'est un
# progrès : les deux côtés viennent désormais du même référentiel — la vérité
# terrain est vérifiée contre lui à la génération, et la sortie du traitement est
# construite à partir de lui. Une comparaison approximative n'aurait plus rien à
# rattraper, et masquerait au contraire un vrai désalignement.


@dataclass
class CaseScore:
    case: Case
    difficulte: str
    client_verdict: str
    """`exact`, `manque` ou `errone`."""
    contract_verdict: str
    intention_exacte: bool
    section_verdict: str = "sans_objet"
    """`exact`, `faux`, `tolere` (lecture ambiguë admise) ou `sans_objet`."""
    pieces_trouvees: int = 0
    pieces_attendues: int = 0
    pieces_a_tort: int = 0
    """Pièces réclamées alors qu'elles étaient réellement fournies. Une faute de
    jugement : elle fait perdre un aller-retour au client."""
    pieces_inventees: int = 0
    """Pièces réclamées hors du référentiel. **Doit rester à zéro par
    construction** — la sortie est bâtie à partir du référentiel, pas rédigée.
    Une valeur non nulle signale que la garantie a sauté, pas que le modèle a
    mal répondu."""
    pieces_manquees: list[str] = field(default_factory=list)
    contamination: list[str] = field(default_factory=list)
    """Références citées par le brouillon sans venir ni du courriel ni du contrat
    rattaché.

    **La faute la plus discrète de la chaîne.** Rattacher au bon client ne suffit
    pas : la recherche remonte volontiers un dossier ANTÉRIEUR du même client, et
    le brouillon en reprend la date ou le numéro. Rien n'est inventé, tout est
    faux, et le collaborateur qui relit voit une référence bien formée du bon
    client. Cette liste doit rester vide."""

    @property
    def critical(self) -> bool:
        return self.client_verdict == "errone" or self.contract_verdict == "errone"


@dataclass
class BrokerReport:
    scores: list[CaseScore] = field(default_factory=list)

    def _count(self, champ: str, valeur: str) -> int:
        return sum(1 for score in self.scores if getattr(score, champ) == valeur)

    @property
    def total(self) -> int:
        return len(self.scores)

    @property
    def clients_exacts(self) -> int:
        return self._count("client_verdict", "exact")

    @property
    def clients_manques(self) -> int:
        return self._count("client_verdict", "manque")

    @property
    def clients_errones(self) -> int:
        return self._count("client_verdict", "errone")

    @property
    def contrats_exacts(self) -> int:
        return self._count("contract_verdict", "exact")

    @property
    def contrats_errones(self) -> int:
        return self._count("contract_verdict", "errone")

    @property
    def intentions_exactes(self) -> int:
        return sum(1 for score in self.scores if score.intention_exacte)

    @property
    def a_valider(self) -> int:
        return sum(1 for score in self.scores if score.case.status == "a_valider")

    @property
    def pieces_rappel(self) -> float:
        attendues = sum(score.pieces_attendues for score in self.scores)
        trouvees = sum(score.pieces_trouvees for score in self.scores)
        return trouvees / attendues if attendues else 0.0

    @property
    def pieces_attendues(self) -> int:
        return sum(score.pieces_attendues for score in self.scores)

    @property
    def pieces_trouvees(self) -> int:
        return sum(score.pieces_trouvees for score in self.scores)

    @property
    def pieces_a_tort(self) -> int:
        return sum(score.pieces_a_tort for score in self.scores)

    @property
    def pieces_inventees(self) -> int:
        return sum(score.pieces_inventees for score in self.scores)

    @property
    def contaminations(self) -> int:
        return sum(1 for score in self.scores if score.contamination)

    def details_contamination(self) -> list[tuple[str, list[str]]]:
        return [
            (score.case.title, score.contamination)
            for score in self.scores
            if score.contamination
        ]

    @property
    def sections_exactes(self) -> int:
        return self._count("section_verdict", "exact")

    @property
    def sections_evaluees(self) -> int:
        """Cas où une section précise était attendue ET exigible.

        Les cas tolérés en sont exclus : les compter au dénominateur ferait
        chuter le taux pour une ambiguïté qu'on a nous-mêmes reconnue.
        """
        return sum(
            1 for score in self.scores if score.section_verdict in {"exact", "faux"}
        )

    def echecs_pieces(self) -> list[tuple[str, list[str]]]:
        """`(objet du courriel, pièces exigées non réclamées)`, pour enquêter.

        Un taux de rappel ne dit pas quoi corriger ; la liste des pièces
        oubliées, si.
        """
        return [
            (score.case.title, score.pieces_manquees)
            for score in self.scores
            if score.pieces_manquees
        ]

    def par_difficulte(self) -> dict[str, tuple[int, int]]:
        """`{difficulté: (exacts, total)}` — c'est là que se lit le vrai profil.

        Un taux global de 80 % peut cacher 100 % sur les cas faciles et 0 % sur
        les cas difficiles, qui sont pourtant la majorité d'une vraie boîte.
        """
        sortie: dict[str, list[int]] = {}
        for score in self.scores:
            case = sortie.setdefault(score.difficulte, [0, 0])
            case[1] += 1
            if score.client_verdict == "exact":
                case[0] += 1
        return {cle: (valeur[0], valeur[1]) for cle, valeur in sorted(sortie.items())}


def _contamination(case: Case) -> list[str]:
    """Références citées par le brouillon et absentes de ses sources légitimes.

    Le contrôle est déterministe — une expression régulière sur les formats de
    référence du cabinet — et c'est ce qui en fait une garantie plutôt qu'une
    impression. Sont admises : les références présentes dans le courriel reçu, et
    celle du contrat rattaché. Tout le reste vient d'un autre dossier.
    """
    if not case.draft.body:
        return []

    autorisees = set(_REFERENCE.findall(case.message.searchable.upper()))
    if case.link.contract:
        autorisees.add(case.link.contract.upper())

    citees = set(_REFERENCE.findall(case.draft.body.upper()))
    return sorted(citees - autorisees)


def score_case(case: Case) -> CaseScore | None:
    """Compare un dossier à la vérité terrain de son courriel.

    `None` quand le courriel n'en porte pas : un message réel n'a pas d'en-tête
    `X-Attendu-*`, et l'inclure dans la mesure fausserait le dénominateur.
    """
    verite = case.message.ground_truth
    if not verite:
        return None

    attendu_client = (verite.get("client") or "").strip() or None
    attendu_contrat = (verite.get("contrat") or "").strip() or None
    obtenu_client = case.link.client
    obtenu_contrat = case.link.contract

    if obtenu_client == attendu_client:
        client_verdict = "exact"
    elif obtenu_client is None:
        client_verdict = "manque"
    else:
        # Couvre les deux cas : mauvais client, et client rattaché là où il ne
        # fallait rattacher personne (prospect inconnu, infolettre).
        client_verdict = "errone"

    if obtenu_contrat == attendu_contrat:
        contract_verdict = "exact"
    elif obtenu_contrat is None:
        contract_verdict = "manque"
    else:
        contract_verdict = "errone"

    intention_attendue = verite.get("intention")
    intentions_tolerees = set(_split(verite.get("intention-tolerees")))
    intention_obtenue = case.classification.intention

    exigees = _split(verite.get("pieces"))
    tolerees = _split(verite.get("pieces-tolerees"))
    section_attendue = (verite.get("section") or "").strip() or None
    proposees = [piece.piece for piece in case.checklist.manquantes]

    # **Une intention fausse ne se compte qu'UNE fois.** Elle envoie le
    # traitement sur une autre section du référentiel, donc sur d'autres pièces.
    # Compter aussi ces pièces ferait payer deux fois la même erreur, et pire :
    # la ligne « pièces » cesserait de mesurer la détection des pièces pour
    # mesurer la qualification, qui a déjà sa ligne. On sort donc ici dans les
    # deux cas — lecture tolérée, ou qualification simplement fausse.
    if intention_obtenue != intention_attendue:
        return CaseScore(
            case=case,
            difficulte=verite.get("difficulte", "inconnue"),
            client_verdict=client_verdict,
            contract_verdict=contract_verdict,
            intention_exacte=intention_obtenue in intentions_tolerees,
            section_verdict="tolere"
            if intention_obtenue in intentions_tolerees
            else "sans_objet",
            contamination=_contamination(case),
        )

    plies_exigees = {_fold(p) for p in exigees}
    plies_tolerees = {_fold(p) for p in tolerees}
    plies_proposees = {_fold(p) for p in proposees}
    # Le référentiel de la section retenue : tout ce qui en vient est au pire une
    # maladresse ; tout ce qui n'en vient pas serait une invention.
    plies_referentiel = {_fold(item) for item in case.checklist.referentiel_items}

    trouvees = len(plies_exigees & plies_proposees)
    manquees = [p for p in exigees if _fold(p) not in plies_proposees]

    # Ce qui a été réclamé sans être ni exigé ni toléré se répartit en deux :
    # ce qui vient quand même du référentiel (maladresse — la pièce était là),
    # et ce qui n'en vient pas du tout (invention). Les deux ne se corrigent pas
    # de la même façon : la première par le prompt, la seconde par le code.
    hors_attendu = plies_proposees - plies_exigees - plies_tolerees
    a_tort = len(hors_attendu & plies_referentiel)
    inventees = len(hors_attendu - plies_referentiel)

    if section_attendue is None:
        section_verdict = "sans_objet"
    elif case.checklist.section == section_attendue:
        section_verdict = "exact"
    else:
        section_verdict = "faux"

    return CaseScore(
        case=case,
        difficulte=verite.get("difficulte", "inconnue"),
        client_verdict=client_verdict,
        contract_verdict=contract_verdict,
        contamination=_contamination(case),
        # Une qualification déclarée tolérable compte comme exacte. Ce n'est pas
        # de l'indulgence : sur un cas où deux gestionnaires compétents
        # trancheraient différemment, punir le modèle reviendrait à mesurer sa
        # capacité à deviner notre arbitrage plutôt que sa compréhension.
        intention_exacte=case.classification.intention
        in {verite.get("intention"), *_split(verite.get("intention-tolerees"))},
        section_verdict=section_verdict,
        pieces_trouvees=trouvees,
        pieces_attendues=len(exigees),
        pieces_a_tort=a_tort,
        pieces_inventees=inventees,
        pieces_manquees=manquees,
    )


def evaluate(batch: Batch) -> BrokerReport:
    report = BrokerReport(
        scores=[score for score in map(score_case, batch.cases) if score is not None]
    )
    logger.info(
        "traitement mesuré",
        extra={
            "courriels": report.total,
            "clients_exacts": report.clients_exacts,
            "clients_errones": report.clients_errones,
        },
    )
    return report


# --- Répétitions : mesurer la variance plutôt que publier un tirage -----------


@dataclass
class Repeated:
    """Plusieurs passages du même lot, et l'écart entre eux.

    **Un modèle à température zéro n'est pas déterministe.** Constaté sur cette
    boîte : deux exécutions strictement identiques donnent 15/15 puis 13/15 sur
    la qualification, sans qu'une ligne de code ait bougé. Publier le premier
    tirage aurait été publier de la chance.

    Sur quinze courriels, un écart d'un cas vaut près de sept points : à cette
    taille, aucun pourcentage isolé ne veut dire quoi que ce soit. On rend donc
    la moyenne ET l'étendue, et c'est l'étendue qui dit combien on peut se fier
    à la moyenne.
    """

    rapports: list[BrokerReport] = field(default_factory=list)

    def _serie(self, attribut: str) -> list[float]:
        return [float(getattr(rapport, attribut)) for rapport in self.rapports]

    def stats(self, attribut: str) -> tuple[float, float, float]:
        """`(moyenne, minimum, maximum)` de la métrique sur les répétitions."""
        serie = self._serie(attribut)
        if not serie:
            return 0.0, 0.0, 0.0
        return sum(serie) / len(serie), min(serie), max(serie)

    @property
    def runs(self) -> int:
        return len(self.rapports)

    @property
    def total(self) -> int:
        return self.rapports[0].total if self.rapports else 0

    def jamais_pire_que(self, attribut: str) -> float:
        """Le PIRE résultat observé sur la métrique.

        C'est lui qu'il faut regarder pour les fautes graves — rattachement
        erroné, contamination, pièce inventée. Une moyenne de 0,3 faute par
        exécution ne rassure personne : ce qui compte est de savoir si une seule
        exécution a fauté.
        """
        return max(self._serie(attribut))
