"""Tests for known-face skip detection."""

from app.bots.outsee import OutseeImageError, outsee_error_is_known_face


def test_known_face_russian():
    err = OutseeImageError(
        "moderation: найдено известное лицо",
        context={"failure": "Найдено известное лицо"},
    )
    assert outsee_error_is_known_face(err)


def test_known_face_english():
    err = OutseeImageError("known public figure detected")
    assert outsee_error_is_known_face(err)


def test_veo_history_known_face_not_noise():
    from app.bots.outsee import _outsee_failure_text_is_noise

    text = (
        "ОшибкаVeo 51 сек, moderation: найдено известное лицо "
        "[ID: P12-F146-6d692a45]"
    )
    assert not _outsee_failure_text_is_noise(
        text, prompt_id_prefix="[ID: P12-F146-6d692a45 r1a1]"
    )


def test_generation_refused_is_skippable():
    from app.bots.outsee import (
        OutseeImageError,
        outsee_error_is_known_face,
        outsee_text_is_generation_refused,
    )

    assert outsee_text_is_generation_refused("Генерация отказано")
    assert outsee_error_is_known_face(
        OutseeImageError("outsee video: Генерация отказано")
    )


def test_generation_refused_not_noise_without_id():
    from app.bots.outsee import _outsee_failure_text_is_noise

    assert not _outsee_failure_text_is_noise(
        "Генерация отказано",
        prompt_id_prefix="[ID: P12-F146-abc r1a1]",
    )


def test_minor_moderation_is_skippable():
    from app.bots.outsee import (
        OutseeImageError,
        outsee_error_is_known_face,
        outsee_text_is_minor_moderation,
    )

    assert outsee_text_is_minor_moderation(
        "Обнаружен несовершеннолетний на изображении"
    )
    assert outsee_error_is_known_face(
        OutseeImageError(
            "outsee video: moderation",
            context={"failure": "Обнаружен несовершеннолетний"},
        )
    )


def test_minor_moderation_not_noise_without_id():
    from app.bots.outsee import _outsee_failure_text_is_noise

    assert not _outsee_failure_text_is_noise(
        "Обнаружен несовершеннолетний",
        prompt_id_prefix="[ID: P17-F123-2ad67ce2 r1a1]",
    )
