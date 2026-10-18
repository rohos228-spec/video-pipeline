"""PR #381: блокеры мягкого удаления/восстановления (чистые помощники, без БД)."""

from __future__ import annotations

import inspect

import pytest

from app.orchestrator.node_store.meta_book import (
    VersionConflict,
    checked_meta_version,
    load_book,
)
from app.services import artifact_verifier
from app.services.action_layer import soft_restore


def test_soft_deleted_attrs_marks_and_keeps_content():
    attrs = {"кадры": [{"id": "s1", "скрыт": True}], "x": 1}
    marked = soft_restore.soft_deleted_attrs(attrs)
    assert marked["удалён"] is True
    assert marked["активен"] is False
    assert marked["кадры"] == [{"id": "s1", "скрыт": True}]
    assert marked["x"] == 1
    assert "удалён" not in attrs


def test_soft_deleted_attrs_handles_empty():
    assert soft_restore.soft_deleted_attrs(None) == {"удалён": True, "активен": False}


def test_restored_attrs_merges_current_row_with_snapshot(monkeypatch):
    calls = []

    def fake_merge(current, incoming):
        calls.append((current, incoming))
        return [*current, *[s for s in incoming if s not in current]]

    monkeypatch.setattr(soft_restore, "merge_kadry_keep_missing", fake_merge)
    current = {
        "удалён": True,
        "активен": False,
        "карантин": True,
        "пометка": "x",
        "кадры": [{"id": "a"}, {"id": "new", "скрыт": True}],
        "keep": 1,
    }
    before = {"number": 3, "attrs": {"кадры": [{"id": "a"}, {"id": "old"}]}}
    attrs = soft_restore.restored_frame_attrs(current, before)
    assert calls == [(current["кадры"], before["attrs"]["кадры"])]
    for key in ("удалён", "активен", "карантин", "пометка"):
        assert key not in attrs
    assert attrs["keep"] == 1
    assert {"id": "new", "скрыт": True} in attrs["кадры"]
    assert {"id": "old"} in attrs["кадры"]
    assert current["удалён"] is True


def test_restored_attrs_without_row_uses_snapshot():
    before = {
        "number": 3,
        "attrs": {"удалён": True, "активен": False, "кадры": [{"id": "a"}], "y": 2},
    }
    attrs = soft_restore.restored_frame_attrs(None, before)
    assert attrs == {"кадры": [{"id": "a"}], "y": 2}


def test_soft_restore_has_no_physical_delete_or_conflict():
    src = inspect.getsource(soft_restore)
    assert "_drop_frame_rows" not in src
    assert "delete_montage_frame" not in src
    assert '409, "conflict"' not in src


def test_checked_meta_version_requires_key():
    book = load_book({"meta_version": 3})
    with pytest.raises(VersionConflict):
        checked_meta_version({}, book)


def test_checked_meta_version_returns_requested():
    book = load_book({"meta_version": 3})
    assert checked_meta_version({"expected_meta_version": 3}, book) == 3
    assert checked_meta_version({"expected_meta_version": "2"}, book) == 2


@pytest.mark.parametrize(
    "stderr",
    [
        b"",
        b"\n  \n",
        b"ffprobe: /opt/tmux/lib/libtinfo.so.6: no version information available "
        b"(required by /lib/x86_64-linux-gnu/libavdevice.so.58)\n",
        b"ffmpeg: /opt/tmux/lib/libncursesw.so.6: no version information available\n",
    ],
)
def test_probe_stderr_linker_warning_ignored(stderr):
    assert artifact_verifier.probe_stderr_blocks(stderr) is False


@pytest.mark.parametrize(
    "stderr",
    [
        b"[mov,mp4] moov atom not found\n",
        b"ffprobe: no version information available\n",
        b"ffprobe: /lib/a.so: no version information available; moov atom not found\n",
        b"ffprobe: /lib/a.so: no version information available (required by x) Invalid data\n",
        b"warning: /lib/a.so: no version information available\n",
        b"ffprobe: /lib/a.so: no version information available\nInvalid data found\n",
    ],
)
def test_probe_stderr_other_lines_block(stderr):
    assert artifact_verifier.probe_stderr_blocks(stderr) is True
