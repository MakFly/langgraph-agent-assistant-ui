"""API d'historisation des conversations.

Le contrat est imposé par le `RemoteThreadListAdapter` d'assistant-ui côté front :

    GET    /api/threads?scope=            liste
    POST   /api/threads                   crée (id fourni par le client)
    GET    /api/threads/{id}              détail
    PATCH  /api/threads/{id}              titre / statut / custom
    DELETE /api/threads/{id}              suppression (messages en cascade)
    GET    /api/threads/{id}/messages     historique
    POST   /api/threads/{id}/messages     ajout d'un message

Le serveur ne comprend pas le contenu des messages : il stocke le couple
(`format`, `content`) tel que le client l'encode. C'est ce qui évite d'avoir une
seconde représentation de la conversation à maintenir en phase avec celle du front.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from agent.infra import db

router = APIRouter(prefix="/api/threads", tags=["threads"])


class ThreadCreate(BaseModel):
    id: str
    scope: str = "default"


class ThreadPatch(BaseModel):
    title: str | None = None
    status: Literal["regular", "archived"] | None = None
    custom: dict[str, Any] | None = None


class MessageCreate(BaseModel):
    id: str
    parent_id: str | None = None
    format: str
    content: dict[str, Any]


def _thread_json(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "title": row["title"],
        "status": row["status"],
        # ISO 8601 : c'est de la donnée machine, le front la formate à l'affichage.
        "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
        "custom": json.loads(row["custom"]) if row.get("custom") else None,
    }


@router.get("")
async def list_threads(scope: str = Query("default")) -> list[dict[str, Any]]:
    rows = await db.pool().fetch(
        """
        SELECT id, title, status, custom, updated_at
        FROM threads
        WHERE scope = $1
        ORDER BY updated_at DESC
        LIMIT 200
        """,
        scope,
    )
    return [_thread_json(dict(row)) for row in rows]


@router.post("")
async def create_thread(payload: ThreadCreate) -> dict[str, Any]:
    row = await db.pool().fetchrow(
        """
        INSERT INTO threads (id, scope)
        VALUES ($1, $2)
        ON CONFLICT (id) DO UPDATE SET updated_at = now()
        RETURNING id, title, status, custom, updated_at
        """,
        payload.id,
        payload.scope,
    )
    return _thread_json(dict(row))


@router.get("/{thread_id}")
async def get_thread(thread_id: str, scope: str = Query("default")) -> dict[str, Any]:
    row = await db.pool().fetchrow(
        "SELECT id, title, status, custom, updated_at FROM threads WHERE id = $1 AND scope = $2",
        thread_id,
        scope,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Conversation introuvable")
    return _thread_json(dict(row))


@router.patch("/{thread_id}")
async def patch_thread(
    thread_id: str, payload: ThreadPatch, scope: str = Query("default")
) -> dict[str, Any]:
    # COALESCE : un champ absent du corps ne doit pas écraser la valeur en base.
    row = await db.pool().fetchrow(
        """
        UPDATE threads
        SET title      = COALESCE($3, title),
            status     = COALESCE($4, status),
            custom     = COALESCE($5::jsonb, custom),
            updated_at = now()
        WHERE id = $1 AND scope = $2
        RETURNING id, title, status, custom, updated_at
        """,
        thread_id,
        scope,
        payload.title,
        payload.status,
        json.dumps(payload.custom) if payload.custom is not None else None,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Conversation introuvable")
    return _thread_json(dict(row))


@router.delete("/{thread_id}")
async def delete_thread(thread_id: str, scope: str = Query("default")) -> dict[str, str]:
    result = await db.pool().execute(
        "DELETE FROM threads WHERE id = $1 AND scope = $2", thread_id, scope
    )
    if result.endswith("0"):
        raise HTTPException(status_code=404, detail="Conversation introuvable")
    return {"status": "deleted"}


@router.get("/{thread_id}/messages")
async def list_messages(thread_id: str, scope: str = Query("default")) -> list[dict[str, Any]]:
    rows = await db.pool().fetch(
        """
        SELECT m.id, m.parent_id, m.format, m.content
        FROM messages m
        JOIN threads t ON t.id = m.thread_id
        WHERE m.thread_id = $1 AND t.scope = $2
        ORDER BY m.created_at, m.id
        """,
        thread_id,
        scope,
    )
    return [
        {
            "id": row["id"],
            "parent_id": row["parent_id"],
            "format": row["format"],
            "content": json.loads(row["content"]),
        }
        for row in rows
    ]


@router.post("/{thread_id}/messages")
async def add_message(
    thread_id: str, payload: MessageCreate, scope: str = Query("default")
) -> dict[str, str]:
    async with db.pool().acquire() as connection, connection.transaction():
        exists = await connection.fetchval(
            "SELECT 1 FROM threads WHERE id = $1 AND scope = $2", thread_id, scope
        )
        if not exists:
            raise HTTPException(status_code=404, detail="Conversation introuvable")

        # ON CONFLICT : assistant-ui réémet un message quand il est édité ou régénéré.
        await connection.execute(
            """
            INSERT INTO messages (id, thread_id, parent_id, format, content)
            VALUES ($1, $2, $3, $4, $5::jsonb)
            ON CONFLICT (thread_id, id)
            DO UPDATE SET content = EXCLUDED.content, parent_id = EXCLUDED.parent_id
            """,
            payload.id,
            thread_id,
            payload.parent_id,
            payload.format,
            json.dumps(payload.content),
        )
        await connection.execute(
            "UPDATE threads SET updated_at = now() WHERE id = $1", thread_id
        )

    return {"status": "ok"}
