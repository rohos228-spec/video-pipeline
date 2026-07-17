"""Fallback видео-модели outsee после 3 сбоев."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.bots.outsee import GenerationResult, OutseeImageError
from app.generation_options import (
    OUTSEE_VIDEO_FALLBACK_AFTER_FAILURES,
    OUTSEE_VIDEO_FALLBACK_ASPECT_LABEL,
    outsee_video_fallback_kwargs,
)
from app.services import outsee_retry as mod


def test_outsee_video_fallback_constants() -> None:
    fb = outsee_video_fallback_kwargs()
    assert fb["model_slug"] == "kling-2-5-turbo"
    assert fb["resolution"] == "720p"
    assert fb["aspect_ratio"] == OUTSEE_VIDEO_FALLBACK_ASPECT_LABEL


def test_merge_video_fallback_before_threshold() -> None:
    base = {
        "model_slug": "veo-3-fast",
        "resolution": "1080p",
        "aspect_ratio": "9:16",
    }
    out = mod._merge_video_fallback_kwargs(
        base, gen_failures=OUTSEE_VIDEO_FALLBACK_AFTER_FAILURES - 1
    )
    assert out == base


def test_merge_video_fallback_after_threshold() -> None:
    base = {
        "model_slug": "veo-3-fast",
        "resolution": "1080p",
        "aspect_ratio": "9:16",
        "relax": True,
    }
    out = mod._merge_video_fallback_kwargs(
        base, gen_failures=OUTSEE_VIDEO_FALLBACK_AFTER_FAILURES
    )
    fb = outsee_video_fallback_kwargs()
    assert out["model_slug"] == fb["model_slug"]
    assert out["resolution"] == fb["resolution"]
    assert out["aspect_ratio"] == fb["aspect_ratio"]
    assert out["relax"] is True


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
    for call in calls[:OUTSEE_VIDEO_FALLBACK_AFTER_FAILURES]:
        assert call["model_slug"] == "veo-3-fast"
        assert call["resolution"] == "1080p"
        assert call["aspect_ratio"] == "9:16"

    fb = outsee_video_fallback_kwargs()
    last = calls[-1]
    assert last["model_slug"] == fb["model_slug"]
    assert last["resolution"] == fb["resolution"]
    assert last["aspect_ratio"] == fb["aspect_ratio"]
