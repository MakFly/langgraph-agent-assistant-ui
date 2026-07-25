"""Fabrique de modèle.

Quatre façons de faire tourner le POC. Toutes savent appeler des outils, seule
exigence réelle du graphe.

  - groq   : free tier, sans CB, de loin le plus rapide  -> console.groq.com/keys
  - google : free tier Gemini, quota quotidien large     -> aistudio.google.com/apikey
  - ollama : 100% local, aucun compte                    -> ollama.com
  - openai : payant à l'usage                            -> platform.openai.com/api-keys
"""

from __future__ import annotations

import logging
import os
from typing import Literal

from langchain_core.language_models import BaseChatModel

logger = logging.getLogger("agent.model")

Provider = Literal["groq", "google", "ollama", "openai"]

# Catalogue proposé au sélecteur de modèle (composer + onglet « Modèle »), par
# provider. Le premier de chaque liste est le défaut du provider.
#
# Liste **curatée à la main**, volontairement courte, et qui DATERA : elle n'est ni
# exhaustive ni validée. Aucun endpoint ne la fait respecter — la surcharge libre du
# panneau reste l'échappatoire pour un modèle absent d'ici, et un nom invalide se
# voit à la première réponse (l'erreur du provider remonte dans l'UI).
#
# Dernière vérification sur les docs des providers : 25-07-2026. Ce qui a bougé et
# qu'il faudra resurveiller :
#   - Groq  : `qwen/qwen3-32b` et `meta-llama/llama-4-scout-17b-16e-instruct` sont
#             éteints depuis le 17-07-2026 ; `llama-3.3-70b-versatile` et
#             `llama-3.1-8b-instant` s'éteignent le 16-08-2026 (remplaçants
#             annoncés : `openai/gpt-oss-*`, `qwen/qwen3.6-27b`). Ils restent listés
#             en fin de liste tant qu'ils répondent.
#   - Google: `gemini-2.0-flash` / `-lite` sont en fin de vie, retirés d'ici.
#   - OpenAI: la génération 5.6 abandonne les suffixes mini/nano pour des paliers
#             nommés (sol > terra > luna, du plus capable au moins cher). GA le
#             09-07-2026 ; `gpt-5.4-nano` reste listé comme option économique.
#   - Ollama: seuls les tags réellement `ollama pull` en local fonctionnent, donc
#             cette liste n'y est qu'un raccourci vers des modèles courants qui
#             savent appeler des outils.
PROVIDER_MODELS: dict[str, list[str]] = {
    "groq": [
        "openai/gpt-oss-120b",
        "qwen/qwen3.6-27b",
        "openai/gpt-oss-20b",
        "llama-3.3-70b-versatile",
        "llama-3.1-8b-instant",
    ],
    "google": [
        "gemini-3.6-flash",
        "gemini-3.5-flash",
        "gemini-3.5-flash-lite",
        "gemini-2.5-pro",
        "gemini-2.5-flash",
    ],
    "ollama": [
        "qwen3:8b",
        "granite4.1:8b",
        "ministral-3:8b",
        "phi4-mini:3.8b",
        "qwen3.6:27b",
        "llama3.2:3b",
    ],
    "openai": [
        "gpt-5.6-luna",
        "gpt-5.6-terra",
        "gpt-5.6-sol",
        "gpt-5.4-nano",
    ],
}

# Une seule source de vérité : le défaut d'un provider est la tête de son catalogue.
DEFAULT_MODELS = {provider: models[0] for provider, models in PROVIDER_MODELS.items()}

# Clé d'environnement attendue par provider. `None` = aucune clé (Ollama tourne
# en local). L'ordre de ce dict pilote l'ordre d'affichage côté front.
PROVIDER_KEYS: dict[str, str | None] = {
    "groq": "GROQ_API_KEY",
    "google": "GOOGLE_API_KEY",
    "ollama": None,
    "openai": "OPENAI_API_KEY",
}

SIGNUP_URLS = {
    "GROQ_API_KEY": "https://console.groq.com/keys",
    "GOOGLE_API_KEY": "https://aistudio.google.com/apikey",
    "OPENAI_API_KEY": "https://platform.openai.com/api-keys",
}

# --- Effort de raisonnement ---------------------------------------------------
#
# Les trois paliers communs à tous les providers qui savent en accepter un. Chaque
# provider en accepte davantage (`none`, `minimal`, `xhigh`, `max` selon les cas) :
# on n'expose que l'intersection, sinon le réglage ne voudrait pas dire la même
# chose d'un provider à l'autre.
EFFORT_LEVELS = ("low", "medium", "high")

EffortLevel = Literal["default", "low", "medium", "high"]

# **L'effort dépend du modèle, pas seulement du provider** — c'est le piège de ce
# réglage. Envoyé à un modèle qui ne l'accepte pas, le paramètre fait échouer la
# requête (Groq : `qwen/qwen3.6-27b` ne connaît que `none`/`default`, les Llama rien
# du tout). D'où cette table, consultée avant tout envoi.
#
#   None            = toute la gamme du provider l'accepte (y compris un modèle
#                     saisi à la main, tant qu'il vient de cette famille) ;
#   frozenset(...)  = seuls ces modèles l'acceptent ;
#   frozenset()     = aucun.
#
# Vérifié le 25-07-2026 : OpenAI GPT-5.x accepte none|minimal|low|medium|high|xhigh|
# max ; Gemini est normalisé par langchain-google-genai en minimal|low|medium|high ;
# Groq ne l'accepte que sur GPT-OSS. Ollama expose `think` (bool ou palier selon le
# modèle pullé) : trop dépendant de l'installation locale pour être promis ici.
#
# Attention, chez OpenAI la contrainte n'est pas seulement le modèle : sur
# `/v1/chat/completions`, `reasoning_effort` est refusé dès qu'il y a des tools. Le
# palier reste donc disponible, mais `create_model()` doit basculer sur l'API
# Responses pour l'honorer — voir plus bas.
EFFORT_MODELS: dict[str, frozenset[str] | None] = {
    "groq": frozenset({"openai/gpt-oss-120b", "openai/gpt-oss-20b"}),
    "google": None,
    "ollama": frozenset(),
    "openai": None,
}


