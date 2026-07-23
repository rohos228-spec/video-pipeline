"""Montage image regen: history recover uses working download path."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.montage_board_regen import (
    ImageRegenPrep,
    _recover_montage_image_from_history,
    execute_image_regen,
)


@pytest.mark.asyncio
async def test_recover_montage_image_from_history_downloads(tmp_path: Path) -> None:
    dest = tmp_path / "frame_001.png"
    prep = ImageRegenPrep(
        project_id=13,
        frame_number=1,
        shot=1,
        prompt_text="x",
        file_path=dest,
        refs=[],
        prompt_id_prefix="[ID: P13-F1-abcd1234]",
        gen_id="abcd1234deadbeef",
        aspect_slug="9:16",
        model_slug=None,
        res_slug=None,
        quality_slug=None,
        image_relax=False,
    )
    outsee = MagicMock()
    page = MagicMock()
    outsee.session.open_page = AsyncMock(return_value=page)

    async def fake_download(page, **kwargs):
        path = kwargs["out_path"]
        path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"y" * 210_000)
        return path

    with (
        patch(
            "app.services.outsee_lane.outsee_lane",
            MagicMock(
                return_value=AsyncMock(
                    __aenter__=AsyncMock(return_value=None),
                    __aexit__=AsyncMock(return_value=None),
                )
            ),
        ),
        patch(
            "app.bots.outsee.download_saved_image_by_prompt_id",
            AsyncMock(side_effect=fake_download),
        ) as dl,
    ):
        got = await _recover_montage_image_from_history(outsee, prep)

    assert got == dest
    assert dest.is_file()
    assert dest.stat().st_size >= 200_000
    # Без img_url — рабочий cascade, не verify-gate.
    assert dl.await_args.kwargs.get("prompt_id_prefix") == prep.prompt_id_prefix
    assert "img_url" not in dl.await_args.kwargs


@pytest.mark.asyncio
async def test_execute_image_regen_uses_history_on_download_fail(
    tmp_path: Path,
) -> None:
    dest = tmp_path / "frame_002.png"
    prep = ImageRegenPrep(
        project_id=13,
        frame_number=2,
        shot=1,
        prompt_text="prompt",
        file_path=dest,
        refs=[],
        prompt_id_prefix="[ID: P13-F2-abcd1234]",
        gen_id="abcd1234deadbeef",
        aspect_slug="9:16",
        model_slug=None,
        res_slug=None,
        quality_slug=None,
        image_relax=False,
    )

    class Boom(Exception):
        pass

    recovered = tmp_path / "recovered.png"
    recovered.write_bytes(b"\x89PNG\r\n\x1a\n" + b"z" * 210_000)

    with (
        patch(
            "app.services.montage_board_regen._ensure_cdp_ready",
            AsyncMock(),
        ),
        patch(
            "app.services.montage_board_regen.browser_session",
        ) as bs_cm,
        patch(
            "app.services.montage_board_regen.generate_image_with_retries",
            AsyncMock(side_effect=Boom("download failed")),
        ),
        patch(
            "app.services.montage_board_regen._recover_montage_image_from_history",
            AsyncMock(return_value=recovered),
        ) as recover,
        patch("app.services.montage_board_regen.OutseeBot"),
        patch("app.services.montage_board_regen.ChatGPTBot"),
    ):
        bs_cm.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        bs_cm.return_value.__aexit__ = AsyncMock(return_value=None)
        out = await execute_image_regen(prep)

    assert out == recovered
    recover.assert_awaited_once()


def test_download_saved_image_by_prompt_id_skips_url_verify() -> None:
    """Контракт: helper не передаёт img_url в card-click."""
    import inspect

    from app.bots.outsee import download_saved_image_by_prompt_id

    src = inspect.getsource(download_saved_image_by_prompt_id)
    assert "img_url" not in src.split("_download_via_card_click")[1].split(")")[0]
    assert "_wait_gallery_thumbs" in src
