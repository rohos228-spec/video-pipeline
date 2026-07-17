"""Классификация ошибок outsee: длина vs модерация."""

import pytest

from app.bots.outsee import (
    OutseeContentRejectedError,
    OutseeImageError,
    OutseePromptTooLongError,
    _failure_text_matches_prompt_id,
    _normalize_pre_failure_baseline,
    _outsee_failure_is_stale,
    _outsee_failure_looks_like_prompt_body,
    _outsee_failure_text_is_noise,
    _raise_outsee_failure,
    outsee_error_is_moderation,
    outsee_error_kind,
    outsee_error_kind_label,
)
from app.generation_options import OUTSEE_PROMPT_MAX_CHARS


def test_outsee_error_kind_length_vs_moderation() -> None:
    length_err = OutseePromptTooLongError(
        "outsee: промт 5000 символов",
        context={"error_kind": "length"},
    )
    mod_err = OutseeContentRejectedError(
        "outsee image: контент отклонён модерацией",
        context={"kind": "moderation"},
    )
    assert outsee_error_kind(length_err) == "length"
    assert outsee_error_kind(mod_err) == "moderation"
    assert outsee_error_kind_label("length") == "лимит символов"
    assert outsee_error_kind_label("moderation") == "модерация"


def test_moderation_wins_over_long_prompt_len() -> None:
    """Явная модерация в UI — moderation, даже если prompt_len > лимита."""
    with pytest.raises(OutseeContentRejectedError) as exc:
        _raise_outsee_failure(
            text="Ваш текстовый запрос содержит запрещённое",
            gen_id="abc",
            elapsed=5.0,
            in_result=True,
            prompt_len=OUTSEE_PROMPT_MAX_CHARS + 100,
        )
    assert exc.value.context.get("kind") == "moderation"


def test_raise_outsee_failure_true_moderation() -> None:
    with pytest.raises(OutseeContentRejectedError):
        _raise_outsee_failure(
            text="Контент отклонён",
            gen_id="abc",
            elapsed=5.0,
            in_result=True,
            prompt_len=4000,
        )


def test_raise_outsee_failure_ui_length_marker() -> None:
    with pytest.raises(OutseePromptTooLongError):
        _raise_outsee_failure(
            text="Prompt is too long",
            gen_id="abc",
            elapsed=3.0,
            in_result=True,
            prompt_len=4000,
        )


def test_failure_text_matches_prompt_id_rejects_foreign_frame() -> None:
    prefix = "[ID: P17-F94-1f534434 r1a3]"
    foreign = (
        "[ID: P17-F93-39192420] Ошибка запрещённый контент"
    )
    own = "[ID: P17-F94-1f534434 r1a3] запрещённый контент"
    assert _failure_text_matches_prompt_id(foreign, prefix) is False
    assert _failure_text_matches_prompt_id(own, prefix) is True


def test_pre_failure_baseline_ignores_foreign_moderation() -> None:
    foreign = "запрещённый контент без id"
    assert (
        _normalize_pre_failure_baseline(
            foreign,
            prompt_id_prefix="[ID: P17-F94-abc]",
        )
        is None
    )


def test_generate_blocked_is_not_length_prompt_error() -> None:
    from app.services.outsee_retry import _is_prompt_related_error

    err = OutseeImageError(
        "outsee: кнопка Generate заблокирована — промт не принят",
        context={"gen_id": "abc", "prompt_len": 4800},
    )
    assert _is_prompt_related_error(err) is False


def test_moderation_in_failure_context_not_length() -> None:
    from app.services.outsee_retry import _is_prompt_related_error

    err = OutseeImageError(
        "outsee image: ошибка генерации на outsee.io",
        context={
            "kind": "moderation",
            "failure": "Ваш текстовый запрос содержит запрещённое",
            "prompt_len": 4800,
        },
    )
    assert _is_prompt_related_error(err) is False
    assert outsee_error_is_moderation(err) is True


