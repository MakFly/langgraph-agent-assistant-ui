"""Fabrique de modèle : catalogue et effort de raisonnement.

Ni base ni réseau ici — instancier un client LangChain ne parle pas au provider, on
peut donc vérifier ce qui lui serait réellement transmis. C'est le seul moyen de
tester le point sensible : un palier d'effort refusé par le modèle ne doit pas
partir, sinon la requête échoue au premier message.
"""

from __future__ import annotations

import pytest

from agent.core.model import (
    DEFAULT_MODELS,
    EFFORT_LEVELS,
    PROVIDER_MODELS,
    create_model,
    effort_levels,
)


def test_le_defaut_est_la_tete_du_catalogue():
    for provider, models in PROVIDER_MODELS.items():
        assert DEFAULT_MODELS[provider] == models[0]


@pytest.mark.parametrize(
    ("provider", "model", "supporte"),
    [
        # Chez Groq, seuls les GPT-OSS acceptent les paliers : `qwen3.6-27b` ne connaît
        # que none/default et les Llama n'ont pas de raisonnement.
        ("groq", "openai/gpt-oss-120b", True),
        ("groq", "openai/gpt-oss-20b", True),
        ("groq", "qwen/qwen3.6-27b", False),
        ("groq", "llama-3.3-70b-versatile", False),
        # Toute la gamme Gemini et GPT-5.x l'accepte, y compris un nom saisi à la main.
        ("google", "gemini-3.6-flash", True),
        ("google", "un-modele-jamais-vu", True),
        ("openai", "gpt-5.6-sol", True),
        # Ollama expose `think`, trop dépendant du modèle pullé pour être promis.
        ("ollama", "qwen3:8b", False),
    ],
)
def test_paliers_par_couple_provider_modele(provider: str, model: str, supporte: bool):
    assert effort_levels(provider, model) == (list(EFFORT_LEVELS) if supporte else [])


def test_le_defaut_du_provider_est_pris_en_compte_sans_modele(monkeypatch):
    """Sans modèle explicite, c'est le défaut du provider qui décide."""
    monkeypatch.delenv("LLM_MODEL", raising=False)
    assert effort_levels("groq") == list(EFFORT_LEVELS)  # défaut = openai/gpt-oss-120b
    assert effort_levels("ollama") == []


def test_openai_passe_toujours_par_l_api_responses(monkeypatch):
    """Régression : `/v1/chat/completions` refuse les tools sur un modèle qui raisonne,
    **même sans** `reasoning_effort` dans la requête (les GPT-5.x raisonnent par
    défaut côté serveur). Comme le graphe binde toujours des tools, le transport ne
    peut pas dépendre du palier choisi.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "sk-de-test")

    for effort in ("default", "high", None):
        client = create_model("openai", "gpt-5.6-luna", reasoning_effort=effort)
        assert client.use_responses_api is True, f"effort={effort}"


def test_un_palier_refuse_n_est_pas_transmis_au_provider(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "sk-de-test")

    capable = create_model("groq", "openai/gpt-oss-120b", reasoning_effort="high")
    assert capable.reasoning_effort == "high"

    # Même palier, modèle qui ne sait pas le lire : ignoré plutôt que transmis.
    incapable = create_model("groq", "llama-3.3-70b-versatile", reasoning_effort="high")
    assert incapable.reasoning_effort is None

    # `default` = ne rien envoyer, y compris sur un modèle capable.
    neutre = create_model("groq", "openai/gpt-oss-120b", reasoning_effort="default")
    assert neutre.reasoning_effort is None
