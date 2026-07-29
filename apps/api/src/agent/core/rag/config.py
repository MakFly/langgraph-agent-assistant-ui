"""Réglages de la chaîne de recherche, et **surtout** ce qui les rend mesurables.

Chaque technique ajoutée à un RAG — reclassement, expansion de requête, seuil
d'abstention, élargissement au voisinage — est une promesse de gain. La plupart
ne tiennent pas sur un corpus donné, et certaines dégradent. Tant qu'on les
empile derrière un seul chemin de code, personne ne peut le savoir : on constate
un score final, jamais la contribution de chaque brique.

D'où cet objet. Il n'apporte aucune fonctionnalité ; il rend possible la seule
chose qui compte, à savoir **débrancher une brique et remesurer**. C'est ce que
fait `rag eval --ablation`, qui exécute le même jeu de questions sous plusieurs
configurations et rend l'écart de chacune.

Une distinction structure tout le module :

- les réglages **de recherche** (`dense`, `rerank`, `multi_query`, `hyde_documents`,
  `neighbours`, seuils) se changent à chaud, question par question. Ils sont donc
  ablatables ;
- le réglage **d'indexation** (`contextual`) ne l'est pas : il détermine ce qui
  a été vectorisé. Le comparer impose de réindexer. Il vit ici pour être lisible
  au même endroit, mais `ablations()` ne le fait pas varier — le prétendre serait
  mentir sur ce qui a été mesuré.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, replace

logger = logging.getLogger("agent.rag.config")

RERANKERS = ("none", "llm")
MAX_HYDE_DOCUMENTS = 8
CUSTOM_PROFILE = "custom"
MODERN_HYDE_PROFILE = "modern-hyde-v1"
PROFILES = (CUSTOM_PROFILE, MODERN_HYDE_PROFILE)

# Seuil d'abstention retenu par `rag eval --calibrate` sur le corpus de
# démonstration, au critère « exactitude maximale, puis seuil le plus bas ».
# À recalculer sur tout autre corpus : ce n'est pas une constante du domaine.
CALIBRATED_THRESHOLD = 4.5


@dataclass(frozen=True)
class RagConfig:
    """Une configuration de recherche. Immuable : deux mesures ne doivent jamais
    partager un objet que l'une des deux pourrait modifier."""

    profile: str = CUSTOM_PROFILE
    """Profil nommé résolu avant les éventuelles surcharges d'environnement."""

    # --- Récupération ---------------------------------------------------------
    dense: bool = True
    """Recherche vectorielle. Coupée, il ne reste que le lexical : c'est la
    mesure qui dit ce que l'embedding apporte réellement."""

    sparse: bool = True
    """Recherche lexicale (`tsvector` français). Coupée, on retombe sur le RAG
    naïf — et les identifiants, références et montants deviennent introuvables."""

    sparse_weight: float = 0.3
    """Poids de la branche lexicale dans la fusion RRF, la dense valant 1.

    **0,3 est une valeur CALIBRÉE sur ce corpus, pas une constante universelle.**
    Elle est le défaut parce qu'il serait absurde de livrer une valeur qu'on a
    mesurée moins bonne (1,0 rend 80,6 % de rappel, 0,3 en rend 89,2 %) ; elle est
    à recalibrer sur un autre corpus, par `make ablation`. Un corpus riche en
    références et en identifiants mérite un poids plus élevé.

    **Le réglage que la RRF « sans calibration » cache sous le tapis.** L'argument
    habituel — RRF ne regarde que les rangs, donc il n'y a rien à régler — est
    vrai pour l'échelle des scores et faux pour l'importance relative des deux
    listes. À poids égal, un fragment premier en lexical et vingtième en dense
    marque 1/61 + 1/80 = 0,029 et passe devant un fragment premier en dense et
    absent en lexical, qui ne marque que 1/61 = 0,016. Autrement dit, le
    récupérateur le plus faible peut faire descendre le meilleur résultat de
    l'autre.

    Mesuré sur ce corpus : dense seul rend 94,6 % de rappel, lexical seul 61,3 %,
    et leur fusion à poids égal **80,6 %** — moins bien que le dense seul. Le
    poids se calibre donc, et l'ablation est là pour ça."""

    candidates: int = 30
    """Vivier tiré de CHAQUE recherche avant fusion. Plus large = plus de chances
    que le bon fragment soit là pour le reclassement, et plus cher."""

    top_k: int = 5
    """Fragments finalement rendus au modèle."""

    # --- Expansion de requête -------------------------------------------------
    multi_query: int = 0
    """Nombre de reformulations générées par le LLM, en plus de la question
    d'origine. 0 désactive. Coûte un appel de modèle par recherche."""

    hyde_documents: int = 0
    """Documents hypothétiques générés puis encodés comme des documents.

    Leurs vecteurs, avec celui de la question si ``hyde_include_query`` est vrai,
    remplacent le vecteur dense de la question originale. Les reformulations
    multi-requête conservent leurs propres vecteurs."""

    hyde_include_query: bool = True
    """Inclut le vecteur de la question dans la moyenne HyDE (équation 8 du papier)."""

    hyde_temperature: float = 0.7
    """Température d'échantillonnage des documents HyDE.

    Le reclassement reste à zéro. HyDE utilise 0,7 dans l'implémentation des
    auteurs afin que plusieurs documents ne soient pas des copies identiques."""

    # --- Reclassement ---------------------------------------------------------
    rerank: str = "none"
    """`none` ou `llm`. Le reclassement relit les candidats et les ordonne sur le
    fond, là où la fusion RRF n'ordonne que sur des rangs."""

    rerank_pool: int = 20
    """Candidats soumis au reclasseur. Au-delà d'une vingtaine, la fenêtre du
    modèle et la latence montent plus vite que la qualité."""

    # --- Abstention -----------------------------------------------------------
    min_rerank_score: float | None = None
    """Score minimal (échelle 0–10 du reclasseur) en dessous duquel un fragment
    est écarté. `None` = aucun seuil, c'est-à-dire le comportement d'un RAG qui
    répond toujours quelque chose.

    La valeur ne se choisit pas : `rag eval --calibrate` la DÉDUIT d'un critère
    écrit, en balayant la plage sur une seule passe de reclassement. Sur le
    corpus de démonstration et le profil ``modern-hyde-v1``, elle vaut 4,5.
    Elle doit être recalculée dès que le corpus ou le modèle change."""

    min_similarity: float | None = None
    """Similarité cosinus minimale du meilleur résultat dense, en dessous de
    laquelle la recherche entière est déclarée sans réponse. Sert quand le
    reclassement est désactivé."""

    # --- Contexte rendu -------------------------------------------------------
    neighbours: int = 0
    """Fragments voisins recollés autour de chaque fragment retenu (*small-to-big*).
    Une réponse coupée en deux par le découpage redevient lisible."""

    # --- Indexation (NON ablatable à chaud) -----------------------------------
    #
    # Ces trois réglages déterminent ce qui a été VECTORISÉ. Les comparer impose
    # de réindexer — ce que l'ingestion fait d'elle-même, en détectant que le
    # profil d'index a changé.
    contextual: bool = False
    """Préfixe chaque fragment par son contexte documentaire avant vectorisation."""

    chunk_tokens: int = 300
    """Taille visée d'un fragment, en tokens estimés.

    300 est un ordre de grandeur usuel, et un compromis assumé : plus gros, un
    fragment contient plusieurs sujets et son vecteur devient la moyenne floue de
    tous ; plus petit, la réponse se retrouve coupée en deux et aucune moitié ne
    répond seule. Sur un corpus de documents courts — attestations, avenants,
    courriels — une valeur élevée revient à indexer un document entier par
    vecteur, ce qui n'est pas absurde mais rend le découpage sans effet."""

    chunk_overlap: int = 1
    """Paragraphes repris du fragment précédent."""

    def index_profile(self) -> str:
        """Signature des réglages qui conditionnent le contenu de l'index.

        Stockée avec chaque document et comparée à l'ingestion : changer l'un de
        ces réglages déclenche une réindexation au lieu de laisser cohabiter deux
        façons de vectoriser dans le même espace. Une chaîne plutôt que des
        colonnes — ajouter un réglage demanderait sinon une migration à chaque
        fois, ce qui décourage précisément l'expérimentation qu'on cherche.
        """
        return f"ctx={int(self.contextual)};tok={self.chunk_tokens};ovl={self.chunk_overlap}"

    def label(self) -> str:
        """Nom court, pour les tableaux de mesure."""
        parties = []
        if self.dense and self.sparse:
            parties.append(
                "hybride" if self.sparse_weight == 1.0 else f"hybride:{self.sparse_weight:g}"
            )
        elif self.dense:
            parties.append("dense")
        elif self.sparse:
            parties.append("lexical")
        else:
            parties.append("aucune-recherche")
        if self.multi_query:
            parties.append(f"mq{self.multi_query}")
        if self.hyde_documents:
            parties.append(f"hyde{self.hyde_documents}")
        if self.rerank != "none":
            parties.append(f"rerank:{self.rerank}")
        if self.neighbours:
            parties.append(f"voisins{self.neighbours}")
        if self.min_rerank_score is not None:
            parties.append(f"seuil{self.min_rerank_score:g}")
        elif self.min_similarity is not None:
            parties.append(f"sim{self.min_similarity:g}")
        label = "+".join(parties)
        return f"{self.profile}[{label}]" if self.profile != CUSTOM_PROFILE else label

    def with_(self, **changements) -> RagConfig:
        return replace(self, **changements)


