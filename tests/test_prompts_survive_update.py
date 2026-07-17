"""Brutal proofs that user prompts survive Studio update — including failure modes.

Previous tests only simulated the happy Python path. These also cover:
- aside backup outside the repo (stash totally broken)
- old updater (stash+reset, no return) then startup recover
- argv ``stash@{0}`` (PowerShell splat footgun)
- Cyrillic filenames
- startup recover without blocking stamp
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
HELPER = REPO / "scripts" / "return_prompts_from_stash.py"


def _load_helper():
    spec = importlib.util.spec_from_file_location("return_prompts_from_stash", HELPER)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "core.quotepath=false", *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _init_prompt_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "prompts" / "05_excel_gpt").mkdir(parents=True)
    (repo / "app").mkdir()
    (repo / "prompts" / "05_excel_gpt" / "a.md").write_text("stock A\n", encoding="utf-8")
    (repo / "prompts" / "05_excel_gpt" / "b.md").write_text("stock B\n", encoding="utf-8")
    (repo / "app" / "main.py").write_text("code1\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "base")
    (repo / "prompts" / "05_excel_gpt" / "b.md").write_text("stock B v2\n", encoding="utf-8")
    (repo / "app" / "main.py").write_text("code2\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "origin update")
    origin = _git(repo, "rev-parse", "HEAD").stdout.strip()
    _git(repo, "reset", "--hard", "HEAD~1")
    return repo, origin


def _assert_user_prompts(repo: Path) -> None:
    assert (repo / "prompts" / "05_excel_gpt" / "a.md").read_text(encoding="utf-8") == "USER custom A\n"
    assert (repo / "prompts" / "05_excel_gpt" / "custom.md").read_text(encoding="utf-8") == "USER new\n"
    assert (repo / "prompts" / "05_excel_gpt" / "мой промт.md").read_text(encoding="utf-8") == "UNICODE\n"


def _plant_user_prompts(repo: Path) -> None:
    (repo / "prompts" / "05_excel_gpt" / "a.md").write_text("USER custom A\n", encoding="utf-8")
    (repo / "prompts" / "05_excel_gpt" / "custom.md").write_text("USER new\n", encoding="utf-8")
    (repo / "prompts" / "05_excel_gpt" / "мой промт.md").write_text("UNICODE\n", encoding="utf-8")
    (repo / "app" / "main.py").write_text("local hack\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# 1) Full new update path (aside + stash + restore)
# ---------------------------------------------------------------------------
def test_1_full_protect_keeps_prompts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    helper = _load_helper()
    repo, origin = _init_prompt_repo(tmp_path)
    aside = tmp_path / "aside"
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "lad"))
    monkeypatch.setattr(helper, "aside_dir_for_repo", lambda _r: aside)
    _plant_user_prompts(repo)

    report = helper.simulate_studio_update(repo, branch_ref=origin)
    assert report.get("ok"), report
    _assert_user_prompts(repo)
    assert (repo / "prompts" / "05_excel_gpt" / "b.md").read_text(encoding="utf-8") == "stock B v2\n"
    assert (repo / "app" / "main.py").read_text(encoding="utf-8") == "code2\n"


# ---------------------------------------------------------------------------
# 2) Stash completely broken — aside alone must save prompts
# ---------------------------------------------------------------------------
def test_2_aside_alone_survives_when_stash_never_restored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    helper = _load_helper()
    repo, origin = _init_prompt_repo(tmp_path)
    aside = tmp_path / "aside_only"
    monkeypatch.setattr(helper, "aside_dir_for_repo", lambda _r: aside)
    _plant_user_prompts(repo)

    bak = helper.backup_prompts_aside(repo, aside=aside)
    assert bak["ok"] and bak["user_files"] >= 3
    # old buggy updater: stash + reset, NO return from stash
    _git(repo, "stash", "push", "-u", "-m", "studio: автосохранение перед обновлением")
    _git(repo, "reset", "--hard", origin)
    assert not (repo / "prompts" / "05_excel_gpt" / "custom.md").exists()

    rest = helper.restore_prompts_from_aside(repo, aside=aside, safe=True)
    assert rest["ok"], rest
    _assert_user_prompts(repo)


# ---------------------------------------------------------------------------
# 3) Old updater then startup recover (no blocking stamp)
# ---------------------------------------------------------------------------
def test_3_old_update_then_startup_recover(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    helper = _load_helper()
    repo, origin = _init_prompt_repo(tmp_path)
    aside = tmp_path / "aside3"
    monkeypatch.setattr(helper, "aside_dir_for_repo", lambda _r: aside)
    _plant_user_prompts(repo)

    # First "startup" with nothing to recover must NOT block later recover
    empty = helper.recover_prompts_on_startup(repo)
    assert empty.get("ok")

    _git(repo, "stash", "push", "-u", "-m", "studio: автосохранение перед обновлением")
    _git(repo, "reset", "--hard", origin)
    assert not (repo / "prompts" / "05_excel_gpt" / "custom.md").exists()

    again = helper.recover_prompts_on_startup(repo)
    assert again.get("ok"), again
    assert (repo / "prompts" / "05_excel_gpt" / "custom.md").is_file()
    _assert_user_prompts(repo)


# ---------------------------------------------------------------------------
# 4) CLI argv stash@{0} (exactly how Studio passes it)
# ---------------------------------------------------------------------------
def test_4_cli_stash_ref_with_braces(tmp_path: Path) -> None:
    repo, origin = _init_prompt_repo(tmp_path)
    _plant_user_prompts(repo)
    _git(repo, "stash", "push", "-u", "-m", "studio: автосохранение перед обновлением")
    _git(repo, "reset", "--hard", origin)

    proc = subprocess.run(
        [
            sys.executable,
            str(HELPER),
            "--repo",
            str(repo),
            "--stash",
            "stash@{0}",
            "--json",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    _assert_user_prompts(repo)


# ---------------------------------------------------------------------------
# 5) write_prompt upload path + update
# ---------------------------------------------------------------------------
def test_5_write_prompt_upload_survives_update(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    helper = _load_helper()
    repo, origin = _init_prompt_repo(tmp_path)
    aside = tmp_path / "aside5"
    monkeypatch.setattr(helper, "aside_dir_for_repo", lambda _r: aside)

    from app.services import prompt_library as plib

    monkeypatch.setattr(plib, "PROMPTS_ROOT", repo / "prompts")
    plib.write_prompt("excel_gpt", "uploaded_by_user", "UPLOAD BODY\n")
    path = repo / "prompts" / "05_excel_gpt" / "uploaded_by_user.md"
    assert path.read_text(encoding="utf-8") == "UPLOAD BODY\n"

    report = helper.simulate_studio_update(repo, branch_ref=origin)
    assert report.get("ok"), report
    assert path.is_file()
    assert path.read_text(encoding="utf-8") == "UPLOAD BODY\n"


# ---------------------------------------------------------------------------
# 6) Scripts on main wired for aside + python fallbacks
# ---------------------------------------------------------------------------
def test_6_studio_scripts_wire_aside_and_python_fallback() -> None:
    studio = (REPO / "scripts" / "studio.ps1").read_text(encoding="utf-8")
    assert "Invoke-StudioBackupPromptsAside" in studio
    assert "Invoke-StudioRestorePromptsAside" in studio
    assert "Get-StudioPython" in studio
    assert "--backup-aside" in studio
    assert "--restore-aside" in studio
    assert "stash@{0}" in studio
    assert "prompts_preserve" not in studio
    assert (REPO / "RECOVER-PROMPTS.cmd").is_file()
    helper = HELPER.read_text(encoding="utf-8")
    assert "backup_prompts_aside" in helper
    assert "LOCALAPPDATA" in helper
