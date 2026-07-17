#!/usr/bin/env python3
"""Return dirty prompts/* paths from a git stash after reset --hard.

Used by Studio update scripts and by tests. Not an overlay / data/ migration:
source of truth stays ``prompts/``; this only finishes the stash cycle for
prompt paths that were local before the update.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    # core.quotepath=false — иначе кириллица в именах приходит как "\320\274..."
    # и checkout по такому пути молча не находит файл.
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "core.quotepath=false", *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _unquote_git_path(raw: str) -> str:
    """Decode git C-quoted path if quotepath was left on."""
    s = raw.strip().replace("\\", "/")
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        # git quotes: "path/with \320\274"
        inner = s[1:-1]
        out = bytearray()
        i = 0
        while i < len(inner):
            if inner[i] == "\\" and i + 3 < len(inner) and inner[i + 1].isdigit():
                try:
                    out.append(int(inner[i + 1 : i + 4], 8))
                    i += 4
                    continue
                except ValueError:
                    pass
            if inner[i] == "\\" and i + 1 < len(inner):
                out.extend(inner[i + 1].encode("utf-8"))
                i += 2
                continue
            out.extend(inner[i].encode("utf-8"))
            i += 1
        try:
            return out.decode("utf-8")
        except UnicodeDecodeError:
            return inner
    return s


def stash_has_ref(repo: Path, stash_ref: str) -> bool:
    return _git(repo, "rev-parse", "--verify", stash_ref).returncode == 0


def list_prompt_paths_in_stash(repo: Path, stash_ref: str) -> tuple[list[str], list[str]]:
    """Return (tracked_prompt_paths, untracked_prompt_paths)."""
    tracked: list[str] = []
    show = _git(repo, "stash", "show", "--name-only", stash_ref)
    if show.returncode == 0:
        for line in show.stdout.splitlines():
            rel = _unquote_git_path(line)
            if rel.startswith("prompts/"):
                tracked.append(rel)

    untracked: list[str] = []
    if _git(repo, "rev-parse", "--verify", f"{stash_ref}^3").returncode == 0:
        ls = _git(repo, "ls-tree", "-r", "--name-only", f"{stash_ref}^3", "--", "prompts")
        if ls.returncode == 0:
            for line in ls.stdout.splitlines():
                rel = _unquote_git_path(line)
                if rel.startswith("prompts/"):
                    untracked.append(rel)
    return tracked, untracked


def return_prompts_from_stash(repo: Path, stash_ref: str = "stash@{0}") -> dict[str, object]:
    """Checkout dirty prompts/* from stash into the working tree."""
    report: dict[str, object] = {
        "ok": False,
        "stash_ref": stash_ref,
        "restored": [],
        "errors": [],
    }
    if not stash_has_ref(repo, stash_ref):
        report["ok"] = True
        report["note"] = "stash ref missing"
        return report

    tracked, untracked = list_prompt_paths_in_stash(repo, stash_ref)
    restored: list[str] = []
    errors: list[str] = []

    for rel in tracked:
        r = _git(repo, "checkout", stash_ref, "--", rel)
        if r.returncode == 0:
            restored.append(rel)
        else:
            errors.append(f"{rel}: {r.stderr.strip() or 'checkout failed'}")

    for rel in untracked:
        r = _git(repo, "checkout", f"{stash_ref}^3", "--", rel)
        if r.returncode == 0:
            restored.append(rel)
        else:
            errors.append(f"{rel}: {r.stderr.strip() or 'checkout failed'}")

    report["restored"] = restored
    report["errors"] = errors
    report["ok"] = not errors
    return report


def simulate_studio_update(repo: Path, *, branch_ref: str) -> dict[str, object]:
    """stash -u → reset --hard branch_ref → return prompts from stash@{0}."""
    status = _git(repo, "status", "--porcelain")
    stashed = False
    if status.stdout.strip():
        push = _git(
            repo,
            "stash",
            "push",
            "-u",
            "-m",
            "studio: автосохранение перед обновлением test",
        )
        if push.returncode != 0:
            return {
                "ok": False,
                "errors": [push.stderr.strip() or "stash push failed"],
                "stashed": False,
            }
        stashed = True

    hard = _git(repo, "reset", "--hard", branch_ref)
    if hard.returncode != 0:
        return {
            "ok": False,
            "errors": [hard.stderr.strip() or "reset failed"],
            "stashed": stashed,
        }

    if not stashed:
        return {"ok": True, "stashed": False, "restored": [], "note": "nothing to stash"}

    restore = return_prompts_from_stash(repo, "stash@{0}")
    restore["stashed"] = True
    return restore


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--stash", default="stash@{0}")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = return_prompts_from_stash(args.repo.resolve(), args.stash)
    if args.json:
        import json

        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(report)
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
