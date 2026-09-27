"""Шлюз vp-verify не должен снова и снова требовать починить старую историю ветки."""
from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

HOOK = Path(__file__).resolve().parents[1] / ".cursor" / "hooks" / "verify_gate.py"


def _load():
    spec = importlib.util.spec_from_file_location("verify_gate_under_test", HOOK)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


vg = _load()


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _commit(cwd: Path, message: str) -> None:
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=test@example.com",
            "-c",
            "user.name=test",
            "commit",
            "-m",
            message,
        ],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


def test_sibling_checkout_is_outside_the_gate(tmp_path: Path) -> None:
    main = tmp_path / "video-pipeline"
    (main / ".worktrees" / "stream-1").mkdir(parents=True)
    sibling = tmp_path / "video-pipeline-strangepc-preview"
    sibling.mkdir()
    assert vg.include_worktree(main, main)
    assert vg.include_worktree(main, main / ".worktrees" / "stream-1")
    assert not vg.include_worktree(main, sibling)


def test_repeat_report_is_not_delivered_again() -> None:
    cache: dict = {}
    first = vg.deliver(cache, "same", "уже говорили", [])
    second = vg.deliver(cache, "same", "уже говорили", [])
    assert first == "уже говорили"
    assert second == ""


def test_branch_history_is_not_a_backlog(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    (repo / "bad.py").write_text("x = missing\n", encoding="utf-8")
    _git(repo, "add", "bad.py")
    _commit(repo, "old undefined name")
    cache: dict = {}
    files, base = vg.scope_files(repo, cache, None)
    assert files == set()
    assert base == "HEAD"
    (repo / "dirty.py").write_text("y = 1\n", encoding="utf-8")
    files, _base = vg.scope_files(repo, cache, None)
    assert files == {"dirty.py"}
    _git(repo, "add", "dirty.py")
    _commit(repo, "new work")
    files, base = vg.scope_files(repo, cache, None)
    assert "bad.py" not in files
    assert files == {"dirty.py"}
    assert base != "HEAD"
    vg.advance_mark(repo, cache)
    files, _base = vg.scope_files(repo, cache, None)
    assert files == set()


def test_save_cache_keeps_watermark(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(vg, "state_dir", lambda: tmp_path)
    cache = {
        "_marks": {"ts": 1, "roots": {"a": {"head": "abc"}}},
        "sig": {"report": "старое", "ts": 2},
    }
    vg.save_cache(cache)
    loaded = json.loads((tmp_path / "verify_cache.json").read_text(encoding="utf-8"))
    assert loaded["_marks"]["roots"]["a"]["head"] == "abc"
    assert loaded["sig"]["report"] == "старое"