def test_queue_sidebar_moderation_stale_until_gen_idle_and_min_elapsed() -> None:
    text = "[ID: P17-F90-dda7487c] запрещённый контент"
    prefix = "[ID: P17-F90-dda7487c]"
    assert _outsee_failure_is_stale(
        text,
        baseline_failure_texts=frozenset(),
        in_result=False,
        elapsed=2.0,
        gen_idle=True,
        queue_mode=True,
        prompt_id_prefix=prefix,
    )
    assert not _outsee_failure_is_stale(
        text,
        baseline_failure_texts=frozenset(),
        in_result=False,
        elapsed=6.0,
        gen_idle=True,
        queue_mode=True,
        prompt_id_prefix=prefix,
    )


def test_video_result_moderation_without_prompt_id_not_stale() -> None:
    """Video UI: result panel shows prompt text + rejection without [ID: …]."""
    text = (
        "парень плачет без звукаГенерировать70Контент отклонён"
        "Аудиодорожка видео не прошла модерацию"
    )
    prefix = "[ID: P42-F6-abc12345 r1a3]"
    assert not _outsee_failure_is_stale(
        text,
        baseline_failure_texts=frozenset(),
        in_result=True,
        elapsed=5.0,
        gen_idle=False,
        queue_mode=True,
        prompt_id_prefix=prefix,
    )


def test_foreign_queue_moderation_without_prompt_id_stays_stale() -> None:
    text = "другой кадр запрещённый контент"
    prefix = "[ID: P42-F6-abc12345 r1a3]"
    assert _outsee_failure_is_stale(
        text,
        baseline_failure_texts=frozenset(),
        in_result=False,
        elapsed=6.0,
        gen_idle=True,
        queue_mode=True,
        prompt_id_prefix=prefix,
    )


def test_queue_rejection_fail_fast_while_generate_disabled() -> None:
    """Регрессия: отказ в очереди по нашему ID — не ждать gen_idle 5 мин."""
    text = "[ID: P42-F1-5bbec734] Контент отклонён"
    prefix = "[ID: P42-F1-5bbec734]"
    assert not _outsee_failure_is_stale(
        text,
        baseline_failure_texts=frozenset(),
        in_result=False,
        elapsed=8.0,
        gen_idle=False,
        queue_mode=True,
        prompt_id_prefix=prefix,
    )


def test_video_audio_moderation_in_result_without_gen_idle() -> None:
    text = (
        "парень плачетКонтент отклонён"
        "Аудиодорожка видео не прошла модерацию"
    )
    prefix = "[ID: P42-F1-abc12345]"
    assert not _outsee_failure_is_stale(
        text,
        baseline_failure_texts=frozenset(),
        in_result=True,
        elapsed=8.0,
        gen_idle=False,
        queue_mode=True,
        prompt_id_prefix=prefix,
    )


def test_prompt_body_not_counted_as_failure_noise() -> None:
    prompt = (
        "--no text, subtitles, captions, logos, watermarks, "
        "added characters, duplicated"
    )
    assert _outsee_failure_looks_like_prompt_body(prompt) is True
    assert _outsee_failure_text_is_noise(prompt) is True


def test_queue_card_rejection_without_id_fail_fast() -> None:
    """Карточка очереди: отказ без [ID: …] в тексте ошибки — fail-fast."""
    text = "Контент отклонён"
    prefix = "[ID: P42-F1-71b220fd]"
    assert not _outsee_failure_is_stale(
        text,
        baseline_failure_texts=frozenset(),
        in_result=False,
        elapsed=8.0,
        gen_idle=False,
        queue_mode=True,
        prompt_id_prefix=prefix,
        card_scoped=True,
    )


def test_queue_card_without_id_still_stale_if_not_card_scoped() -> None:
    text = "Контент отклонён"
    prefix = "[ID: P42-F1-71b220fd]"
    assert _outsee_failure_is_stale(
        text,
        baseline_failure_texts=frozenset(),
        in_result=False,
        elapsed=8.0,
        gen_idle=False,
        queue_mode=True,
        prompt_id_prefix=prefix,
        card_scoped=False,
    )
