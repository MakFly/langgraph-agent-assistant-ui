"""Surface HTTP de la configuration : router `/api/settings`.

Séparé de `agent.core.settings` volontairement. Ici vivent **les seules choses qui
relèvent du web** : les corps de requête, les codes d'erreur et le router. Le domaine,
lui, ne sait pas que FastAPI existe — c'est ce qui rend le serveur remplaçable sans
toucher à l'agent.

Convention conservée : chaque mutation renvoie **l'état complet** (`settings.state()`),
pas seulement le domaine touché. Le front remplace son state par la réponse, il ne peut
donc pas diverger de ce qui est enregistré.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, ValidationError, model_validator

from agent.core import settings
from agent.core.model import EffortLevel, Provider
from agent.core.settings import state
from agent.core.tools import TOOLS
from agent.infra import db

router = APIRouter(prefix="/api/settings", tags=["settings"])

Transport = Literal["stdio", "http", "sse"]


# --- Corps de requête ---------------------------------------------------------


class AgentPatch(BaseModel):
    """Patch partiel. Une chaîne vide sur `system_prompt` remet le défaut :
    en JSON on ne distingue pas « champ absent » de « champ à null » côté client,
    donc on se donne un moyen explicite d'effacer la surcharge."""

    system_prompt: str | None = None
    max_tool_loops: int | None = Field(default=None, ge=1, le=20)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)


class ModelPatch(BaseModel):
    provider: Provider | None = None
    model: str | None = None
    reasoning_effort: EffortLevel | None = None


class ToolPatch(BaseModel):
    enabled: bool


class McpServerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    transport: Transport
    url: str | None = None
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True

    @model_validator(mode="after")
    def _check_transport(self) -> McpServerCreate:
        """stdio se lance, http/sse se joignent : les champs requis diffèrent."""
        if self.transport == "stdio":
            if not self.command:
                raise ValueError("`command` est requis pour le transport stdio")
        elif not self.url:
            raise ValueError("`url` est requis pour les transports http et sse")
        return self


class McpServerPatch(BaseModel):
    """Patch partiel : fusionné avec la ligne existante, puis revalidé en entier
    par `McpServerCreate` — sinon on pourrait passer un serveur stdio en http
    sans url et casser la cohérence."""

    name: str | None = Field(default=None, min_length=1, max_length=120)
    transport: Transport | None = None
    url: str | None = None
    command: str | None = None
    args: list[str] | None = None
    env: dict[str, str] | None = None
    enabled: bool | None = None



async def _require_db() -> None:
    """Les lectures dégradent silencieusement, pas les écritures : mieux vaut un
    503 explicite qu'un réglage accepté puis perdu au redémarrage.

    C'est une décision de la couche HTTP, d'où sa place ici et non dans le domaine.
    """
    if not await db.is_available():
        raise HTTPException(
            status_code=503,
            detail="Base indisponible : la configuration ne peut pas être enregistrée",
        )


# --- Endpoints ----------------------------------------------------------------


@router.get("")
async def get_settings() -> dict[str, Any]:
    return await state()


@router.patch("/agent")
async def patch_agent(payload: AgentPatch) -> dict[str, Any]:
    await _require_db()
    updates = payload.model_dump(exclude_unset=True)
    # Chaîne vide = « efface la surcharge », cf. docstring d'AgentPatch.
    if updates.get("system_prompt") == "":
        updates["system_prompt"] = None
    merged = settings.current().agent.model_copy(update=updates)
    await settings.save("agent", merged.model_dump())
    await settings.refresh()
    return await state()


@router.patch("/model")
async def patch_model(payload: ModelPatch) -> dict[str, Any]:
    await _require_db()
    updates = payload.model_dump(exclude_unset=True)
    if updates.get("model") == "":
        updates["model"] = None
    merged = settings.current().model.model_copy(update=updates)
    await settings.save("model", merged.model_dump())
    await settings.refresh()
    return await state()


@router.patch("/tools/{name}")
async def patch_tool(name: str, payload: ToolPatch) -> dict[str, Any]:
    await _require_db()
    if name not in {tool.name for tool in TOOLS}:
        raise HTTPException(status_code=404, detail="Outil inconnu")
    await settings.save("tools", {**settings.current().tools, name: payload.enabled})
    await settings.refresh()
    return await state()


@router.get("/mcp")
async def list_mcp_servers() -> list[dict[str, Any]]:
    return await settings.list_mcp()


@router.post("/mcp")
async def create_mcp_server(payload: McpServerCreate) -> dict[str, Any]:
    await _require_db()
    row = await db.pool().fetchrow(
        """
        INSERT INTO mcp_servers (id, name, transport, url, command, args, env, enabled)
        VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7::jsonb, $8)
        RETURNING id, name, transport, url, command, args, env, enabled
        """,
        uuid.uuid4().hex,
        payload.name,
        payload.transport,
        payload.url,
        payload.command,
        json.dumps(payload.args),
        json.dumps(payload.env),
        payload.enabled,
    )
    # Comme les autres mutations : republier le snapshot. C'est ce `refresh()` qui
    # découvre les outils du serveur et fait reconstruire le graphe — sans lui, un
    # serveur ajouté restait inerte jusqu'au redémarrage.
    await settings.refresh()
    return settings.mcp_json(dict(row))


@router.patch("/mcp/{server_id}")
async def patch_mcp_server(server_id: str, payload: McpServerPatch) -> dict[str, Any]:
    await _require_db()
    existing = await db.pool().fetchrow(
        """
        SELECT id, name, transport, url, command, args, env, enabled
        FROM mcp_servers WHERE id = $1
        """,
        server_id,
    )
    if existing is None:
        raise HTTPException(status_code=404, detail="Serveur MCP introuvable")

    # Fusion puis revalidation complète : un patch ne doit pas pouvoir produire
    # une ligne incohérente (transport http sans url, par exemple).
    merged = {**settings.mcp_json(dict(existing)), **payload.model_dump(exclude_unset=True)}
    merged.pop("id", None)
    try:
        server = McpServerCreate(**merged)
    except ValidationError as error:
        # `include_context=False` : le contexte d'un model_validator contient la
        # ValueError d'origine, que FastAPI ne sait pas sérialiser en JSON.
        detail = error.errors(include_url=False, include_context=False, include_input=False)
        raise HTTPException(status_code=422, detail=detail) from error

    row = await db.pool().fetchrow(
        """
        UPDATE mcp_servers
        SET name = $2, transport = $3, url = $4, command = $5,
            args = $6::jsonb, env = $7::jsonb, enabled = $8, updated_at = now()
        WHERE id = $1
        RETURNING id, name, transport, url, command, args, env, enabled
        """,
        server_id,
        server.name,
        server.transport,
        server.url,
        server.command,
        json.dumps(server.args),
        json.dumps(server.env),
        server.enabled,
    )
    # Activer/désactiver un serveur ou changer son URL doit rebrancher les outils.
    await settings.refresh()
    return settings.mcp_json(dict(row))


@router.delete("/mcp/{server_id}")
async def delete_mcp_server(server_id: str) -> dict[str, str]:
    await _require_db()
    result = await db.pool().execute("DELETE FROM mcp_servers WHERE id = $1", server_id)
    if result.endswith("0"):
        raise HTTPException(status_code=404, detail="Serveur MCP introuvable")
    # Les outils du serveur supprimé doivent disparaître du graphe, pas seulement de la
    # liste : sans ce `refresh()`, le modèle pourrait encore les appeler.
    await settings.refresh()
    return {"status": "deleted"}
