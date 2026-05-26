"""Парсер и batch-сообщения anim_pr."""

from __future__ import annotations

from app.services.animation_prompt_gpt import _clean_animation_text, build_batch_message
from app.services.animation_prompt_gpt import FrameImageBatchItem
from types import SimpleNamespace
from pathlib import Path


def test_clean_animation_text_strips_label() -> None:
    raw = "текст анимации: Camera dolly in slowly."
    assert _clean_animation_text(raw) == "Camera dolly in slowly."


def test_build_batch_message_has_id_and_voiceover() -> None:
    fr = SimpleNamespace(number=3, voiceover_text="Hello")
    item = FrameImageBatchItem(
        frame=fr,
        image_path=Path("/x.png"),
        image_id="[ID: P9-F3-deadbeef]",
        voiceover="Hello",
    )
    msg = build_batch_message([item])
    assert "ID изображения: [ID: P9-F3-deadbeef]" in msg
    assert "Закадровый текст: Hello" in msg