def _int_env(name: str, fallback: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return fallback
    try:
        value = int(raw)
    except ValueError:
        logger.warning("%s illisible (%r), repli sur %d", name, raw, fallback)
        return fallback
    if value < 0:
        logger.warning("%s négatif (%d), repli sur %d", name, value, fallback)
        return fallback
    return value


def _float_env(name: str) -> float | None:
    """`None` quand la variable est absente ou vide — et c'est significatif.

    Un seuil absent ne vaut pas « seuil à zéro » : le premier laisse tout passer,
    le second aussi, mais seul le premier dit « aucun seuil n'a été calibré ».
    La distinction compte quand on relit une mesure six mois plus tard.
    """
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        logger.warning("%s illisible (%r), seuil ignoré", name, raw)
        return None


def _bool_env(name: str, fallback: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return fallback
    return raw in {"1", "true", "yes", "oui", "on"}


def _profile_defaults() -> RagConfig:
    name = os.getenv("RAG_PROFILE", CUSTOM_PROFILE).strip().lower() or CUSTOM_PROFILE
    if name == MODERN_HYDE_PROFILE:
        return RagConfig(
            profile=name,
            dense=True,
            sparse=True,
            sparse_weight=0.3,
            candidates=30,
            top_k=5,
            multi_query=3,
            hyde_documents=1,
            hyde_include_query=True,
            hyde_temperature=0.7,
            rerank="llm",
            rerank_pool=20,
            min_rerank_score=CALIBRATED_THRESHOLD,
            neighbours=1,
        )
    if name not in PROFILES:
        logger.warning(
            "RAG_PROFILE inconnu (%s), repli sur %s. Connus : %s",
            name,
            CUSTOM_PROFILE,
            ", ".join(PROFILES),
        )
    return RagConfig()


def from_env() -> RagConfig:
    """Configuration par défaut du service, lue à chaque appel.

    Relue et non mémorisée : changer une variable dans le conteneur doit prendre
    effet sans redémarrage, ce qui est la seule façon de comparer deux réglages
    en production sans interrompre le service.
    """
    defaut = _profile_defaults()
    if defaut.profile != CUSTOM_PROFILE:
        # Un profil versionné est atomique : des variables laissées dans un ancien
        # `.env` ne doivent pas en désactiver silencieusement la moitié. Les
        # réglages d'indexation restent séparés car ils imposent une réindexation
        # et ne font volontairement pas partie du profil de recherche à chaud.
        return defaut.with_(
            contextual=_bool_env("RAG_CONTEXTUAL", defaut.contextual),
            chunk_tokens=_int_env("RAG_CHUNK_TOKENS", defaut.chunk_tokens)
            or defaut.chunk_tokens,
            chunk_overlap=_int_env("RAG_CHUNK_OVERLAP", defaut.chunk_overlap),
        )

    rerank = os.getenv("RAG_RERANK", defaut.rerank).strip().lower() or defaut.rerank
    if rerank not in RERANKERS:
        logger.warning("RAG_RERANK inconnu (%s), reclassement désactivé", rerank)
        rerank = "none"

    poids = _float_env("RAG_SPARSE_WEIGHT")
    hyde_temperature = _float_env("RAG_HYDE_TEMPERATURE")
    min_rerank_score = _float_env("RAG_MIN_RERANK_SCORE")
    min_similarity = _float_env("RAG_MIN_SIMILARITY")
    return RagConfig(
        profile=defaut.profile,
        dense=_bool_env("RAG_DENSE", defaut.dense),
        sparse=_bool_env("RAG_SPARSE", defaut.sparse),
        sparse_weight=defaut.sparse_weight if poids is None else max(0.0, poids),
        candidates=_int_env("RAG_CANDIDATES", defaut.candidates) or defaut.candidates,
        top_k=_int_env("RAG_TOP_K", defaut.top_k) or defaut.top_k,
        multi_query=_int_env("RAG_MULTI_QUERY", defaut.multi_query),
        hyde_documents=min(
            _int_env("RAG_HYDE_DOCUMENTS", defaut.hyde_documents),
            MAX_HYDE_DOCUMENTS,
        ),
        hyde_include_query=_bool_env(
            "RAG_HYDE_INCLUDE_QUERY",
            defaut.hyde_include_query,
        ),
        hyde_temperature=(
            defaut.hyde_temperature
            if hyde_temperature is None
            else min(max(hyde_temperature, 0.0), 2.0)
        ),
        rerank=rerank,
        rerank_pool=_int_env("RAG_RERANK_POOL", defaut.rerank_pool)
        or defaut.rerank_pool,
        min_rerank_score=(
            defaut.min_rerank_score
            if min_rerank_score is None
            else min_rerank_score
        ),
        min_similarity=(
            defaut.min_similarity if min_similarity is None else min_similarity
        ),
        neighbours=_int_env("RAG_NEIGHBOURS", defaut.neighbours),
        contextual=_bool_env("RAG_CONTEXTUAL", defaut.contextual),
        chunk_tokens=_int_env("RAG_CHUNK_TOKENS", defaut.chunk_tokens)
        or defaut.chunk_tokens,
        chunk_overlap=_int_env("RAG_CHUNK_OVERLAP", defaut.chunk_overlap),
    )


# --- Jeu d'ablation -----------------------------------------------------------


def ablations(base: RagConfig | None = None) -> list[tuple[str, RagConfig]]:
    """Configurations comparées par `rag eval --ablation`.

    Le principe est qu'**une seule chose change à la fois** par rapport à la
    référence hybride. Empiler deux techniques et constater un gain ne dit pas
    laquelle a servi ; c'est exactement l'erreur qui produit des billets de blog
    enthousiastes et des systèmes qu'on ne sait plus régler.

    La dernière ligne cumule tout : c'est la seule qui a le droit de le faire, et
    son intérêt est de montrer que la somme des gains isolés n'est presque jamais
    le gain total.

    **L'élargissement au voisinage se lit sur la couverture du fait, pas sur le
    rappel.** Il modifie le texte restitué et non le classement : rappel et MRR,
    qui portent sur les sources retrouvées, y sont aveugles par construction et
    afficheront un écart nul. C'est pour le voir qu'existe la colonne
    « couverture », qui mesure si le texte rendu contient réellement la réponse.
    """
    base = base or RagConfig()
    # Le seuil de similarité est laissé à None ici : sa valeur ne se devine pas,
    # elle se calibre sur les négatifs difficiles.
    # Les poids sont écrits explicitement plutôt qu'hérités de `base` : le défaut
    # du projet est lui-même une valeur calibrée, et une ligne « à poids égal »
    # qui suivrait le défaut ne serait plus à poids égal le jour où on le change.
    nu = base.with_(
        sparse_weight=1.0,
        multi_query=0,
        hyde_documents=0,
        rerank="none",
        neighbours=0,
    )
    pondere = nu.with_(sparse_weight=0.3)

    return [
        ("dense seul (l'état réel de départ)", nu.with_(dense=True, sparse=False)),
        ("lexical seul", nu.with_(dense=False, sparse=True)),
        ("hybride RRF à poids égal  ← référence", nu),
        ("hybride RRF pondéré 0,5", nu.with_(sparse_weight=0.5)),
        ("hybride RRF pondéré 0,3", pondere),
        ("+ voisinage", pondere.with_(neighbours=1)),
        ("+ multi-requête", pondere.with_(multi_query=3)),
        ("+ HyDE", pondere.with_(hyde_documents=1)),
        ("+ reclassement", pondere.with_(rerank="llm")),
        (
            "+ reclassement + seuil",
            # Valeur DÉDUITE par `rag eval --calibrate`, pas choisie.
            pondere.with_(rerank="llm", min_rerank_score=CALIBRATED_THRESHOLD),
        ),
        (
            "tout cumulé sans HyDE",
            pondere.with_(
                multi_query=3,
                rerank="llm",
                min_rerank_score=CALIBRATED_THRESHOLD,
                neighbours=1,
            ),
        ),
        (
            "tout cumulé + HyDE",
            pondere.with_(
                multi_query=3,
                hyde_documents=1,
                rerank="llm",
                min_rerank_score=CALIBRATED_THRESHOLD,
                neighbours=1,
            ),
        ),
    ]
