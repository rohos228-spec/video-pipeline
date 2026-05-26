"""Тесты сопр. сообщения шага anim_pr (промты анимации)."""

from __future__ import annotations

from app.models import Project
from app.services import gpt_text_builder as gtb


def test_anim_pr_is_supported() -> None:
    assert gtb.is_supported("anim_pr")


def test_anim_pr_default_has_placeholders_and_master(monkeypatch) -> None:
    project = Project(topic="test")
    monkeypatch.setattr(
        "app.services.gpt_text_builder.get_project_prompt",
        lambda _p, _c: "# VIDEO master\n\nAnimate this.",
    )
    text = gtb.build_default_text(project, "anim_pr")
    assert "VIDEO master" in text
    assert "\n\n---\n\n" in text
    assert gtb.ANIM_PLACEHOLDER_N in text
    assert gtb.ANIM_PLACEHOLDER_VOICEOVER in text
    assert "Задача: составь ОДИН промт" in text


def test_render_anim_pr_text_substitutes_frame() -> None:
    template = gtb.build_default_text(Project(), "anim_pr")
    rendered = gtb.render_anim_pr_text(
        template,
        frame_number=3,
        duration_seconds=2.5,
        voiceover_text="Привет мир",
        image_prompt="A cat on a roof",
    )
    assert "{{" not in rendered
    assert "Номер кадра: 3" in rendered
    assert "2.5 сек" in rendered
    assert "Привет мир" in rendered
    assert "A cat on a roof" in rendered


def test_anim_pr_override_preserved() -> None:
    project = Project(topic="t")
    project.gpt_text_overrides = {"anim_pr": "Кадр {{N}}: {{VOICEOVER}}"}
    assert gtb.get_effective_text(project, "anim_pr") == "Кадр {{N}}: {{VOICEOVER}}"
    out = gtb.render_anim_pr_text(
        gtb.get_effective_text(project, "anim_pr"),
        frame_number=1,
        duration_seconds=4,
        voiceover_text="VO",
        image_prompt="img",
    )
    assert out == "Кадр 1: VO"
