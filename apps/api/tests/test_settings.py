"""API de configuration.

Comme pour les conversations, les tests tapent la vraie base (`infra-postgres`)
via l'app ASGI. Différence importante : la configuration est **globale** — elle
n'appartient à personne, elle s'applique à tout le monde. La fixture vide donc
les tables `settings` et `mcp_servers` avant ET après chaque test, et republie le
snapshot en mémoire — sinon un réglage laissé en base contaminerait le reste de
la suite.

C'est aussi pour cette raison que muter la configuration demande le rôle `admin` :
la fixture `client` se connecte avec un compte administrateur jetable. Le refus
opposé à un simple membre est vérifié dans `test_auth.py`.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from langchain_core.messages import AIMessage
from pydantic import Field

from agent.core import graph, settings, users
from agent.core.model import DEFAULT_MODELS
from agent.infra import db
from agent.main import app
from tests.fakes import FakeToolCallingModel, tool_call

ADMIN_EMAIL = "pytest-settings-admin@example.com"
ADMIN_PASSWORD = "mot-de-passe-de-test-1"


@pytest.fixture(autouse=True)
async def database(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AUTH_SECRET", "secret-de-test-suffisamment-long-pour-hs256")

    try:
        await db.connect()
    except Exception as error:  # pragma: no cover
        pytest.skip(f"Postgres injoignable : {error}")

    await _reset()
    users.reset_throttle()
    yield
    await _reset()
    await db.disconnect()


async def _reset() -> None:
    await db.pool().execute("DELETE FROM settings")
    await db.pool().execute("DELETE FROM mcp_servers")
    await db.pool().execute("DELETE FROM users WHERE email = $1", ADMIN_EMAIL)
    await settings.refresh()
    # Un test monkeypatche `build_graph` : on jette le cache pour ne pas laisser
    # un faux graphe derrière nous.
    graph._cached = None


@pytest.fixture
async def client():
    await users.create_user(ADMIN_EMAIL, ADMIN_PASSWORD, role="admin")
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as http_client:
        login = await http_client.post(
            "/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )
        assert login.status_code == 200, login.text
        yield http_client


class RecordingModel(FakeToolCallingModel):
    """Mémorise les outils reçus par `bind_tools` : c'est la seule façon de
    vérifier qu'un outil désactivé n'est pas déclaré au modèle."""

    bound_tools: list[str] = Field(default_factory=list)

    def bind_tools(self, tools, **kwargs):  # type: ignore[override]
        self.bound_tools = [tool.name for tool in tools]
        return self


# --- Lecture ------------------------------------------------------------------


async def test_valeurs_par_defaut_sans_configuration(client: AsyncClient):
    body = (await client.get("/api/settings")).json()

    assert body["persisted"] is True
    assert body["agent"] == {
        "system_prompt": None,
        "max_tool_loops": settings.DEFAULT_MAX_TOOL_LOOPS,
        "temperature": 0.0,
        "max_context_tokens": settings.DEFAULT_MAX_CONTEXT_TOKENS,
        "max_tool_loops_range": [1, 20],
        "temperature_range": [0.0, 2.0],
        "max_context_tokens_range": list(settings.MAX_CONTEXT_TOKENS_RANGE),
        # Le front affiche le contexte restant : il lui faut le plafond réellement
        # appliqué, pas une constante dupliquée côté client.
        "context_window_tokens": settings.MAX_CONTEXT_TOKENS,
    }
    # Tous les outils du code sont actifs par défaut.
    assert all(tool["enabled"] for tool in body["tools"])
    assert {tool["name"] for tool in body["tools"]} == {
        "wikipedia_search",
        "hacker_news_search",
        "weather_forecast",
        "calculator",
        # Recherche dans le corpus interne : activable et désactivable comme les
        # autres, mais seule à filtrer ses résultats sur l'identité de l'appelant.
        "document_search",
    }
    assert body["mcp_servers"] == []


async def test_les_cles_api_ne_sortent_jamais(client: AsyncClient, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "sk-secret-de-test")
    raw = (await client.get("/api/settings")).text

    assert "sk-secret-de-test" not in raw
    assert "api_key" not in raw
    assert "GROQ_API_KEY" not in raw

    # Seul un booléen est exposé, et il reflète bien l'environnement.
    providers = (await client.get("/api/settings")).json()["model"]["providers"]
    by_id = {provider["id"]: provider for provider in providers}
    assert by_id["groq"]["has_key"] is True
    assert by_id["ollama"]["requires_key"] is False

    monkeypatch.delenv("GROQ_API_KEY")
    providers = (await client.get("/api/settings")).json()["model"]["providers"]
    assert {p["id"]: p["has_key"] for p in providers}["groq"] is False


async def test_chaque_provider_expose_son_catalogue_de_modeles(client: AsyncClient):
    """Le sélecteur du composer ne propose que les modèles du provider courant : le
    catalogue voyage donc avec chaque provider, son défaut en tête (c'est de cette
    tête que `DEFAULT_MODELS` est dérivé — l'invariant est vérifié ici)."""
    providers = (await client.get("/api/settings")).json()["model"]["providers"]

    for provider in providers:
        assert provider["models"], f"catalogue vide pour {provider['id']}"
        assert provider["models"][0] == provider["default_model"]


# --- Écriture -----------------------------------------------------------------


async def test_mise_a_jour_de_l_agent_puis_relecture(client: AsyncClient):
    patched = await client.patch(
        "/api/settings/agent",
        json={"system_prompt": "Réponds en breton.", "max_tool_loops": 12, "temperature": 1.5},
    )
    assert patched.status_code == 200

    agent = (await client.get("/api/settings")).json()["agent"]
    assert agent["system_prompt"] == "Réponds en breton."
    assert agent["max_tool_loops"] == 12
    assert agent["temperature"] == 1.5

    # Un patch partiel ne doit pas écraser les autres champs.
    await client.patch("/api/settings/agent", json={"temperature": 0.2})
    agent = (await client.get("/api/settings")).json()["agent"]
    assert agent["max_tool_loops"] == 12
    assert agent["system_prompt"] == "Réponds en breton."

    # Chaîne vide = retour au prompt par défaut.
    await client.patch("/api/settings/agent", json={"system_prompt": ""})
    assert (await client.get("/api/settings")).json()["agent"]["system_prompt"] is None


@pytest.mark.parametrize(
    "payload",
    [
        {"max_tool_loops": 0},
        {"max_tool_loops": 21},
        {"max_tool_loops": -1},
        {"temperature": -0.1},
        {"temperature": 2.5},
    ],
)
async def test_bornes_refusees(client: AsyncClient, payload: dict):
    assert (await client.patch("/api/settings/agent", json=payload)).status_code == 422


async def test_mise_a_jour_du_modele(client: AsyncClient):
    patched = await client.patch(
        "/api/settings/model", json={"provider": "google", "model": "gemini-2.5-pro"}
    )
    assert patched.status_code == 200
    assert patched.json()["model"]["provider"] == "google"
    assert patched.json()["model"]["effective_model"] == "gemini-2.5-pro"

    # Surcharge effacée : on retombe sur le modèle par défaut du provider. Comparé à
    # `DEFAULT_MODELS` et non à un nom en dur : le catalogue suit le marché, ce test
    # vérifie le repli, pas la mode du moment.
    await client.patch("/api/settings/model", json={"model": ""})
    body = (await client.get("/api/settings")).json()["model"]
    assert body["model"] is None
    assert body["effective_model"] == DEFAULT_MODELS["google"]

    # Provider inconnu refusé par le Literal.
    assert (
        await client.patch("/api/settings/model", json={"provider": "mistral"})
    ).status_code == 422


async def test_effort_de_raisonnement(client: AsyncClient):
    body = (await client.get("/api/settings")).json()["model"]
    assert body["reasoning_effort"] == "default"

    # Modèle capable : le palier est accepté et les niveaux sont annoncés au front.
    patched = await client.patch(
        "/api/settings/model",
        json={"provider": "groq", "model": "openai/gpt-oss-120b", "reasoning_effort": "high"},
    )
    assert patched.status_code == 200
    assert patched.json()["model"]["reasoning_effort"] == "high"
    assert patched.json()["model"]["effort_levels"] == ["low", "medium", "high"]

    # Modèle qui ne connaît pas les paliers : le réglage est neutralisé à la lecture,
    # et le front reçoit une liste vide (il désactive le contrôle).
    patched = await client.patch(
        "/api/settings/model", json={"model": "llama-3.3-70b-versatile"}
    )
    assert patched.json()["model"]["reasoning_effort"] == "default"
    assert patched.json()["model"]["effort_levels"] == []

    # La neutralisation est définitive : revenir à un modèle capable ne ressuscite pas
    # le palier, il faut le rechoisir (cf. `_resolve`).
    patched = await client.patch("/api/settings/model", json={"model": "openai/gpt-oss-120b"})
    assert patched.json()["model"]["reasoning_effort"] == "default"
    assert patched.json()["model"]["effort_levels"] == ["low", "medium", "high"]

    # Palier inconnu refusé par le Literal.
    assert (
        await client.patch("/api/settings/model", json={"reasoning_effort": "extreme"})
    ).status_code == 422


async def test_activation_des_outils(client: AsyncClient):
    patched = await client.patch("/api/settings/tools/calculator", json={"enabled": False})
    assert patched.status_code == 200

    body = (await client.get("/api/settings")).json()
    tools = {tool["name"]: tool["enabled"] for tool in body["tools"]}
    assert tools["calculator"] is False
    assert tools["wikipedia_search"] is True

    # /api/health expose les outils réellement actifs (la sidebar s'en sert).
    assert "calculator" not in (await client.get("/api/health")).json()["tools"]

    unknown = await client.patch("/api/settings/tools/nope", json={"enabled": True})
    assert unknown.status_code == 404


# --- Effet réel sur le graphe -------------------------------------------------


async def test_un_outil_desactive_n_est_pas_passe_au_modele(client: AsyncClient):
    await client.patch("/api/settings/tools/calculator", json={"enabled": False})

    model = RecordingModel(responses=[AIMessage(content="ok")])
    graph.build_graph(model)

    assert "calculator" not in model.bound_tools
    assert "wikipedia_search" in model.bound_tools


async def test_le_prompt_et_le_plafond_surchargent_les_defauts(client: AsyncClient):
    await client.patch(
        "/api/settings/agent", json={"system_prompt": "Prompt surchargé.", "max_tool_loops": 1}
    )

    model = RecordingModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[tool_call("calculator", {"expression": "1+1"}, f"c{i}")],
            )
            for i in range(10)
        ]
    )
    compiled = graph.build_graph(model)
    await compiled.ainvoke({"messages": [("user", "boucle")]})

    # max_tool_loops = 1 : bien moins de tours qu'avec le défaut (5).
    assert model.call_count <= 3
    # Le prompt système transmis au modèle est celui de la configuration.
    assert model.call_log[0][0].content == "Prompt surchargé."


