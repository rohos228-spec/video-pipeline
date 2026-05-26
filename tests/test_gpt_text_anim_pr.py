"""Тесты сопр. сообщения и batch-парсера шага anim_pr."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.models import Project
from app.services import animation_prompt_gpt as apg
from app.services import gpt_text_builder as gtb
from app.services.chatgpt_xlsx import write_anim_pr_prompt_file


def test_anim_pr_is_supported() -> None:
    assert gtb.is_supported("anim_pr")


def test_anim_pr_initial_default_no_master_in_chat(monkeypatch) -> None:
    project = Project(topic="test")
    frames = [
        SimpleNamespace(number=1, voiceover_text="Первая фраза"),
    ]
    monkeypatch.setattr(
        "app.services.gpt_text_builder.get_project_prompt",
        lambda _p, _c: "# MASTER\n\nRules.",
    )
    text = gtb.build_anim_pr_initial_default(
        project, frames, prompt_file_name="prompt_anim_pr_test.md"
    )
    assert "MASTER" not in text
    assert "prompt_anim_pr_test.md" in text
    assert "Первая фраза" in text


def test_write_anim_pr_prompt_file(tmp_path: Path, monkeypatch) -> None:
    project = Project(topic="t")
    monkeypatch.setattr(
        "app.services.chatgpt_xlsx._get_master_or_fallback",
        lambda _p, _c, _f: "# Video master\n\nDo animation.",
    )
    f = write_anim_pr_prompt_file(project, tmp_path, ts="test")
    assert f.exists()
    assert "Video master" in f.read_text(encoding="utf-8")


def test_parse_animation_reply_pairs() -> None:
    frames = [
        SimpleNamespace(number=1, voiceover_text="VO1", image_prompt=""),
        SimpleNamespace(number=2, voiceover_text="VO2", image_prompt=""),
    ]
    batch = [
        apg.FrameImageBatchItem(
            frame=frames[0],
            image_path=Path("/tmp/a.png"),
            image_id="[ID: P1-F1-abc12345]",
            voiceover="VO1",
        ),
        apg.FrameImageBatchItem(
            frame=frames[1],
            image_path=Path("/tmp/b.png"),
            image_id="[ID: P1-F2-def67890]",
            voiceover="VO2",
        ),
    ]
    reply = (
        "ID изображения: [ID: P1-F1-abc12345]\n"
        "текст анимации: Slow camera push on the hero.\n\n"
        "ID изображения: [ID: P1-F2-def67890]\n"
        "текст анимации: Gentle wind in the curtains.\n"
    )
    pairs = apg.parse_animation_reply(reply, frames, batch_items=batch)
    assert len(pairs) == 2
    assert pairs[0].frame_number == 1
    assert "Slow camera" in pairs[0].animation_text
