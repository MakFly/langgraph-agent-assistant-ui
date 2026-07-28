"""Configuration globale de l'agent : outils, prompt, modèle, serveurs MCP.

**Aucun import FastAPI ici.** Ce module est le domaine : modèles de configuration,
snapshot en mémoire, lecture/écriture en base. La surface HTTP vit dans
`agent.api.settings` et ne fait qu'appeler ces fonctions.

Trois contraintes ont dicté la forme de ce module.

1. **Pas d'auth, donc pas de config par utilisateur.** La configuration est
   globale : une ligne par domaine (`agent`, `model`, `tools`) dans la table
   `settings`, valeur en JSONB.

2. **Le chat ne dépend jamais de la base.** Les lectures dégradent vers les
   valeurs par défaut (celles de l'environnement) quand Postgres est injoignable ;
   seules les écritures échouent — le refus HTTP 503, lui, est décidé par la couche
   API. `db.is_available()` est le juge.

3. **Le graphe est mis en cache.** Le nœud agent lit un *snapshot en mémoire*
   (`current()`), jamais la base — le chemin du chat reste synchrone et sans I/O.
   Chaque mutation appelle `refresh()`, qui incrémente `version()` et invalide
   ainsi le cache de `agent.core.graph.get_graph()`. Sans ce compteur, un réglage
   n'aurait aucun effet avant le redémarrage du conteneur : c'est le piège
   principal de cette fonctionnalité.

`refresh()` redécouvre aussi les outils MCP (`agent.core.mcp`).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from agent.core import mcp
from agent.core.model import (
    DEFAULT_MODELS,
    PROVIDER_KEYS,
    PROVIDER_MODELS,
    EffortLevel,
    Provider,
    default_provider,
    default_temperature,
    effort_levels,
    has_key,
)
from agent.core.tools import TOOLS
from agent.infra import db

logger = logging.getLogger("agent.settings")

# Garde-fou de la boucle ReAct : voir `agent.core.graph.MAX_TOOL_LOOPS`. La valeur vit
# ici parce que c'est d'abord un réglage — le graphe la lit, il ne la possède pas.
DEFAULT_MAX_TOOL_LOOPS = 5

# Bornes exposées au front pour qu'il affiche les mêmes limites que la validation.
MAX_TOOL_LOOPS_RANGE = (1, 20)
TEMPERATURE_RANGE = (0.0, 2.0)

# Fenêtre de contexte envoyée au modèle, **valeur par défaut**. Le client renvoie tout
# l'historique à chaque tour : sans plafond, le coût d'une conversation croît en O(N²) et
# on finit sur un `context_length_exceeded`. 24 000 tokens tient dans le plus petit modèle
# du catalogue (`qwen3:8b`, 32k) en laissant la place à la réponse et aux résultats
# d'outils.
#
# C'est désormais un **défaut**, plus une constante : les fenêtres vont de 32 k à
# beaucoup plus selon le modèle, et un plafond unique dimensionné sur le plus petit
# gaspille le contexte de tous les autres. La valeur est donc réglable
# (`AgentConfig.max_context_tokens`).
#
# Pourquoi un réglage plutôt qu'un catalogue « fenêtre par modèle » : ce catalogue
# existerait à côté de `PROVIDER_MODELS`, qui est déjà le point de rot connu du projet.
# L'opérateur, lui, connaît le modèle qu'il a choisi.
DEFAULT_MAX_CONTEXT_TOKENS = 24_000

# Plancher : en dessous, l'historique est rogné si vite que la conversation perd le fil.
# Plafond : borne de garde-fou, pas une limite technique — au-delà, c'est le provider
# qui refusera, et son message sera plus juste que le nôtre.
MAX_CONTEXT_TOKENS_RANGE = (2_000, 1_000_000)

# Conservé pour compatibilité : plusieurs modules et tests importent ce nom comme
# « la fenêtre par défaut ».
MAX_CONTEXT_TOKENS = DEFAULT_MAX_CONTEXT_TOKENS


# --- Modèles de configuration -------------------------------------------------


class AgentConfig(BaseModel):
    """`system_prompt` à None = on garde le prompt par défaut du graphe."""

    system_prompt: str | None = None
    max_tool_loops: int = Field(default=DEFAULT_MAX_TOOL_LOOPS, ge=1, le=20)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    # Au-delà, l'historique le plus ancien n'est plus envoyé au modèle. À régler sur
    # la fenêtre du modèle actif, moins la place voulue pour la réponse.
    max_context_tokens: int = Field(
        default=DEFAULT_MAX_CONTEXT_TOKENS,
        ge=MAX_CONTEXT_TOKENS_RANGE[0],
        le=MAX_CONTEXT_TOKENS_RANGE[1],
    )


class ModelConfig(BaseModel):
    """`model` à None = on garde le modèle par défaut du provider.

    `reasoning_effort` à `"default"` = on ne transmet aucun palier, le modèle garde
    son comportement natif. Les paliers ne sont pas acceptés par tous les modèles :
    `agent.model.effort_levels()` arbitre, ici on ne fait que stocker le choix.
    """

    provider: Provider = "groq"
    model: str | None = None
    reasoning_effort: EffortLevel = "default"


class Settings(BaseModel):
    """Configuration résolue : défauts d'environnement + surcharges en base."""

    agent: AgentConfig
    model: ModelConfig
    # Un booléen par outil de `TOOLS`, toujours complet (cf. `_resolve`).
    tools: dict[str, bool]


