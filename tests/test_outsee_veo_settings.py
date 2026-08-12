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
async def test_ensure_public_hosts_data_url_via_litterbox_first() -> None:
    from app.bots import outsee_http as oh

    png = base64.b64encode(
        b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    ).decode("ascii")
    data = f"data:image/png;base64,{png}"

    async def fake_litter(_client, _raw, _mime, _filename):
        return "https://litter.catbox.moe/hosted.png"

    with (
        patch.object(oh, "_host_via_litterbox", side_effect=fake_litter),
        patch.object(oh, "_host_via_uguu", side_effect=AssertionError("no uguu")),
        patch.object(oh, "_host_via_catbox", side_effect=AssertionError("no catbox")),
        patch.object(oh, "_host_via_0x0", side_effect=AssertionError("no 0x0")),
        patch.object(oh, "_host_via_tmpfiles", side_effect=AssertionError("no tmp")),
    ):
        out = await oh.ensure_public_image_url(data)
    assert out == "https://litter.catbox.moe/hosted.png"


@pytest.mark.asyncio
async def test_ensure_public_falls_back_when_litterbox_fails() -> None:
    from app.bots import outsee_http as oh

    png = base64.b64encode(
        b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    ).decode("ascii")
    data = f"data:image/png;base64,{png}"

    async def boom(*_a, **_k):
        raise oh.OutseeApiError("litterbox down")

    async def uguu(_client, _raw, _mime, _filename):
        return "https://d.uguu.se/ok.png"

    with (
        patch.object(oh, "_host_via_litterbox", side_effect=boom),
        patch.object(oh, "_host_via_uguu", side_effect=uguu),
        patch.object(oh, "_host_via_tmpfiles", side_effect=AssertionError("no tmp")),
        patch.object(oh, "_host_via_catbox", side_effect=AssertionError("no catbox")),
        patch.object(oh, "_host_via_0x0", side_effect=AssertionError("no 0x0")),
    ):
        out = await oh.ensure_public_image_url(data)
    assert out == "https://d.uguu.se/ok.png"


def test_looks_like_image_bytes_rejects_html_landing() -> None:
    from app.bots.outsee_http import _looks_like_image_bytes

    assert _looks_like_image_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 20)
    assert not _looks_like_image_bytes(
        b"<!DOCTYPE html><html>", "text/html; charset=utf-8"
    )
    assert not _looks_like_image_bytes(b"<html>not an image</html>")


@pytest.mark.asyncio
async def test_accept_hosted_url_rejects_verify_miss() -> None:
    """Verify miss → следующий хост (не soft-accept мёртвого catbox)."""
    from app.bots import outsee_http as oh

    class _Client:
        pass

    with patch.object(oh, "_verify_hosted_image", AsyncMock(return_value=False)):
        with pytest.raises(oh.OutseeApiError, match="verify miss"):
            await oh._accept_hosted_url(
                _Client(),  # type: ignore[arg-type]
                "https://files.catbox.moe/b6cnhy.png",
                host="catbox",
                raw_len=4_000_000,
            )


def test_is_outsee_image_fetch_error() -> None:
    from app.bots.outsee_http import OutseeApiError, _is_outsee_image_fetch_error

    assert _is_outsee_image_fetch_error(
        OutseeApiError(
            "Outsee API /api/v1/videos/generate: Не удалось скачать "
            "изображение по ссылке (таймаут или сеть)."
        )
    )
    assert not _is_outsee_image_fetch_error(
        OutseeApiError("CONTENT_POLICY celebrity")
    )


def test_upload_payload_variants_prefer_jpeg_for_huge_png() -> None:
    from io import BytesIO

    from PIL import Image

    from app.bots.outsee_http import _FRAME_HOST_SOFT_MAX_BYTES, _upload_payload_variants

    # Шумный кадр → PNG > soft max → JPEG идёт первым.
    img = Image.effect_noise((1800, 1800), 40).convert("RGB")
    buf = BytesIO()
    img.save(buf, format="PNG")
    raw = buf.getvalue()
    assert len(raw) > _FRAME_HOST_SOFT_MAX_BYTES
    variants = _upload_payload_variants(raw, "image/png")
    assert variants[0][2].startswith("jpeg")
    assert variants[0][1] == "image/jpeg"
    assert any(v[2] == "orig" for v in variants)


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
        out_path.write_bytes(b"\x00" * 2_500_000)
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


