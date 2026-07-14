"""Версии файлов промтов: авто-архив при сохранении + метаданные имён."""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from app.services.prompt_library import (
    DEFAULT_NAME,
    is_valid_prompt_name,
    list_prompts,
    prompt_path,
    step_dir,
    write_prompt,
)

_HISTORY_DIR = ".history"
_INDEX = "index.json"
_MAX_VERSIONS = 100


def _history_root(step_code: str) -> Path:
    root = step_dir(step_code) / _HISTORY_DIR
    root.mkdir(parents=True, exist_ok=True)
    return root


def _prompt_history_dir(step_code: str, name: str) -> Path:
    d = _history_root(step_code) / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def _index_path(step_code: str, name: str) -> Path:
    return _prompt_history_dir(step_code, name) / _INDEX


def _default_label(saved_at: float) -> str:
    dt = datetime.fromtimestamp(saved_at, tz=timezone.utc).astimezone()
    return dt.strftime("%d.%m.%Y %H:%M")


def _rebuild_index_from_snapshots(step_code: str, name: str) -> dict[str, Any]:
    """Восстановить index.json из *.md в .history/<name>/."""
    hist_dir = _prompt_history_dir(step_code, name)
    versions: list[dict[str, Any]] = []
    for snap in sorted(hist_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True):
        vid = snap.stem
        if not vid or vid == _INDEX:
            continue
        try:
            saved_at = float(snap.stat().st_mtime)
        except OSError:
            saved_at = 0.0
        try:
            size = snap.stat().st_size
        except OSError:
            size = 0
        versions.append(
            {
                "id": vid,
                "label": _default_label(saved_at),
                "saved_at": saved_at,
                "size": size,
            }
        )
    versions = versions[:_MAX_VERSIONS]
    data = {"versions": versions}
    if versions:
        _save_index(step_code, name, data)
        logger.info(
            "prompt_history: rebuilt index for {}/{} ({} versions)",
            step_code,
            name,
            len(versions),
        )
    return data


def _load_index(step_code: str, name: str) -> dict[str, Any]:
    path = _index_path(step_code, name)
    hist_dir = _prompt_history_dir(step_code, name)
    has_snapshots = any(hist_dir.glob("*.md"))

    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("prompt_history: bad index {}: {}", path, e)
            data = None
        else:
            if isinstance(data, dict) and isinstance(data.get("versions"), list):
                versions = data.get("versions") or []
                if versions or not has_snapshots:
                    return data
            else:
                data = None
        if data is None and has_snapshots:
            return _rebuild_index_from_snapshots(step_code, name)
        return {"versions": []}

    if has_snapshots:
        return _rebuild_index_from_snapshots(step_code, name)
    return {"versions": []}


def _save_index(step_code: str, name: str, data: dict[str, Any]) -> None:
    path = _index_path(step_code, name)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _new_version_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def archive_prompt_version(step_code: str, name: str, content: str) -> str | None:
    text = content or ""
    if not text.strip():
        return None
    vid = _new_version_id()
    hist_dir = _prompt_history_dir(step_code, name)
    idx = _load_index(step_code, name)
    (hist_dir / f"{vid}.md").write_text(text, encoding="utf-8")
    saved_at = datetime.now(timezone.utc).timestamp()
    versions: list[dict[str, Any]] = list(idx.get("versions") or [])
    versions.insert(
        0,
        {
            "id": vid,
            "label": _default_label(saved_at),
            "saved_at": saved_at,
            "size": len(text.encode("utf-8")),
        },
    )
    idx["versions"] = versions[:_MAX_VERSIONS]
    for stale in versions[_MAX_VERSIONS:]:
        sid = stale.get("id")
        if isinstance(sid, str):
            (hist_dir / f"{sid}.md").unlink(missing_ok=True)
    _save_index(step_code, name, idx)
    return vid


def write_prompt_with_history(step_code: str, name: str, content: str) -> Path:
    p = prompt_path(step_code, name)
    if p.exists():
        try:
            old = p.read_text(encoding="utf-8")
        except OSError:
            old = ""
        if old != content:
            archive_prompt_version(step_code, name, old)
    return write_prompt(step_code, name, content)


def list_prompt_versions(step_code: str, name: str) -> list[dict[str, Any]]:
    idx = _load_index(step_code, name)
    out: list[dict[str, Any]] = []
    hist_dir = _prompt_history_dir(step_code, name)
    for item in idx.get("versions") or []:
        if not isinstance(item, dict):
            continue
        vid = item.get("id")
        if not isinstance(vid, str):
            continue
        snap = hist_dir / f"{vid}.md"
        if not snap.is_file():
            continue
        saved_raw = item.get("saved_at")
        saved_at = float(saved_raw) if saved_raw is not None else 0.0
        out.append(
            {
                "id": vid,
                "label": str(item.get("label") or _default_label(saved_at)),
                "saved_at": saved_at,
                "size": int(item.get("size") or snap.stat().st_size),
            }
        )
    return out


