"""Тесты классификатора плашек outsee (_outsee_failure_kind)."""

from __future__ import annotations

import pytest

from app.bots.outsee import (
    OutseeContentRejectedError,
    OutseeDownloadError,
    _is_refusal_audio,
    _is_refusal_person,
    _outsee_failure_is_stale,
    _outsee_failure_kind,
    _raise_outsee_failure,
)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Нельзя генерировать изображение известной личности", "refusal_person"),
        ("Cannot generate images of a real person", "refusal_person"),
        ("Запрос содержит публичную личность", "refusal_person"),
        ("Identifiable people are not allowed", "refusal_person"),
        ("Мы не можем создать аудио для этого запроса", "refusal_audio"),
        ("Audio is not supported for this model", "refusal_audio"),
        ("Can't generate audio track", "refusal_audio"),
        ("Контент отклонён модерацией", "moderation"),
        ("Content rejected by moderation", "moderation"),
        ("Ошибка генерации. Попробуйте снова.", "generation"),
    ],
)
def test_outsee_failure_kind_categories(text: str, expected: str) -> None:
    assert _outsee_failure_kind(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "В этом длинном тексте про личность и характер героя нет отказа outsee",
        "Описание сцены: аудио дорожка на фоне, но это не про генерацию звука",
        "Научпоп про известность открытия и аудио визуализацию данных",
    ],
)
def test_outsee_failure_kind_non_refusal_long_text(text: str) -> None:
    assert _outsee_failure_kind(text) == "unknown"


def test_refusal_person_helpers() -> None:
    assert _is_refusal_person("real person detected")
    assert not _is_refusal_person("личность героя в сцене")


def test_refusal_audio_helpers() -> None:
    assert _is_refusal_audio("не могу создать аудио")
    assert not _is_refusal_audio("фоновое аудио в описании")


def test_raise_outsee_failure_refusal_person() -> None:
    with pytest.raises(OutseeContentRejectedError) as exc:
        _raise_outsee_failure(
            text="Известная личность не может быть сгенерирована",
            gen_id="g1",
            elapsed=5.0,
            in_result=True,
        )
    assert exc.value.context.get("kind") == "refusal_person"
    assert "известная личность" in exc.value.reason.lower()


def test_raise_outsee_failure_refusal_audio() -> None:
    with pytest.raises(OutseeContentRejectedError) as exc:
        _raise_outsee_failure(
            text="Can't generate audio for this prompt",
            gen_id="g1",
            elapsed=5.0,
            in_result=True,
        )
    assert exc.value.context.get("kind") == "refusal_audio"
    assert "аудио" in exc.value.reason.lower()


def test_refusal_person_not_stale_in_result() -> None:
    text = "Нельзя: известная личность"
    assert _outsee_failure_is_stale(
        text,
        baseline_failure_texts=frozenset(),
        in_result=True,
        elapsed=5.0,
        gen_idle=True,
    ) is False


def test_validate_download_raises_download_error(tmp_path) -> None:
    from app.bots.outsee import _validate_downloaded_image

    tiny = tmp_path / "tiny.png"
    tiny.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 100)
    with pytest.raises(OutseeDownloadError):
        _validate_downloaded_image(tiny, gen_id="g", img_url="http://x/y.png")
