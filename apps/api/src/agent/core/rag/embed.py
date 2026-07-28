"""Vectorisation des fragments.

Deux fournisseurs, pour deux usages qui n'ont rien à voir :

- **`openai`** — le vrai. C'est lui qui coûte de l'argent, et il en coûte à
  l'ingestion, pas à la requête : un `make ingest` lancé sur un gros corpus brûle
  un budget sans que personne ne regarde. D'où le décompte de tokens rendu par
  `estimate()` et le plafond appliqué par `agent.core.rag.ingest`.

- **`hash`** — un vectoriseur déterministe et local (*hashing trick*), sans clé
  ni réseau. Il ne comprend rien à la sémantique : deux formulations différentes
  du même fait lui semblent étrangères. Il n'est là que pour rendre la chaîne
  complète — découpage, indexation, filtrage ACL, fusion RRF — exécutable dans
  les tests et sur une machine sans clé API. **Toute mesure de qualité faite avec
  `hash` ne veut rien dire** ; seule la mécanique est vérifiée.

Le nom du modèle n'est pas codé en dur dans une constante figée : les catalogues
de modèles changent plus vite que les projets. `EMBEDDING_MODEL` le porte, et la
valeur par défaut ci-dessous est à confronter au catalogue en vigueur avant toute
ingestion réelle.
"""

from __future__ import annotations

import hashlib
import logging
import math
import os
import re

from agent.infra import ragdb

logger = logging.getLogger("agent.rag.embed")

PROVIDERS = ("openai", "hash")

# À VÉRIFIER dans le catalogue OpenAI en vigueur avant la première ingestion
# facturée. Cette famille accepte le paramètre `dimensions`, qui permet de
# raccourcir le vecteur et donc de rester sous la limite d'index de pgvector
# (cf. `ragdb.MAX_HNSW_DIM`).
DEFAULT_OPENAI_MODEL = "text-embedding-3-small"


def provider() -> str:
    name = os.getenv("EMBEDDING_PROVIDER", "openai").strip().lower()
    if name not in PROVIDERS:
        logger.warning("EMBEDDING_PROVIDER inconnu (%s), repli sur openai", name)
        return "openai"
    return name


def model_name() -> str:
    """Identifiant du modèle, enregistré avec chaque document.

    Il est stocké en base pour que « ces vecteurs viennent-ils du même modèle ? »
    soit une question à laquelle on puisse répondre six mois plus tard.
    """
    if provider() == "hash":
        return "hash-local"
    return os.getenv("EMBEDDING_MODEL", "").strip() or DEFAULT_OPENAI_MODEL


def dimension() -> int:
    return ragdb.embedding_dim()


def estimate(texts: list[str]) -> int:
    """Tokens facturés, approximativement (≈ 4 caractères par token)."""
    return sum(max(1, len(text) // 4) for text in texts)


def cost_per_million_tokens() -> float | None:
    """Tarif du modèle, s'il a été renseigné.

    Aucune valeur par défaut : un prix codé en dur devient faux sans prévenir, et
    afficher un coût faux est pire que n'en afficher aucun.
    """
    raw = os.getenv("EMBEDDING_COST_PER_MTOK", "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        logger.warning("EMBEDDING_COST_PER_MTOK illisible, coût non estimé")
        return None


# --- Vectoriseur local --------------------------------------------------------


def _hash_vector(text: str, dim: int) -> list[float]:
    """*Hashing trick* : chaque mot tombe dans une case, avec un signe.

    Déterministe (aucune graine aléatoire), donc deux exécutions produisent le
    même index — condition sans laquelle un test d'idempotence n'aurait aucun sens.
    """
    vector = [0.0] * dim
    for token in re.findall(r"\w+", text.lower()):
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        index = int.from_bytes(digest[:4], "big") % dim
        vector[index] += 1.0 if digest[4] % 2 == 0 else -1.0

    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0.0:
        # Un vecteur nul rend la distance cosinus indéfinie (NaN côté pgvector),
        # ce qui contaminerait silencieusement le classement.
        vector[0] = 1.0
        return vector
    return [value / norm for value in vector]


# --- Vectoriseur OpenAI -------------------------------------------------------


def _openai_client():
    from langchain_openai import OpenAIEmbeddings

    if not os.getenv("OPENAI_API_KEY", "").strip():
        raise RuntimeError(
            "OPENAI_API_KEY absente : impossible de vectoriser. Renseignez la clé, "
            "ou passez EMBEDDING_PROVIDER=hash pour exercer la chaîne sans réseau "
            "(la pertinence, elle, ne voudra alors plus rien dire)."
        )

    return OpenAIEmbeddings(model=model_name(), dimensions=dimension())


# --- Surface publique ---------------------------------------------------------


async def embed_documents(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []

    if provider() == "hash":
        dim = dimension()
        return [_hash_vector(text, dim) for text in texts]

    vectors = await _openai_client().aembed_documents(texts)
    _check(vectors[0])
    return vectors


async def embed_query(text: str) -> list[float]:
    """Vectorise une question.

    Passe par le même chemin que les documents : utiliser deux modèles — ou deux
    dimensions — de part et d'autre produit une recherche qui *fonctionne* et ne
    trouve rien de pertinent.
    """
    if provider() == "hash":
        return _hash_vector(text, dimension())

    vector = await _openai_client().aembed_query(text)
    _check(vector)
    return vector


def _check(vector: list[float]) -> None:
    expected = dimension()
    if len(vector) != expected:
        raise RuntimeError(
            f"Le modèle {model_name()} a produit un vecteur de dimension "
            f"{len(vector)}, or l'index attend {expected}. Alignez EMBEDDING_DIM "
            f"sur le modèle, puis réindexez (`make rag-reset && make ingest`)."
        )