# --- Snapshot en mémoire ------------------------------------------------------

_snapshot: Settings | None = None
_version = 0


def _defaults() -> Settings:
    """Valeurs par défaut, relues dans l'environnement à chaque appel (les tests
    peuvent donc jouer sur LLM_PROVIDER / LLM_TEMPERATURE)."""
    return Settings(
        agent=AgentConfig(temperature=default_temperature()),
        model=ModelConfig(provider=default_provider()),  # type: ignore[arg-type]
        tools={tool.name: True for tool in TOOLS},
    )


def _resolve(stored: dict[str, Any]) -> Settings:
    defaults = _defaults()

    # Une ligne corrompue ou écrite par une version antérieure ne doit pas
    # empêcher l'agent de démarrer : le domaine fautif retombe sur ses défauts,
    # et lui seul.
    try:
        agent = AgentConfig(**{**defaults.agent.model_dump(), **(stored.get("agent") or {})})
    except (ValidationError, TypeError):
        logger.warning("réglages `agent` illisibles, retour aux défauts", exc_info=True)
        agent = defaults.agent
    try:
        model = ModelConfig(**{**defaults.model.model_dump(), **(stored.get("model") or {})})
    except (ValidationError, TypeError):
        logger.warning("réglages `model` illisibles, retour aux défauts", exc_info=True)
        model = defaults.model

    # Un palier d'effort que le modèle actif n'accepte pas est neutralisé : le graphe
    # ne doit jamais transmettre un paramètre que le provider refuserait (cf.
    # agent.model.EFFORT_MODELS). Comme les mutations repartent de ce snapshot, la
    # neutralisation finit aussi en base : basculer vers un modèle sans paliers
    # **perd** le choix, il faut le refaire en revenant. C'est assumé — conserver la
    # valeur exigerait de relire la ligne brute à chaque patch pour un confort mince.
    if model.reasoning_effort != "default" and model.reasoning_effort not in effort_levels(
        model.provider, model.model
    ):
        logger.info(
            "palier d'effort neutralisé : modèle incompatible",
            extra={
                "provider": model.provider,
                "modele": model.model or "défaut du provider",
                "effort": model.reasoning_effort,
            },
        )
        model = model.model_copy(update={"reasoning_effort": "default"})

    # On repart de `TOOLS` : un outil ajouté au code est actif par défaut, un
    # outil supprimé disparaît même si la base en garde la trace.
    stored_tools = stored.get("tools") or {}
    tools = {tool.name: bool(stored_tools.get(tool.name, True)) for tool in TOOLS}

    return Settings(agent=agent, model=model, tools=tools)


def current() -> Settings:
    """Snapshot synchrone, sans I/O : c'est ce que lit le graphe."""
    global _snapshot
    if _snapshot is None:
        _snapshot = _defaults()
    return _snapshot


def version() -> int:
    """Compteur de mutations. `agent.graph.get_graph()` s'en sert de clé de cache."""
    return _version


def enabled_tools() -> list[Any]:
    """Les outils réellement passés au modèle."""
    config = current()
    return [tool for tool in TOOLS if config.tools.get(tool.name, True)]


async def refresh() -> Settings:
    """Relit la base et republie le snapshot. Appelée au démarrage et après
    chaque mutation. Sans base : snapshot = valeurs par défaut."""
    global _snapshot, _version
    _snapshot = _resolve(await _load())

    # Redécouverte des outils MCP **avant** d'incrémenter la version : c'est elle qui
    # déclenche la reconstruction du graphe, qui doit donc voir le cache à jour.
    # `mcp.refresh()` ne lève jamais — un serveur injoignable ne doit pas faire échouer
    # un réglage sans rapport.
    await mcp.refresh(await list_mcp())

    _version += 1
    return _snapshot


