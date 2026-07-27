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
    assert oh.studio_id_to_outsee_image_slug("nano-banana-pro") == "nano-banana-pro"
    assert oh.studio_id_to_outsee_image_slug("unknown-xyz") == "gpt-image-2"


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
