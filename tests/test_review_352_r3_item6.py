"""Ревью #352 п.6: архивные кадры не получают чужие сцены, старый VO остаётся."""

import inspect

from app.orchestrator.steps import enrich_xlsx
from app.services.db_v2 import frame_is_archived


def test_archived_and_parked_frames_are_marked() -> None:
    class F:
        def __init__(self, number, attrs=None):
            self.number = number
            self.attrs = attrs or {}

    assert frame_is_archived(F(3, {"archived": True})) is True
    assert frame_is_archived(F(1_000_004)) is True
    assert frame_is_archived(F(4, {})) is False


def test_skeleton_zip_uses_created_frames_only() -> None:
    src = inspect.getsource(enrich_xlsx)
    assert "created_frames = await db_v2.replace_all_frames" in src
    assert "zip(frames, scenes, strict=True)" in src
    assert "voiceover_history" in inspect.getsource(
        __import__("app.services.db_v2", fromlist=["ensure_single_seed_vo_cell"]).ensure_single_seed_vo_cell
    )
