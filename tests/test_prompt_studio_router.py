"""Тесты REST-эндпоинтов /api/prompt-studio/step-template/{step_id} —
блочный редактор шаблонов шагов (Studio UI, GET/PUT карточек 1..N)."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.services import prompt_composer as pc
from app.web.api import create_app

from tests.conftest import patch_prompt_roots

app = create_app()


@pytest.fixture
def step_templates_dir(tmp_path, monkeypatch):
    bundled, user = patch_prompt_roots(monkeypatch, tmp_path, folders=())
    steps_root = bundled / "steps"
    step_dir = steps_root / "99_test"
    step_dir.mkdir(parents=True)
    (step_dir / "template.md").write_text(
        "# Шаг 99 — Тест\n\n"
        "## 1. ТЕХНИЧЕСКАЯ ЧАСТЬ\n\nоткуда читаю / куда пишу / внимание\n\n"
        "## 2. РОЛЬ\n\nроль\n\n"
        "## 3. ТЕМА\n\nтема\n\n"
        "## 4. ЗАПРЕТЫ\n\nзапреты\n\n"
        "## 5. ФОРМАТ\n\nформат\n",
        encoding="utf-8",
    )
    return "99_test"


@pytest.mark.asyncio
async def test_get_step_template_returns_parsed_blocks(step_templates_dir) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(f"/api/prompt-studio/step-template/{step_templates_dir}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["step_id"] == step_templates_dir
    assert len(data["blocks"]) == 5
    assert data["blocks"][0]["title"] == "ТЕХНИЧЕСКАЯ ЧАСТЬ"


@pytest.mark.asyncio
async def test_get_step_template_404_for_unknown_step() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/prompt-studio/step-template/no_such_step")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_put_step_template_saves_edited_blocks(step_templates_dir) -> None:
    payload = {
        "blocks": [
            {"number": 1, "title": "ТЕХНИЧЕСКАЯ ЧАСТЬ", "body": "новый техтекст"},
            {"number": 2, "title": "РОЛЬ", "body": "новая роль"},
            {"number": 3, "title": "ТЕМА", "body": "тема"},
            {"number": 4, "title": "ЗАПРЕТЫ", "body": "запреты"},
            {"number": 5, "title": "ФОРМАТ", "body": "формат"},
        ]
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.put(
            f"/api/prompt-studio/step-template/{step_templates_dir}", json=payload
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["blocks"][1]["body"] == "новая роль"
    # Реально записалось на диск.
    assert pc.parse_step_template_blocks(step_templates_dir)[0]["body"] == "новый техтекст"


@pytest.mark.asyncio
async def test_put_step_template_rejects_too_few_blocks(step_templates_dir) -> None:
    payload = {
        "blocks": [
            {"number": 1, "title": "ТЕХНИЧЕСКАЯ ЧАСТЬ", "body": "x"},
            {"number": 2, "title": "РОЛЬ", "body": "y"},
        ]
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.put(
            f"/api/prompt-studio/step-template/{step_templates_dir}", json=payload
        )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_put_step_template_rejects_non_technical_first_block(step_templates_dir) -> None:
    payload = {
        "blocks": [
            {"number": 1, "title": "РОЛЬ", "body": "x"},
            {"number": 2, "title": "ТЕМА", "body": "y"},
            {"number": 3, "title": "СТИЛЬ", "body": "z"},
            {"number": 4, "title": "ЗАПРЕТЫ", "body": "w"},
            {"number": 5, "title": "ФОРМАТ", "body": "v"},
        ]
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.put(
            f"/api/prompt-studio/step-template/{step_templates_dir}", json=payload
        )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_put_step_template_rejects_bad_numbering(step_templates_dir) -> None:
    payload = {
        "blocks": [
            {"number": 1, "title": "ТЕХНИЧЕСКАЯ ЧАСТЬ", "body": "x"},
            {"number": 3, "title": "РОЛЬ", "body": "y"},
            {"number": 4, "title": "ТЕМА", "body": "z"},
            {"number": 5, "title": "ЗАПРЕТЫ", "body": "w"},
            {"number": 6, "title": "ФОРМАТ", "body": "v"},
        ]
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.put(
            f"/api/prompt-studio/step-template/{step_templates_dir}", json=payload
        )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_put_step_template_unknown_step_404(step_templates_dir) -> None:
    payload = {
        "blocks": [
            {"number": 1, "title": "ТЕХНИЧЕСКАЯ ЧАСТЬ", "body": "x"},
            {"number": 2, "title": "РОЛЬ", "body": "y"},
            {"number": 3, "title": "ТЕМА", "body": "z"},
            {"number": 4, "title": "ЗАПРЕТЫ", "body": "w"},
            {"number": 5, "title": "ФОРМАТ", "body": "v"},
        ]
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.put("/api/prompt-studio/step-template/no_such_step", json=payload)
    assert resp.status_code == 404
