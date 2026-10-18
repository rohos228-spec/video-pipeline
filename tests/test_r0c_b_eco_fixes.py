"""Eco-фиксы R0c-B: единый предикат, перенос пометок, fail-closed платного regen."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.services import frame_tombstones as ft
from app.services.scene_design.chronology import _append_missing_kadry


def test_tombstones_predicate_follows_activity_flags_quarantine():
    assert ft.frame_is_active({"карантин": True}) is False
    assert ft.frame_is_active(SimpleNamespace(attrs={"удалён": "да"})) is False
    assert ft.frame_is_active({"активен": "false"}) is False
    assert ft.frame_is_active({}) is True
    assert ft.frame_marked_deleted({"удалён": "да"}) is True
    assert ft.shot_is_active({"id": "a", "активен": "нет"}) is False


def test_merge_carries_all_hide_marks_from_old_inactive_shot():
    old = [{"id": "a", "карантин": True, "активен": False, "пометка": "брак"}]
    new = [{"id": "a", "действие": "новое"}]
    merged = ft.merge_kadry_keep_missing(old, new)
    row = next(r for r in merged if r.get("id") == "a")
    assert row["действие"] == "новое"
    assert row["активен"] is False
    assert row["карантин"] is True
    assert row["пометка"] == "брак"


def test_merge_quarantined_old_shot_not_revived():
    old = [{"id": "a", "карантин": True}]
    merged = ft.merge_kadry_keep_missing(old, [{"id": "a"}])
    assert all(not ft.shot_is_active(r) for r in merged if r.get("id") == "a")


def test_chronology_restored_frame_not_sticky_inactive():
    previous = [{"uuid": "u1", "активен": False}]
    current = [{"uuid": "u1", "number": 1}]
    _append_missing_kadry(previous, current)
    assert current == [{"uuid": "u1", "number": 1}]


def test_chronology_missing_frame_kept_marked():
    previous = [{"uuid": "u1"}, {"uuid": "u2"}]
    current = [{"uuid": "u1"}]
    _append_missing_kadry(previous, current)
    assert [r["uuid"] for r in current] == ["u1", "u2"]
    assert current[1]["активен"] is False


def test_regen_paid_blocked_fails_closed_on_session_error(monkeypatch):
    from app.services import montage_board_apply, montage_board_regen

    def boom(_pid):
        raise RuntimeError("db down")

    monkeypatch.setattr(montage_board_apply, "session_scope", boom)
    blocked = asyncio.run(montage_board_regen._regen_paid_blocked(1, 3, where="t"))
    assert blocked is True
