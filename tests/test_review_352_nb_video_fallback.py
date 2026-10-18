"""Неблокирующее: смена видеомодели на Kling видна в логе, трекере и результате."""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from loguru import logger

from app.bots.outsee import GenerationResult, OutseeImageError
from app.generation_options import KIE_KLING_FALLBACK_SLUG, VIDEO_PRIMARY_TOTAL_ATTEMPTS
from app.orchestrator.steps import generate_videos
from app.services import outsee_retry as mod
from app.services.model_substitution import clear_substitution_events, substitution_events


@pytest.fixture(autouse=True)
def _force_cdp_video_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.bots.outsee_http.outsee_api_configured", lambda: False)


@pytest.mark.asyncio
async def test_kling_ladder_substitution_is_visible(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[dict] = []

    class FakeOutsee:
        async def generate_video(self, prompt: str, out_path: Path, **kwargs):
            raise OutseeImageError("outsee video: таймаут", context={"kind": "timeout"})

    async def _fake_kling(prompt: str, out_path: Path, **kwargs):
        out_path.write_bytes(b"k" * 2048)
        return GenerationResult(file_path=out_path, gen_id="kling-ok")

    async def _no_prepare(gpt, body, prefix, *, project_id=None, max_full=None, max_body=None):
        return body

    async def _no_sleep(*_a, **_k):
        return None

    def _track(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(mod, "_prepare_prompt_for_outsee", _no_prepare)
    monkeypatch.setattr(mod, "sleep_cancellable", _no_sleep)
    monkeypatch.setattr("app.bots.kie_http.generate_kie_video", _fake_kling)
    monkeypatch.setattr("app.services.api_tracker_hook.record_api_call", _track)
    clear_substitution_events()
    warnings: list[str] = []
    sink = logger.add(lambda msg: warnings.append(str(msg)), level="WARNING")
    out = tmp_path / "clip.mp4"
    try:
        result = await mod.generate_video_with_retries(
            FakeOutsee(),
            None,
            prompt="animate a calm street",
            out_path=out,
            gpt_rewrite=False,
            model_slug="veo-3-fast",
            resolution="720p",
            aspect_ratio="9:16",
            project_id=13,
        )
    finally:
        logger.remove(sink)

    assert result.file_path == out
    line = result.model_substitution or ""
    assert "veo-3-fast" in line
    assert KIE_KLING_FALLBACK_SLUG in line
    assert any("veo-3-fast" in m and KIE_KLING_FALLBACK_SLUG in m for m in warnings)
    marked = [
        c for c in calls if (c.get("metadata") or {}).get("event") == "model_substitution"
    ]
    assert len(marked) == 1
    meta = marked[0]["metadata"]
    assert meta["original_model"] == "veo-3-fast"
    assert meta["substituted_model"] == KIE_KLING_FALLBACK_SLUG
    assert "Kling" in meta["reason"]
    assert meta["node"] == "video"
    assert meta["run"] == "13"
    assert substitution_events()[-1]["line"] == line
    assert "frame_card_with_substitution" in inspect.getsource(generate_videos)
    assert VIDEO_PRIMARY_TOTAL_ATTEMPTS >= 1
