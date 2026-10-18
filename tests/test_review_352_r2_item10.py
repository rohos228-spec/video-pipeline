"""Повторное ревью #352 №10: пустой кусок не оставляет шот без роли."""

from __future__ import annotations

from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_default_expand_sets_role_on_empty_child(monkeypatch) -> None:
    from app.services import vo_shot_expand as vse

    parent = SimpleNamespace(
        id=1,
        uuid="parent",
        voiceover_text="весь закадр ячейки здесь",
        attrs={},
        sort_key=1.0,
        number=1,
        start_ts=0.0,
        end_ts=4.0,
        duration_seconds=4.0,
    )
    child = SimpleNamespace(
        id=2,
        uuid="child",
        voiceover_text="",
        attrs={},
        sort_key=2.0,
        number=2,
        start_ts=9.0,
        end_ts=9.0,
        duration_seconds=None,
    )

    async def _insert(*_a, **_k):
        return child

    async def _renumber(*_a, **_k):
        return [parent, child]

    monkeypatch.setattr(vse, "already_subdivided", lambda frames: False)
    monkeypatch.setattr(vse, "insert_frame_after", _insert)
    monkeypatch.setattr(vse, "renumber_frames_by_sort_key", _renumber)
    monkeypatch.setattr(
        vse,
        "resolve_shot_plan",
        lambda *_a, **_k: (2, ["кусок родителя", ""]),
    )
    class _Session:
        async def flush(self) -> None:
            return None

    ordered, _report = await vse._expand_vo_cells_into_shots_default(
        _Session(), SimpleNamespace(id=3), [parent]
    )
    assert child in ordered
    cs = vse._cs(child)
    assert cs.get("role") == "shot"
    assert cs.get("parent_uuid") == "parent"
    assert not str(child.voiceover_text or "").strip()
    assert child.voiceover_text != parent.voiceover_text
