"""Montage image regen: history recover after download failure."""

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
        prompt_id_prefix="P1-F1-S1-abc",
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

    with (
        patch(
            "app.services.outsee_lane.outsee_lane",
            MagicMock(return_value=AsyncMock(
                __aenter__=AsyncMock(return_value=None),
                __aexit__=AsyncMock(return_value=None),
            )),
        ),
        patch(
            "app.bots.outsee.find_img_src_by_prompt_id_in_gallery",
            AsyncMock(
                return_value=(
                    "https://storage.yandexcloud.net/outseehistory/generated/"
                    "1/2/image_9_0_thumb.jpg"
                )
            ),
        ),
        patch(
            "app.bots.outsee._download_via_card_click",
            AsyncMock(side_effect=fake_download),
        ),
    ):
        got = await _recover_montage_image_from_history(outsee, prep)

    assert got == dest
    assert dest.is_file()
    assert dest.stat().st_size >= 200_000


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
        prompt_id_prefix="P1-F2-S1-xyz",
        aspect_slug="9:16",
        model_slug=None,
        res_slug=None,
        quality_slug=None,
        image_relax=False,
    )

    class Boom(Exception):
        pass

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
            AsyncMock(return_value=dest),
        ) as recover,
    ):
        dest.write_bytes(b"\x89PNG\r\n\x1a\n" + b"z" * 210_000)
        session = MagicMock()
        bs_cm.return_value.__aenter__ = AsyncMock(return_value=session)
        bs_cm.return_value.__aexit__ = AsyncMock(return_value=None)
        with patch("app.services.montage_board_regen.OutseeBot"), patch(
            "app.services.montage_board_regen.ChatGPTBot"
        ):
            # File already ready — should return without needing recover
            # Clear file to force recover path
            dest.unlink()
            recover.return_value = tmp_path / "recovered.png"
            recover.return_value.write_bytes(
                b"\x89PNG\r\n\x1a\n" + b"z" * 210_000
            )
            out = await execute_image_regen(prep)

    assert out == recover.return_value
    recover.assert_awaited_once()
