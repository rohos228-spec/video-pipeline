"""Тесты xlsx-flow: промт файлом, сопр. текст в композер (как в боте)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.bots.chatgpt import ChatGPTBot
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
async def test_ask_with_prompt_files_delegates_to_bot_flow(tmp_path: Path) -> None:
    prompt_file = tmp_path / "prompt_plan.md"
    prompt_file.write_text("MASTER", encoding="utf-8")
    xlsx = tmp_path / "project.xlsx"
    xlsx.write_bytes(b"xlsx")

    gpt = AsyncMock(spec=ChatGPTBot)
    gpt.new_conversation = AsyncMock()
    gpt.ask_with_files = AsyncMock(return_value="ok")

    reply = await cx.ask_with_prompt_files(
        gpt,
        "Сопр. сообщение",
        [prompt_file, xlsx],
        step_code="plan",
    )

    assert reply == "ok"
    gpt.new_conversation.assert_awaited_once()
    gpt.ask_with_files.assert_awaited_once_with(
        "Сопр. сообщение",
        [prompt_file, xlsx],
        timeout=900,
        project_id=None,
    )
