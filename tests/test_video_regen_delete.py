"""Vision video regen: удаляем все clip_NNN_*, не только newest."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models import ArtifactKind, FrameStatus
from app.services.vision_check_loop import _delete_video_clips


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


@pytest.mark.asyncio
async def test_delete_video_clips_removes_all_shot1(tmp_path: Path) -> None:
    videos = tmp_path / "videos"
    videos.mkdir()
    old = videos / "clip_009_aaaa.mp4"
    new = videos / "clip_009_bbbb.mp4"
    s2 = videos / "clip_009_s2_cccc.mp4"
    for p in (old, new, s2):
        p.write_bytes(b"fake")

    fr = SimpleNamespace(
        id=1,
        number=9,
        status=FrameStatus.video_generated,
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

    async def _execute(stmt):  # noqa: ANN001
        # first call: frames; later: artifacts for frame
        sql = str(stmt)
        if "frames" in sql.lower() or "Frame" in type(stmt).__name__:
            return _FakeResult([fr])
        return _FakeResult([art_old, art_new, art_s2])

    # sqlalchemy select() objects don't stringify reliably — sequence by call count
    calls = {"n": 0}

    async def _execute2(_stmt):  # noqa: ANN001
        calls["n"] += 1
        if calls["n"] == 1:
            return _FakeResult([fr])
        return _FakeResult([art_old, art_new, art_s2])

    session.execute = AsyncMock(side_effect=_execute2)
    session.delete = MagicMock(side_effect=lambda a: deleted.append(a))
    session.flush = AsyncMock()

    removed = await _delete_video_clips(
        session, project, [{"number": 9, "shot": 1}]
    )

    assert not old.exists()
    assert not new.exists()
    assert s2.exists()  # shot2 не трогаем
    assert removed >= 2
    assert fr.status is FrameStatus.animation_prompt_ready
    assert art_old in deleted and art_new in deleted
    assert art_s2 not in deleted
