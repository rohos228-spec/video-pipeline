"""Локальный fallback anim_pr без GPT."""

from __future__ import annotations

from types import SimpleNamespace

from app.services.animation_prompt_local import (
    build_local_ops_for_missing,
    compose_local_animation_prompt,
)


def test_compose_local_has_no_voice_and_continuous_take() -> None:
    fr = SimpleNamespace(
        number=3,
        image_prompt=(
            "STYLE: Archival Noir. Improve only inside this style, never change medium. "
            "Background (detailed): серые панели переполненного вагона, жёлтая полоса у дверей. "
            "Action: пассажиры входят через открытые двери. "
            "Light: cold fluorescent. "
            "Negative: text, logos."
        ),
        voiceover_text="Утром 20 марта 1995 года токийское метро вошло в час пик.",
        meaning="",
        animation_prompt="",
        uuid="u-3",
    )
    text = compose_local_animation_prompt(fr)  # type: ignore[arg-type]
    assert "NO VOICE" in text
    assert "single continuous take" in text
    assert "--no voice" in text
    assert "вагон" in text.lower()
    assert "пассажир" in text.lower()


def test_build_ops_skips_ready() -> None:
    ready = SimpleNamespace(
        number=1,
        uuid="a",
        animation_prompt="x" * 40,
        image_prompt="scene",
        voiceover_text="",
        meaning="",
    )
    need = SimpleNamespace(
        number=2,
        uuid="b",
        animation_prompt="",
        image_prompt="STYLE: x. Сюжет: дверь вагона.",
        voiceover_text="дверь",
        meaning="",
    )
    ops = build_local_ops_for_missing([ready, need], shot=1)  # type: ignore[list-item]
    assert len(ops) == 1
    assert ops[0]["frame_uuid"] == "b"
    assert "промт_видео" in ops[0]["fields"]
