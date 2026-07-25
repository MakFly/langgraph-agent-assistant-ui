"""Les outils sont testés en vrai contre leurs APIs publiques : ce sont des
dépendances externes assumées du POC, un mock ne dirait rien de leur santé.
Le calculateur, lui, est local et testé à fond côté sécurité.
"""

from __future__ import annotations

import json

import pytest

from agent.core.tools import (
    calculator,
    hacker_news_search,
    tool_error_message,
    weather_forecast,
    wikipedia_search,
)


async def call(tool, **kwargs) -> dict:
    return json.loads(await tool.ainvoke(kwargs))


class TestCalculator:
    @pytest.mark.parametrize(
        ("expression", "expected"),
        [
            ("(1234 * 0.2) + 15", 261.8),
            ("2 ** 10", 1024),
            ("sqrt(16) + 2", 6.0),
            ("90 / 3.6", 25.0),
            ("round(0.18 * 2340, 2)", 421.2),
        ],
    )
    async def test_calculs_valides(self, expression, expected):
        assert (await call(calculator, expression=expression))["result"] == pytest.approx(expected)

    @pytest.mark.parametrize(
        "expression",
        [
            "__import__('os').system('id')",  # exécution de commande
            "().__class__.__bases__[0]",  # évasion par attributs
            "open('/etc/passwd').read()",  # fonction non autorisée
            "[x for x in range(10)]",  # comprehension
            "lambda: 1",  # lambda
            "2 ** 10 ** 9",  # épuisement CPU
            "print(1)",  # fonction hors liste blanche
        ],
    )
    async def test_expressions_refusees(self, expression):
        """L'expression vient du LLM : tout ce qui n'est pas explicitement
        autorisé doit être rejeté, pas seulement filtré.

        L'outil **lève** désormais au lieu de renvoyer `{"error": ...}` : c'est
        `ToolNode(handle_tool_errors=…)` qui met l'erreur en forme pour le modèle. Ce
        que le LLM reçoit au bout du compte est vérifié à travers le graphe, dans
        `test_graph.py::test_erreur_d_outil_renvoyee_au_modele`.
        """
        with pytest.raises(Exception, match="refus|autoris|dépasse|invalide|trop"):
            await call(calculator, expression=expression)

    @pytest.mark.parametrize("expression", ["lambda: 1", "print(1)"])
    async def test_une_expression_refusee_devient_un_message_pour_le_modele(self, expression):
        """Le contrat côté LLM, au niveau unitaire : une erreur d'outil doit arriver
        sous la forme `{"error": ...}` — c'est ce qui lui permet de se rattraper."""
        try:
            await call(calculator, expression=expression)
        except Exception as error:  # noqa: BLE001 - c'est justement l'objet du test
            assert "error" in json.loads(tool_error_message(error))
        else:
            pytest.fail("l'expression aurait dû être refusée")


class TestApisExternes:
    async def test_wikipedia(self):
        result = await call(wikipedia_search, query="LangChain", lang="en")
        assert result["results"], "aucun article renvoyé"
        assert result["results"][0]["extract"]

    async def test_hacker_news(self):
        result = await call(hacker_news_search, query="langgraph", sort_by="date")
        assert isinstance(result["stories"], list)

    async def test_meteo(self):
        result = await call(weather_forecast, city="Lyon")
        assert "Lyon" in result["location"]
        assert result["current"]["temperature"].endswith("°C")
        assert len(result["forecast"]) == 3

    async def test_ville_inconnue_renvoie_une_erreur_lisible(self):
        """L'outil lève, et le message reste compréhensible pour le modèle une fois mis
        en forme par `ToolNode(handle_tool_errors=…)`."""
        with pytest.raises(Exception, match="introuvable"):
            await call(weather_forecast, city="ZzzVilleInexistante")