async def test_le_cache_du_graphe_est_invalide_par_un_changement_de_config(
    client: AsyncClient, monkeypatch
):
    """Le piège de la fonctionnalité : sans invalidation, un réglage n'aurait
    aucun effet avant le redémarrage du conteneur."""
    built = 0

    def fake_build(*args, **kwargs):
        nonlocal built
        built += 1
        return object()

    monkeypatch.setattr(graph, "build_graph", fake_build)
    graph._cached = None

    first = graph.get_graph()
    assert graph.get_graph() is first  # deuxième appel : servi par le cache
    assert built == 1

    await client.patch("/api/settings/agent", json={"max_tool_loops": 7})

    assert graph.get_graph() is not first
    assert built == 2


# --- CRUD MCP -----------------------------------------------------------------


async def test_cycle_de_vie_d_un_serveur_mcp(client: AsyncClient):
    created = await client.post(
        "/api/settings/mcp",
        json={
            "name": "Filesystem",
            "transport": "stdio",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
            "env": {"NODE_ENV": "production"},
        },
    )
    assert created.status_code == 200
    server = created.json()
    assert server["transport"] == "stdio"
    assert server["args"][0] == "-y"
    assert server["env"] == {"NODE_ENV": "production"}
    assert server["enabled"] is True

    listing = await client.get("/api/settings/mcp")
    assert [s["id"] for s in listing.json()] == [server["id"]]
    # Le serveur apparaît aussi dans l'état global.
    assert len((await client.get("/api/settings")).json()["mcp_servers"]) == 1

    patched = await client.patch(
        f"/api/settings/mcp/{server['id']}", json={"enabled": False, "name": "FS local"}
    )
    assert patched.json()["enabled"] is False
    assert patched.json()["name"] == "FS local"
    # Patch partiel : le reste est préservé.
    assert patched.json()["command"] == "npx"

    deleted = await client.delete(f"/api/settings/mcp/{server['id']}")
    assert deleted.status_code == 200
    assert (await client.get("/api/settings/mcp")).json() == []


