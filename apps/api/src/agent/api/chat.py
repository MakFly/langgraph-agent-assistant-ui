"""Les deux endpoints du chat : `/api/chat` (SSE) et `/api/health`.

Sortis de `main.py`, qui n'assemble plus que l'application. Toute la logique de stream
vit dans `agent.protocol.stream`, pour que les tests exercent exactement ce chemin sans
passer par HTTP.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from agent.core import settings
from agent.infra import db
from agent.protocol.stream import SSE_HEADERS, ui_message_stream

router = APIRouter(tags=["chat"])


class ChatRequest(BaseModel):
    # Le client envoie aussi id / trigger / metadata / tools : on les ignore.
    messages: list[dict] = []


@router.get("/api/health")
async def health() -> dict:
    # `provider` et `tools` reflètent la configuration effective, pas seulement
    # l'environnement : la sidebar affiche ainsi les outils réellement actifs.
    return {
        "status": "ok",
        "provider": settings.current().model.provider,
        "tools": [tool.name for tool in settings.enabled_tools()],
        "history": await db.is_available(),
    }


@router.post("/api/chat")
async def chat(request: ChatRequest):
    """Le seul endpoint qui compte."""
    if not request.messages:
        return JSONResponse(
            {"error": "Le corps de la requête doit contenir un tableau `messages`."},
            status_code=400,
        )

    return StreamingResponse(
        ui_message_stream(request.messages),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )
