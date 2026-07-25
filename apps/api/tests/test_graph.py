"""Tests du graphe via `ui_message_stream()`, c'est-à-dire le chemin exact de
l'endpoint /api/chat. Seul l'appel réseau au LLM est remplacé par un faux modèle.

Les outils, eux, s'exécutent réellement (mathjs-like local, Open-Meteo) :
ce ne sont pas des mocks.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.messages.utils import count_tokens_approximately

from agent.core.graph import MAX_CONTEXT_TOKENS, _windowed, build_graph
from agent.protocol.stream import ui_message_stream
from tests.fakes import FakeToolCallingModel, tool_call


def user(text: str) -> dict:
    return {"id": "u1", "role": "user", "parts": [{"type": "text", "text": text}]}


async def collect(model: FakeToolCallingModel, messages: list[dict]) -> list[dict]:
    """Consomme le stream SSE et renvoie les chunks décodés."""
    chunks: list[dict] = []
    async for line in ui_message_stream(messages, graph=build_graph(model)):
        payload = line.removeprefix("data: ").strip()
        if payload and payload != "[DONE]":
            chunks.append(json.loads(payload))
    return chunks


def types_of(chunks: list[dict]) -> list[str]:
    return [chunk["type"] for chunk in chunks]


async def test_appelle_un_outil_puis_repond():
    model = FakeToolCallingModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[tool_call("calculator", {"expression": "(1234 * 0.2) + 15"}, "c1")],
            ),
            AIMessage(content="Le résultat est 261.8."),
        ]
    )

    chunks = await collect(model, [user("Calcule 20% de 1234 plus 15")])

    assert "tool-input-available" in types_of(chunks)
    assert "tool-output-available" in types_of(chunks)

    # L'outil a réellement calculé.
    output = next(c for c in chunks if c["type"] == "tool-output-available")
    assert "261.8" in json.dumps(output)

    # Deux tours de modèle : demande d'outil, puis réponse.
    assert model.call_count == 2


async def test_les_arguments_de_l_outil_sont_emis_vers_le_client():
    """Régression : sans `tool-input-available`, le client stocke un appel sans
    arguments et le tour suivant se fait rejeter en 400 par l'API du modèle."""
    model = FakeToolCallingModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[tool_call("weather_forecast", {"city": "Lyon"}, "c1")],
            ),
            AIMessage(content="Il fait beau."),
        ]
    )

    chunks = await collect(model, [user("Météo à Lyon ?")])
    input_chunk = next(c for c in chunks if c["type"] == "tool-input-available")

    assert input_chunk["input"] == {"city": "Lyon"}
    assert input_chunk["toolName"] == "weather_forecast"
    assert input_chunk["toolCallId"] == "c1"

    # L'id doit être celui du modèle, pas un id de run inventé : c'est lui qui
    # relie l'appel à son résultat.
    output_chunk = next(c for c in chunks if c["type"] == "tool-output-available")
    assert output_chunk["toolCallId"] == "c1"


async def test_enchaine_plusieurs_outils_dans_le_meme_run():
    model = FakeToolCallingModel(
        responses=[
            AIMessage(
                content="", tool_calls=[tool_call("weather_forecast", {"city": "Lyon"}, "c1")]
            ),
            AIMessage(
                content="", tool_calls=[tool_call("calculator", {"expression": "2+2"}, "c2")]
            ),
            AIMessage(content="Voilà."),
        ]
    )

    chunks = await collect(model, [user("Météo à Lyon puis 2+2")])
    names = [c["toolName"] for c in chunks if c["type"] == "tool-input-available"]

    assert names == ["weather_forecast", "calculator"]
    assert model.call_count == 3


async def test_le_plafond_de_boucle_stoppe_un_modele_qui_boucle():
    model = FakeToolCallingModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[tool_call("calculator", {"expression": "1+1"}, f"c{i}")],
            )
            for i in range(20)
        ]
    )

    await collect(model, [user("boucle")])

    # MAX_TOOL_LOOPS = 5 : le graphe s'arrête bien avant d'épuiser la file.
    assert model.call_count <= 7


async def test_erreur_d_outil_renvoyee_au_modele():
    model = FakeToolCallingModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[tool_call("weather_forecast", {"city": "ZzzVilleInexistante"}, "c1")],
            ),
            AIMessage(content="Je n'ai pas trouvé cette ville."),
        ]
    )

    chunks = await collect(model, [user("météo à ZzzVilleInexistante")])

    # L'échec est signalé **comme un échec** dans le protocole. Avant, l'outil avalait
    # son exception et renvoyait un succès contenant `{"error": ...}` : l'UI ne pouvait
    # pas distinguer un outil en panne d'un outil qui a répondu. Depuis que
    # `ToolNode(handle_tool_errors=…)` s'en charge, le ToolMessage porte
    # `status="error"` et `stream.py` émet `tool-output-error`.
    assert "tool-output-available" not in types_of(chunks)
    error_chunk = next(c for c in chunks if c["type"] == "tool-output-error")
    assert "introuvable" in error_chunk["errorText"]

    # Ce qui compte le plus n'a pas changé : le modèle reçoit l'erreur et le run va au
    # bout, il peut donc l'expliquer plutôt que d'inventer.
    assert model.call_count == 2


