"""Veo postprocess + public frame URL helpers."""

from __future__ import annotations

import base64
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_ensure_public_keeps_http() -> None:
    from app.bots.outsee_http import ensure_public_image_url

    url = "https://example.com/a.png"
    assert await ensure_public_image_url(url) == url


@pytest.mark.asyncio
async def test_ensure_public_hosts_data_url(tmp_path: Path) -> None:
    from app.bots import outsee_http as oh

    png = base64.b64encode(
        b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    ).decode("ascii")
    data = f"data:image/png;base64,{png}"

    class FakeResp:
        status_code = 200
        text = "https://litterbox.catbox.moe/hosted.png"

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def post(self, *a, **k):
            return FakeResp()

    with patch.object(oh.httpx, "AsyncClient", return_value=FakeClient()):
        out = await oh.ensure_public_image_url(data)
    assert out == "https://litterbox.catbox.moe/hosted.png"


@pytest.mark.asyncio
async def test_postprocess_mute_and_trim(tmp_path: Path, monkeypatch) -> None:
    from app.bots import outsee_http as oh

    src = tmp_path / "v.mp4"
    src.write_bytes(b"\x00" * 128)

    async def fake_probe(_p):
        return 8.0

    async def fake_has_audio(_p):
        return True

    calls: list[list[str]] = []

    class FakeProc:
        returncode = 0

        async def communicate(self):
            # simulate ffmpeg writing out
            out = Path(calls[-1][-1])
            out.write_bytes(b"\x01" * 200)
            return b"", b""

    async def fake_exec(*cmd, **kwargs):
        calls.append(list(cmd))
        return FakeProc()

    monkeypatch.setattr(oh, "probe_has_audio", fake_has_audio)
    with (
        patch("app.services.media_probe.probe_duration", side_effect=fake_probe),
        patch.object(oh.asyncio, "create_subprocess_exec", side_effect=fake_exec),
    ):
        await oh.postprocess_veo_mp4(src, duration=4, generate_audio=False)

    assert calls, "ffmpeg should run"
    assert src.read_bytes() == b"\x01" * 200


@pytest.mark.asyncio
async def test_veo_generate_video_hosts_frame_and_postprocesses(
    tmp_path: Path, monkeypatch
) -> None:
    from app.bots import outsee_http as oh

    monkeypatch.setattr(oh.settings, "outsee_api_key", "test-key")
    out = tmp_path / "v.mp4"
    captured: dict = {}

    async def fake_post(path: str, body: dict):
        captured["body"] = body
        return {"id": 1, "status": "queued"}

    async def fake_poll(gen_id, *, timeout):
        return {"status": "completed", "result_url": "https://example.com/x.mp4"}

    async def fake_dl(url: str, out_path: Path):
        out_path.write_bytes(b"\x00" * 64)
        return out_path

    async def fake_host(url):
        return "https://cdn.example/frame.png" if url else None

    async def fake_pp(path, *, duration, generate_audio):
        captured["pp"] = {"duration": duration, "audio": generate_audio}
        return path

    with (
        patch.object(oh, "_post_generate", side_effect=fake_post),
        patch.object(oh, "_poll_generation", side_effect=fake_poll),
        patch.object(oh, "_download", side_effect=fake_dl),
        patch.object(oh, "ensure_public_image_url", side_effect=fake_host),
        patch.object(oh, "postprocess_veo_mp4", side_effect=fake_pp),
    ):
        await oh.generate_video(
            "sky",
            out,
            model_slug="veo-3-1-lite",
            aspect_ratio="9:16",
            resolution="720p",
            duration=4,
            generate_audio=False,
            first_frame_url="data:image/png;base64,aaa",
        )

    assert captured["body"]["image_url"] == "https://cdn.example/frame.png"
    assert "first_frame_url" not in captured["body"]
    assert captured["body"]["generate_audio"] is False
    assert captured["body"]["duration_sec"] == 4
    assert captured["pp"] == {"duration": 4, "audio": False}
