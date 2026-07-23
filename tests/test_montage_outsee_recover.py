"""Тесты recover монтажа: strategy C + download_image_like_generate."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.montage_outsee_recover import (
    GalleryHit,
    collect_stub_prefixes,
    rebuild_prefix_from_filename,
    recover_before_regen_ops,
)


def test_rebuild_prefix_from_filename() -> None:
    p = Path("frame_007_a1b2c3d4.png")
    assert rebuild_prefix_from_filename(13, p) == "[ID: P13-F7-a1b2c3d4]"
    p2 = Path("frame_007_s2_a1b2c3d4.png")
    assert rebuild_prefix_from_filename(13, p2) == "[ID: P13-F7-a1b2c3d4]-S2"


def test_collect_stub_prefixes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.models import Project

    data = tmp_path / "data"
    monkeypatch.setattr("app.settings.settings.data_dir", str(data))
    p = Project(id=13, slug="n", topic="t", hero_mode="auto")
    scenes = p.data_dir / "scenes"
    scenes.mkdir(parents=True)
    stub = scenes / "frame_002_abcd1234.png"
    stub.write_bytes(b"x" * 1000)  # too small
    ready = scenes / "frame_003_ffffeeee.png"
    ready.write_bytes(b"y" * 250_000)
    stubs = collect_stub_prefixes(p)
    assert len(stubs) == 1
    assert stubs[0][0] == 2
    assert stubs[0][2].startswith("[ID: P13-F2-")


@pytest.mark.asyncio
async def test_recover_before_regen_ops_fills_gaps_only() -> None:
    from app.models import Project

    project = Project(id=13, slug="n", topic="t", hero_mode="auto")
    session = MagicMock()
    ops = [
        {"type": "image_regen_correction", "frame_number": 1, "shot": 1},
        {"type": "image_regen", "frame_number": 2, "shot": 1},
        {"type": "video_regen", "frame_number": 1, "shot": 1},
    ]
    with patch(
        "app.services.montage_outsee_recover.recover_montage_images_from_outsee",
        AsyncMock(
            return_value={
                "ok": True,
                "saved": [{"frame_number": 1, "shot": 1, "path": "/x.png"}],
                "saved_count": 1,
            }
        ),
    ) as recover:
        res = await recover_before_regen_ops(session, project, ops)
    assert recover.await_args.kwargs.get("force_replace") is False
    assert recover.await_args.kwargs.get("frame_filter") == {(1, 1), (2, 1)}
    assert res["removed_ops"] == 1
    remaining = res["remaining_ops"]
    assert len(remaining) == 2
    assert remaining[0]["frame_number"] == 2
    assert remaining[1]["type"] == "video_regen"


def test_gallery_hit_dataclass() -> None:
    h = GalleryHit(
        project_id=13,
        frame_number=1,
        shot=1,
        short_uuid="abcd1234",
        prompt_id_prefix="[ID: P13-F1-abcd1234]",
        img_src="https://x",
    )
    assert h.shot == 1
