"""Тесты авто-истории промтов."""

from __future__ import annotations

import json

import pytest

from app.services.prompt_active_global import get_global_active, set_global_active
from app.services.prompt_history import (
    _load_index,
    archive_prompt_version,
    bootstrap_saved_at_from_history,
    list_prompt_versions,
    rename_prompt_file,
    rename_prompt_version_label,
    restore_prompt_version,
    write_prompt_with_history,
)
from app.services.prompt_library import get_prompt_saved_at, read_prompt, write_prompt
from tests.conftest import patch_prompt_roots


@pytest.fixture
def plan_step(tmp_path, monkeypatch):
    patch_prompt_roots(monkeypatch, tmp_path, folders=("01_plan",))
    return "plan"


def test_write_prompt_archives_previous_content(plan_step):
    write_prompt(plan_step, "draft", "v1")
    write_prompt_with_history(plan_step, "draft", "v2")
    versions = list_prompt_versions(plan_step, "draft")
    assert len(versions) == 1
    assert read_prompt(plan_step, "draft") == "v2"


def test_write_prompt_does_not_set_global_active(plan_step):
    write_prompt(plan_step, "other", "x")
    set_global_active(plan_step, "other")
    write_prompt(plan_step, "draft", "new content")
    write_prompt_with_history(plan_step, "draft", "saved")
    assert get_global_active(plan_step) == "other"


def test_rebuild_index_from_history_snapshots(plan_step):
    from app.services.prompt_library import step_dir

    write_prompt(plan_step, "draft", "current")
    hdir = step_dir(plan_step) / ".history" / "draft"
    hdir.mkdir(parents=True)
    snap = hdir / "20260101T120000000000Z.md"
    snap.write_text("archived body", encoding="utf-8")
    bad_index = hdir / "index.json"
    bad_index.write_text("{not json", encoding="utf-8")
    idx = _load_index(plan_step, "draft")
    assert len(idx.get("versions") or []) == 1
    versions = list_prompt_versions(plan_step, "draft")
    assert len(versions) == 1
    assert versions[0]["id"] == "20260101T120000000000Z"


def test_bootstrap_saved_at_from_history(plan_step):
    from app.services.prompt_library import load_file_meta, step_dir

    write_prompt(plan_step, "draft", "v1")
    write_prompt_with_history(plan_step, "draft", "v2")
    meta = load_file_meta(plan_step)
    meta.pop("draft", None)
    meta_path = step_dir(plan_step) / ".file_meta.json"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    assert get_prompt_saved_at(plan_step, "draft") is None
    n = bootstrap_saved_at_from_history(plan_step)
    assert n == 1
    saved = get_prompt_saved_at(plan_step, "draft")
    assert saved is not None
    versions = list_prompt_versions(plan_step, "draft")
    assert abs(saved - float(versions[0]["saved_at"])) < 2


def test_archive_skips_empty_content(plan_step):
    write_prompt(plan_step, "draft", "keep")
    vid = archive_prompt_version(plan_step, "draft", "   ")
    assert vid is None
    assert list_prompt_versions(plan_step, "draft") == []


def test_rename_version_label(plan_step):
    write_prompt(plan_step, "draft", "v1")
    write_prompt_with_history(plan_step, "draft", "v2")
    versions = list_prompt_versions(plan_step, "draft")
    assert len(versions) == 1
    updated = rename_prompt_version_label(
        plan_step, "draft", versions[0]["id"], "Before rewrite"
    )
    assert updated["label"] == "Before rewrite"
    listed = list_prompt_versions(plan_step, "draft")
    assert listed[0]["label"] == "Before rewrite"


def test_restore_prompt_version(plan_step):
    write_prompt(plan_step, "draft", "old")
    write_prompt_with_history(plan_step, "draft", "new")
    versions = list_prompt_versions(plan_step, "draft")
    assert len(versions) == 1
    restore_prompt_version(plan_step, "draft", versions[0]["id"])
    assert read_prompt(plan_step, "draft") == "old"
    versions_after = list_prompt_versions(plan_step, "draft")
    assert len(versions_after) == 2


def test_rename_prompt_file_moves_history(plan_step):
    write_prompt(plan_step, "alpha", "text")
    write_prompt_with_history(plan_step, "alpha", "text2")
    rename_prompt_file(plan_step, "alpha", "beta")
    assert read_prompt(plan_step, "beta") == "text2"
    assert list_prompt_versions(plan_step, "beta")
    assert list_prompt_versions(plan_step, "alpha") == []


def test_rename_prompt_file_merges_history(plan_step):
    from app.services.prompt_library import prompt_path

    write_prompt(plan_step, "alpha", "a1")
    write_prompt_with_history(plan_step, "alpha", "a2")
    write_prompt(plan_step, "beta", "b1")
    write_prompt_with_history(plan_step, "beta", "b2")
    prompt_path(plan_step, "beta").unlink()
    rename_prompt_file(plan_step, "alpha", "beta")
    assert read_prompt(plan_step, "beta") == "a2"
    versions = list_prompt_versions(plan_step, "beta")
    assert len(versions) >= 2


def test_prompt_file_meta_stable_date(plan_step):
    import os
    import time

    from app.services.prompt_library import prompt_path

    write_prompt(plan_step, "draft", "hello")
    saved = get_prompt_saved_at(plan_step, "draft")
    assert saved is not None
    p = prompt_path(plan_step, "draft")
    os.utime(p, (time.time() + 3600, time.time() + 3600))
    assert abs(p.stat().st_mtime - saved) > 1
    assert get_prompt_saved_at(plan_step, "draft") == saved


def test_resolve_project_prompt_with_source_override(plan_step):
    from app.services.prompt_library import resolve_project_prompt_with_source

    write_prompt(plan_step, "custom", "x")
    name, source = resolve_project_prompt_with_source(
        {"plan": "custom"}, "plan", meta={}
    )
    assert name == "custom"
    assert source == "override"


def test_resolve_project_prompt_with_source_slot(plan_step):
    from app.services.prompt_library import resolve_project_prompt_with_source

    write_prompt(plan_step, "slot_v", "x")
    meta = {"prompt_slot_variants": {"n1": {"main": "slot_v"}}}
    name, source = resolve_project_prompt_with_source(
        {}, "plan", meta=meta, node_key="n1", slot_id="main"
    )
    assert name == "slot_v"
    assert source == "slot"


def test_api_modified_uses_meta_not_mtime(plan_step):
    import os
    import time
    from pathlib import Path

    from app.services.prompt_library import prompt_path, step_dir
    from app.web.routers.prompt_files import _prompt_modified

    write_prompt(plan_step, "draft", "hello")
    p = prompt_path(plan_step, "draft")
    os.utime(p, (time.time() + 7200, time.time() + 7200))
    saved = get_prompt_saved_at(plan_step, "draft")
    assert _prompt_modified(plan_step, "draft", p) == saved
    meta_path = step_dir(plan_step) / ".file_meta.json"
    if meta_path.is_file():
        meta_path.unlink()
    assert _prompt_modified(plan_step, "draft", p) is None