async def _load() -> dict[str, Any]:
    if not await db.is_available():
        return {}
    try:
        rows = await db.pool().fetch("SELECT key, value FROM settings")
    except Exception:  # noqa: BLE001 - la config est optionnelle, le chat non
        logger.warning("lecture des réglages impossible, défauts appliqués", exc_info=True)
        return {}
    return {row["key"]: json.loads(row["value"]) for row in rows}


async def save(key: str, value: dict[str, Any]) -> None:
    # Point de passage unique de toute mutation de configuration : c'est ici qu'on
    # trace « qui a changé quoi », plutôt que dans chacun des endpoints.
    logger.info("réglages enregistrés", extra={"domaine": key, "valeur": value})
    await db.pool().execute(
        """
        INSERT INTO settings (key, value, updated_at)
        VALUES ($1, $2::jsonb, now())
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()
        """,
        key,
        json.dumps(value),
    )


# --- Sérialisation ------------------------------------------------------------


def mcp_json(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "transport": row["transport"],
        "url": row["url"],
        "command": row["command"],
        "args": json.loads(row["args"]) if isinstance(row["args"], str) else row["args"],
        "env": json.loads(row["env"]) if isinstance(row["env"], str) else row["env"],
        "enabled": row["enabled"],
    }


def _providers_json() -> list[dict[str, Any]]:
    """Jamais de valeur de clé ici : seulement « est-elle configurée ? »."""
    return [
        {
            "id": provider,
            "default_model": DEFAULT_MODELS.get(provider),
            # Catalogue indicatif (cf. agent.model.PROVIDER_MODELS) : il alimente le
            # sélecteur du composer, il ne borne pas ce que `PATCH /model` accepte.
            "models": PROVIDER_MODELS.get(provider, []),
            "requires_key": key_name is not None,
            "has_key": has_key(provider),
        }
        for provider, key_name in PROVIDER_KEYS.items()
    ]


async def list_mcp(with_status: bool = False) -> list[dict[str, Any]]:
    if not await db.is_available():
        return []
    rows = await db.pool().fetch(
        """
        SELECT id, name, transport, url, command, args, env, enabled
        FROM mcp_servers
        ORDER BY created_at, id
        """
    )
    servers = [mcp_json(dict(row)) for row in rows]
    if not with_status:
        return servers

    # État réel de la dernière découverte : le front affiche « 3 outils » ou l'erreur,
    # au lieu de laisser croire qu'un serveur enregistré est un serveur branché.
    etats = mcp.status()
    for server in servers:
        server["status"] = etats.get(
            server["id"],
            {"state": "idle" if not server["enabled"] else "unknown", "tools": 0, "error": None},
        )
    return servers


async def state() -> dict[str, Any]:
    """L'état complet, tel que le consomme le panneau de configuration du front."""
    config = current()
    effective = config.model.model or DEFAULT_MODELS.get(config.model.provider)
    return {
        # false = on sert les défauts, rien ne sera enregistré (base absente).
        "persisted": await db.is_available(),
        "agent": {
            **config.agent.model_dump(),
            "max_tool_loops_range": list(MAX_TOOL_LOOPS_RANGE),
            "temperature_range": list(TEMPERATURE_RANGE),
            "max_context_tokens_range": list(MAX_CONTEXT_TOKENS_RANGE),
            # Alias historique de `max_context_tokens`, conservé parce que la jauge de
            # contexte du composer le lit sous ce nom. Il vaut désormais la valeur
            # CONFIGURÉE, plus une constante : sinon la jauge afficherait un plafond
            # que le serveur n'applique plus.
            "context_window_tokens": config.agent.max_context_tokens,
        },
        "model": {
            **config.model.model_dump(),
            "effective_model": effective,
            # Paliers d'effort acceptés par le modèle **actif** : liste vide = le
            # réglage n'a pas de sens ici, le front le désactive au lieu d'envoyer
            # un paramètre que le provider refuserait.
            "effort_levels": effort_levels(config.model.provider, effective),
            "providers": _providers_json(),
        },
        "tools": [
            {
                "name": tool.name,
                # Docstring repliée sur une ligne : la couper au premier \n
                # tronquerait en plein milieu d'une phrase.
                "description": " ".join((tool.description or "").split()),
                "enabled": config.tools.get(tool.name, True),
            }
            for tool in TOOLS
        ],
        "mcp_servers": await list_mcp(with_status=True),
    }


