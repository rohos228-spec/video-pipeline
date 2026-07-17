"""Регрессия: UI list API не должен скрывать bundled-промты при пустом data/prompts/."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.services.prompt_library import list_prompts, prompt_path, write_prompt
from app.web.api import create_app
from tests.conftest import patch_prompt_roots


@pytest.fixture
def prompt_dirs(tmp_path, monkeypatch):
    return patch_prompt_roots(
        monkeypatch,
        tmp_path,
        folders=("01_plan", "02_script", "05_excel_gpt", "05a_enrich_1"),
    )


@pytest.mark.asyncio
async def test_list_prompt_files_shows_bundled_when_user_empty(prompt_dirs) -> None:
    bundled, user = prompt_dirs
    (bundled / "01_plan" / "default.md").write_text("bundled default", encoding="utf-8")
    (bundled / "01_plan" / "stock_v2.md").write_text("stock", encoding="utf-8")
    assert not any((user / "01_plan").glob("*.md"))

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/api/prompt-files/plan")
    assert r.status_code == 200
    names = {row["name"] for row in r.json()}
    assert "default" in names
    assert "stock_v2" in names


@pytest.mark.asyncio
async def test_list_prompt_files_user_overlay_wins(prompt_dirs) -> None:
    bundled, user = prompt_dirs
    (bundled / "01_plan" / "default.md").write_text("bundled", encoding="utf-8")
    write_prompt("plan", "custom_only", "user text")

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/api/prompt-files/plan")
    assert r.status_code == 200
    names = {row["name"] for row in r.json()}
    assert "default" in names
    assert "custom_only" in names
    # size from resolved path (user for custom)
    custom = next(row for row in r.json() if row["name"] == "custom_only")
    assert custom["size"] == len("user text".encode("utf-8"))


def test_prompt_path_excel_gpt_resolves_legacy_enrich(prompt_dirs) -> None:
    bundled, _user = prompt_dirs
    (bundled / "05a_enrich_1" / "legacy_slot.md").write_text("legacy", encoding="utf-8")
    names = list_prompts("excel_gpt")
    assert "legacy_slot" in names
    p = prompt_path("excel_gpt", "legacy_slot")
    assert p.is_file()
    assert p.read_text(encoding="utf-8") == "legacy"


def test_delete_prompt_does_not_unlink_bundled(prompt_dirs) -> None:
    from app.services.prompt_library import delete_prompt

    bundled, user = prompt_dirs
    (bundled / "01_plan" / "keep_me.md").write_text("bundled only", encoding="utf-8")
    assert delete_prompt("plan", "keep_me") is False
    assert (bundled / "01_plan" / "keep_me.md").is_file()
    write_prompt("plan", "user_del", "x")
    assert (user / "01_plan" / "user_del.md").is_file()
    assert delete_prompt("plan", "user_del") is True
    assert not (user / "01_plan" / "user_del.md").exists()