async def test_validation_du_transport_mcp(client: AsyncClient):
    # stdio sans command
    assert (
        await client.post("/api/settings/mcp", json={"name": "x", "transport": "stdio"})
    ).status_code == 422
    # http sans url
    assert (
        await client.post("/api/settings/mcp", json={"name": "x", "transport": "http"})
    ).status_code == 422
    # transport inconnu
    assert (
        await client.post(
            "/api/settings/mcp", json={"name": "x", "transport": "grpc", "url": "http://x"}
        )
    ).status_code == 422

    # Un patch ne doit pas pouvoir produire une ligne incohérente : passer un
    # serveur stdio en http sans fournir d'url doit être refusé.
    created = await client.post(
        "/api/settings/mcp", json={"name": "x", "transport": "stdio", "command": "npx"}
    )
    server_id = created.json()["id"]
    assert (
        await client.patch(f"/api/settings/mcp/{server_id}", json={"transport": "http"})
    ).status_code == 422


async def test_serveur_mcp_inconnu(client: AsyncClient):
    assert (await client.patch("/api/settings/mcp/nope", json={"enabled": True})).status_code == 404
    assert (await client.delete("/api/settings/mcp/nope")).status_code == 404


# --- Dégradation sans base ----------------------------------------------------


async def test_sans_base_la_lecture_sert_les_defauts(client: AsyncClient, monkeypatch):
    """Contrainte forte : le chat et la lecture de la config ne dépendent pas de
    la disponibilité de Postgres. Seules les écritures répondent 503."""
    await client.patch("/api/settings/agent", json={"max_tool_loops": 9})

    async def unavailable() -> bool:
        return False

    monkeypatch.setattr(db, "is_available", unavailable)
    await settings.refresh()

    body = (await client.get("/api/settings")).json()
    assert body["persisted"] is False
    assert body["agent"]["max_tool_loops"] == settings.DEFAULT_MAX_TOOL_LOOPS
    assert body["mcp_servers"] == []
    assert all(tool["enabled"] for tool in body["tools"])

    # Le graphe se construit quand même, avec les défauts.
    model = RecordingModel(responses=[AIMessage(content="ok")])
    assert graph.build_graph(model) is not None

    for method, url, payload in [
        ("patch", "/api/settings/agent", {"temperature": 1.0}),
        ("patch", "/api/settings/model", {"provider": "groq"}),
        ("patch", "/api/settings/tools/calculator", {"enabled": False}),
        ("post", "/api/settings/mcp", {"name": "x", "transport": "http", "url": "http://x"}),
    ]:
        response = await getattr(client, method)(url, json=payload)
        assert response.status_code == 503, url


async def test_l_environnement_fournit_les_defauts(client: AsyncClient, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("LLM_TEMPERATURE", "0.7")
    await settings.refresh()

    body = (await client.get("/api/settings")).json()
    assert body["model"]["provider"] == "ollama"
    assert body["agent"]["temperature"] == 0.7

    # Une valeur d'environnement invalide ne doit pas faire tomber l'API.
    monkeypatch.setenv("LLM_PROVIDER", "n-importe-quoi")
    monkeypatch.setenv("LLM_TEMPERATURE", "chaud")
    await settings.refresh()
    body = (await client.get("/api/settings")).json()
    assert body["model"]["provider"] == "groq"
    assert body["agent"]["temperature"] == 0.0
