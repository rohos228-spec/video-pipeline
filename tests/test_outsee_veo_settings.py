"""Veo 3.1 Lite Outsee: тело запроса со всеми настройками."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_veo_generate_video_sends_all_settings(tmp_path: Path, monkeypatch) -> None:
    from app.bots import outsee_http as oh

    monkeypatch.setattr(oh.settings, "outsee_api_key", "test-key")
    out = tmp_path / "v.mp4"
    captured: dict = {}

    async def fake_post(path: str, body: dict):
        captured["path"] = path
        captured["body"] = body
        return {"id": 1, "status": "queued"}

    async def fake_poll(gen_id, *, timeout):
        return {"status": "completed", "result_url": "https://example.com/x.mp4"}

    async def fake_dl(url: str, out_path: Path):
        out_path.write_bytes(b"\x00" * 64)
        return out_path

    with (
        patch.object(oh, "_post_generate", side_effect=fake_post),
        patch.object(oh, "_poll_generation", side_effect=fake_poll),
        patch.object(oh, "_download", side_effect=fake_dl),
    ):
        await oh.generate_video(
            "sky",
            out,
            model_slug="veo-3-1-lite",
            aspect_ratio="9:16",
            resolution="1080p",  # должен стать 720p
            duration=6,
            generate_audio=False,
            first_frame_url="data:image/png;base64,aaa",
            last_frame_url="data:image/png;base64,bbb",
        )

    assert captured["path"] == "/api/v1/videos/generate"
    body = captured["body"]
    assert body["model"] == "veo-3-1-lite"
    assert body["aspect_ratio"] == "9:16"
    assert body["resolution"] == "720p"
    assert body["duration_sec"] == 6
    assert body["generate_audio"] is False
    assert body["first_frame_url"].startswith("data:")
    assert body["last_frame_url"].startswith("data:")
