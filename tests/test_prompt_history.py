"""Тесты авто-истории промтов."""

from __future__ import annotations

import pytest

from app.services.prompt_history import (
    archive_prompt_version,
    list_prompt_versions,
    rename_prompt_file,
    rename_prompt_version_label,
    restore_prompt_version,
    write_prompt_with_history,
)
from app.services.prompt_library import read_prompt, write_prompt


@pytest.fixture
def plan_step(tmp_path, monkeypatch):
    root = tmp_path / "prompts" / "01_plan"
    root.mkdir(parents=True)
    prompts_root = tmp_path / "prompts"
    monkeypatch.setattr("app.services.prompt_library.PROMPTS_ROOT", prompts_root)
    return "plan"


def test_write_prompt_archives_previous_content(plan_step):
    write_prompt(plan_step, "draft", "v1")
    write_prompt_with_history(plan_step, "draft", "v2")
    versions = list_prompt_versions(plan_step, "draft")
    assert len(versions) == 1
    assert read_prompt(plan_step, "draft") == "v2"


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

    from app.services.prompt_library import get_prompt_saved_at, prompt_path

    write_prompt(plan_step, "draft", "hello")
    saved = get_prompt_saved_at(plan_step, "draft")
    assert saved is not None
    p = prompt_path(plan_step, "draft")
    os.utime(p, (time.time() + 3600, time.time() + 3600))
    assert abs(p.stat().st_mtime - saved) > 1
    assert get_prompt_saved_at(plan_step, "draft") == saved
