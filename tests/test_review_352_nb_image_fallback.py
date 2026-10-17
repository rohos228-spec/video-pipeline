"""Неблокирующее: gpt-image-2 → nano-banana-2 видно в логе, трекере и карточке."""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from loguru import logger

from app.bots.outsee import GenerationResult
from app.orchestrator.steps import generate_images
from app.services import outsee_retry as mod
from app.services.model_substitution import (
    clear_substitution_events,
    frame_card_with_substitution,
    substitution_events,
)


@pytest.mark.asyncio
async def test_gpt_image_2_fallback_is_visible(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from app.bots.outsee_http import OutseeApiError

    calls: list[dict] = []
    out = tmp_path / "fish.png"

    async def fake_api(prompt, out_path, **kwargs):
        slug = str(kwargs.get("model_slug") or "")
        if slug != "nano-banana-2":
            raise OutseeApiError(
                "Outsee generation failed: {'code': 'CONTENT_POLICY'}",
                context={"code": "content_policy"},
            )
        out_path.write_bytes(b"ok" * 50)
        return GenerationResult(file_path=out_path, raw_url="https://example/x.png")

    async def fake_prepare(gpt, body, prefix, *, project_id=None):
        return body

    async def no_sleep(*_a, **_k):
        return None

    def _track(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(mod, "_prepare_prompt_for_outsee", fake_prepare)
    monkeypatch.setattr(mod, "sleep_cancellable", no_sleep)
    monkeypatch.setattr("app.bots.outsee_http.outsee_api_configured", lambda: True)
    monkeypatch.setattr("app.bots.outsee_http.generate_image", fake_api)
    monkeypatch.setattr("app.services.api_tracker_hook.record_api_call", _track)
    clear_substitution_events()
    warnings: list[str] = []
    sink = logger.add(lambda msg: warnings.append(str(msg)), level="WARNING")
    try:
        result = await mod.generate_image_with_retries(
            None,
            None,
            prompt="рыба",
            out_path=out,
            max_attempts_per_prompt=1,
            gpt_rewrite=False,
            model_slug="gpt-image-2",
            aspect_ratio="16:9",
            resolution="2K",
            project_id=63,
        )
    finally:
        logger.remove(sink)

    line = result.model_substitution or ""
    assert "gpt-image-2" in line
    assert "nano-banana-2" in line
    assert any("gpt-image-2" in m and "nano-banana-2" in m for m in warnings)
    marked = [
        c for c in calls if (c.get("metadata") or {}).get("event") == "model_substitution"
    ]
    assert marked, calls
    meta = marked[0]["metadata"]
    assert meta["original_model"] == "gpt-image-2"
    assert meta["substituted_model"] == "nano-banana-2"
    assert "CONTENT_POLICY" in meta["reason"]
    assert meta["node"] == "image"
    assert meta["run"] == "63"
    events = substitution_events()
    assert events[-1]["line"] == line
    card = frame_card_with_substitution({"voice": "реплика"}, line)
    assert card["подмена_модели"] == line
    assert card["voice"] == "реплика"
    src = inspect.getsource(generate_images)
    assert "frame_card_with_substitution" in src
    retry_src = inspect.getsource(mod.generate_image_with_retries)
    # Мёртвый повторный return без гарда outsee is None снят.
    assert retry_src.count("return await outsee.generate_image(") == 1
