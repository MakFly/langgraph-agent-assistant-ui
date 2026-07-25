"""Configuration de la journalisation.

On teste les briques pures (analyse des niveaux, rendu du contexte) et pas
`setup_logging()` : celui-ci reconfigure la journalisation du processus entier, donc
l'appeler ici saboterait la capture de `caplog` des autres tests.
"""

from __future__ import annotations

import json
import logging
from uuid import uuid4

from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from agent.core.callbacks import RunMetricsHandler
from agent.infra.log import JsonFormatter, TextFormatter, _channel_levels, _level


def record(**contexte) -> logging.LogRecord:
    rec = logging.LogRecord("agent.test", logging.INFO, "f.py", 1, "message %s", ("x",), None)
    rec.__dict__.update(contexte)
    return rec


def test_niveau_nomme_insensible_a_la_casse():
    assert _level("debug") == logging.DEBUG
    assert _level(" Warning ") == logging.WARNING


def test_un_niveau_inconnu_ne_casse_pas_le_demarrage():
    """Un `LOG_LEVEL` mal orthographié doit dégrader, pas empêcher le service de
    démarrer."""
    assert _level("verbeux") == logging.INFO
    assert _level(None) == logging.INFO


def test_niveaux_par_canal():
    assert _channel_levels("agent.stream=DEBUG, uvicorn.access=WARNING") == {
        "agent.stream": logging.DEBUG,
        "uvicorn.access": logging.WARNING,
    }
    # Entrées vides ou incomplètes ignorées plutôt que fatales.
    assert _channel_levels("") == {}
    assert _channel_levels("agent.db,=INFO") == {}


def test_le_contexte_est_rendu_en_texte():
    """Sans ça, un `extra={...}` serait silencieusement perdu à l'affichage."""
    rendu = TextFormatter("%(levelname)s %(name)s : %(message)s").format(
        record(provider="openai", modele="gpt-5.6-luna")
    )

    assert "INFO agent.test : message x" in rendu
    assert "provider=openai" in rendu
    assert "modele=gpt-5.6-luna" in rendu


def test_le_contexte_precede_la_trace():
    """Placé après, il se perdait sous une trace de 40 lignes — donc invisible là où
    il est le plus utile."""
    try:
        raise RuntimeError("400 refusé")
    except RuntimeError:
        import sys

        rec = record(provider="openai")
        rec.exc_info = sys.exc_info()

    rendu = TextFormatter("%(message)s").format(rec)
    assert rendu.index("provider=openai") < rendu.index("RuntimeError: 400 refusé")


def test_le_contexte_est_rendu_en_json():
    payload = json.loads(JsonFormatter().format(record(provider="groq")))

    assert payload["level"] == "INFO"
    assert payload["channel"] == "agent.test"
    assert payload["message"] == "message x"
    assert payload["provider"] == "groq"


def test_la_trace_est_incluse_en_json():
    try:
        raise RuntimeError("400 refusé")
    except RuntimeError:
        import sys

        rec = record()
        rec.exc_info = sys.exc_info()

    payload = json.loads(JsonFormatter().format(rec))
    assert "RuntimeError: 400 refusé" in payload["exception"]


# --- Métriques de run ---------------------------------------------------------


async def test_les_metriques_du_tour_llm_sont_journalisees(caplog):
    """Latence, temps jusqu'au premier token et tokens, sur `agent.metrics`."""
    handler = RunMetricsHandler()
    run_id = uuid4()
    message = AIMessage(
        content="ok",
        usage_metadata={"input_tokens": 12, "output_tokens": 3, "total_tokens": 15},
    )

    with caplog.at_level(logging.INFO, logger="agent.metrics"):
        await handler.on_chat_model_start({}, [[]], run_id=run_id)
        await handler.on_llm_new_token("o", run_id=run_id)
        await handler.on_llm_end(
            LLMResult(generations=[[ChatGeneration(message=message)]]), run_id=run_id
        )

    record = next(r for r in caplog.records if r.getMessage() == "tour LLM terminé")
    assert record.duree_ms >= 0
    assert record.premier_token_ms >= 0
    assert record.tokens_entree == 12 and record.tokens_total == 15


async def test_la_duree_de_chaque_outil_est_journalisee(caplog):
    """Indexé par `run_id` : deux outils du même tour peuvent tourner en parallèle."""
    handler = RunMetricsHandler()
    premier, second = uuid4(), uuid4()

    with caplog.at_level(logging.INFO, logger="agent.metrics"):
        await handler.on_tool_start({"name": "weather_forecast"}, "{}", run_id=premier)
        await handler.on_tool_start({"name": "calculator"}, "{}", run_id=second)
        await handler.on_tool_end("...", run_id=second)
        await handler.on_tool_error(RuntimeError("boum"), run_id=premier)

    fins = [r for r in caplog.records if r.getMessage().startswith("outil")]
    assert [(r.outil, r.getMessage()) for r in fins] == [
        ("calculator", "outil terminé"),
        ("weather_forecast", "outil en échec"),
    ]
