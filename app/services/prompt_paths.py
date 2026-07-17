"""Overlay-хранилище промтов: bundled `prompts/` (репо) + user `data/prompts/` (gitignored).

Чтение: сначала data/prompts/, затем prompts/.
Запись пользовательских файлов: только data/prompts/.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from loguru import logger

from app.project_root import find_project_root
from app.settings import settings

REPO_ROOT = find_project_root()
BUNDLED_PROMPTS_ROOT = REPO_ROOT / "prompts"

# Обратная совместимость: старый импорт PROMPTS_ROOT = bundled (только чтение дефолтов).
PROMPTS_ROOT = BUNDLED_PROMPTS_ROOT

_STASH_MSG_PREFIX = "studio: автосохранение перед обновлением"
# Доп. префиксы (hotfix / ручные stash) — тоже сканируем на prompts/.
_STASH_MSG_EXTRA = (
    "before-hotfix-pull-",
    "studio:",
    "автосохранение",
)


def user_prompts_root() -> Path:
    return settings.data_dir / "prompts"


def ensure_user_prompts_root() -> Path:
    root = user_prompts_root()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _is_history_path(rel: Path) -> bool:
    return ".history" in rel.parts


def resolve_prompt_file(*parts: str) -> Path | None:
    """Путь к существующему файлу (user overlay → bundled)."""
    rel = Path(*parts)
    if _is_history_path(rel):
        user = user_prompts_root() / rel
        if user.is_file():
            return user
        bundled = BUNDLED_PROMPTS_ROOT / rel
        return bundled if bundled.is_file() else None
    user = user_prompts_root() / rel
    if user.is_file():
        return user
    bundled = BUNDLED_PROMPTS_ROOT / rel
    if bundled.is_file():
        return bundled
    return None


def user_prompt_file(*parts: str) -> Path:
    """Путь для записи пользовательского файла (mkdir parents)."""
    path = ensure_user_prompts_root().joinpath(*parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def overlay_exists(*parts: str) -> bool:
    return resolve_prompt_file(*parts) is not None


def read_prompt_text(*parts: str) -> str:
    path = resolve_prompt_file(*parts)
    if path is None:
        raise FileNotFoundError(f"prompt file not found: {Path(*parts)}")
    return path.read_text(encoding="utf-8")


def write_prompt_text(*parts: str, content: str) -> Path:
    path = user_prompt_file(*parts)
    path.write_text(content, encoding="utf-8")
    return path


def list_overlay_md_stems(rel_dir: str) -> list[str]:
    """Имена .md (без расширения) в каталоге — union bundled + user."""
    stems: set[str] = set()
    for root in (BUNDLED_PROMPTS_ROOT, user_prompts_root()):
        d = root / rel_dir
        if not d.is_dir():
            continue
        for p in d.glob("*.md"):
            if p.is_file():
                stems.add(p.stem)
    return sorted(stems)


def overlay_roots_for_read() -> list[Path]:
    """Порядок поиска: user первым, bundled fallback."""
    roots: list[Path] = []
    user = user_prompts_root()
    if user.is_dir():
        roots.append(user)
    if BUNDLED_PROMPTS_ROOT.is_dir():
        roots.append(BUNDLED_PROMPTS_ROOT)
    return roots


def first_existing_under_prompts(*parts: str) -> Path | None:
    for root in overlay_roots_for_read():
        path = root.joinpath(*parts)
        if path.is_file():
            return path
    return None


def _git_run(args: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd or REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )


def _git_show_file(revision: str, repo_path: str) -> bytes | None:
    r = _git_run(["show", f"{revision}:{repo_path}"])
    if r.returncode != 0:
        return None
    return r.stdout.encode("utf-8")


def _git_head_blob(repo_rel: str) -> bytes | None:
    return _git_show_file("HEAD", repo_rel)


def _git_ls_tree_recursive(tree_ref: str, prefix: str = "") -> list[str]:
    args = ["ls-tree", "-r", "--name-only", tree_ref]
    if prefix:
        args.extend(["--", prefix])
    r = _git_run(args)
    if r.returncode != 0:
        return []
    return [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]


def _is_user_prompt_file(rel: Path, *, disk_content: bytes) -> bool:
    if _is_history_path(rel):
        return True
    repo_path = f"prompts/{rel.as_posix()}"
    head = _git_head_blob(repo_path)
    if head is None:
        return True
    return head != disk_content


def migrate_user_prompts_to_data(*, dry_run: bool = False) -> dict[str, int]:
    """Скопировать пользовательские файлы из prompts/ → data/prompts/."""
    stats = {"scanned": 0, "copied": 0, "skipped": 0}
    if not BUNDLED_PROMPTS_ROOT.is_dir():
        return stats
    user_root = ensure_user_prompts_root()
    # active_variants.json — если только в bundled, подтянуть в user.
    bundled_active = BUNDLED_PROMPTS_ROOT / "active_variants.json"
    user_active = user_root / "active_variants.json"
    if bundled_active.is_file() and not user_active.is_file():
        if not dry_run:
            user_active.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(bundled_active, user_active)
        stats["copied"] += 1
    for src in sorted(BUNDLED_PROMPTS_ROOT.rglob("*")):
        if not src.is_file():
            continue
        rel = src.relative_to(BUNDLED_PROMPTS_ROOT)
        if _is_history_path(rel):
            continue
        stats["scanned"] += 1
        try:
            content = src.read_bytes()
        except OSError as e:
            logger.warning("prompt_migrate: cannot read {}: {}", src, e)
            stats["skipped"] += 1
            continue
        if not _is_user_prompt_file(rel, disk_content=content):
            stats["skipped"] += 1
            continue
        dest = user_root / rel
        if dest.exists():
            try:
                if dest.read_bytes() == content:
                    stats["skipped"] += 1
                    continue
                if dest.stat().st_mtime >= src.stat().st_mtime:
                    stats["skipped"] += 1
                    continue
            except OSError:
                pass
        if dry_run:
            stats["copied"] += 1
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        stats["copied"] += 1
        logger.info("prompt_migrate: {} → {}", rel, dest)
    if stats["copied"]:
        logger.info(
            "prompt_migrate: copied {} file(s) to {}",
            stats["copied"],
            user_root,
        )
    return stats


def _stash_message_matches(msg: str) -> bool:
    if _STASH_MSG_PREFIX in msg:
        return True
    low = msg.lower()
    return any(p.lower() in low for p in _STASH_MSG_EXTRA)


def list_studio_update_stashes() -> list[tuple[str, str]]:
    """(stash_ref, message) от новых к старым — studio/hotfix и любые с prompts/."""
    r = _git_run(["stash", "list", "--format=%gd|%s"])
    if r.returncode != 0:
        return []
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for line in r.stdout.splitlines():
        if "|" not in line:
            continue
        ref, msg = line.split("|", 1)
        ref = ref.strip()
        msg = msg.strip()
        if not _stash_message_matches(msg):
            # Fallback: stash содержит prompts/* (даже без studio-префикса).
            files = extract_prompt_files_from_stash(ref)
            if not files:
                continue
        if ref in seen:
            continue
        seen.add(ref)
        out.append((ref, msg))
    return out


def _collect_files_from_tree(tree_ref: str, prefix: str = "prompts/") -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for repo_path in _git_ls_tree_recursive(tree_ref, prefix):
        if not repo_path.startswith(prefix):
            continue
        rel = repo_path[len(prefix) :]
        if not rel or _is_history_path(Path(rel)):
            continue
        blob = _git_show_file(tree_ref, repo_path)
        if blob is not None:
            files[rel] = blob
    return files


def extract_prompt_files_from_stash(stash_ref: str) -> dict[str, bytes]:
    """Все prompts/* из stash (tracked + untracked stash^3)."""
    files: dict[str, bytes] = {}
    rev = _git_run(["rev-parse", f"{stash_ref}^3"])
    if rev.returncode == 0:
        ut = rev.stdout.strip()
        if ut and ut != rev.stdout.strip():
            pass
        if ut:
            files.update(_collect_files_from_tree(ut))
    files.update(_collect_files_from_tree(stash_ref))
    idx = _git_run(["rev-parse", f"{stash_ref}^2"])
    if idx.returncode == 0:
        parent2 = idx.stdout.strip()
        if parent2:
            for rel, blob in _collect_files_from_tree(parent2).items():
                files.setdefault(rel, blob)
    return files


def _stash_commit_time(stash_ref: str) -> float:
    r = _git_run(["log", "-1", "--format=%ct", stash_ref])
    if r.returncode != 0:
        return 0.0
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def restore_prompts_from_stashes(*, dry_run: bool = False) -> dict[str, object]:
    """Восстановить prompts/ из studio-stash → data/prompts/ (не затирая новее)."""
    user_root = ensure_user_prompts_root()
    stashes = list_studio_update_stashes()
    report: dict[str, object] = {
        "stashes_scanned": len(stashes),
        "files_restored": 0,
        "files_skipped": 0,
        "by_stash": [],
    }
    restored_rels: set[str] = set()
    for stash_ref, msg in stashes:
        stash_info = {"stash": stash_ref, "message": msg, "restored": [], "skipped": []}
        stash_ts = _stash_commit_time(stash_ref)
        files = extract_prompt_files_from_stash(stash_ref)
        for rel, content in sorted(files.items()):
            if rel in restored_rels:
                stash_info["skipped"].append(rel)
                continue
            dest = user_root / rel
            if dest.is_file():
                try:
                    if dest.read_bytes() == content:
                        stash_info["skipped"].append(rel)
                        continue
                    if stash_ts and dest.stat().st_mtime > stash_ts:
                        stash_info["skipped"].append(rel)
                        continue
                except OSError:
                    pass
            if not dry_run:
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(content)
            stash_info["restored"].append(rel)
            restored_rels.add(rel)
            report["files_restored"] = int(report["files_restored"]) + 1
        report["files_skipped"] = int(report["files_skipped"]) + len(stash_info["skipped"])
        report["by_stash"].append(stash_info)
    return report


def export_merged_prompts_snapshot(target_dir: Path) -> None:
    """Скопировать overlay prompts (bundled + user) в target_dir."""
    if target_dir.exists():
        logger.info("prompt_paths: snapshot already exists at {}", target_dir)
        return
    target_dir.parent.mkdir(parents=True, exist_ok=True)
    if BUNDLED_PROMPTS_ROOT.is_dir():
        shutil.copytree(BUNDLED_PROMPTS_ROOT, target_dir, dirs_exist_ok=False)
    else:
        target_dir.mkdir(parents=True, exist_ok=True)
    user = user_prompts_root()
    if user.is_dir():
        for src in user.rglob("*"):
            if src.is_file():
                rel = src.relative_to(user)
                dest = target_dir / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
    logger.info("prompt_paths: merged prompts snapshot → {}", target_dir)
