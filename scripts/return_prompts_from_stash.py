#!/usr/bin/env python3
"""Keep user prompts across Studio git update (stash + reset --hard).

Runtime source of truth stays ``prompts/`` (no overlay).
Protection layers:
1) aside backup outside the repo (LOCALAPPDATA/TEMP) before reset
2) return dirty prompts/* from the Studio git stash after reset
3) safe scan of all Studio stashes on start (idempotent, no blocking stamp)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


MANIFEST_NAME = "_vp_aside_manifest.json"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    # Windows: default text mode uses cp1251 and dies on UTF-8 stash messages
    # («автосохранение…») → stdout=None → AttributeError on .splitlines().
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "core.quotepath=false", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _git_bytes(repo: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "core.quotepath=false", *args],
        capture_output=True,
        check=False,
    )


def _stdout_lines(proc: subprocess.CompletedProcess[str]) -> list[str]:
    """Safe lines from git text output (stdout may be None after decode errors)."""
    raw = proc.stdout
    if raw is None:
        return []
    return raw.splitlines()


def _unquote_git_path(raw: str) -> str:
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


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def stash_has_ref(repo: Path, stash_ref: str) -> bool:
    return _git(repo, "rev-parse", "--verify", stash_ref).returncode == 0


def list_prompt_paths_in_stash(repo: Path, stash_ref: str) -> tuple[list[str], list[str]]:
    tracked: list[str] = []
    show = _git(repo, "stash", "show", "--name-only", stash_ref)
    if show.returncode == 0:
        for line in _stdout_lines(show):
            rel = _unquote_git_path(line)
            if rel.startswith("prompts/"):
                tracked.append(rel)

    untracked: list[str] = []
    if _git(repo, "rev-parse", "--verify", f"{stash_ref}^3").returncode == 0:
        ls = _git(repo, "ls-tree", "-r", "--name-only", f"{stash_ref}^3", "--", "prompts")
        if ls.returncode == 0:
            for line in _stdout_lines(ls):
                rel = _unquote_git_path(line)
                if rel.startswith("prompts/"):
                    untracked.append(rel)
    return tracked, untracked


def list_studio_stash_refs(repo: Path, *, limit: int = 30) -> list[str]:
    listed = _git(repo, "stash", "list", "--format=%gd|%s")
    if listed.returncode != 0:
        return []
    studio: list[str] = []
    others: list[str] = []
    for line in _stdout_lines(listed)[:limit]:
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
    if head is not None and disk != head:
        return False
    return True


def return_prompts_from_stash(
    repo: Path,
    stash_ref: str = "stash@{0}",
    *,
    safe: bool = False,
) -> dict[str, object]:
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
            errors.append(f"{rel}: {(r.stderr or '').strip() or 'checkout failed'}")

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
            key = str(rel)
            if key in seen:
                continue
            seen.add(key)
            report["restored"].append(rel)
        report["skipped"].extend(list(one.get("skipped") or []))
        report["errors"].extend(list(one.get("errors") or []))
        if not one.get("ok", True):
            report["ok"] = False
    return report


def aside_dir_for_repo(repo: Path) -> Path:
    """Backup location OUTSIDE the git repo (survives reset --hard)."""
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("TMPDIR") or os.environ.get("TMP")
    if not base:
        base = tempfile.gettempdir()
    # Isolate by repo path hash so multiple installs do not clash.
    key = _sha256_bytes(str(repo.resolve()).encode("utf-8"))[:12]
    return Path(base) / "video-pipeline" / f"prompts_aside_{key}"


def _iter_prompt_files(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    out: list[Path] = []
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root)
        if ".history" in rel.parts:
            continue
        if rel.name == MANIFEST_NAME:
            continue
        out.append(p)
    return out


def backup_prompts_aside(repo: Path, *, aside: Path | None = None) -> dict[str, object]:
    """Copy prompts/ + mark user vs HEAD stock into aside dir (outside repo)."""
    prompts = repo / "prompts"
    dest_root = (aside or aside_dir_for_repo(repo)).resolve()
    report: dict[str, object] = {
        "ok": False,
        "aside": str(dest_root),
        "files": 0,
        "user_files": 0,
    }
    if not prompts.is_dir():
        report["note"] = "no prompts/"
        report["ok"] = True
        return report

    if dest_root.exists():
        shutil.rmtree(dest_root, ignore_errors=True)
    dest_root.mkdir(parents=True, exist_ok=True)

    entries: dict[str, object] = {}
    files = _iter_prompt_files(prompts)
    for src in files:
        rel = src.relative_to(prompts).as_posix()
        repo_rel = f"prompts/{rel}"
        disk_hash = _sha256_file(src)
        head = _head_blob(repo, repo_rel)
        head_hash = _sha256_bytes(head) if head is not None else None
        is_user = head_hash is None or head_hash != disk_hash
        entries[rel] = {"sha256": disk_hash, "user": is_user}
        if is_user:
            report["user_files"] = int(report["user_files"]) + 1
        report["files"] = int(report["files"]) + 1
        target = dest_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)

    (dest_root / MANIFEST_NAME).write_text(
        json.dumps(
            {
                "version": 1,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "repo": str(repo.resolve()),
                "entries": entries,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    report["ok"] = True
    return report


def restore_prompts_from_aside(
    repo: Path,
    *,
    aside: Path | None = None,
    safe: bool = True,
) -> dict[str, object]:
    """Restore user prompt files from aside backup into prompts/."""
    dest_root = (aside or aside_dir_for_repo(repo)).resolve()
    report: dict[str, object] = {
        "ok": False,
        "aside": str(dest_root),
        "restored": [],
        "skipped": [],
        "errors": [],
    }
    if not dest_root.is_dir():
        report["ok"] = True
        report["note"] = "no aside backup"
        return report

    entries: dict[str, object] = {}
    manifest_path = dest_root / MANIFEST_NAME
    if manifest_path.is_file():
        try:
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
            entries = dict(raw.get("entries") or {})
        except (OSError, json.JSONDecodeError):
            entries = {}

    prompts = repo / "prompts"
    prompts.mkdir(parents=True, exist_ok=True)
    for src in _iter_prompt_files(dest_root):
        rel = src.relative_to(dest_root).as_posix()
        meta = entries.get(rel) or {}
        is_user = bool(meta.get("user", True)) if entries else True
        if entries and not is_user:
            report["skipped"].append(rel)
            continue
        try:
            content = src.read_bytes()
        except OSError as exc:
            report["errors"].append(f"{rel}: {exc}")
            continue
        repo_rel = f"prompts/{rel}"
        if safe and not _should_write_prompt(repo, repo_rel, content):
            report["skipped"].append(rel)
            continue
        target = prompts / rel
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            report["restored"].append(rel)
        except OSError as exc:
            report["errors"].append(f"{rel}: {exc}")

    report["ok"] = not report["errors"]
    return report


def recover_prompts_on_startup(repo: Path | None = None) -> dict[str, object]:
    """Idempotent safe recover: aside backup + all studio stashes.

    No blocking stamp — safe mode will not clobber newer local edits.
    """
    try:
        from app.project_root import find_project_root

        root = (repo or find_project_root()).resolve()
    except Exception:
        root = (repo or Path.cwd()).resolve()

    report: dict[str, object] = {"ok": True, "from_aside": None, "from_stash": None}
    try:
        report["from_aside"] = restore_prompts_from_aside(root, safe=True)
    except Exception as exc:  # noqa: BLE001
        report["from_aside"] = {"ok": False, "error": str(exc)}
        report["ok"] = False
    try:
        report["from_stash"] = return_prompts_from_all_studio_stashes(root, safe=True)
    except Exception as exc:  # noqa: BLE001
        report["from_stash"] = {"ok": False, "error": str(exc)}
        report["ok"] = False
    restored = list(report["from_aside"].get("restored") or []) + list(
        report["from_stash"].get("restored") or []
    )
    report["restored"] = restored
    return report


# Back-compat name used by app/main.py
def recover_prompts_on_startup_once(repo: Path | None = None) -> dict[str, object]:
    return recover_prompts_on_startup(repo)


def simulate_studio_update(repo: Path, *, branch_ref: str) -> dict[str, object]:
    """Full protect path: aside backup → stash → reset → stash return → aside restore."""
    aside = backup_prompts_aside(repo)
    status = _git(repo, "status", "--porcelain")
    stashed = False
    if (status.stdout or "").strip():
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
                "errors": [(push.stderr or "").strip() or "stash push failed"],
                "aside": aside,
            }
        stashed = True

    hard = _git(repo, "reset", "--hard", branch_ref)
    if hard.returncode != 0:
        return {
            "ok": False,
            "errors": [(hard.stderr or "").strip() or "reset failed"],
            "stashed": stashed,
            "aside": aside,
        }

    stash_restore: dict[str, object] = {"restored": []}
    if stashed:
        stash_restore = return_prompts_from_stash(repo, "stash@{0}", safe=False)
    aside_restore = restore_prompts_from_aside(repo, safe=True)
    restored = sorted(
        set(list(stash_restore.get("restored") or []) + [f"prompts/{r}" for r in (aside_restore.get("restored") or [])])
    )
    # normalize aside restored to prompts/ prefix for asserts
    return {
        "ok": bool(aside.get("ok")) and bool(stash_restore.get("ok", True)) and bool(aside_restore.get("ok")),
        "stashed": stashed,
        "aside": aside,
        "stash_restore": stash_restore,
        "aside_restore": aside_restore,
        "restored": restored,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--stash", default="stash@{0}")
    parser.add_argument("--all-studio", action="store_true")
    parser.add_argument("--startup-once", action="store_true", help="safe recover (aside+stash)")
    parser.add_argument("--backup-aside", action="store_true")
    parser.add_argument("--restore-aside", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    repo = args.repo.resolve()
    if args.backup_aside:
        report = backup_prompts_aside(repo)
    elif args.restore_aside:
        report = restore_prompts_from_aside(repo, safe=not args.force)
    elif args.startup_once:
        report = recover_prompts_on_startup(repo)
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
