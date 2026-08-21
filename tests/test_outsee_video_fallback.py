"""Лестница видео: Veo 3 rewrite + 1 final → Kling 2.6 ×3 → exhausted."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.bots.outsee import GenerationResult, OutseeImageError
from app.generation_options import (
    KIE_KLING_FALLBACK_SLUG,
    OUTSEE_VIDEO_FALLBACK_AFTER_FAILURES,
    OUTSEE_VIDEO_FALLBACK_ASPECT_LABEL,
    VIDEO_FALLBACK_ATTEMPTS,
    VIDEO_PRIMARY_TOTAL_ATTEMPTS,
    outsee_video_fallback_fields,
)
from app.services import outsee_retry as mod
from app.services.video_error_policy import VideoLadderExhaustedError


@pytest.fixture(autouse=True)
def _force_cdp_video_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Primary бьёт FakeOutsee; fallback — мок kie_kling."""
    monkeypatch.setattr("app.bots.grsai.grsai_key_configured", lambda: False)
    monkeypatch.setattr(
        "app.bots.outsee_http.outsee_api_configured", lambda: False
    )


def test_outsee_video_fallback_constants() -> None:
    fb = outsee_video_fallback_fields()
    assert fb["model_slug"] == "kling-2-6"
    assert fb["resolution"] == "720p"
    assert fb["aspect_ratio"] == OUTSEE_VIDEO_FALLBACK_ASPECT_LABEL
    assert OUTSEE_VIDEO_FALLBACK_AFTER_FAILURES == VIDEO_PRIMARY_TOTAL_ATTEMPTS == 4
    assert VIDEO_FALLBACK_ATTEMPTS == 3


def test_should_use_video_fallback_after_primary_total() -> None:
    assert mod._should_use_video_fallback(round_idx=0, gen_failures=0) is False
    assert mod._should_use_video_fallback(round_idx=0, gen_failures=3) is False
    assert mod._should_use_video_fallback(round_idx=0, gen_failures=4) is True
    assert mod._should_use_video_fallback(round_idx=1, gen_failures=0) is True


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
async def test_ladder_switches_to_kling_after_four_primary_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    primary_calls: list[dict[str, object]] = []
    kling_calls: list[str] = []

    class FakeOutsee:
        async def generate_video(self, prompt: str, out_path: Path, **kwargs):
            primary_calls.append(dict(kwargs))
            raise OutseeImageError(
                "outsee video: таймаут",
                context={"kind": "timeout"},
            )

    async def _fake_kling(prompt: str, out_path: Path, **kwargs):
        kling_calls.append(prompt)
        out_path.write_bytes(b"k" * 2048)
        return GenerationResult(file_path=out_path, gen_id="kling-ok")

    async def _no_prepare(gpt, body, prefix, *, project_id=None, max_full=None, max_body=None):
        return body

    class FakeGpt:
        async def ask_fresh(self, ask: str, *, timeout: float = 300, project_id=None) -> str:
            return "rewritten soft prompt about calm scene " * 3

    async def _no_sleep(*_a, **_k):
        return None

    monkeypatch.setattr(mod, "_prepare_prompt_for_outsee", _no_prepare)
    monkeypatch.setattr(mod, "sleep_cancellable", _no_sleep)
    monkeypatch.setattr("app.bots.kie_kling.kie_api_configured", lambda: True)
    monkeypatch.setattr("app.bots.kie_kling.generate_video", _fake_kling)

    await mod.generate_video_with_retries(
        FakeOutsee(),
        FakeGpt(),
        prompt="animate this Асахара",
        out_path=Path("/tmp/clip_ladder.mp4"),
        gpt_rewrite=True,
        model_slug="veo-3-fast",
        resolution="1080p",
        aspect_ratio="9:16",
    )

    assert len(primary_calls) == VIDEO_PRIMARY_TOTAL_ATTEMPTS
    assert len(kling_calls) == 1
    for call in primary_calls:
        assert call["model_slug"] == "veo-3-fast"


