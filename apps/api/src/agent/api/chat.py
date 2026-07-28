"""Les deux endpoints du chat : `/api/chat` (SSE) et `/api/health`.

Sortis de `main.py`, qui n'assemble plus que l'application. Toute la logique de stream
vit dans `agent.protocol.stream`, pour que les tests exercent exactement ce chemin sans
passer par HTTP.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from agent.api.auth import current_user, optional_user
from agent.core import settings
from agent.core.users import User
from agent.infra import db
from agent.protocol.stream import SSE_HEADERS, ui_message_stream

router = APIRouter(tags=["chat"])


class ChatRequest(BaseModel):
    # Le client envoie aussi id / trigger / metadata / tools : on les ignore.
    messages: list[dict] = []


@router.get("/api/health")
async def health(user: User | None = Depends(optional_user)) -> dict:
    """Sonde publique — le healthcheck de docker-compose l'appelle sans jeton.

    D'où le détail à deux niveaux : anonyme, la réponse dit seulement que le
    service répond ; authentifié, elle porte la configuration effective dont la
    sidebar a besoin. Publier le provider et la liste des outils à un visiteur
    non authentifié renseignerait gratuitement sur la stack.
    """
    base = {"status": "ok", "authenticated": user is not None}
    if user is None:
        return base

    # `provider` et `tools` reflètent la configuration effective, pas seulement
    # l'environnement : la sidebar affiche ainsi les outils réellement actifs.
    return {
        **base,
        "provider": settings.current().model.provider,
        "tools": [tool.name for tool in settings.enabled_tools()],
        "history": await db.is_available(),
    }


@router.post("/api/chat")
async def chat(request: ChatRequest, user: User = Depends(current_user)):
    """Le seul endpoint qui compte."""
    if not request.messages:
        return JSONResponse(
            {"error": "Le corps de la requête doit contenir un tableau `messages`."},
            status_code=400,
        )

    # L'utilisateur descend jusqu'au graphe : c'est lui qui porte les groupes dont
    # le RAG a besoin pour filtrer ce qu'il a le droit de retourner.
    return StreamingResponse(
        ui_message_stream(request.messages, user=user),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )
