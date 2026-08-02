"""DB-first extract helpers for plan/split/script-style GPT replies."""

from __future__ import annotations

from app.services.xlsx_step_runners import (
    extract_frames_spec_from_gpt_reply,
    extract_general_plan_from_gpt_reply,
)


def test_extract_replace_frames() -> None:
    reply = (
        '{"ops":[{"target":"replace_frames","frames":['
        '{"закадр":"первый блок текста кадра"},'
        '{"закадр":"второй блок текста кадра","длительность":2.5}'
        "]}]}"
    )
    specs = extract_frames_spec_from_gpt_reply(reply, voiceover_path=None)
    assert len(specs) == 2
    assert "первый" in specs[0]["закадр"]
    assert specs[1].get("длительность") == 2.5


def test_extract_general_plan_still_works() -> None:
    body = "Б" * 220
    got = extract_general_plan_from_gpt_reply(
        '{"ops":[{"target":"project","fields":{"общий_план":"' + body + '"}}]}'
    )
    assert got == body
