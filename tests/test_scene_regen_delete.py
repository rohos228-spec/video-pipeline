"""Vision scene regen: удаляем все frame_NNN_*.png, не только newest."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models import FrameStatus
from app.services.vision_check_loop import _delete_scene_pngs


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


@pytest.mark.asyncio
async def test_delete_scene_pngs_removes_all_shot1(tmp_path: Path) -> None:
    scenes = tmp_path / "scenes"
    scenes.mkdir()
    old = scenes / "frame_009_aaaaaaa1.png"
    new = scenes / "frame_009_bbbbbbb2.png"
    s2 = scenes / "frame_009_s2_ccccccc3.png"
    for p in (old, new, s2):
        p.write_bytes(b"x" * 100)

    fr = SimpleNamespace(
        id=1,
        number=9,
        status=FrameStatus.image_generated,
        attrs={},
    )
    art_old = SimpleNamespace(
        id=10,
        path=str(old),
        meta={"shot": 1},
        frame_id=1,
    )
    art_new = SimpleNamespace(
        id=11,
        path=str(new),
        meta={"shot": 1},
        frame_id=1,
    )
    art_s2 = SimpleNamespace(
        id=12,
        path=str(s2),
        meta={"shot": 2},
        frame_id=1,
    )

    project = SimpleNamespace(id=1, data_dir=tmp_path, meta={})
    session = MagicMock()
    deleted: list[object] = []
    calls = {"n": 0}

    async def _execute(_stmt):  # noqa: ANN001
        calls["n"] += 1
        if calls["n"] == 1:
            return _FakeResult([fr])
        return _FakeResult([art_old, art_new, art_s2])

    session.execute = AsyncMock(side_effect=_execute)
    session.delete = MagicMock(side_effect=lambda a: deleted.append(a))
    session.flush = AsyncMock()

    removed = await _delete_scene_pngs(
        session, project, [{"number": 9, "shot": 1}]
    )

    assert not old.exists()
    assert not new.exists()
    assert s2.exists()
    assert removed >= 2
    assert fr.status is FrameStatus.image_prompt_ready
    assert art_old in deleted and art_new in deleted
    assert art_s2 not in deleted
