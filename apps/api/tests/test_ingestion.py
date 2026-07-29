from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from langchain_core.messages import AIMessage

from agent.core import ingestion, users
from agent.core.rag import ocr, parse
from agent.infra import db
from agent.main import app

PREFIX = "pytest-ingestion-"
PASSWORD = "mot-de-passe-de-test-1"


@pytest.fixture(autouse=True)
async def database(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("AUTH_SECRET", "secret-de-test-suffisamment-long-pour-hs256")
    monkeypatch.setenv("INGESTION_DATA_DIR", str(tmp_path / "ingestion"))
    try:
        await db.connect()
    except Exception as error:  # pragma: no cover
        pytest.skip(f"Postgres injoignable : {error}")
    await db.pool().execute(
        "DELETE FROM users WHERE email LIKE $1",
        f"{PREFIX}%",
    )
    yield
    await db.pool().execute("DELETE FROM users WHERE email LIKE $1", f"{PREFIX}%")
    await db.pool().execute(
        "DELETE FROM ingestion_sources WHERE name LIKE $1", f"{PREFIX}%"
    )
    await ingestion.stop()
    await db.disconnect()


async def _session(name: str, *, role: str) -> AsyncClient:
    user = await users.create_user(
        f"{PREFIX}{name}@example.com",
        PASSWORD,
        role=role,
        groups=["public"],
    )
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    response = await client.post(
        "/api/auth/login", json={"email": user.email, "password": PASSWORD}
    )
    assert response.status_code == 200
    return client


async def test_sources_reservees_aux_admins():
    member = await _session("member", role="member")
    try:
        assert (await member.get("/api/ingestion")).status_code == 403
    finally:
        await member.aclose()


async def test_source_upload_et_configuration_ocr_dynamique():
    admin = await _session("admin", role="admin")
    try:
        created = await admin.post(
            "/api/ingestion/sources",
            json={
                "name": f"{PREFIX}contrats",
                "groups": ["public", "gestion"],
                "ocr": {
                    "enabled": True,
                    "provider": "openai",
                    "model": "vision-specifique",
                    "max_pages": 12,
                    "dpi": 180,
                },
                "options": {"max_chunks": 500, "prune": True},
            },
        )
        assert created.status_code == 201, created.text
        source = created.json()
        assert source["ocr"]["provider"] == "openai"
        assert source["ocr"]["model"] == "vision-specifique"

        uploaded = await admin.post(
            f"/api/ingestion/sources/{source['id']}/files?group=gestion",
            files={"file": ("contrat.md", b"# Contrat\n\nGarantie valide.", "text/markdown")},
        )
        assert uploaded.status_code == 201, uploaded.text
        files = await admin.get(f"/api/ingestion/sources/{source['id']}/files")
        assert files.json()[0]["group"] == "gestion"
        assert files.json()[0]["name"] == "contrat.md"

        patched = await admin.patch(
            f"/api/ingestion/sources/{source['id']}",
            json={
                "ocr": {
                    **source["ocr"],
                    "provider": "google",
                    "model": "gemini-vision-interne",
                }
            },
        )
        assert patched.status_code == 200, patched.text
        assert patched.json()["ocr"]["provider"] == "google"
        assert patched.json()["ocr"]["model"] == "gemini-vision-interne"
    finally:
        await admin.aclose()


async def test_ocr_instancie_exactement_le_provider_et_le_modele(monkeypatch):
    calls: list[dict] = []

    class FakeModel:
        async def ainvoke(self, messages):
            calls.append({"message": messages[0]})
            return AIMessage(content="Référence ABC-123")

    def fake_create_model(**kwargs):
        calls.append(kwargs)
        return FakeModel()

    monkeypatch.setattr(ocr, "create_model", fake_create_model)
    config = ocr.OcrConfig(
        enabled=True,
        provider="google",
        model="modele-vision-prive",
    )

    result = await ocr.transcribe_image(
        b"image",
        mime_type="image/png",
        config=config,
        page=3,
    )

    assert result == "Référence ABC-123"
    assert calls[0]["provider"] == "google"
    assert calls[0]["model"] == "modele-vision-prive"
    assert calls[0]["temperature"] == 0
    image_block = calls[1]["message"].content[1]
    assert image_block["image_url"]["url"].startswith("data:image/png;base64,")


async def test_pdf_scane_passe_par_ocr(monkeypatch, tmp_path: Path):
    import fitz

    pdf = tmp_path / "scan.pdf"
    with fitz.open() as document:
        document.new_page()
        document.save(pdf)

    seen: list[int] = []

    def fake_render(path: Path, page_index: int, *, dpi: int) -> bytes:
        assert path == pdf
        assert dpi == 144
        seen.append(page_index)
        return b"png"

    async def fake_transcribe(image: bytes, **kwargs) -> str:
        assert image == b"png"
        assert kwargs["page"] == 1
        return "Texte fidèlement extrait"

    monkeypatch.setattr(ocr, "render_pdf_page", fake_render)
    monkeypatch.setattr(ocr, "transcribe_image", fake_transcribe)

    document = await parse.read_document_async(
        pdf,
        {
            "enabled": True,
            "provider": "ollama",
            "model": "modele-vision-local",
            "dpi": 144,
        },
    )
    assert document.text == "Texte fidèlement extrait"
    assert seen == [0]


async def test_run_persistant_termine_avec_son_rapport(monkeypatch):
    admin = await _session("runner", role="admin")

    async def available() -> bool:
        return True

    async def fake_ingest(*args, **kwargs):
        await asyncio.sleep(0)
        return ingestion.ingest.IngestReport(dry_run=kwargs["dry_run"])

    monkeypatch.setattr(ingestion.ragdb, "is_available", available)
    monkeypatch.setattr(ingestion.ingest, "ingest", fake_ingest)
    try:
        source = (
            await admin.post(
                "/api/ingestion/sources",
                json={"name": f"{PREFIX}jobs", "groups": ["public"]},
            )
        ).json()
        response = await admin.post(
            f"/api/ingestion/sources/{source['id']}/runs",
            json={"dry_run": True},
        )
        assert response.status_code == 202, response.text

        for _ in range(20):
            runs = (
                await admin.get(f"/api/ingestion/sources/{source['id']}/runs")
            ).json()
            if runs[0]["status"] == "succeeded":
                break
            await asyncio.sleep(0.01)
        assert runs[0]["status"] == "succeeded"
        assert runs[0]["mode"] == "dry_run"
        assert runs[0]["report"]["dry_run"] is True
    finally:
        await admin.aclose()
