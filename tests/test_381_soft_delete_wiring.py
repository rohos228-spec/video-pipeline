"""PR #381: montage.delete_frame / delete_child_frame — мягкое удаление без DELETE."""

from __future__ import annotations

import pytest

from app.services.action_layer import builtin_montage_more as m


class _NoDeleteCtx:
    """ctx.call падает на любом запросе: мягкое удаление не ходит в HTTP."""

    plan_id = "plan-test"
    step_index = 0
    operator = "tester"

    async def call(self, method, path, **kwargs):
        raise AssertionError(f"ctx.call не должен вызываться: {method} {path}")


def _before(frame_id: int, number: int) -> dict:
    return {
        "project_id": 1, "frame_id": frame_id, "number": number,
        "voiceover_text": "", "image_prompt": None, "animation_prompt": None,
        "meaning": None, "sort_key": None, "uuid": None, "attrs": {},
    }


def _patch_common(monkeypatch, marked: list):
    async def fake_remember(ctx, **kwargs):
        return "snap-5"

    async def fake_mark(project_id, number):
        marked.append((project_id, number))
        return True

    monkeypatch.setattr(m, "remember_soft_delete", fake_remember)
    monkeypatch.setattr(m, "mark_frame_soft_deleted", fake_mark)


@pytest.mark.asyncio
async def test_delete_frame_soft_without_http_delete(monkeypatch):
    marked: list = []
    _patch_common(monkeypatch, marked)

    async def fake_capture(project_id, frame_id):
        return _before(frame_id, 3)

    monkeypatch.setattr(m, "capture_frame", fake_capture)

    out = await m._delete_frame(_NoDeleteCtx(), {"project_id": 1, "frame_id": 7})

    assert marked == [(1, 3)]
    assert out == {"frame_id": 7, "number": 3, "deleted": 1, "promoted": None,
                   "soft": True, "snapshot_id": "snap-5"}


@pytest.mark.asyncio
async def test_delete_frame_missing_row_returns_deleted_0(monkeypatch):
    marked: list = []
    _patch_common(monkeypatch, marked)

    async def fake_capture(project_id, frame_id):
        return None

    monkeypatch.setattr(m, "capture_frame", fake_capture)

    out = await m._delete_frame(_NoDeleteCtx(), {"project_id": 1, "frame_id": 7})

    assert marked == []
    assert out["deleted"] == 0
    assert out["soft"] is True


@pytest.mark.asyncio
async def test_delete_child_soft_without_coverage_delete(monkeypatch):
    marked: list = []
    _patch_common(monkeypatch, marked)

    async def fake_capture(project_id, number):
        return _before(7, number)

    monkeypatch.setattr(m, "capture_frame_by_number", fake_capture)

    out = await m._delete_child(_NoDeleteCtx(), {"project_id": 1, "frame_number": 4})

    assert marked == [(1, 4)]
    assert out == {"ok": True, "deleted_frame_number": 4, "soft": True,
                   "snapshot_id": "snap-5"}