@pytest.mark.asyncio
async def test_ladder_exhausted_after_kling_three_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    primary_n = 0
    kling_n = 0

    class FakeOutsee:
        async def generate_video(self, prompt: str, out_path: Path, **kwargs):
            nonlocal primary_n
            primary_n += 1
            raise OutseeImageError("fail primary", context={"kind": "generation"})

    async def _fake_kling(prompt: str, out_path: Path, **kwargs):
        nonlocal kling_n
        kling_n += 1
        raise OutseeImageError(
            "kie fail",
            context={"provider_code": 501, "kind": "generation"},
        )

    async def _no_prepare(gpt, body, prefix, *, project_id=None, max_full=None, max_body=None):
        return body

    async def _no_sleep(*_a, **_k):
        return None

    monkeypatch.setattr(mod, "_prepare_prompt_for_outsee", _no_prepare)
    monkeypatch.setattr(mod, "sleep_cancellable", _no_sleep)
    monkeypatch.setattr("app.bots.kie_kling.kie_api_configured", lambda: True)
    monkeypatch.setattr("app.bots.kie_kling.generate_video", _fake_kling)

    with pytest.raises(VideoLadderExhaustedError) as ei:
        await mod.generate_video_with_retries(
            FakeOutsee(),
            None,
            prompt="animate",
            out_path=Path("/tmp/clip_ex.mp4"),
            gpt_rewrite=False,
            model_slug="veo-3-fast",
            resolution="720p",
            aspect_ratio="9:16",
        )

    assert primary_n == VIDEO_PRIMARY_TOTAL_ATTEMPTS
    assert kling_n == VIDEO_FALLBACK_ATTEMPTS
    assert ei.value.context.get("video_ladder_exhausted") is True
    assert ei.value.context.get("fallback_model") == KIE_KLING_FALLBACK_SLUG


@pytest.mark.asyncio
async def test_generate_video_sanitizes_triggers_during_primary_rewrites(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prompts: list[str] = []

    class FakeOutsee:
        async def generate_video(self, prompt: str, out_path: Path, **kwargs):
            prompts.append(prompt)
            if len(prompts) <= VIDEO_PRIMARY_TOTAL_ATTEMPTS:
                raise OutseeImageError(
                    "outsee video: контент отклонён",
                    context={"kind": "moderation"},
                )
            return GenerationResult(file_path=out_path, gen_id="ok")

    async def _fake_kling(prompt: str, out_path: Path, **kwargs):
        prompts.append(prompt)
        out_path.write_bytes(b"z" * 2048)
        return GenerationResult(file_path=out_path, gen_id="kling")

    async def _no_prepare(gpt, body, prefix, *, project_id=None, max_full=None, max_body=None):
        return body

    class FakeGpt:
        async def ask_fresh(self, ask: str, *, timeout: float = 300, project_id=None) -> str:
            return "Асахара walks slowly in soft light " * 3

    async def _no_sleep(*_a, **_k):
        return None

    monkeypatch.setattr(mod, "_prepare_prompt_for_outsee", _no_prepare)
    monkeypatch.setattr(mod, "sleep_cancellable", _no_sleep)
    monkeypatch.setattr("app.bots.kie_kling.kie_api_configured", lambda: True)
    monkeypatch.setattr("app.bots.kie_kling.generate_video", _fake_kling)

    await mod.generate_video_with_retries(
        FakeOutsee(),
        FakeGpt(),
        prompt="Портрет Асахары и зарин в метро",
        out_path=Path("/tmp/clip_san.mp4"),
        gpt_rewrite=True,
        model_slug="veo-3-fast",
        resolution="720p",
        aspect_ratio="9:16",
    )

    assert len(prompts) == VIDEO_PRIMARY_TOTAL_ATTEMPTS + 1
    assert "Асахар" in prompts[0]
    # Kling-попытка — без триггеров (локальный пост-sanitize)
    assert "Асахар" not in prompts[-1]
    assert "зарин" not in prompts[-1].lower()
