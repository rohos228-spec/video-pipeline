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


def test_get_img_streams_from_meta(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_settings.settings, "data_dir", tmp_path)
    monkeypatch.setattr(app_settings.settings, "img_max_streams", 1)
    p = Project(slug="s", topic="t", status=ProjectStatus.generating_images, meta={})
    assert get_img_streams(p) == 1
    set_img_streams_meta(p, 3)
    assert get_img_streams(p) == 3
    set_img_streams_meta(p, 99)
    assert get_img_streams(p) == 4


def test_get_img_streams_defaults_to_2_when_meta_and_env_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Code fallback is 2 when project meta has neither key and env is unset."""
    monkeypatch.setattr(app_settings.settings, "data_dir", tmp_path)
    monkeypatch.setattr(app_settings.settings, "img_max_streams", None)
    p = Project(slug="s", topic="t", status=ProjectStatus.generating_images, meta={})
    assert "img_streams" not in (p.meta or {})
    assert "outsee_streams" not in (p.meta or {})
    assert not (tmp_path / "runtime_streams.json").exists()
    assert get_img_streams(p) == 2
    assert not (tmp_path / "runtime_streams.json").exists()


def test_get_img_streams_keeps_existing_runtime_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Не перетирать runtime_streams.json, если default_outsee_streams уже задан."""
    monkeypatch.setattr(app_settings.settings, "data_dir", tmp_path)
    monkeypatch.setattr(app_settings.settings, "img_max_streams", None)
    from app.services.runtime_streams import save_runtime_streams

    save_runtime_streams({"default_outsee_streams": 3})
    before = (tmp_path / "runtime_streams.json").read_text(encoding="utf-8")
    p = Project(slug="s", topic="t", status=ProjectStatus.generating_images, meta={})
    assert get_img_streams(p) == 3
    assert (tmp_path / "runtime_streams.json").read_text(encoding="utf-8") == before


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


def test_claim_shot1_waits_for_cell_parent_png(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """K2 не в батч, пока PNG K1 этой ячейки нет на диске."""
    from unittest.mock import MagicMock

    monkeypatch.setattr(app_settings.settings, "data_dir", tmp_path / "data")
    out = tmp_path / "scenes"
    out.mkdir()
    p = Project(
        slug="c",
        topic="t",
        status=ProjectStatus.generating_images,
        meta={
            "canvas_graph": {
                "nodes": [
                    {
                        "id": "n_excel_gpt_fw_shots",
                        "data": {"groupId": "script_frames_qc"},
                    }
                ],
                "edges": [],
            }
        },
    )
    p.id = 1
    parent = Frame(
        id=10,
        project_id=1,
        number=13,
        uuid="parent-13",
        image_prompt="style watercolor archival noir vo parent k1",
        status=FrameStatus.image_prompt_ready,
        attrs={"camera_subdivide": {"role": "vo_parent", "shot_id": "5-K1"}},
    )
    child = Frame(
        id=11,
        project_id=1,
        number=14,
        uuid="child-14",
        image_prompt="style watercolor archival noir shot child k2",
        status=FrameStatus.image_prompt_ready,
        attrs={
            "camera_subdivide": {
                "role": "shot",
                "shot_id": "5-K2",
                "parent_uuid": "parent-13",
                "coverage_parent_id": "2-K1",
            }
        },
    )
    frames = [parent, child]

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
        gi._claim_shot1_batch(_Sess(), 1, out, project=p, limit=4)
    )
    assert [f.number for f in batch] == [13]
    assert (child.attrs or {}).get(INFLIGHT_ATTR) is None

    (out / "frame_013_parent.png").write_bytes(
        b"\x89PNG\r\n\x1a\n" + b"x" * 200_000
    )
    batch_ready = asyncio.run(
        gi._claim_shot1_batch(_Sess(), 1, out, project=p, limit=4)
    )
    assert [f.number for f in batch_ready] == [14]
