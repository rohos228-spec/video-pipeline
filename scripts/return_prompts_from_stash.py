#!/usr/bin/env python3
"""Return dirty prompts/* paths from a git stash after reset --hard.

Used by Studio update scripts and by tests. Not an overlay / data/ migration:
source of truth stays ``prompts/``; this only finishes the stash cycle for
prompt paths that were local before the update.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
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


def _git_bytes(repo: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "core.quotepath=false", *args],
        capture_output=True,
        check=False,
    )


def _unquote_git_path(raw: str) -> str:
    """Decode git C-quoted path if quotepath was left on."""
    s = raw.strip().replace("\\", "/")
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
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


def list_studio_stash_refs(repo: Path, *, limit: int = 20) -> list[str]:
    """Newest-first stash refs created by Studio update (or any with prompts/)."""
    listed = _git(repo, "stash", "list", "--format=%gd|%s")
    if listed.returncode != 0:
        return []
    studio: list[str] = []
    others: list[str] = []
    for line in listed.stdout.splitlines()[:limit]:
        if "|" not in line:
            continue
        ref, msg = line.split("|", 1)
        ref, msg = ref.strip(), msg.strip().lower()
        if not ref:
            continue
        if "studio" in msg or "автосохранение" in msg or "pre-update" in msg:
            studio.append(ref)
        else:
            others.append(ref)
    return studio or others


def _blob_from_stash(repo: Path, stash_ref: str, rel: str, *, untracked: bool) -> bytes | None:
    tree = f"{stash_ref}^3" if untracked else stash_ref
    raw = _git_bytes(repo, "show", f"{tree}:{rel}")
    if raw.returncode != 0:
        if not untracked:
            # try untracked tree as fallback
            raw = _git_bytes(repo, "show", f"{stash_ref}^3:{rel}")
        if raw.returncode != 0:
            return None
    return raw.stdout


def _head_blob(repo: Path, rel: str) -> bytes | None:
    raw = _git_bytes(repo, "show", f"HEAD:{rel}")
    if raw.returncode != 0:
        return None
    return raw.stdout


def _should_write_prompt(repo: Path, rel: str, content: bytes) -> bool:
    """Write if missing, or disk still equals stock HEAD (post-reset), or content newer."""
    dest = repo / rel
    if not dest.is_file():
        return True
    try:
        disk = dest.read_bytes()
    except OSError:
        return True
    if disk == content:
        return False
    head = _head_blob(repo, rel)
    # Disk already has non-stock edits — do not clobber.
    if head is not None and disk != head:
        return False
    return True


def return_prompts_from_stash(
    repo: Path,
    stash_ref: str = "stash@{0}",
    *,
    safe: bool = False,
) -> dict[str, object]:
    """Checkout dirty prompts/* from stash into the working tree.

    safe=True: only restore missing files or files that still match HEAD stock
    (used on backend startup / RECOVER, so fresh local edits are not overwritten).
    """
    report: dict[str, object] = {
        "ok": False,
        "stash_ref": stash_ref,
        "restored": [],
        "skipped": [],
        "errors": [],
    }
    if not stash_has_ref(repo, stash_ref):
        report["ok"] = True
        report["note"] = "stash ref missing"
        return report

    tracked, untracked = list_prompt_paths_in_stash(repo, stash_ref)
    restored: list[str] = []
    skipped: list[str] = []
    errors: list[str] = []

    def _apply(rel: str, *, untracked_flag: bool) -> None:
        if safe:
            blob = _blob_from_stash(repo, stash_ref, rel, untracked=untracked_flag)
            if blob is None:
                errors.append(f"{rel}: show failed")
                return
            if not _should_write_prompt(repo, rel, blob):
                skipped.append(rel)
                return
            dest = repo / rel
            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(blob)
                restored.append(rel)
            except OSError as exc:
                errors.append(f"{rel}: {exc}")
            return
        tree_ref = f"{stash_ref}^3" if untracked_flag else stash_ref
        r = _git(repo, "checkout", tree_ref, "--", rel)
        if r.returncode == 0:
            restored.append(rel)
        else:
            errors.append(f"{rel}: {r.stderr.strip() or 'checkout failed'}")

    for rel in tracked:
        _apply(rel, untracked_flag=False)
    for rel in untracked:
        _apply(rel, untracked_flag=True)

    report["restored"] = restored
    report["skipped"] = skipped
    report["errors"] = errors
    report["ok"] = not errors
    return report


def return_prompts_from_all_studio_stashes(
    repo: Path,
    *,
    safe: bool = True,
) -> dict[str, object]:
    """Walk Studio stashes newest→oldest and restore prompts (safe by default)."""
    refs = list_studio_stash_refs(repo)
    report: dict[str, object] = {
        "ok": True,
        "stashes": refs,
        "restored": [],
        "skipped": [],
        "errors": [],
    }
    if not refs:
        report["note"] = "no stashes"
        return report
    seen: set[str] = set()
    for ref in refs:
        one = return_prompts_from_stash(repo, ref, safe=safe)
        for rel in one.get("restored") or []:
            if rel in seen:
                continue
            seen.add(str(rel))
            report["restored"].append(rel)
        report["skipped"].extend(list(one.get("skipped") or []))
        report["errors"].extend(list(one.get("errors") or []))
        if not one.get("ok", True):
            report["ok"] = False
    return report


def recover_prompts_on_startup_once(repo: Path | None = None) -> dict[str, object]:
    """One-shot after update: pull prompts out of Studio stashes if still missing.

    Stamp: ``data/.prompts_studio_stash_recover_done`` — does not re-run forever.
    Safe mode: never overwrites non-stock local files.
    """
    from app.project_root import find_project_root
    from app.settings import settings

    root = (repo or find_project_root()).resolve()
    stamp = Path(settings.data_dir) / ".prompts_studio_stash_recover_done"
    if stamp.is_file():
        return {"ok": True, "note": "already recovered once", "stamp": str(stamp)}

    report = return_prompts_from_all_studio_stashes(root, safe=True)
    try:
        stamp.parent.mkdir(parents=True, exist_ok=True)
        stamp.write_text(
            json.dumps(
                {
                    "at": datetime.now(timezone.utc).isoformat(),
                    "restored": report.get("restored"),
                    "stashes": report.get("stashes"),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        report["stamp_error"] = str(exc)
    report["stamp"] = str(stamp)
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
    parser.add_argument(
        "--all-studio",
        action="store_true",
        help="Scan all Studio stashes (safe restore)",
    )
    parser.add_argument(
        "--startup-once",
        action="store_true",
        help="One-shot recover with data/ stamp",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="With --all-studio: overwrite even non-stock disk files",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    repo = args.repo.resolve()
    if args.startup_once:
        report = recover_prompts_on_startup_once(repo)
    elif args.all_studio:
        report = return_prompts_from_all_studio_stashes(repo, safe=not args.force)
    else:
        report = return_prompts_from_stash(repo, args.stash, safe=False)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(report)
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
