"""img_streams 0..4 + claim batch for parallel image gen."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app import settings as app_settings
from app.models import Frame, FrameStatus, Project, ProjectStatus
from app.services.img_streams import (
    INFLIGHT_ATTR,
    clamp_img_streams,
    get_img_streams,
    set_img_streams_meta,
)
from app.orchestrator.steps import generate_images as gi


def test_clamp_img_streams() -> None:
    assert clamp_img_streams(-1) == 0
    assert clamp_img_streams(0) == 0
    assert clamp_img_streams(1) == 1
    assert clamp_img_streams(4) == 4
    assert clamp_img_streams(9) == 4
    assert clamp_img_streams("3") == 3
    assert clamp_img_streams("x") == 1


def test_outsee_alias_reads_same_meta() -> None:
    from app.services.img_streams import get_outsee_streams, set_img_streams_meta

    p = Project(topic="t", slug="s", status=ProjectStatus.new, meta={})
    set_img_streams_meta(p, 3)
    assert get_outsee_streams(p) == 3
    assert p.meta.get("outsee_streams") == 3
    assert get_img_streams(p) == 3


def test_get_img_streams_from_meta(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_settings.settings, "img_max_streams", 1)
    p = Project(slug="s", topic="t", status=ProjectStatus.generating_images, meta={})
    assert get_img_streams(p) == 1
    set_img_streams_meta(p, 3)
    assert get_img_streams(p) == 3
    set_img_streams_meta(p, 99)
    assert get_img_streams(p) == 4


def test_claim_shot1_batch_marks_inflight(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from unittest.mock import MagicMock

    monkeypatch.setattr(app_settings.settings, "data_dir", tmp_path / "data")
    out = tmp_path / "scenes"
    out.mkdir()
    p = Project(slug="c", topic="t", status=ProjectStatus.generating_images, meta={})
    p.id = 1
    frames = [
        Frame(
            id=10,
            project_id=1,
            number=1,
            image_prompt="style watercolor archival noir scene one",
            status=FrameStatus.image_prompt_ready,
            attrs={},
        ),
        Frame(
            id=11,
            project_id=1,
            number=2,
            image_prompt="style watercolor archival noir scene two",
            status=FrameStatus.image_prompt_ready,
            attrs={},
        ),
    ]

    class _R:
        def scalars(self):
            return MagicMock(all=lambda: frames)

    class _Sess:
        async def execute(self, *_a, **_k):
            return _R()

        async def flush(self):
            return None

    monkeypatch.setattr(gi, "frame_needs_shot1_image", lambda fr, d: True)
    monkeypatch.setattr(
        "app.services.vision_check_loop.scene_regen_allows",
        lambda *_a, **_k: None,
    )

    batch = asyncio.run(
        gi._claim_shot1_batch(_Sess(), 1, out, project=p, limit=2)
    )
    assert len(batch) == 2
    assert all((f.attrs or {}).get(INFLIGHT_ATTR) for f in batch)

    batch2 = asyncio.run(
        gi._claim_shot1_batch(_Sess(), 1, out, project=p, limit=2)
    )
    assert batch2 == []
