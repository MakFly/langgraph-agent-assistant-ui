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

**Isolation.** Chaque requête filtre sur `owner_id`, pris dans le jeton et jamais
dans l'URL. Le paramètre `scope` reste au contrat du front, mais il n'est plus
qu'un sous-espace *à l'intérieur* d'un compte : avant l'authentification, il
suffisait de passer le `?scope=` d'autrui pour lire ses conversations.

Un piège moins visible : l'identifiant de conversation est **choisi par le
client**. Un `ON CONFLICT (id) DO UPDATE` non filtré permettrait donc à B de
toucher — et de se faire renvoyer — la conversation de A rien qu'en devinant son
identifiant. D'où le `WHERE threads.owner_id = ...` sur la branche de conflit.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from agent.api.auth import current_user
from agent.core.users import User
from agent.infra import db

router = APIRouter(prefix="/api/threads", tags=["threads"])

_NOT_FOUND = HTTPException(status_code=404, detail="Conversation introuvable")


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
async def list_threads(
    scope: str = Query("default"), user: User = Depends(current_user)
) -> list[dict[str, Any]]:
    rows = await db.pool().fetch(
        """
        SELECT id, title, status, custom, updated_at
        FROM threads
        WHERE owner_id = $1 AND scope = $2
        ORDER BY updated_at DESC
        LIMIT 200
        """,
        user.id,
        scope,
    )
    return [_thread_json(dict(row)) for row in rows]


@router.post("")
async def create_thread(
    payload: ThreadCreate, user: User = Depends(current_user)
) -> dict[str, Any]:
    row = await db.pool().fetchrow(
        """
        INSERT INTO threads (id, scope, owner_id)
        VALUES ($1, $2, $3)
        ON CONFLICT (id) DO UPDATE SET updated_at = now()
        WHERE threads.owner_id = $3
        RETURNING id, title, status, custom, updated_at
        """,
        payload.id,
        payload.scope,
        user.id,
    )
    if row is None:
        # Conflit sur un identifiant qui appartient à quelqu'un d'autre. Le 409 ne
        # dit pas à qui : il dit seulement de réessayer avec un autre identifiant.
        raise HTTPException(status_code=409, detail="Identifiant de conversation déjà utilisé")
    return _thread_json(dict(row))


@router.get("/{thread_id}")
async def get_thread(
    thread_id: str, scope: str = Query("default"), user: User = Depends(current_user)
) -> dict[str, Any]:
    row = await db.pool().fetchrow(
        """
        SELECT id, title, status, custom, updated_at
        FROM threads
        WHERE id = $1 AND scope = $2 AND owner_id = $3
        """,
        thread_id,
        scope,
        user.id,
    )
    if row is None:
        raise _NOT_FOUND
    return _thread_json(dict(row))


@router.patch("/{thread_id}")
async def patch_thread(
    thread_id: str,
    payload: ThreadPatch,
    scope: str = Query("default"),
    user: User = Depends(current_user),
) -> dict[str, Any]:
    # COALESCE : un champ absent du corps ne doit pas écraser la valeur en base.
    row = await db.pool().fetchrow(
        """
        UPDATE threads
        SET title      = COALESCE($4, title),
            status     = COALESCE($5, status),
            custom     = COALESCE($6::jsonb, custom),
            updated_at = now()
        WHERE id = $1 AND scope = $2 AND owner_id = $3
        RETURNING id, title, status, custom, updated_at
        """,
        thread_id,
        scope,
        user.id,
        payload.title,
        payload.status,
        json.dumps(payload.custom) if payload.custom is not None else None,
    )
    if row is None:
        raise _NOT_FOUND
    return _thread_json(dict(row))


@router.delete("/{thread_id}")
async def delete_thread(
    thread_id: str, scope: str = Query("default"), user: User = Depends(current_user)
) -> dict[str, str]:
    result = await db.pool().execute(
        "DELETE FROM threads WHERE id = $1 AND scope = $2 AND owner_id = $3",
        thread_id,
        scope,
        user.id,
    )
    if result.endswith("0"):
        raise _NOT_FOUND
    return {"status": "deleted"}


@router.get("/{thread_id}/messages")
async def list_messages(
    thread_id: str, scope: str = Query("default"), user: User = Depends(current_user)
) -> list[dict[str, Any]]:
    rows = await db.pool().fetch(
        """
        SELECT m.id, m.parent_id, m.format, m.content
        FROM messages m
        JOIN threads t ON t.id = m.thread_id
        WHERE m.thread_id = $1 AND t.scope = $2 AND t.owner_id = $3
        ORDER BY m.created_at, m.id
        """,
        thread_id,
        scope,
        user.id,
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
    thread_id: str,
    payload: MessageCreate,
    scope: str = Query("default"),
    user: User = Depends(current_user),
) -> dict[str, str]:
    async with db.pool().acquire() as connection, connection.transaction():
        exists = await connection.fetchval(
            "SELECT 1 FROM threads WHERE id = $1 AND scope = $2 AND owner_id = $3",
            thread_id,
            scope,
            user.id,
        )
        if not exists:
            raise _NOT_FOUND

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
