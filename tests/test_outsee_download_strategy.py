"""Тесты стратегии скачивания outsee (URL-first vs card cascade)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.bots import outsee as os_mod

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_VALID_MP4 = b"\x00\x00\x00\x20ftypisom" + b"x" * 100_000


@pytest.mark.asyncio
async def test_video_download_url_first_skips_card_cascade(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out_path = tmp_path / "clip.mp4"
    video_url = "https://cdn.example.com/generated/clip.mp4?sig=1"
    called = {"card": 0, "context": 0}

    async def _fake_context(
        _page: object, _url: str, path: Path, **_kw: object
    ) -> None:
        called["context"] += 1
        path.write_bytes(_VALID_MP4)

    async def _fake_find_videos(*_a: object, **_k: object) -> object:
        called["card"] += 1
        raise AssertionError("card cascade must not run when URL download succeeds")

    monkeypatch.setattr(os_mod, "_download_via_context", _fake_context)
    monkeypatch.setattr(
        os_mod, "_find_card_by_clicking_videos", _fake_find_videos
    )
    monkeypatch.setattr(
        os_mod, "_wait_gallery_video_thumbs", AsyncMock(return_value=1)
    )
    monkeypatch.setattr(os_mod, "_update_download_progress", AsyncMock())
    monkeypatch.setattr(os_mod, "_log_download_stage", lambda **_kw: None)

    page = MagicMock()
    await os_mod._download_via_video_card_click(
        page,
        prompt_id_prefix="[ID: P1-F1-deadbeef]",
        out_path=out_path,
        video_url=video_url,
        project_id=42,
    )

    assert called["context"] == 1
    assert called["card"] == 0
    assert out_path.exists()


@pytest.mark.asyncio
async def test_image_download_url_first_skips_card_cascade(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out_path = tmp_path / "frame.png"
    img_url = (
        "https://storage.yandexcloud.net/outseehistory/generated/"
        "image_100_0_thumb.jpg?sig=1"
    )
    called = {"card": 0, "candidates": 0}

    async def _fake_candidates(
        _page: object, _url: str, path: Path, **_kw: object
    ) -> str:
        called["candidates"] += 1
        path.write_bytes(_PNG_MAGIC + b"x" * 210_000)
        return "https://storage.yandexcloud.net/x/image_100_0.png"

    async def _fake_find_images(*_a: object, **_k: object) -> object:
        called["card"] += 1
        raise AssertionError("card cascade must not run when URL download succeeds")

    monkeypatch.setattr(
        os_mod, "_download_via_context_candidates", _fake_candidates
    )
    monkeypatch.setattr(
        os_mod, "_find_card_by_clicking_images", _fake_find_images
    )
    monkeypatch.setattr(
        os_mod,
        "verify_img_url_matches_prompt_id_in_gallery",
        AsyncMock(),
    )
    monkeypatch.setattr(os_mod, "_composer_has_prompt_id", AsyncMock(return_value=False))
    monkeypatch.setattr(os_mod, "_find_full_png_in_dom", AsyncMock(return_value=None))
    monkeypatch.setattr(
        os_mod,
        "_resolve_best_download_url",
        lambda *_a, **_k: "https://storage.yandexcloud.net/x/image_100_0.png",
    )
    monkeypatch.setattr(os_mod, "_update_download_progress", AsyncMock())
    monkeypatch.setattr(os_mod, "_log_download_stage", lambda **_kw: None)

    page = MagicMock()
    await os_mod._download_via_card_click(
        page,
        prompt_id_prefix="[ID: P1-F1-cafebabe]",
        out_path=out_path,
        img_url=img_url,
        project_id=7,
    )

    assert called["candidates"] == 1
    assert called["card"] == 0
    assert out_path.exists()
