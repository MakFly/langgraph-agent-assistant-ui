"""Vectorisation des fragments — registre de fournisseurs interchangeables.

**Pourquoi un registre plutôt qu'un `if`.** Le modèle d'embedding est le choix le
plus coûteux à revenir dessus dans un RAG : il fixe la dimension, donc le schéma,
donc l'index, et en changer impose de tout réindexer. C'est précisément pour ça
qu'il faut pouvoir en essayer un autre sans réécrire la chaîne — sinon on garde
le premier par inertie, et on ne saura jamais ce qu'un autre aurait donné.

Un fournisseur se déclare en trois choses : un nom, une dimension native, deux
méthodes. `register()` en bas de fichier suffit à en ajouter un ; rien d'autre
dans le projet n'a besoin de le connaître.

**Ce que chaque fournisseur coûte :**

- `openai` — le défaut. Facturé à l'ingestion, pas à la requête. La famille
  `text-embedding-3-*` accepte le paramètre `dimensions`, donc on peut raccourcir
  le vecteur pour rester sous la limite d'index de pgvector.
- `ollama` — local, gratuit, sans clé. Demande un serveur Ollama joignable et le
  modèle déjà tiré. C'est le fournisseur à préférer pour comparer sans dépenser,
  et le seul qui garde le corpus sur la machine.
- `hash` — *hashing trick* déterministe, sans réseau ni clé. Il ne comprend
  **rien** à la sémantique : deux formulations du même fait lui sont étrangères.
  Il n'existe que pour rendre la chaîne exécutable dans les tests. **Toute mesure
  de pertinence faite avec `hash` ne veut rien dire.**

Le nom du modèle n'est pas figé dans une constante : les catalogues changent plus
vite que les projets. `EMBEDDING_MODEL` le porte, et les défauts ci-dessous sont
à confronter au catalogue en vigueur avant toute ingestion facturée.
"""

from __future__ import annotations

import hashlib
import logging
import math
import os
import re
from dataclasses import dataclass
from typing import Protocol

from agent.infra import ragdb

logger = logging.getLogger("agent.rag.embed")

# À VÉRIFIER dans le catalogue du fournisseur avant la première ingestion facturée.
DEFAULT_MODELS = {
    "openai": "text-embedding-3-small",
    "ollama": "nomic-embed-text",
    "hash": "hash-local",
}

# Dimension native de chaque défaut. Sert à prévenir AVANT l'appel quand
# EMBEDDING_DIM ne correspond pas, plutôt que de le découvrir sur le vecteur rendu.
NATIVE_DIMENSIONS = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "nomic-embed-text": 768,
    "mxbai-embed-large": 1024,
}


class EmbeddingProvider(Protocol):
    """Contrat minimal d'un fournisseur.

    Volontairement réduit à ce dont la chaîne a besoin. Un fournisseur n'a pas à
    connaître le découpage, les ACL ni la base : il transforme du texte en
    vecteurs, et c'est tout.
    """

    name: str

    def supports_dimension(self, dim: int) -> bool:
        """La dimension demandée est-elle atteignable ?

        Appelé avant toute vectorisation : découvrir l'incompatibilité sur le
        premier vecteur rendu, c'est la découvrir après avoir payé.
        """
        ...

    async def embed_documents(self, texts: list[str], dim: int) -> list[list[float]]: ...

    async def embed_query(self, text: str, dim: int) -> list[float]: ...


# --- Fournisseur local, déterministe ------------------------------------------


@dataclass
class HashProvider:
    """*Hashing trick* : chaque mot tombe dans une case, avec un signe.

    Aucune graine aléatoire, donc deux exécutions produisent le même index —
    condition sans laquelle un test d'idempotence n'aurait aucun sens.
    """

    name: str = "hash"

    def supports_dimension(self, dim: int) -> bool:
        return dim > 0

    def _vector(self, text: str, dim: int) -> list[float]:
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

    async def embed_documents(self, texts: list[str], dim: int) -> list[list[float]]:
        return [self._vector(text, dim) for text in texts]

    async def embed_query(self, text: str, dim: int) -> list[float]:
        return self._vector(text, dim)


# --- OpenAI -------------------------------------------------------------------


@dataclass
class OpenAIProvider:
    name: str = "openai"

    def _client(self, dim: int):
        from langchain_openai import OpenAIEmbeddings

        if not os.getenv("OPENAI_API_KEY", "").strip():
            raise RuntimeError(
                "OPENAI_API_KEY absente : impossible de vectoriser. Renseignez la clé, "
                "choisissez EMBEDDING_PROVIDER=ollama pour un modèle local, ou "
                "EMBEDDING_PROVIDER=hash pour exercer la chaîne sans réseau (la "
                "pertinence, elle, ne voudra alors plus rien dire)."
            )
        return OpenAIEmbeddings(model=model_name(), dimensions=dim)

    def supports_dimension(self, dim: int) -> bool:
        """La famille `text-embedding-3-*` sait raccourcir ses vecteurs.

        Les modèles antérieurs (`ada-002`) n'acceptent pas `dimensions` : demander
        autre chose que leur dimension native produirait une erreur d'API, ou pire
        un vecteur tronqué côté client.
        """
        model = model_name()
        if model.startswith("text-embedding-3"):
            return 0 < dim <= NATIVE_DIMENSIONS.get(model, dim)
        return dim == NATIVE_DIMENSIONS.get(model, dim)

    async def embed_documents(self, texts: list[str], dim: int) -> list[list[float]]:
        return await self._client(dim).aembed_documents(texts)

    async def embed_query(self, text: str, dim: int) -> list[float]:
        return await self._client(dim).aembed_query(text)


