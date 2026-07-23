"""Fallback видео-модели outsee после 3 сбоев / 2-го раунда."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.bots.outsee import GenerationResult, OutseeImageError
from app.generation_options import (
    OUTSEE_VIDEO_FALLBACK_AFTER_FAILURES,
    OUTSEE_VIDEO_FALLBACK_ASPECT_LABEL,
    outsee_video_fallback_fields,
)
from app.services import outsee_retry as mod


def test_outsee_video_fallback_constants() -> None:
    fb = outsee_video_fallback_fields()
    assert fb["model_slug"] == "kling-2-5-turbo"
    assert fb["resolution"] == "720p"
    assert fb["aspect_ratio"] == OUTSEE_VIDEO_FALLBACK_ASPECT_LABEL


def test_should_use_video_fallback_second_round() -> None:
    assert mod._should_use_video_fallback(round_idx=0, gen_failures=0) is False
    assert mod._should_use_video_fallback(round_idx=0, gen_failures=2) is False
    assert mod._should_use_video_fallback(round_idx=0, gen_failures=3) is True
    assert mod._should_use_video_fallback(round_idx=1, gen_failures=0) is True
    assert mod._should_use_video_fallback(round_idx=1, gen_failures=1) is True


def test_apply_video_fallback_overrides_project_model() -> None:
    base = {
        "model_slug": "veo-3-1-lite",
        "resolution": "720p",
        "aspect_ratio": "9:16",
        "relax": True,
    }
    out = mod._apply_video_fallback_kwargs(base)
    fb = outsee_video_fallback_fields()
    assert out["model_slug"] == fb["model_slug"]
    assert out["resolution"] == fb["resolution"]
    assert out["aspect_ratio"] == fb["aspect_ratio"]
    assert out["relax"] is True


@pytest.mark.asyncio
async def test_rewritten_round_uses_kling_even_if_failure_counter_broken(
    monkeypatch,
) -> None:
    """Регрессия: round «rewritten» всегда на Kling после 3× original."""
    calls: list[dict[str, object]] = []

    class FakeOutsee:
        async def generate_video(self, prompt: str, out_path: Path, **kwargs):
            calls.append(dict(kwargs))
            raise OutseeImageError(
                "outsee video: результат не появился за 300 сек",
                context={"kind": "timeout"},
            )

    async def _no_prepare(gpt, body, prefix, *, project_id=None):
        return body

    class FakeGpt:
        async def ask_fresh(self, ask: str, *, timeout: float = 300, project_id=None) -> str:
            return "rewritten prompt " * 5

    async def _no_sleep(*_a, **_k):
        return None

    monkeypatch.setattr(mod, "_prepare_prompt_for_outsee", _no_prepare)
    monkeypatch.setattr(mod, "sleep_cancellable", _no_sleep)

    with pytest.raises(OutseeImageError):
        await mod.generate_video_with_retries(
            FakeOutsee(),
            FakeGpt(),
            prompt="animate this",
            out_path=Path("/tmp/clip.mp4"),
            max_attempts_per_prompt=3,
            gpt_rewrite=True,
            model_slug="veo-3-1-lite",
            resolution="720p",
            aspect_ratio="9:16",
        )

    assert len(calls) == 6
    fb = outsee_video_fallback_fields()
    for call in calls[:OUTSEE_VIDEO_FALLBACK_AFTER_FAILURES]:
        assert call["model_slug"] == "veo-3-1-lite"
        assert call["aspect_ratio"] == "9:16"
    for call in calls[OUTSEE_VIDEO_FALLBACK_AFTER_FAILURES :]:
        assert call["model_slug"] == fb["model_slug"]
        assert call["resolution"] == fb["resolution"]
        assert call["aspect_ratio"] == fb["aspect_ratio"]


@pytest.mark.asyncio
async def test_generate_video_switches_model_after_three_failures(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    class FakeOutsee:
        async def generate_video(self, prompt: str, out_path: Path, **kwargs):
            calls.append(dict(kwargs))
            if len(calls) <= OUTSEE_VIDEO_FALLBACK_AFTER_FAILURES:
                raise OutseeImageError(
                    "outsee video: таймаут",
                    context={"kind": "timeout"},
                )
            return GenerationResult(file_path=out_path, gen_id="ok")

    async def _no_prepare(gpt, body, prefix, *, project_id=None):
        return body

    class FakeGpt:
        async def ask_fresh(self, ask: str, *, timeout: float = 300, project_id=None) -> str:
            return "rewritten prompt " * 5

    async def _no_sleep(*_a, **_k):
        return None

    monkeypatch.setattr(mod, "_prepare_prompt_for_outsee", _no_prepare)
    monkeypatch.setattr(mod, "sleep_cancellable", _no_sleep)

    await mod.generate_video_with_retries(
        FakeOutsee(),
        FakeGpt(),
        prompt="animate this",
        out_path=Path("/tmp/clip.mp4"),
        max_attempts_per_prompt=3,
        gpt_rewrite=True,
        model_slug="veo-3-fast",
        resolution="1080p",
        aspect_ratio="9:16",
    )

    assert len(calls) == OUTSEE_VIDEO_FALLBACK_AFTER_FAILURES + 1
    fb = outsee_video_fallback_fields()
    for call in calls[OUTSEE_VIDEO_FALLBACK_AFTER_FAILURES:]:
        assert call["model_slug"] == fb["model_slug"]
        assert call["aspect_ratio"] == fb["aspect_ratio"]
