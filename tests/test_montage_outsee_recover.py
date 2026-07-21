"""Тесты скана/парсинга ID из истории Outsee для монтажа."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.montage_outsee_recover import (
    GalleryHit,
    _parse_ids_from_text,
    collect_stub_prefixes,
    rebuild_prefix_from_filename,
    recover_before_regen_ops,
    scan_gallery_hits_for_project,
)


def test_parse_ids_from_text_project_filter() -> None:
    text = (
        "foo [ID: P13-F3-abcdef12] bar "
        "[ID: P99-F1-11111111] "
        "[ID: P13-F5-deadbeef]-S2"
    )
    got = _parse_ids_from_text(text, project_id=13)
    assert {(f, s, h) for _, f, s, h in got} == {
        (3, 1, "abcdef12"),
        (5, 2, "deadbeef"),
    }


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
async def test_scan_gallery_hits_for_project() -> None:
    page = MagicMock()
    page.evaluate = AsyncMock(
        return_value=[
            {
                "src": "https://cdn/x_thumb.jpg",
                "text": "prompt [ID: P13-F4-aabbccdd] more",
            },
            {
                "src": "https://cdn/y_thumb.jpg",
                "text": "other project [ID: P1-F1-00000000]",
            },
        ]
    )
    hits = await scan_gallery_hits_for_project(page, 13, limit=80)
    assert len(hits) == 1
    assert hits[0].frame_number == 4
    assert hits[0].short_uuid == "aabbccdd"
    assert hits[0].img_src.endswith("x_thumb.jpg")


@pytest.mark.asyncio
async def test_recover_before_regen_ops_removes_saved() -> None:
    from app.models import Project

    project = Project(id=13, slug="n", topic="t", hero_mode="auto")
    session = MagicMock()
    ops = [
        {"type": "image_regen", "frame_number": 1, "shot": 1},
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
    ):
        res = await recover_before_regen_ops(session, project, ops)
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