# --- Ollama -------------------------------------------------------------------


@dataclass
class OllamaProvider:
    """Modèle local. Le serveur tourne sur l'hôte, pas dans le conteneur."""

    name: str = "ollama"

    def _client(self):
        from langchain_ollama import OllamaEmbeddings

        return OllamaEmbeddings(
            model=model_name(),
            base_url=os.getenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434"),
        )

    def supports_dimension(self, dim: int) -> bool:
        """Un modèle Ollama rend sa dimension native, sans troncature possible.

        On refuse donc tout ce qui ne correspond pas — quand la dimension native
        est connue. Pour un modèle inconnu du tableau, on laisse passer : la
        vérification post-vectorisation de `_check` prendra le relais avec un
        message qui dit quoi corriger.
        """
        native = NATIVE_DIMENSIONS.get(model_name())
        return native is None or native == dim

    async def embed_documents(self, texts: list[str], dim: int) -> list[list[float]]:
        return await self._client().aembed_documents(texts)

    async def embed_query(self, text: str, dim: int) -> list[float]:
        return await self._client().aembed_query(text)


# --- Registre -----------------------------------------------------------------

_REGISTRY: dict[str, EmbeddingProvider] = {}


def register(provider: EmbeddingProvider) -> None:
    """Déclare un fournisseur. Le seul point d'extension du module.

    Pour en ajouter un (Voyage, Mistral, un modèle servi en interne) : écrire une
    classe qui satisfait `EmbeddingProvider`, l'enregistrer ici, renseigner sa
    dimension native dans `NATIVE_DIMENSIONS`, et rien d'autre dans le projet n'a
    besoin d'être touché.
    """
    _REGISTRY[provider.name] = provider


register(OpenAIProvider())
register(OllamaProvider())
register(HashProvider())

PROVIDERS = tuple(_REGISTRY)


def provider_name() -> str:
    name = os.getenv("EMBEDDING_PROVIDER", "openai").strip().lower()
    if name not in _REGISTRY:
        logger.warning(
            "EMBEDDING_PROVIDER inconnu (%s), repli sur openai. Connus : %s",
            name,
            ", ".join(sorted(_REGISTRY)),
        )
        return "openai"
    return name


def current() -> EmbeddingProvider:
    return _REGISTRY[provider_name()]


# Conservé sous son ancien nom : `cli.py` et les tests l'appellent.
def provider() -> str:
    return provider_name()


def model_name() -> str:
    """Identifiant du modèle, enregistré avec chaque document.

    Il est stocké en base pour que « ces vecteurs viennent-ils du même modèle ? »
    soit une question à laquelle on puisse répondre six mois plus tard.
    """
    explicite = os.getenv("EMBEDDING_MODEL", "").strip()
    if explicite:
        return explicite
    return DEFAULT_MODELS.get(provider_name(), "inconnu")


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


def describe() -> str:
    """Une ligne lisible, à afficher avant toute mesure.

    Une mesure dont on ne sait pas avec quel modèle elle a été prise n'est pas
    une mesure, c'est un nombre.
    """
    return f"{model_name()} ({dimension()} dimensions, fournisseur {provider_name()})"


# --- Surface publique ---------------------------------------------------------


def _guard() -> tuple[EmbeddingProvider, int]:
    fournisseur, dim = current(), dimension()
    if not fournisseur.supports_dimension(dim):
        native = NATIVE_DIMENSIONS.get(model_name())
        raise RuntimeError(
            f"Le modèle {model_name()} ({fournisseur.name}) ne peut pas produire de "
            f"vecteur de dimension {dim}"
            + (f" — sa dimension native est {native}." if native else ".")
            + " Alignez EMBEDDING_DIM sur le modèle, puis réindexez "
            "(`make rag-reset && make ingest`)."
        )
    return fournisseur, dim


async def embed_documents(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    fournisseur, dim = _guard()
    vectors = await fournisseur.embed_documents(texts, dim)
    _check(vectors[0])
    return vectors


async def embed_query(text: str) -> list[float]:
    """Vectorise une question.

    Passe par le même chemin que les documents : utiliser deux modèles — ou deux
    dimensions — de part et d'autre produit une recherche qui *fonctionne* et ne
    trouve rien de pertinent.
    """
    fournisseur, dim = _guard()
    vector = await fournisseur.embed_query(text, dim)
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