@pytest.mark.asyncio
async def test_veo_generate_video_raises_if_ref_not_hosted(
    tmp_path: Path, monkeypatch
) -> None:
    """Реф передан, но host вернул None — нельзя молча уходить в text→video."""
    from app.bots import outsee_http as oh

    monkeypatch.setattr(oh.settings, "outsee_api_key", "test-key")
    ref = tmp_path / "frame.png"
    ref.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20)

    with (
        patch.object(oh, "ensure_public_image_url", side_effect=lambda u: None),
        pytest.raises(oh.OutseeApiError, match="image_url не получен"),
    ):
        await oh.generate_video(
            "sky",
            tmp_path / "v.mp4",
            model_slug="veo-3-1-lite",
            reference_image=ref,
        )


@pytest.mark.asyncio
async def test_veo_generate_video_accepts_str_path_ref(
    tmp_path: Path, monkeypatch
) -> None:
    from app.bots import outsee_http as oh

    monkeypatch.setattr(oh.settings, "outsee_api_key", "test-key")
    ref = tmp_path / "frame.png"
    ref.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20)
    captured: dict = {}

    async def fake_post(path: str, body: dict):
        captured["body"] = body
        return {"id": 1, "status": "queued"}

    async def fake_poll(gen_id, *, timeout):
        return {"status": "completed", "result_url": "https://example.com/x.mp4"}

    async def fake_dl(url: str, out_path: Path):
        out_path.write_bytes(b"\x00" * 2_500_000)
        return out_path

    async def fake_host(url):
        if not url:
            return None
        assert str(url).startswith("data:")
        return "https://cdn.example/frame.png"

    async def fake_pp(path, *, duration, generate_audio):
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
            tmp_path / "v.mp4",
            model_slug="veo-3-1-lite",
            reference_image=str(ref),
        )

    assert captured["body"]["image_url"] == "https://cdn.example/frame.png"


@pytest.mark.asyncio
async def test_veo_generate_video_defaults_to_silent(tmp_path: Path, monkeypatch) -> None:
    """Pipeline/API: без явного True клип всегда generate_audio=false + mute pp."""
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
        out_path.write_bytes(b"\x00" * 2_500_000)
        return out_path

    async def fake_pp(path, *, duration, generate_audio):
        captured["pp"] = {"duration": duration, "audio": generate_audio}
        return path

    with (
        patch.object(oh, "_post_generate", side_effect=fake_post),
        patch.object(oh, "_poll_generation", side_effect=fake_poll),
        patch.object(oh, "_download", side_effect=fake_dl),
        patch.object(oh, "ensure_public_image_url", side_effect=lambda u: u),
        patch.object(oh, "postprocess_veo_mp4", side_effect=fake_pp),
    ):
        await oh.generate_video(
            "sky",
            out,
            model_slug="veo-3-1-lite",
            aspect_ratio="9:16",
            duration=8,
            generate_audio=True,  # даже явный True — форсим off
        )

    assert captured["body"]["generate_audio"] is False
    assert "Silent video only" in captured["body"]["prompt"]
    assert captured["pp"]["audio"] is False


def test_assert_video_not_mush_rejects_tiny_file(tmp_path: Path) -> None:
    from app.bots import outsee_http as oh

    tiny = tmp_path / "soft.mp4"
    tiny.write_bytes(b"\x00" * 800_000)
    with pytest.raises(oh.OutseeApiError, match="слишком лёгкое"):
        oh._assert_video_not_mush(tiny, duration_sec=8)
    ok = tmp_path / "ok.mp4"
    ok.write_bytes(b"\x00" * 2_500_000)
    oh._assert_video_not_mush(ok, duration_sec=8)
