"""PR #388: frame_tombstones делегирует activity_flags, импорт и restore не оживляют/не теряют пометки."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.orchestrator.validators import activity_flags as af
from app.services import frame_tombstones as ft
from app.services import vo_shot_expand as vse

CASES = [
    {},
    {"активен": True},
    {"активен": False},
    {"активен": None},
    {"активен": "ложь"},
    {"активен": "0"},
    {"активен": "0,0"},
    {"карантин": True},
    {"карантин": "да"},
    {"карантин": "нет"},
    {"удалён": "0.0"},
    {"удалён": "1"},
    {"удалён": "ложь"},
]


@pytest.mark.parametrize("attrs", CASES)
def test_frame_is_active_matches_activity_flags(attrs):
    expected = af.frame_is_active(attrs)
    assert ft.frame_is_active(SimpleNamespace(attrs=attrs)) is expected
    assert ft.frame_is_active(attrs) is expected


@pytest.mark.parametrize("shot", CASES)
def test_shot_is_active_matches_activity_flags(shot):
    assert ft.shot_is_active(shot) is af.shot_is_active(shot)


def test_quarantined_frame_skips_paid_generation():
    frame = SimpleNamespace(number=3, attrs={"карантин": True})
    assert ft.frame_is_active(frame) is False
    assert ft.paid_frame_skip_reason(frame)


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, False), ("0", False), ("ложь", False), ("нет", False), ("да", True), (True, True)],
)
def test_frame_marked_deleted_uses_flag_is_true(value, expected):
    assert ft.frame_marked_deleted(SimpleNamespace(attrs={"удалён": value})) is expected


@pytest.mark.parametrize("attrs", [{"активен": False}, {"активен": "нет"}, {"карантин": True}])
def test_import_skips_inactive_frame(attrs):
    project = SimpleNamespace(meta={})
    frame = SimpleNamespace(number=7, attrs=dict(attrs))
    assert ft.import_skip_reason(project, frame, 7)
    assert ft.seal_import_skip(project, frame, 7)
    # Неактивный, но не удалённый кадр не попадает в tombstone.
    assert ft.deleted_numbers(project) == set()
    assert frame.attrs == attrs


def test_import_updates_active_frame():
    project = SimpleNamespace(meta={})
    frame = SimpleNamespace(number=7, attrs={"активен": True})
    assert ft.import_skip_reason(project, frame, 7) is None


def test_carry_hide_marks_copies_all_marks():
    old = {"активен": False, "удалён": True, "удален": True, "карантин": True, "пометка": "x"}
    new = {"текст": "a"}
    merged = ft.carry_hide_marks(old, new)
    assert merged == {**old, "текст": "a"}
    assert new == {"текст": "a"}
    assert af.shot_is_active(merged) is False


def test_carry_hide_marks_quarantine_only():
    merged = ft.carry_hide_marks({"карантин": "да"}, {"текст": "a"})
    assert merged == {"текст": "a", "карантин": "да"}
    assert af.shot_is_active(merged) is False


def test_carry_hide_marks_active_old_keeps_new():
    new = {"текст": "a", "активен": True}
    assert ft.carry_hide_marks({"активен": True, "пометка": "y"}, new) == new


@pytest.fixture
def no_flag(monkeypatch):
    monkeypatch.setattr(vse, "_flag_attrs", lambda *_a, **_k: None)


def test_archive_keeps_user_note_and_restore_clears_leftover(no_flag):
    fr = SimpleNamespace(id=1, number=5, uuid="u-5", attrs={"пометка": "моя заметка"})
    assert vse._archive_frames_as_leftover([fr]) == 1
    assert vse.is_coverage_leftover(fr)
    assert fr.attrs["пометка"] == "моя заметка; archived leftover — кадр сохранён"
    assert vse._clear_leftover_on_restore(fr) is True
    assert not vse.is_coverage_leftover(fr)
    assert fr.attrs["пометка"] == "моя заметка"
    assert "archived_leftover" not in fr.attrs


def test_restore_drops_archive_note_without_user_note(no_flag):
    fr = SimpleNamespace(id=2, number=6, uuid="u-6", attrs={})
    vse._archive_frames_as_leftover([fr])
    assert vse._clear_leftover_on_restore(fr) is True
    assert not vse.is_coverage_leftover(fr)
    assert "пометка" not in fr.attrs


def test_restore_keeps_foreign_leftover(no_flag):
    fr = SimpleNamespace(id=3, number=8, uuid="u-8", attrs={})
    vse._set_cs(fr, leftover=True)
    assert vse._clear_leftover_on_restore(fr) is False
    assert vse.is_coverage_leftover(fr)
