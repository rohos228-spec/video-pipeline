"""Классификатор ошибок видео + контракт лестницы Veo→Kling."""

from __future__ import annotations

from app.bots.outsee import OutseeContentRejectedError, OutseeImageError
from app.generation_options import (
    VIDEO_FALLBACK_ATTEMPTS,
    VIDEO_PRIMARY_REWRITE_ATTEMPTS,
    VIDEO_PRIMARY_TOTAL_ATTEMPTS,
    outsee_video_fallback_fields,
)
from app.services.video_error_policy import (
    VideoErrorAction,
    VideoLadderExhaustedError,
    classify_video_error,
    should_rewrite_after_primary_burn,
)


def test_ladder_constants() -> None:
    assert VIDEO_PRIMARY_REWRITE_ATTEMPTS == 3
    assert VIDEO_PRIMARY_TOTAL_ATTEMPTS == 4
    assert VIDEO_FALLBACK_ATTEMPTS == 3
    fb = outsee_video_fallback_fields()
    assert fb["model_slug"] == "kling-2-6"


def test_should_rewrite_after_primary_burn() -> None:
    assert should_rewrite_after_primary_burn(1) is True
    assert should_rewrite_after_primary_burn(2) is True
    assert should_rewrite_after_primary_burn(3) is True
    assert should_rewrite_after_primary_burn(4) is False
    assert should_rewrite_after_primary_burn(0) is False


def test_classify_moderation_rewrite() -> None:
    err = OutseeContentRejectedError("контент отклонён", context={"kind": "moderation"})
    c = classify_video_error(err)
    assert c.code == "media_moderation"
    assert c.action == VideoErrorAction.RETRY_REWRITE


def test_classify_credits_stop() -> None:
    err = OutseeImageError("no credits", context={"provider_code": 402})
    c = classify_video_error(err)
    assert c.code == "media_credits"
    assert c.action == VideoErrorAction.STOP_AUTH


def test_classify_rate_limit_no_burn() -> None:
    err = OutseeImageError("rate limit", context={"provider_code": 429})
    c = classify_video_error(err)
    assert c.action == VideoErrorAction.RETRY_SAME


def test_classify_ladder_exhausted() -> None:
    err = VideoLadderExhaustedError("done", context={"primary_burns": 4})
    c = classify_video_error(err)
    assert c.code == "media_ladder_exhausted"
    assert c.action == VideoErrorAction.EXHAUSTED
    assert err.context.get("video_ladder_exhausted") is True
