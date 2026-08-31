"""Unit-тесты Outsee Developer API client (без сети)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.bots import outsee_http as oh


def test_pick_result_url_prefers_list() -> None:
    url = oh._pick_result_url(
        {
            "status": "completed",
            "result_url": "https://cdn.example/a.jpg",
            "result_urls": ["https://cdn.example/a.png"],
        }
    )
    assert url.endswith(".png")


def test_pick_result_url_fallback() -> None:
    url = oh._pick_result_url(
        {"status": "done", "result_url": "https://cdn.example/v.mp4"}
    )
    assert url.endswith(".mp4")


def test_pick_result_url_missing() -> None:
    with pytest.raises(oh.OutseeApiError):
        oh._pick_result_url({"status": "completed"})


def test_studio_image_slug_mapping() -> None:
    assert oh.studio_id_to_outsee_image_slug("gpt_image_2") == "gpt-image-2"
    with pytest.raises(oh.OutseeApiError, match="Nano Banana Pro"):
        oh.studio_id_to_outsee_image_slug("nano-banana-pro")
    with pytest.raises(oh.OutseeApiError, match="Nano Banana Pro"):
        oh.studio_id_to_outsee_image_slug("nano_banana_pro")
    assert "nano-banana-pro" not in oh.OUTSEE_WIRED_IMAGE_MODELS
    assert oh.studio_id_to_outsee_image_slug("unknown-xyz") == "gpt-image-2"
    stripped = oh._strip_banned_outsee_image_models(
        {
            "models": [
                {"id": "gpt-image-2"},
                {"id": "nano-banana-pro"},
                {"slug": "nano-banana-pro-vt"},
                {"id": "nano-banana-2"},
            ]
        }
    )
    ids = [x.get("id") or x.get("slug") for x in stripped["models"]]
    assert "nano-banana-pro" not in ids
    assert "nano-banana-pro-vt" not in ids
    assert "gpt-image-2" in ids
    assert "nano-banana-2" in ids


@pytest.mark.asyncio
async def test_generate_image_rejects_nano_banana_pro(tmp_path: Path) -> None:
    with pytest.raises(oh.OutseeApiError, match="Nano Banana Pro"):
        await oh.generate_image(
            "x",
            tmp_path / "a.png",
            model_slug="nano-banana-pro",
        )


def test_studio_video_slug_mapping() -> None:
    assert oh.studio_id_to_outsee_video_slug("veo-3-fast") == "veo-3-1-lite"
    assert oh.studio_id_to_outsee_video_slug("veo-3-1-lite") == "veo-3-1-lite"


@pytest.mark.asyncio
async def test_post_generate_parses_id(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResp:
        status_code = 200

        def json(self):
            return {"id": 314764, "status": "queued"}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def post(self, *a, **k):
            return FakeResp()

    monkeypatch.setattr(oh.httpx, "AsyncClient", lambda **kw: FakeClient())
    monkeypatch.setattr(oh, "_headers", lambda: {"Authorization": "Bearer x"})
    data = await oh._post_generate("/api/v1/images/generate", {"prompt": "hi"})
    assert data["id"] == 314764


@pytest.mark.asyncio
async def test_post_generate_waits_on_concurrency_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}

    class LimitResp:
        status_code = 429
        text = "busy"

        def json(self):
            return {
                "error": {
                    "code": "concurrency_limit",
                    "message": "Достигнут лимит одновременных генераций (4).",
                    "active": 4,
                    "limit": 4,
                }
            }

    class OkResp:
        status_code = 200

        def json(self):
            return {"id": 99, "status": "queued"}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def post(self, *a, **k):
            calls["n"] += 1
            return LimitResp() if calls["n"] == 1 else OkResp()

    slept: list[float] = []

    async def fake_sleep(sec):
        slept.append(sec)

    monkeypatch.setattr(oh.httpx, "AsyncClient", lambda **kw: FakeClient())
    monkeypatch.setattr(oh, "_headers", lambda: {"Authorization": "Bearer x"})
    monkeypatch.setattr(oh.asyncio, "sleep", fake_sleep)
    data = await oh._post_generate("/api/v1/videos/generate", {"prompt": "hi"})
    assert data["id"] == 99
    assert calls["n"] == 2
    assert slept and slept[0] > 0


def test_outsee_http_enabled_follows_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(oh, "outsee_api_key", lambda: "outsee-test")
    assert oh.outsee_http_enabled() is True
    monkeypatch.setattr(oh, "outsee_api_key", lambda: "")
    assert oh.outsee_http_enabled() is False


def test_normalize_refs_path(tmp_path: Path) -> None:
    p = tmp_path / "a.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20)
    refs = oh._normalize_refs([p, "https://cdn.example/r.png"])
    assert refs is not None
    assert refs[0].startswith("data:image/png;base64,")
    assert refs[1].startswith("https://")
