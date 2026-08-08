"""Политика ошибок видео-генерации: классификация + лестница Veo→Kling.

Лестница на один клип (без отката модели назад):

  primary (Veo / модель проекта):
    попытки 1..3 — после каждого gen-fail меняем текст промта
    попытка 4   — финальная на primary
  fallback (Kling 2.6 через kie.ai):
    попытки 1..3 — затем VideoLadderExhaustedError → пропуск кадра

Не сжигают попытку: concurrency/rate-limit, download-only, soften start_frame
(до лимита), transient network (внутри лимита), Chrome/CDP recovery (вне этого слоя).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from app.generation_options import (
    VIDEO_FALLBACK_ATTEMPTS,
    VIDEO_PRIMARY_FINAL_ATTEMPTS,
    VIDEO_PRIMARY_REWRITE_ATTEMPTS,
    VIDEO_PRIMARY_TOTAL_ATTEMPTS,
)


class VideoErrorAction(str, Enum):
    """Что делать retry-слою с этой ошибкой."""

    RETRY_SAME = "retry_same"  # не сжигать попытку
    RETRY_REWRITE = "retry_rewrite"  # сжечь + заменить промт
    RETRY_BURN = "retry_burn"  # сжечь попытку, промт как есть
    SOFTEN_FRAME = "soften_frame"  # soften start_frame, не сжигать
    SILENT_AUDIO = "silent_audio"  # sound off + sanitize, потом burn/retry
    STOP_AUTH = "stop_auth"  # 401/402/403 — не крутить лестницу
    STOP_CANCEL = "stop_cancel"
    DOWNLOAD_RETRY = "download_retry"
    EXHAUSTED = "exhausted"  # вся лестница кончилась


class VideoLadderExhaustedError(RuntimeError):
    """Исчерпаны primary+fallback попытки — кадр надо пропустить."""

    def __init__(self, message: str, *, context: dict[str, Any] | None = None):
        super().__init__(message)
        self.context = dict(context or {})
        self.context["video_ladder_exhausted"] = True


@dataclass(frozen=True)
class ClassifiedVideoError:
    code: str
    action: VideoErrorAction
    detail: str = ""


def classify_video_error(exc: BaseException) -> ClassifiedVideoError:  # noqa: C901
    """Единый классификатор для Outsee/Grsai/Kie ошибок видео."""
    name = type(exc).__name__
    msg = str(exc)
    low = msg.lower()
    ctx = getattr(exc, "context", None)
    ctx_d = ctx if isinstance(ctx, dict) else {}

    if name in ("StepCancelledError", "CancelledError") or "cancelled" in low:
        return ClassifiedVideoError("pipeline_cancelled", VideoErrorAction.STOP_CANCEL, msg)

    if ctx_d.get("video_ladder_exhausted") or name == "VideoLadderExhaustedError":
        return ClassifiedVideoError("media_ladder_exhausted", VideoErrorAction.EXHAUSTED, msg)

    provider_code = ctx_d.get("provider_code") or ctx_d.get("code")
    try:
        code_num = int(provider_code) if provider_code is not None else None
    except (TypeError, ValueError):
        code_num = None

    if code_num in (401, 403) or "unauthorized" in low or "неверный ключ" in low:
        return ClassifiedVideoError("media_auth", VideoErrorAction.STOP_AUTH, msg)
    if code_num == 402 or "insufficient credit" in low or "недостаточно кредит" in low:
        return ClassifiedVideoError("media_credits", VideoErrorAction.STOP_AUTH, msg)

    if name == "OutseeDownloadError" or (
        ("download" in low or "скач" in low) and "content" not in low
    ):
        if ctx_d.get("video_url") or ctx_d.get("raw_url"):
            return ClassifiedVideoError(
                "media_download", VideoErrorAction.DOWNLOAD_RETRY, msg
            )
        return ClassifiedVideoError("media_download", VideoErrorAction.RETRY_BURN, msg)

    # Concurrency / rate
    if code_num in (429, 433) or any(
        m in low
        for m in (
            "rate limit",
            "too many",
            "concurrent",
            "одновремен",
            "лимит одновремен",
            "busy",
            "queue full",
        )
    ):
        return ClassifiedVideoError("media_rate_limit", VideoErrorAction.RETRY_SAME, msg)

    # Start-frame CONTENT_POLICY (celebrity / загруженное изображение)
    _frame_markers = (
        "start_frame",
        "стартов",
        "image_url",
        "reference image",
        "celebrity",
        "public figure",
        "загруженное изображение",
        "известную личность",
        "известной личности",
        "известн",
        "uploaded image",
        "known person",
        "на нём известн",
    )
    _policy_markers = (
        "content_policy",
        "content policy",
        "moderation",
        "отклон",
        "violat",
        "контент",
    )
    if any(m in low for m in _frame_markers) and any(m in low for m in _policy_markers):
        # Аудио-policy тоже содержит content_policy — не путать
        if not any(m in low for m in ("аудио", "audio", "sound", "голос", "дорожк")):
            return ClassifiedVideoError(
                "media_start_frame_policy", VideoErrorAction.SOFTEN_FRAME, msg
            )

    # Audio CONTENT_POLICY
    if ("audio" in low or "sound" in low or "голос" in low) and (
        "content_policy" in low
        or "content policy" in low
        or "moderation" in low
        or "отклон" in low
    ):
        return ClassifiedVideoError(
            "media_audio_policy", VideoErrorAction.SILENT_AUDIO, msg
        )

    # Moderation / content policy on prompt
    if (
        name in ("OutseeContentRejectedError",)
        or "content_policy" in low
        or "content policy" in low
        or "content rejected" in low
        or "контент отклон" in low
        or "moderation" in low
        or "violation" in low
        or "safety" in low
    ):
        return ClassifiedVideoError(
            "media_moderation", VideoErrorAction.RETRY_REWRITE, msg
        )

    # Prompt length
    if (
        name in ("OutseePromptTooLongError",)
        or "too long" in low
        or "prompt length" in low
        or (
            "символ" in low
            and ("лимит" in low or "max" in low or "4096" in low or "1000" in low)
        )
    ):
        return ClassifiedVideoError(
            "media_prompt_length", VideoErrorAction.RETRY_REWRITE, msg
        )

    # Validation (kie 422) — один раз пытаемся поправить через rewrite/truncate
    if code_num == 422 or "validation" in low:
        return ClassifiedVideoError(
            "media_validation", VideoErrorAction.RETRY_REWRITE, msg
        )

    # Transient network / maintenance — не сжигать (ограничено streak в retry-слое)
    if code_num in (455, 502, 503) or any(
        m in low
        for m in (
            "connecterror",
            "connection reset",
            "connection refused",
            "network",
            "сеть недоступ",
            "maintenance",
            "temporarily unavailable",
            "upload failed",
            "name or service not known",
        )
    ):
        return ClassifiedVideoError(
            "media_transient", VideoErrorAction.RETRY_SAME, msg
        )

    # Generation timeout / fail / empty — сжигают попытку лестницы
    if code_num in (500, 501, 504) or any(
        m in low
        for m in (
            "timeout",
            "таймаут",
            "timed out",
            "generation failed",
            "результат не появился",
            "empty file",
            "пустой",
            "failed",
        )
    ):
        return ClassifiedVideoError("media_failed", VideoErrorAction.RETRY_BURN, msg)

    return ClassifiedVideoError("media_failed", VideoErrorAction.RETRY_BURN, msg)


def ladder_phase_for_burn(burn_index: int) -> str:
    """burn_index: 0-based счётчик сожжённых попыток (до текущего fail)."""
    if burn_index < VIDEO_PRIMARY_TOTAL_ATTEMPTS:
        return "primary"
    return "fallback"


def should_rewrite_after_primary_burn(burn_count_after: int) -> bool:
    """После N сожжённых primary-попыток — нужна ли замена промта перед следующей.

    После 1,2,3 — да (ещё есть rewrite-слоты / финальная идёт с уже заменённым).
    После 4 — нет, уходим в Kling.
    """
    return 1 <= burn_count_after <= VIDEO_PRIMARY_REWRITE_ATTEMPTS


def primary_attempts_left(primary_burns: int) -> int:
    return max(0, VIDEO_PRIMARY_TOTAL_ATTEMPTS - primary_burns)


def fallback_attempts_left(fallback_burns: int) -> int:
    return max(0, VIDEO_FALLBACK_ATTEMPTS - fallback_burns)


def ladder_summary(
    *,
    primary_burns: int,
    fallback_burns: int,
    model_primary: str,
    model_fallback: str,
) -> str:
    return (
        f"ladder primary={primary_burns}/{VIDEO_PRIMARY_TOTAL_ATTEMPTS}"
        f"({model_primary}; rewrite≤{VIDEO_PRIMARY_REWRITE_ATTEMPTS}"
        f"+final{VIDEO_PRIMARY_FINAL_ATTEMPTS}) "
        f"fallback={fallback_burns}/{VIDEO_FALLBACK_ATTEMPTS}({model_fallback})"
    )