def effort_levels(provider: str, model: str | None = None) -> list[str]:
    """Paliers d'effort acceptés par ce couple provider/modèle, sinon liste vide.

    Le front s'en sert pour n'afficher le réglage que là où il a un sens, et
    `create_model()` pour ne jamais transmettre un palier refusé.
    """
    allowed = EFFORT_MODELS.get(provider, frozenset())
    if allowed is None:
        return list(EFFORT_LEVELS)
    resolved = model or default_model(provider)
    return list(EFFORT_LEVELS) if resolved in allowed else []


def has_key(provider: str) -> bool:
    """Une clé est-elle configurée pour ce provider ?

    C'est la SEULE information qu'on expose sur les clés : l'API de configuration
    ne doit jamais renvoyer la valeur d'une clé, même tronquée.
    """
    name = PROVIDER_KEYS.get(provider)
    if name is None:
        return True  # Ollama : rien à configurer.
    return bool(os.getenv(name))


def default_provider() -> str:
    """Provider issu de l'environnement, avec repli si la valeur est inconnue."""
    provider = os.getenv("LLM_PROVIDER", "groq").lower()
    return provider if provider in PROVIDER_KEYS else "groq"


def default_temperature() -> float:
    """Température issue de l'environnement, bornée comme côté configuration."""
    try:
        value = float(os.getenv("LLM_TEMPERATURE", "0"))
    except ValueError:
        return 0.0
    return min(max(value, 0.0), 2.0)


def default_model(provider: str) -> str | None:
    return os.getenv("LLM_MODEL") or DEFAULT_MODELS.get(provider)


def _require_key(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(
            f"{name} manquante. Copiez apps/api/.env.example vers apps/api/.env "
            f"et renseignez une clé : {SIGNUP_URLS[name]}"
        )
    return value


def create_model(
    provider: str | None = None,
    model: str | None = None,
    temperature: float | None = None,
    reasoning_effort: str | None = None,
) -> BaseChatModel:
    """Instancie le modèle.

    Args:
        provider: surcharge du provider ; par défaut `LLM_PROVIDER`.
        model: surcharge du nom de modèle ; par défaut `LLM_MODEL` puis le défaut
            du provider.
        temperature: surcharge de la température ; par défaut `LLM_TEMPERATURE`.
        reasoning_effort: palier d'effort de raisonnement (`low`/`medium`/`high`).
            `None` ou `"default"` = on ne transmet rien, le modèle garde son
            comportement. Un palier que le modèle n'accepte pas est **ignoré**, pas
            transmis : mieux vaut un réglage sans effet qu'une requête refusée.

    Ces arguments viennent de la configuration (`agent.settings`) : c'est ce qui
    permet de changer de provider sans redémarrer le conteneur.
    """
    provider = (provider or default_provider()).lower()
    temperature = default_temperature() if temperature is None else temperature
    model = model or default_model(provider)

    effort = reasoning_effort if reasoning_effort in effort_levels(provider, model) else None
    if reasoning_effort not in (None, "default") and effort is None:
        logger.info(
            "palier d'effort ignoré : non accepté par ce modèle",
            extra={"provider": provider, "modele": model, "effort": reasoning_effort},
        )

    # Une seule ligne pour savoir avec quoi le graphe a été construit : c'est ce qui
    # manquait pour diagnostiquer un 400 du provider.
    logger.info(
        "modèle instancié",
        extra={
            "provider": provider,
            "modele": model,
            "temperature": temperature,
            "effort": effort or "défaut du modèle",
        },
    )

    if provider == "groq":
        from langchain_groq import ChatGroq

        return ChatGroq(
            model=model,
            temperature=temperature,
            reasoning_effort=effort,
            api_key=_require_key("GROQ_API_KEY"),
        )

    if provider == "google":
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(
            model=model,
            temperature=temperature,
            reasoning_effort=effort,
            api_key=_require_key("GOOGLE_API_KEY"),
        )

    if provider == "openai":
        from langchain_openai import ChatOpenAI

        # **Toujours** l'API Responses, pas seulement quand un palier est demandé.
        #   « Function tools with reasoning_effort are not supported for gpt-5.6-luna
        #     in /v1/chat/completions. To use function tools, use /v1/responses or
        #     set reasoning_effort to 'none'. »
        # Le piège : ce refus ne dépend pas de ce qu'on envoie. Les GPT-5.x raisonnent
        # par défaut côté serveur, donc `/v1/chat/completions` + tools échoue même
        # avec `reasoning_effort` absent — constaté avec le réglage sur « défaut du
        # modèle ». La seule alternative serait d'envoyer `reasoning_effort="none"`,
        # c'est-à-dire d'éteindre le raisonnement pour contourner le transport : le
        # mauvais compromis. Et ce graphe binde toujours des tools.
        return ChatOpenAI(
            model=model,
            temperature=temperature,
            reasoning_effort=effort,
            use_responses_api=True,
            api_key=_require_key("OPENAI_API_KEY"),
        )

    if provider == "ollama":
        from langchain_ollama import ChatOllama

        return ChatOllama(
            model=model,
            temperature=temperature,
            base_url=os.getenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434"),
        )

    raise RuntimeError(
        f'LLM_PROVIDER inconnu : "{provider}". Valeurs acceptées : groq | google | ollama | openai.'
    )
