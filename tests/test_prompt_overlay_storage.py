"""Тесты overlay-хранилища промтов (data/prompts/ + bundled prompts/)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.services.prompt_library import (
    STEP_FOLDERS,
    read_prompt,
    write_prompt,
)
from app.services.prompt_paths import (
    export_merged_prompts_snapshot,
    migrate_user_prompts_to_data,
    restore_prompts_from_stashes,
    seed_bundled_prompts_into_data,
)

from tests.conftest import patch_prompt_roots


@pytest.fixture
def prompt_dirs(tmp_path, monkeypatch):
    return patch_prompt_roots(monkeypatch, tmp_path, folders=("01_plan", "02_script", "04_hero"))


def test_read_user_overlay_over_bundled(prompt_dirs) -> None:
    bundled, user = prompt_dirs
    folder = STEP_FOLDERS["plan"]
    (bundled / folder).mkdir(parents=True, exist_ok=True)
    (user / folder).mkdir(parents=True, exist_ok=True)
    (bundled / folder / "default.md").write_text("bundled text", encoding="utf-8")
    (user / folder / "default.md").write_text("user text", encoding="utf-8")
    assert read_prompt("plan", "default") == "user text"


def test_write_goes_to_user_dir_only(prompt_dirs) -> None:
    bundled, user = prompt_dirs
    write_prompt("plan", "custom_v1", "# hello")
    rel = STEP_FOLDERS["plan"]
    assert (user / rel / "custom_v1.md").is_file()
    assert not (bundled / rel / "custom_v1.md").exists()
    assert read_prompt("plan", "custom_v1") == "# hello"


def test_migrate_copies_modified_from_bundled(prompt_dirs, monkeypatch) -> None:
    bundled, user = prompt_dirs
    folder = STEP_FOLDERS["script"]
    (bundled / folder).mkdir(parents=True, exist_ok=True)
    path = bundled / folder / "default.md"
    path.write_text("user edited script", encoding="utf-8")

    def _head(_repo_rel: str) -> bytes | None:
        return b"repo default script"

    monkeypatch.setattr(
        "app.services.prompt_paths._git_head_blob",
        _head,
    )
    stats = migrate_user_prompts_to_data()
    assert stats["copied"] == 1
    assert (user / folder / "default.md").read_text(encoding="utf-8") == "user edited script"


def test_migrate_skips_identical_to_repo_but_seeds_gap(prompt_dirs, monkeypatch) -> None:
    bundled, user = prompt_dirs
    folder = STEP_FOLDERS["plan"]
    (bundled / folder).mkdir(parents=True, exist_ok=True)
    content = b"same as repo"
    (bundled / folder / "default.md").write_bytes(content)

    monkeypatch.setattr(
        "app.services.prompt_paths._git_head_blob",
        lambda _rel: content,
    )
    stats = migrate_user_prompts_to_data()
    # identical-to-HEAD не считается «user copy», но seed добивает пробел
    assert stats["copied"] == 0
    assert stats["seeded"] >= 1
    assert (user / folder / "default.md").is_file()


def test_seed_fills_missing_without_clobber(prompt_dirs) -> None:
    bundled, user = prompt_dirs
    folder = STEP_FOLDERS["plan"]
    (bundled / folder / "stock.md").write_text("bundled stock", encoding="utf-8")
    (user / folder / "stock.md").write_text("user keeps", encoding="utf-8")
    (bundled / folder / "only_bundled.md").write_text("fill me", encoding="utf-8")
    stats = seed_bundled_prompts_into_data()
    assert stats["seeded"] >= 1
    assert (user / folder / "stock.md").read_text(encoding="utf-8") == "user keeps"
    assert (user / folder / "only_bundled.md").read_text(encoding="utf-8") == "fill me"


def test_export_merged_snapshot(prompt_dirs) -> None:
    bundled, user = prompt_dirs
    folder = STEP_FOLDERS["hero"]
    (bundled / folder).mkdir(parents=True, exist_ok=True)
    (user / folder).mkdir(parents=True, exist_ok=True)
    (bundled / folder / "default.md").write_text("bundled", encoding="utf-8")
    (user / folder / "default.md").write_text("user wins", encoding="utf-8")
    (user / folder / "extra.md").write_text("only user", encoding="utf-8")
    target = bundled.parent / "snapshot"
    export_merged_prompts_snapshot(target)
    assert (target / folder / "default.md").read_text(encoding="utf-8") == "user wins"
    assert (target / folder / "extra.md").read_text(encoding="utf-8") == "only user"


def test_git_stash_reset_cycle_preserves_user_prompts(tmp_path, monkeypatch) -> None:
    """V1: stash + reset --hard не теряет промты в data/prompts/."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "t@example.com"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "test"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    prompts = repo / "prompts" / "01_plan"
    prompts.mkdir(parents=True)
    (prompts / "default.md").write_text("repo default", encoding="utf-8")
    (repo / ".gitignore").write_text("data/\n", encoding="utf-8")
    (repo / "README.md").write_text("init", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

    data_prompts = repo / "data" / "prompts" / "01_plan"
    data_prompts.mkdir(parents=True)
    (data_prompts / "default.md").write_text("USER default", encoding="utf-8")
    (data_prompts / "my_new.md").write_text("brand new prompt", encoding="utf-8")

    (prompts / "default.md").write_text("tracked edit before stash", encoding="utf-8")
    subprocess.run(
        ["git", "stash", "push", "-u", "-m", "studio: автосохранение перед обновлением test"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "reset", "--hard", "HEAD"], cwd=repo, check=True, capture_output=True)

    assert (prompts / "default.md").read_text(encoding="utf-8") == "repo default"
    assert (data_prompts / "default.md").read_text(encoding="utf-8") == "USER default"
    assert (data_prompts / "my_new.md").read_text(encoding="utf-8") == "brand new prompt"


def test_restore_from_stash_to_user(prompt_dirs, monkeypatch, tmp_path) -> None:
    bundled, user = prompt_dirs
    repo = tmp_path / "gitrepo"
    repo.mkdir()
    monkeypatch.setattr("app.services.prompt_paths.REPO_ROOT", repo)

    def _fake_stashes() -> list[tuple[str, str]]:
        return [("stash@{0}", "studio: автосохранение перед обновлением 2026-01-01")]

    def _fake_extract(stash_ref: str) -> dict[str, bytes]:
        assert stash_ref == "stash@{0}"
        return {
            "01_plan/lost.md": b"recovered content",
        }

    monkeypatch.setattr(
        "app.services.prompt_paths.list_studio_update_stashes",
        _fake_stashes,
    )
    monkeypatch.setattr(
        "app.services.prompt_paths.extract_prompt_files_from_stash",
        _fake_extract,
    )
    monkeypatch.setattr("app.services.prompt_paths._stash_commit_time", lambda _r: 0.0)

    report = restore_prompts_from_stashes()
    assert report["files_restored"] == 1
    assert (user / "01_plan" / "lost.md").read_bytes() == b"recovered content"
