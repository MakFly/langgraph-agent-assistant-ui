"""API administrateur des sources documentaires et de leur ingestion."""

from __future__ import annotations

import os
from typing import Annotated, Any

import anyio
import asyncpg
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

from agent.api.auth import require_admin
from agent.core import ingestion
from agent.core.model import DEFAULT_MODELS, PROVIDER_KEYS, PROVIDER_MODELS, has_key
from agent.core.rag import ocr, parse
from agent.infra import db

router = APIRouter(
    prefix="/api/ingestion",
    tags=["ingestion"],
    dependencies=[Depends(require_admin)],
)


class OcrSettings(BaseModel):
    enabled: bool = False
    provider: str = "openai"
    model: str = ""
    prompt: str = Field(default=ocr.DEFAULT_PROMPT, max_length=6000)
    max_pages: int = Field(default=40, ge=1, le=200)
    dpi: int = Field(default=160, ge=72, le=300)


class IngestionOptions(BaseModel):
    max_chunks: int = Field(default=2000, ge=1, le=100_000)
    prune: bool = True
    force_prune: bool = False


class SourceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    groups: list[str] = Field(default_factory=lambda: ["public"], min_length=1, max_length=20)
    enabled: bool = True
    ocr: OcrSettings = Field(default_factory=OcrSettings)
    options: IngestionOptions = Field(default_factory=IngestionOptions)


class SourcePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    groups: list[str] | None = Field(default=None, min_length=1, max_length=20)
    enabled: bool | None = None
    ocr: OcrSettings | None = None
    options: IngestionOptions | None = None


class RunCreate(BaseModel):
    dry_run: bool = False


async def _require_db() -> None:
    if not await db.is_available():
        raise HTTPException(status_code=503, detail="Base de configuration indisponible")


async def _source_or_404(source_id: str) -> dict[str, Any]:
    source = await ingestion.get_source(source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source introuvable")
    return source


def _validation_error(error: Exception) -> HTTPException:
    return HTTPException(status_code=422, detail=str(error))


@router.get("")
async def state() -> dict[str, Any]:
    await _require_db()
    return {
        "sources": await ingestion.list_sources(),
        "providers": [
            {
                "id": provider,
                "models": PROVIDER_MODELS.get(provider, []),
                "default_model": DEFAULT_MODELS.get(provider),
                "requires_key": key is not None,
                "has_key": has_key(provider),
            }
            for provider, key in PROVIDER_KEYS.items()
        ],
        "supported_extensions": sorted(parse.SUPPORTED_SUFFIXES),
        "max_file_bytes": ingestion.max_file_bytes(),
    }


@router.post("/sources", status_code=201)
async def create_source(payload: SourceCreate) -> dict[str, Any]:
    await _require_db()
    try:
        return await ingestion.create_source(
            name=payload.name,
            groups=payload.groups,
            enabled=payload.enabled,
            ocr_config=payload.ocr.model_dump(),
            options=payload.options.model_dump(),
        )
    except (ValueError, ocr.OcrError) as error:
        raise _validation_error(error) from error


@router.patch("/sources/{source_id}")
async def patch_source(source_id: str, payload: SourcePatch) -> dict[str, Any]:
    await _require_db()
    updates = payload.model_dump(exclude_unset=True)
    try:
        source = await ingestion.update_source(source_id, updates)
    except (ValueError, ocr.OcrError) as error:
        raise _validation_error(error) from error
    if source is None:
        raise HTTPException(status_code=404, detail="Source introuvable")
    return source


@router.delete("/sources/{source_id}", status_code=204)
async def delete_source(source_id: str) -> None:
    await _require_db()
    if not await ingestion.delete_source(source_id):
        raise HTTPException(status_code=404, detail="Source introuvable")


@router.get("/sources/{source_id}/files")
async def list_files(source_id: str) -> list[dict[str, Any]]:
    await _require_db()
    await _source_or_404(source_id)
    return ingestion.list_files(source_id)


@router.post("/sources/{source_id}/files", status_code=201)
async def upload_file(
    source_id: str,
    file: Annotated[UploadFile, File()],
    group: Annotated[str, Query()],
) -> dict[str, Any]:
    await _require_db()
    source = await _source_or_404(source_id)
    if group not in source["groups"]:
        raise HTTPException(status_code=422, detail="Groupe absent de cette source")
    try:
        target = ingestion.file_path(source_id, group, file.filename or "")
    except ValueError as error:
        raise _validation_error(error) from error

    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{os.urandom(6).hex()}.upload")
    size = 0
    try:
        async with await anyio.open_file(temporary, "wb") as stream:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > ingestion.max_file_bytes():
                    raise HTTPException(
                        status_code=413,
                        detail=(
                            "Fichier trop volumineux "
                            f"(maximum {ingestion.max_file_bytes()} octets)"
                        ),
                    )
                await stream.write(chunk)
        await anyio.to_thread.run_sync(os.replace, temporary, target)
    finally:
        await file.close()
        if temporary.exists():
            temporary.unlink()

    return {
        "name": target.name,
        "group": group,
        "size": size,
        "updated_at": target.stat().st_mtime,
    }


@router.delete("/sources/{source_id}/files/{filename}", status_code=204)
async def delete_file(source_id: str, filename: str, group: str = Query(...)) -> None:
    await _require_db()
    source = await _source_or_404(source_id)
    if group not in source["groups"]:
        raise HTTPException(status_code=422, detail="Groupe absent de cette source")
    try:
        target = ingestion.file_path(source_id, group, filename)
    except ValueError as error:
        raise _validation_error(error) from error
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Fichier introuvable")
    target.unlink()


@router.get("/sources/{source_id}/runs")
async def list_runs(source_id: str) -> list[dict[str, Any]]:
    await _require_db()
    await _source_or_404(source_id)
    return await ingestion.list_runs(source_id)


@router.post("/sources/{source_id}/runs", status_code=202)
async def create_run(source_id: str, payload: RunCreate) -> dict[str, Any]:
    await _require_db()
    await _source_or_404(source_id)
    try:
        return await ingestion.create_run(source_id, dry_run=payload.dry_run)
    except asyncpg.UniqueViolationError as error:
        raise HTTPException(
            status_code=409, detail="Une ingestion est déjà en cours pour cette source"
        ) from error
