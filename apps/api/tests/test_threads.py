"""API d'historisation.

Les tests tapent la vraie base (`infra-postgres`) via l'app ASGI : c'est du SQL
réel, avec la contrainte de clé étrangère et la cascade. Ils travaillent dans un
scope dédié et nettoient derrière eux.

Depuis le RBAC, ces routes exigent une session : la fixture `client` crée un
compte jetable et se connecte. Le `scope` reste testé ici comme sous-espace d'un
même compte ; l'étanchéité *entre comptes*, elle, est prouvée dans
`test_auth.py` — c'est une autre propriété.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from agent.core import users
from agent.infra import db
from agent.main import app

SCOPE = "pytest"
EMAIL = "pytest-threads@example.com"
PASSWORD = "mot-de-passe-de-test-1"


@pytest.fixture(autouse=True)
async def database(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AUTH_SECRET", "secret-de-test-suffisamment-long-pour-hs256")

    try:
        await db.connect()
    except Exception as error:  # pragma: no cover
        pytest.skip(f"Postgres injoignable : {error}")

    await _cleanup()
    users.reset_throttle()
    yield
    await _cleanup()
    await db.disconnect()


async def _cleanup() -> None:
    await db.pool().execute("DELETE FROM threads WHERE scope = $1", SCOPE)
    # Les conversations du compte jetable partent en cascade.
    await db.pool().execute("DELETE FROM users WHERE email = $1", EMAIL)


@pytest.fixture
async def client():
    await users.create_user(EMAIL, PASSWORD, display_name="pytest")
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as http_client:
        login = await http_client.post(
            "/api/auth/login", json={"email": EMAIL, "password": PASSWORD}
        )
        assert login.status_code == 200, login.text
        yield http_client


async def create_thread(client: AsyncClient, thread_id: str) -> dict:
    response = await client.post(
        "/api/threads", json={"id": thread_id, "scope": SCOPE}
    )
    assert response.status_code == 200
    return response.json()


async def test_cycle_de_vie_d_une_conversation(client: AsyncClient):
    await create_thread(client, "t1")

    renamed = await client.patch(
        f"/api/threads/t1?scope={SCOPE}", json={"title": "Météo Lyon"}
    )
    assert renamed.json()["title"] == "Météo Lyon"

    listing = await client.get(f"/api/threads?scope={SCOPE}")
    assert [t["id"] for t in listing.json()] == ["t1"]

    archived = await client.patch(
        f"/api/threads/t1?scope={SCOPE}", json={"status": "archived"}
    )
    assert archived.json()["status"] == "archived"
    # Un PATCH partiel ne doit pas écraser le titre.
    assert archived.json()["title"] == "Météo Lyon"

    deleted = await client.delete(f"/api/threads/t1?scope={SCOPE}")
    assert deleted.status_code == 200
    assert (await client.get(f"/api/threads?scope={SCOPE}")).json() == []


async def test_les_messages_sont_rendus_dans_l_ordre(client: AsyncClient):
    await create_thread(client, "t2")

    for index, role in enumerate(["user", "assistant", "user"]):
        response = await client.post(
            f"/api/threads/t2/messages?scope={SCOPE}",
            json={
                "id": f"m{index}",
                "parent_id": f"m{index - 1}" if index else None,
                "format": "aui/v0",
                "content": {"role": role},
            },
        )
        assert response.status_code == 200

    messages = (await client.get(f"/api/threads/t2/messages?scope={SCOPE}")).json()
    assert [m["id"] for m in messages] == ["m0", "m1", "m2"]
    assert [m["content"]["role"] for m in messages] == ["user", "assistant", "user"]
    assert messages[1]["parent_id"] == "m0"


async def test_reenvoyer_un_message_le_met_a_jour(client: AsyncClient):
    """assistant-ui réémet un message lors d'une édition ou d'une régénération."""
    await create_thread(client, "t3")
    payload = {
        "id": "m0",
        "parent_id": None,
        "format": "aui/v0",
        "content": {"text": "avant"},
    }
    await client.post(f"/api/threads/t3/messages?scope={SCOPE}", json=payload)
    await client.post(
        f"/api/threads/t3/messages?scope={SCOPE}",
        json={**payload, "content": {"text": "après"}},
    )

    messages = (await client.get(f"/api/threads/t3/messages?scope={SCOPE}")).json()
    assert len(messages) == 1
    assert messages[0]["content"]["text"] == "après"


async def test_suppression_en_cascade(client: AsyncClient):
    await create_thread(client, "t4")
    await client.post(
        f"/api/threads/t4/messages?scope={SCOPE}",
        json={"id": "m0", "parent_id": None, "format": "aui/v0", "content": {}},
    )

    await client.delete(f"/api/threads/t4?scope={SCOPE}")
    assert (await client.get(f"/api/threads/t4/messages?scope={SCOPE}")).json() == []


async def test_les_scopes_sont_etanches(client: AsyncClient):
    await create_thread(client, "t5")
    assert (await client.get("/api/threads?scope=un-autre-scope")).json() == []
    # Et on ne peut pas lire une conversation depuis le mauvais scope.
    assert (await client.get("/api/threads/t5?scope=un-autre-scope")).status_code == 404


async def test_conversation_inconnue(client: AsyncClient):
    assert (await client.get(f"/api/threads/nope?scope={SCOPE}")).status_code == 404
    assert (await client.delete(f"/api/threads/nope?scope={SCOPE}")).status_code == 404
    response = await client.post(
        f"/api/threads/nope/messages?scope={SCOPE}",
        json={"id": "m", "parent_id": None, "format": "aui/v0", "content": {}},
    )
    assert response.status_code == 404
