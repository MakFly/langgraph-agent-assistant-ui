"""Sources documentaires administrées et exécutions d'ingestion persistantes."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from agent.core.rag import ingest, ocr, parse
from agent.infra import db, ragdb

logger = logging.getLogger("agent.ingestion")

_GROUP = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_tasks: set[asyncio.Task] = set()


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        value = json.loads(value)
    return dict(value or {})


def data_dir() -> Path:
    return Path(os.getenv("INGESTION_DATA_DIR", "/data/ingestion")).resolve()


def max_file_bytes() -> int:
    try:
        return max(1, int(os.getenv("INGESTION_MAX_FILE_BYTES", "52428800")))
    except ValueError:
        return 52_428_800


def validate_groups(groups: list[str]) -> list[str]:
    normalized = list(dict.fromkeys(group.strip().lower() for group in groups if group.strip()))
    if not normalized:
        raise ValueError("Au moins un groupe d'accès est requis")
    invalid = [group for group in normalized if not _GROUP.fullmatch(group)]
    if invalid:
        raise ValueError(
            "Groupe invalide : utilisez uniquement lettres minuscules, chiffres, _ ou -"
        )
    return normalized


def validate_filename(filename: str) -> str:
    safe = Path(filename).name.strip()
    if (
        not safe
        or safe in {".", ".."}
        or safe.startswith(".")
        or safe != filename.strip()
        or any(ord(char) < 32 for char in safe)
    ):
        raise ValueError("Nom de fichier invalide")
    if Path(safe).suffix.lower() not in parse.SUPPORTED_SUFFIXES:
        allowed = ", ".join(sorted(parse.SUPPORTED_SUFFIXES))
        raise ValueError(f"Format non pris en charge. Formats acceptés : {allowed}")
    return safe


def source_root(source_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", source_id):
        raise ValueError("Identifiant de source invalide")
    root = (data_dir() / source_id / "corpus").resolve()
    if data_dir() not in root.parents:
        raise ValueError("Chemin de source invalide")
    return root


def file_path(source_id: str, group: str, filename: str) -> Path:
    group = validate_groups([group])[0]
    safe = validate_filename(filename)
    root = source_root(source_id)
    target = (root / group / safe).resolve()
    if root not in target.parents:
        raise ValueError("Chemin de fichier invalide")
    return target


def _source_json(row) -> dict[str, Any]:
    value = dict(row)
    value["ocr"] = _json_object(value.get("ocr"))
    value["options"] = _json_object(value.get("options"))
    for key in ("created_at", "updated_at"):
        if value.get(key):
            value[key] = value[key].isoformat()
    return value


def _run_json(row) -> dict[str, Any]:
    value = dict(row)
    value["report"] = _json_object(value["report"]) if value.get("report") else None
    for key in ("created_at", "started_at", "finished_at"):
        if value.get(key):
            value[key] = value[key].isoformat()
    return value


async def list_sources() -> list[dict[str, Any]]:
    rows = await db.pool().fetch(
        """
        SELECT id, name, kind, groups, enabled, ocr, options, created_at, updated_at
        FROM ingestion_sources ORDER BY updated_at DESC, name
        """
    )
    return [_source_json(row) for row in rows]


async def get_source(source_id: str) -> dict[str, Any] | None:
    row = await db.pool().fetchrow(
        """
        SELECT id, name, kind, groups, enabled, ocr, options, created_at, updated_at
        FROM ingestion_sources WHERE id = $1
        """,
        source_id,
    )
    return _source_json(row) if row else None


async def create_source(
    *,
    name: str,
    groups: list[str],
    enabled: bool,
    ocr_config: dict[str, Any],
    options: dict[str, Any],
) -> dict[str, Any]:
    source_id = uuid.uuid4().hex
    validated_ocr = ocr.OcrConfig.from_mapping(ocr_config).json()
    groups = validate_groups(groups)
    root = source_root(source_id)
    root.mkdir(parents=True, exist_ok=False)
    try:
        row = await db.pool().fetchrow(
            """
            INSERT INTO ingestion_sources (id, name, groups, enabled, ocr, options)
            VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb)
            RETURNING id, name, kind, groups, enabled, ocr, options, created_at, updated_at
            """,
            source_id,
            name.strip(),
            groups,
            enabled,
            json.dumps(validated_ocr, ensure_ascii=False),
            json.dumps(options, ensure_ascii=False),
        )
    except Exception:
        shutil.rmtree(root.parent, ignore_errors=True)
        raise
    return _source_json(row)


async def update_source(source_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
    existing = await get_source(source_id)
    if existing is None:
        return None
    merged = {
        "name": updates.get("name", existing["name"]),
        "groups": validate_groups(updates.get("groups", existing["groups"])),
        "enabled": updates.get("enabled", existing["enabled"]),
        "ocr": ocr.OcrConfig.from_mapping(updates.get("ocr", existing["ocr"])).json(),
        "options": {**existing["options"], **updates.get("options", {})},
    }
    file_groups = {item["group"] for item in list_files(source_id)}
    missing_groups = sorted(file_groups - set(merged["groups"]))
    if missing_groups:
        raise ValueError(
            "Impossible de retirer un groupe qui contient encore des fichiers : "
            + ", ".join(missing_groups)
        )
    row = await db.pool().fetchrow(
        """
        UPDATE ingestion_sources
        SET name = $2, groups = $3, enabled = $4, ocr = $5::jsonb,
            options = $6::jsonb, updated_at = now()
        WHERE id = $1
        RETURNING id, name, kind, groups, enabled, ocr, options, created_at, updated_at
        """,
        source_id,
        merged["name"].strip(),
        merged["groups"],
        merged["enabled"],
        json.dumps(merged["ocr"], ensure_ascii=False),
        json.dumps(merged["options"], ensure_ascii=False),
    )
    return _source_json(row) if row else None


async def delete_source(source_id: str) -> bool:
    existing = await get_source(source_id)
    if existing is None:
        return False
    root = source_root(source_id)
    if await ragdb.is_available():
        await ragdb.pool().execute("DELETE FROM rag_documents WHERE root = $1", str(root))
    deleted = await db.pool().fetchval(
        "DELETE FROM ingestion_sources WHERE id = $1 RETURNING id", source_id
    )
    if deleted:
        shutil.rmtree(root.parent, ignore_errors=True)
    return bool(deleted)


def list_files(source_id: str) -> list[dict[str, Any]]:
    root = source_root(source_id)
    if not root.exists():
        return []
    return [
        {
            "name": path.name,
            "group": path.relative_to(root).parts[0],
            "size": path.stat().st_size,
            "updated_at": path.stat().st_mtime,
        }
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.suffix.lower() in parse.SUPPORTED_SUFFIXES
    ]


async def list_runs(source_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
    rows = await db.pool().fetch(
        """
        SELECT id, source_id, mode, status, report, error,
               created_at, started_at, finished_at
        FROM ingestion_runs WHERE source_id = $1
        ORDER BY created_at DESC LIMIT $2
        """,
        source_id,
        limit,
    )
    return [_run_json(row) for row in rows]


def _report_json(report: ingest.IngestReport) -> dict[str, Any]:
    return {
        "dry_run": report.dry_run,
        "indexed": report.indexed,
        "unchanged": report.unchanged,
        "removed": report.removed,
        "failed": report.failed,
        "chunks": report.chunks,
        "tokens": report.tokens,
        "documents": [asdict(document) for document in report.documents],
    }


def _schedule(run_id: str) -> None:
    task = asyncio.create_task(_execute_run(run_id), name=f"ingestion:{run_id}")
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def create_run(source_id: str, *, dry_run: bool) -> dict[str, Any]:
    run_id = uuid.uuid4().hex
    row = await db.pool().fetchrow(
        """
        INSERT INTO ingestion_runs (id, source_id, mode)
        VALUES ($1, $2, $3)
        RETURNING id, source_id, mode, status, report, error,
                  created_at, started_at, finished_at
        """,
        run_id,
        source_id,
        "dry_run" if dry_run else "sync",
    )
    _schedule(run_id)
    return _run_json(row)


async def _execute_run(run_id: str) -> None:
    # Claim atomique : plusieurs workers peuvent voir un job repris, un seul
    # l'exécutera réellement.
    claimed = await db.pool().fetchrow(
        """
        UPDATE ingestion_runs SET status = 'running', started_at = now(), error = NULL
        WHERE id = $1 AND status = 'queued'
        RETURNING source_id, mode
        """,
        run_id,
    )
    if claimed is None:
        return

    try:
        source = await get_source(claimed["source_id"])
        if source is None:
            raise RuntimeError("Source supprimée")
        if not source["enabled"]:
            raise RuntimeError("Source désactivée")
        if not await ragdb.is_available():
            raise RuntimeError("Index RAG indisponible")
        options = source["options"]
        report = await ingest.ingest(
            source_root(source["id"]),
            dry_run=claimed["mode"] == "dry_run",
            max_chunks=int(options.get("max_chunks", ingest.max_chunks_per_run())),
            prune=bool(options.get("prune", True)),
            force=bool(options.get("force_prune", False)),
            ocr_config=source["ocr"],
        )
        await db.pool().execute(
            """
            UPDATE ingestion_runs
            SET status = 'succeeded', report = $2::jsonb, finished_at = now()
            WHERE id = $1
            """,
            run_id,
            json.dumps(_report_json(report), ensure_ascii=False),
        )
    except Exception as error:  # noqa: BLE001 - le job doit toujours se fermer
        logger.exception("ingestion échouée", extra={"run_id": run_id})
        await db.pool().execute(
            """
            UPDATE ingestion_runs
            SET status = 'failed', error = $2, finished_at = now()
            WHERE id = $1
            """,
            run_id,
            str(error)[:4000],
        )


async def resume_pending() -> None:
    """Replace les jobs interrompus en file, puis les relance."""
    if not await db.is_available():
        return
    await db.pool().execute(
        """
        UPDATE ingestion_runs
        SET status = 'queued', started_at = NULL,
            error = 'Traitement repris après un redémarrage'
        WHERE status = 'running'
        """
    )
    rows = await db.pool().fetch("SELECT id FROM ingestion_runs WHERE status = 'queued'")
    for row in rows:
        _schedule(row["id"])


async def stop() -> None:
    """Laisse une trace reprenable avant de fermer le pool."""
    tasks = list(_tasks)
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    _tasks.clear()
