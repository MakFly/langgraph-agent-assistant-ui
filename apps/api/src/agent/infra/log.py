"""Journalisation : canaux, niveaux, formats.

**On ne réécrit pas Monolog.** Le module `logging` de la stdlib est déjà exactement
ça : canaux hiérarchiques, niveaux, handlers, formatters, contexte par message. Ce
module ne fait donc qu'une seule chose — le *configurer* une fois au démarrage — et
expose les variables d'environnement qui pilotent tout. Écrire une couche par-dessus
n'ajouterait que du code à maintenir et casserait l'intégration avec uvicorn,
langchain et httpx, qui journalisent déjà via `logging`.

Correspondance pour qui vient de Monolog :

    Monolog (PHP)                      ici (Python)
    ---------------------------------------------------------------------------
    new Logger('db')                   logging.getLogger("agent.db")
    pushHandler(new StreamHandler)     un handler, sur stdout
    processors / context               `extra={...}` à l'appel, rendu par le format
    formatters (Line / Json)           LOG_FORMAT=text | json
    niveau par canal                   LOG_LEVELS="agent.stream=DEBUG,agent.db=ERROR"

Trois décisions assumées :

  - **stdout, pas de fichier.** Le conteneur est éphémère : un fichier disparaîtrait
    au `docker compose down` et resterait invisible de Dozzle (http://localhost:8888),
    qui lit le flux Docker.
  - **La racine reste à WARNING.** Sinon httpx, langchain et asyncpg noient les
    messages de l'application en DEBUG. Seul le canal `agent` suit `LOG_LEVEL`.
  - **`uvicorn.access` est réglable à part** (`LOG_ACCESS=off` pour le couper) : une
    ligne par requête est utile en dev, bruyante quand on suit un run d'agent.
"""

from __future__ import annotations

import json
import logging
import logging.config
import os
from typing import Any

CHANNEL = "agent"

# Attributs standards d'un LogRecord : tout le reste vient d'un `extra={...}` et
# constitue le « contexte » du message, à la Monolog.
_STANDARD = frozenset(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {
    "asctime",
    "message",
    "taskName",
    # uvicorn glisse une variante colorée de son message dans `extra` : c'est un
    # doublon, pas du contexte.
    "color_message",
}


def _context(record: logging.LogRecord) -> dict[str, Any]:
    return {key: value for key, value in record.__dict__.items() if key not in _STANDARD}


class TextFormatter(logging.Formatter):
    """Format humain : en-tête, message, contexte en `clé=valeur`, puis la trace.

    Deux raisons de ne pas déléguer à `logging.Formatter.format()` :
      - un `extra={"provider": "openai"}` y serait silencieusement perdu — le genre
        de piège qui fait croire qu'on a journalisé alors qu'on n'a rien dit ;
      - le contexte doit précéder la trace. Ajouté après, il se retrouvait sous 40
        lignes de stack, donc invisible là où il sert justement le plus.
    """

    def format(self, record: logging.LogRecord) -> str:
        record.message = record.getMessage()
        if self.usesTime():
            record.asctime = self.formatTime(record, self.datefmt)

        line = self.formatMessage(record)
        context = _context(record)
        if context:
            line += " — " + " ".join(f"{key}={value}" for key, value in context.items())
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        if record.stack_info:
            line += "\n" + self.formatStack(record.stack_info)
        return line


class JsonFormatter(logging.Formatter):
    """Une ligne JSON par message : Dozzle sait les replier, `jq` les filtrer."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "channel": record.name,
            "message": record.getMessage(),
            **_context(record),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def _level(name: str | None, fallback: int = logging.INFO) -> int:
    """Niveau nommé, insensible à la casse, avec repli silencieux.

    Un `LOG_LEVEL` mal orthographié ne doit pas empêcher le service de démarrer : on
    retombe sur le défaut, et `setup_logging()` le signale.
    """
    if not name:
        return fallback
    resolved = logging.getLevelNamesMapping().get(name.strip().upper())
    return resolved if isinstance(resolved, int) else fallback


def _channel_levels(raw: str | None) -> dict[str, int]:
    """`"agent.stream=DEBUG,uvicorn=WARNING"` → `{"agent.stream": 10, ...}`."""
    levels: dict[str, int] = {}
    for pair in (raw or "").split(","):
        channel, _, level = pair.partition("=")
        if channel.strip() and level.strip():
            levels[channel.strip()] = _level(level)
    return levels


def setup_logging() -> None:
    """À appeler une fois, au tout début du démarrage (cf. `agent.main.lifespan`)."""
    fmt = (os.getenv("LOG_FORMAT") or "text").strip().lower()
    formatter = "json" if fmt == "json" else "text"
    requested = os.getenv("LOG_LEVEL")
    level = _level(requested)

    logging.config.dictConfig(
        {
            "version": 1,
            # Les loggers déjà créés (uvicorn les instancie avant nous) doivent garder
            # leurs messages : les désactiver rendrait le serveur muet.
            "disable_existing_loggers": False,
            "formatters": {
                "text": {
                    "()": TextFormatter,
                    "format": "%(asctime)s %(levelname)-8s %(name)s : %(message)s",
                    "datefmt": "%H:%M:%S",
                },
                "json": {"()": JsonFormatter},
            },
            "handlers": {
                "stdout": {
                    "class": "logging.StreamHandler",
                    "stream": "ext://sys.stdout",
                    "formatter": formatter,
                }
            },
            # Racine à WARNING : les bibliothèques ne parlent qu'en cas de problème.
            "root": {"handlers": ["stdout"], "level": "WARNING"},
            "loggers": {
                CHANNEL: {"level": level},
                # `propagate: False` sur les canaux uvicorn : ils ont déjà leur propre
                # handler, sans quoi chaque ligne sortirait en double.
                "uvicorn": {"handlers": ["stdout"], "level": "INFO", "propagate": False},
                "uvicorn.error": {
                    "handlers": ["stdout"],
                    "level": "INFO",
                    "propagate": False,
                },
                "uvicorn.access": {
                    "handlers": ["stdout"],
                    "level": "INFO" if (os.getenv("LOG_ACCESS") or "on") != "off" else "WARNING",
                    "propagate": False,
                },
            },
        }
    )

    # Les avertissements Python (dépréciations de langchain, par exemple) passent par
    # le même tuyau au lieu d'aller sur stderr sans horodatage.
    logging.captureWarnings(True)

    logger = logging.getLogger(f"{CHANNEL}.log")
    for channel, channel_level in _channel_levels(os.getenv("LOG_LEVELS")).items():
        logging.getLogger(channel).setLevel(channel_level)
        logger.info(
            "niveau de canal appliqué",
            extra={"canal": channel, "niveau": logging.getLevelName(channel_level)},
        )

    if requested and level != _level(requested, fallback=-1):
        logger.warning("LOG_LEVEL inconnu, INFO appliqué", extra={"demande": requested})

    logger.info(
        "journalisation prête",
        extra={"niveau": logging.getLevelName(level), "format": formatter},
    )
