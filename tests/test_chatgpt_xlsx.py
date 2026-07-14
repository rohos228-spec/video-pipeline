"""Тесты xlsx-flow: промт файлом, текст чата отдельно."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from app.models import Project
from app.services import chatgpt_xlsx as cx
from app.services import gpt_text_builder as gtb


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Project:
    data_root = tmp_path / "data"
    data_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("app.settings.settings.data_dir", str(data_root))
    p = Project(slug="test-proj", topic="Тема теста", hero_mode="auto")
    p.id = 1
    return p


def test_plan_prompt_file_contains_master_not_in_chat(project: Project) -> None:
    master = "MASTER PROMPT BODY unique-token-xyz"
    tmp_dir = cx.tmp_gpt_dir(project)

    with patch(
        "app.services.chatgpt_xlsx.get_project_prompt", return_value=master
    ):
        prompt_file = cx.write_plan_prompt_file(project, tmp_dir)
        chat = cx.chat_message(
            project, "plan", prompt_file_name=prompt_file.name
        )

    content = prompt_file.read_text(encoding="utf-8")
    assert master in content
    assert "Тема теста" in content
    assert master not in chat
    assert "unique-token-xyz" not in chat


def test_chat_message_uses_override_only(project: Project) -> None:
    override = "Только мой текст для GPT без промта"
    project.gpt_text_overrides = {"plan": override}

    with patch(
        "app.services.chatgpt_xlsx.get_project_prompt",
        return_value="SHOULD NOT APPEAR",
    ):
        chat = cx.chat_message(project, "plan", prompt_file_name="p.md")

    assert chat == override
    assert "SHOULD NOT APPEAR" not in chat


def test_img_pr_prompt_file_and_chat_separated(project: Project) -> None:
    master = "IMAGE MASTER unique-img-abc"
    tmp_dir = cx.tmp_gpt_dir(project)

    with patch(
        "app.services.chatgpt_xlsx.get_project_prompt", return_value=master
    ):
        prompt_file = cx.write_img_pr_prompt_file(project, tmp_dir)
        chat = cx.chat_message(
            project,
            "img_pr",
            prompt_file_name=prompt_file.name,
            n_frames=3,
        )

    assert prompt_file.read_text(encoding="utf-8") == master
    assert master not in chat
    assert "unique-img-abc" not in chat


def test_default_accompanying_never_includes_master(project: Project) -> None:
    master = "SECRET MASTER CONTENT"
    with patch(
        "app.services.gpt_text_builder.get_project_prompt", return_value=master
    ):
        default = gtb.build_default_text(
            project, "script", prompt_file_name="prompt.txt"
        )
    assert master not in default
    assert "prompt.txt" in default


@pytest.mark.asyncio
async def test_sync_project_xlsx_raises_when_both_imports_fail(
    project: Project, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openpyxl import Workbook

    from app.services.xlsx_v8_import import SHEET_GENERAL_V8, SHEET_PLAN_V8

    xlsx = tmp_path / "bad.xlsx"
    wb = Workbook()
    wb.active.title = SHEET_PLAN_V8
    wb.create_sheet(SHEET_GENERAL_V8)
    wb.save(xlsx)

    async def _fail_v8(*_a: object, **_k: object) -> dict:
        raise RuntimeError("v8 boom")

    async def _fail_v7(*_a: object, **_k: object) -> dict:
        raise RuntimeError("v7 boom")

    marked: list[str] = []

    async def _fake_mark(
        _session: object, _project: Project, error: str, **_: object
    ) -> None:
        marked.append(error)

    monkeypatch.setattr(cx, "import_v8_xlsx", _fail_v8)
    monkeypatch.setattr(cx, "reload_from_xlsx", _fail_v7)
    monkeypatch.setattr(
        "app.services.run_sync.mark_running_node_failed", _fake_mark
    )

    session = object()
    with pytest.raises(RuntimeError, match="xlsx-sync"):
        await cx.sync_project_xlsx(session, project, xlsx)  # type: ignore[arg-type]

    assert marked
    assert "v8" in marked[0] or "v7" in marked[0]