async def test_le_texte_est_streame_en_text_delta():
    model = FakeToolCallingModel(responses=[AIMessage(content="Le résultat est 261.8.")])

    chunks = await collect(model, [user("2+2 ?")])
    text = "".join(c["delta"] for c in chunks if c["type"] == "text-delta")

    assert text == "Le résultat est 261.8."
    # Le protocole exige l'encadrement text-start / text-end.
    assert types_of(chunks).count("text-start") == 1
    assert types_of(chunks).count("text-end") == 1


async def test_enveloppe_du_protocole():
    model = FakeToolCallingModel(responses=[AIMessage(content="ok")])
    chunks = await collect(model, [user("salut")])

    assert types_of(chunks)[0] == "start"
    assert types_of(chunks)[-1] == "finish"


class GrapheEnPanne:
    """Graphe qui échoue comme le fait un provider qui refuse un paramètre."""

    async def astream(self, *_args, **_kwargs):
        raise RuntimeError(
            "Error code: 400 - Function tools with reasoning_effort are not supported"
        )
        yield  # pragma: no cover - rend la méthode génératrice asynchrone


async def test_un_echec_du_provider_est_journalise(caplog):
    """Régression : l'erreur ne partait QUE dans le flux SSE.

    Comme la réponse est un 200 qui streame, un 400 du provider n'apparaissait ni
    dans les logs uvicorn ni dans Dozzle — le symptôme était « 0 log conteneur ».
    """
    chunks: list[dict] = []
    with caplog.at_level(logging.ERROR, logger="agent.stream"):
        async for line in ui_message_stream([user("?")], graph=GrapheEnPanne()):
            payload = line.removeprefix("data: ").strip()
            if payload and payload != "[DONE]":
                chunks.append(json.loads(payload))

    # L'UI reçoit toujours l'erreur…
    assert [c for c in chunks if c["type"] == "error"], types_of(chunks)

    # …et le conteneur aussi, avec la trace et le contexte du modèle : sans provider
    # ni modèle, un « 400 invalid_request_error » n'est pas diagnosticable.
    erreurs = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(erreurs) == 1
    assert erreurs[0].exc_info is not None
    assert erreurs[0].provider and erreurs[0].modele and erreurs[0].effort


# --- Fenêtre de contexte ------------------------------------------------------


def test_la_fenetre_de_contexte_est_bornee():
    """Le client renvoie tout l'historique à chaque tour : sans plafond, le coût croît
    en O(N²) puis le provider répond `context_length_exceeded`."""
    longue = [HumanMessage("x" * 4000) for _ in range(50)]

    fenetre = _windowed(longue)

    assert 0 < len(fenetre) < len(longue)
    assert count_tokens_approximately(fenetre) <= MAX_CONTEXT_TOKENS


def test_la_fenetre_ne_commence_jamais_par_un_tool_message():
    """Le piège du rognage : un `ToolMessage` dont l'`AIMessage` porteur des
    `tool_call_id` est tombé hors fenêtre fait échouer la requête chez le provider."""
    messages: list[BaseMessage] = []
    for index in range(40):
        messages += [
            HumanMessage("q" * 2000),
            AIMessage(
                content="",
                tool_calls=[tool_call("calculator", {"expression": "1+1"}, f"c{index}")],
            ),
            ToolMessage(content="r" * 2000, tool_call_id=f"c{index}"),
        ]

    fenetre = _windowed(messages)

    assert len(fenetre) < len(messages)
    assert not isinstance(fenetre[0], ToolMessage)


# --- Reprise sur erreur transitoire -------------------------------------------


class Erreur429(Exception):
    """Quota dépassé, comme le renvoient les free tiers."""

    status_code = 429


class Erreur400(Exception):
    """Requête invalide : la rejouer ne sert qu'à tripler latence et facture."""

    status_code = 400


class ProviderInstable(FakeToolCallingModel):
    """Échoue les `pannes` premières tentatives, puis répond normalement.

    `echecs` est compté à part : `call_log` sert au parent à choisir la réponse
    suivante, une tentative avortée ne doit pas la consommer.
    """

    pannes: int = 1
    erreur: Any = None
    echecs: int = 0

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):  # type: ignore[override]
        if self.echecs < self.pannes:
            self.echecs += 1
            raise self.erreur
        return super()._stream(messages, stop, run_manager, **kwargs)


async def test_une_erreur_transitoire_est_rejouee():
    model = ProviderInstable(
        responses=[AIMessage(content="ok")], pannes=1, erreur=Erreur429("quota dépassé")
    )

    chunks = await collect(model, [user("salut")])

    # Le 429 est absorbé : l'utilisateur voit une réponse, pas une erreur.
    assert "error" not in types_of(chunks)
    assert "".join(c["delta"] for c in chunks if c["type"] == "text-delta") == "ok"
    assert model.echecs == 1  # une tentative perdue…
    assert model.call_count == 1  # …puis une seule réponse produite


async def test_un_400_n_est_jamais_rejoue():
    """Vécu dans ce projet : trois tentatives sur un `reasoning_effort` refusé, c'est
    trois fois la latence pour la même erreur."""
    model = ProviderInstable(
        responses=[AIMessage(content="jamais atteint")],
        pannes=99,
        erreur=Erreur400("param invalide"),
    )

    chunks = await collect(model, [user("salut")])

    # Une seule tentative, malgré `pannes=99` : le graphe n'a pas rejoué.
    assert "error" in types_of(chunks)
    assert model.echecs == 1