def read_prompt_version(step_code: str, name: str, version_id: str) -> str:
    if ".." in version_id or "/" in version_id or "\\" in version_id:
        raise ValueError("invalid version id")
    snap = _prompt_history_dir(step_code, name) / f"{version_id}.md"
    if not snap.is_file():
        raise FileNotFoundError(f"version not found: {version_id}")
    return snap.read_text(encoding="utf-8")


def rename_prompt_version_label(
    step_code: str, name: str, version_id: str, label: str
) -> dict[str, Any]:
    clean = (label or "").strip()
    if not clean:
        raise ValueError("label required")
    idx = _load_index(step_code, name)
    versions: list[dict[str, Any]] = list(idx.get("versions") or [])
    found: dict[str, Any] | None = None
    for item in versions:
        if isinstance(item, dict) and item.get("id") == version_id:
            item["label"] = clean
            found = item
            break
    if found is None:
        raise FileNotFoundError(f"version not found: {version_id}")
    idx["versions"] = versions
    _save_index(step_code, name, idx)
    return {
        "id": version_id,
        "label": clean,
        "saved_at": float(found.get("saved_at") or 0),
        "size": int(found.get("size") or 0),
    }


def rename_prompt_file(step_code: str, old_name: str, new_name: str) -> str:
    if old_name == DEFAULT_NAME:
        raise ValueError("default переименовывать нельзя")
    if not is_valid_prompt_name(new_name):
        raise ValueError(f"invalid prompt name: {new_name!r}")
    src = prompt_path(step_code, old_name)
    if not src.exists():
        raise FileNotFoundError(f"prompt not found: {old_name}")
    dst = prompt_path(step_code, new_name)
    if dst.exists():
        raise ValueError(f"prompt already exists: {new_name}")
    src.rename(dst)
    from app.services.prompt_library import rename_prompt_meta

    rename_prompt_meta(step_code, old_name, new_name)
    hist_root = _history_root(step_code)
    old_hist = hist_root / old_name
    if old_hist.is_dir():
        new_hist = hist_root / new_name
        if not new_hist.exists():
            old_hist.rename(new_hist)
        else:
            _merge_prompt_history_dirs(old_hist, new_hist)
            shutil.rmtree(old_hist, ignore_errors=True)
    return new_name


def _merge_prompt_history_dirs(old_hist: Path, new_hist: Path) -> None:
    """Слить историю при переименовании, если у нового имени уже есть архив."""
    new_hist.mkdir(parents=True, exist_ok=True)
    for snap in old_hist.glob("*.md"):
        dest = new_hist / snap.name
        if not dest.exists():
            snap.rename(dest)
    old_index = old_hist / _INDEX
    new_index = new_hist / _INDEX
    old_data = {"versions": []}
    new_data = {"versions": []}
    if old_index.is_file():
        try:
            raw = json.loads(old_index.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and isinstance(raw.get("versions"), list):
                old_data = raw
        except (json.JSONDecodeError, OSError):
            pass
    if new_index.is_file():
        try:
            raw = json.loads(new_index.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and isinstance(raw.get("versions"), list):
                new_data = raw
        except (json.JSONDecodeError, OSError):
            pass
    seen: set[str] = set()
    merged: list[dict[str, Any]] = []
    for item in list(new_data.get("versions") or []) + list(old_data.get("versions") or []):
        if not isinstance(item, dict):
            continue
        vid = item.get("id")
        if not isinstance(vid, str) or vid in seen:
            continue
        seen.add(vid)
        merged.append(item)
    merged.sort(key=lambda x: float(x.get("saved_at") or 0), reverse=True)
    new_data["versions"] = merged[:_MAX_VERSIONS]
    tmp = new_index.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(new_data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, new_index)


def restore_prompt_version(step_code: str, name: str, version_id: str) -> str:
    content = read_prompt_version(step_code, name, version_id)
    write_prompt_with_history(step_code, name, content)
    return content


def bootstrap_saved_at_from_history(step_code: str | None = None) -> int:
    """Проставить saved_at файлам без меты из последней версии .history."""
    from app.services.prompt_library import (
        STEP_FOLDERS,
        get_prompt_saved_at,
        touch_prompt_meta_at,
    )

    codes = [step_code] if step_code else list(STEP_FOLDERS.keys())
    updated = 0
    for code in codes:
        if code not in STEP_FOLDERS:
            continue
        for name in list_prompts(code):
            if get_prompt_saved_at(code, name) is not None:
                continue
            versions = list_prompt_versions(code, name)
            if not versions:
                continue
            latest = versions[0]
            saved_at = float(latest.get("saved_at") or 0)
            if saved_at <= 0:
                continue
            size = int(latest.get("size") or 0)
            touch_prompt_meta_at(code, name, saved_at, size)
            updated += 1
    if updated:
        logger.info("prompt_history: bootstrapped saved_at for {} file(s)", updated)
    return updated
