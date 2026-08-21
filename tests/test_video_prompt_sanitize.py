"""Локальная санация video-промтов: триггеры → нейтральные замены."""

from __future__ import annotations

from app.services.video_prompt_sanitize import (
    replace_trigger_words,
    sanitize_video_prompt_after_errors,
    simplify_video_prompt,
)


def test_ensure_silent_video_prompt() -> None:
    from app.services.video_prompt_sanitize import ensure_silent_video_prompt

    out = ensure_silent_video_prompt("Animate soft light")
    assert out.lower().startswith("silent video only")
    assert "Animate soft light" in out
    # повторно не дублирует
    out2 = ensure_silent_video_prompt(out)
    assert out2.count("Silent video only") == 1


def test_replace_asahara_with_accent() -> None:
    text = "Портрет Асаха́ры на стене, Мацумо́то говорит о зарине."
    out, n = replace_trigger_words(text)
    assert n >= 3
    low = out.lower()
    assert "асахар" not in low
    assert "мацумо" not in low
    assert "зарин" not in low
    # «Портрет Асахары» → «лидер группы» → «фотография человека»
    assert "фотография человека" in out or "лидер группы" in out
    assert "преподаватель" in out
    assert "химическое вещество" in out


def test_replace_aum_shinrikyo() -> None:
    text = "Аум Синрикё проводит теракт в метро"
    out, n = replace_trigger_words(text)
    assert n >= 2
    assert "аум" not in out.lower()
    assert "теракт" not in out.lower()
    assert "закрытая община" in out


def test_simplify_strips_empty_fillers() -> None:
    text = (
        "Animate soft light.\n"
        "Scene Feature: нет исходных данных для заполнения.\n"
        "Camera: нет исходных данных для заполнения.\n"
        "More action."
    )
    out = simplify_video_prompt(text)
    assert "нет исходных данных" not in out.lower()
    assert "Animate soft light" in out
    assert "More action" in out


def test_sanitize_after_errors_combines() -> None:
    long = ("Асахара " * 200) + ("detail " * 200)
    out, stats = sanitize_video_prompt_after_errors(long, simplify=True)
    assert stats["trigger_replacements"] >= 1
    assert "Асахара" not in out
    assert len(out) <= 900
    assert stats["chars_after"] <= stats["chars_before"]
