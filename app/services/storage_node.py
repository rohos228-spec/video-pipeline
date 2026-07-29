"""Нода «Хранилище»: только хранит файлы, пришедшие со стрелок / загрузкой.

Изоляция по node_key:
  data/.../storage/<node_key>/
  meta.storage_nodes[node_key]
"""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from app.models import Project
from app.services.canvas_graph import canvas_graph_from_meta

STORAGE_NODE_TYPE = "storage"

StorageFormat = Literal["image", "video", "xlsx", "text", "any"]

VALID_FORMATS: frozenset[str] = frozenset({"image", "video", "xlsx", "text", "any"})

_IMAGE = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif"})
_VIDEO = frozenset({".mp4", ".webm", ".mov", ".mkv"})
_XLSX = frozenset({".xlsx", ".xls"})
_TEXT = frozenset({".txt", ".md", ".json", ".csv", ".pdf"})
_ANY = _IMAGE | _VIDEO | _XLSX | _TEXT

# formats=any → любой файл (не только whitelist суффиксов)
_ACCEPT_ALL = object()


def is_storage_node_type(node_type: str) -> bool:
    return str(node_type or "") == STORAGE_NODE_TYPE


def storage_dir(project: Project, node_key: str) -> Path:
    return project.data_dir / "storage" / str(node_key).strip()


def ensure_storage_dir(project: Project, node_key: str) -> Path:
    d = storage_dir(project, node_key)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _nodes_meta(project: Project) -> dict[str, dict[str, Any]]:
    meta = project.meta if isinstance(project.meta, dict) else {}
    raw = meta.get("storage_nodes")
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for k, v in raw.items():
        if str(k).strip() and isinstance(v, dict):
            out[str(k)] = dict(v)
    return out


def node_config(project: Project, node_key: str) -> dict[str, Any]:
    cfg = dict(_nodes_meta(project).get(str(node_key).strip()) or {})
    formats = cfg.get("formats")
    if not isinstance(formats, list) or not formats:
        formats = ["any"]
    else:
        formats = [str(x) for x in formats if str(x) in VALID_FORMATS]
        if not formats:
            formats = ["any"]
    cfg["formats"] = formats
    cfg["label"] = str(cfg.get("label") or "Хранилище")
    # По умолчанию авто-забор со всех входящих стрелок.
    if "autoSync" in cfg:
        cfg["autoSync"] = bool(cfg.get("autoSync"))
    else:
        cfg["autoSync"] = True
    return cfg


def patch_config(project: Project, node_key: str, patch: dict[str, Any]) -> dict[str, Any]:
    key = str(node_key).strip()
    if not key:
        raise ValueError("storage node_key required")
    meta = dict(project.meta or {})
    nodes = dict(meta.get("storage_nodes") or {})
    cur = dict(nodes.get(key) or {})
    if "label" in patch and patch["label"] is not None:
        cur["label"] = str(patch["label"]).strip() or "Хранилище"
    if "formats" in patch:
        raw = patch["formats"]
        if isinstance(raw, list):
            fmts = [str(x) for x in raw if str(x) in VALID_FORMATS]
            cur["formats"] = fmts or ["any"]
        elif isinstance(raw, str) and raw in VALID_FORMATS:
            cur["formats"] = [raw]
    if "autoSync" in patch:
        cur["autoSync"] = bool(patch.get("autoSync"))
    nodes[key] = cur
    meta["storage_nodes"] = nodes
    project.meta = meta
    return node_config(project, key)


def _suffixes_for_formats(formats: list[str]) -> frozenset[str] | object:
    if "any" in formats:
        return _ACCEPT_ALL
    out: set[str] = set()
    if "image" in formats:
        out |= _IMAGE
    if "video" in formats:
        out |= _VIDEO
    if "xlsx" in formats:
        out |= _XLSX
    if "text" in formats:
        out |= _TEXT
    return frozenset(out) if out else _ACCEPT_ALL


def _allowed(path: Path, allow: frozenset[str] | object) -> bool:
    if allow is _ACCEPT_ALL:
        return path.is_file() and path.stat().st_size > 0
    return path.suffix.lower() in allow  # type: ignore[operator]


def _file_kind(path: Path) -> str:
    s = path.suffix.lower()
    if s in _IMAGE:
        return "image"
    if s in _VIDEO:
        return "video"
    if s in _XLSX:
        return "xlsx"
    if s in _TEXT:
        return "text"
    return "other"


def list_stored_files(project: Project, node_key: str) -> list[dict[str, Any]]:
    root = storage_dir(project, node_key)
    if not root.is_dir():
        return []
    files: list[dict[str, Any]] = []
    for p in sorted(root.iterdir()):
        if not p.is_file() or p.stat().st_size < 1:
            continue
        kind = _file_kind(p)
        files.append(
            {
                "name": p.name,
                "path": str(p),
                "size": int(p.stat().st_size),
                "kind": kind,
                "ok": True,
                "preview_url": (
                    f"/api/files?path={p}" if kind in ("image", "video", "text") else None
                ),
            }
        )
    return files


def stored_paths(project: Project, node_key: str, *, limit: int = 24) -> list[Path]:
    root = storage_dir(project, node_key)
    if not root.is_dir():
        return []
    out: list[Path] = []
    for p in sorted(root.iterdir()):
        if p.is_file() and p.stat().st_size > 0:
            out.append(p)
            if len(out) >= limit:
                break
    return out


def _incoming_sources(project: Project, node_key: str) -> list[str]:
    meta = project.meta if isinstance(project.meta, dict) else {}
    cg = canvas_graph_from_meta(meta) or {}
    sources: list[str] = []
    for e in cg.get("edges") or []:
        if not isinstance(e, dict):
            continue
        if str(e.get("target") or "") != node_key:
            continue
        src = str(e.get("source") or "").strip()
        if src:
            sources.append(src)
    return sources


def sync_from_edges(project: Project, node_key: str, *, limit_per_source: int = 200) -> dict[str, Any]:
    """Скопировать все подходящие файлы с входящих нод в папку хранилища."""
    from app.services.gpt_operator import files_from_source_node

    cfg = node_config(project, node_key)
    allow = _suffixes_for_formats(list(cfg.get("formats") or ["any"]))
    dest_root = ensure_storage_dir(project, node_key)
    copied: list[str] = []
    skipped: list[str] = []
    errors: list[str] = []

    for src in _incoming_sources(project, node_key):
        try:
            paths = files_from_source_node(
                project, src, use_snapshot=False, limit=limit_per_source
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{src}: {exc}")
            continue
        if not paths:
            skipped.append(f"{src}: нет файлов")
            continue
        for src_path in paths:
            if not _allowed(src_path, allow):
                skipped.append(f"{src_path.name}: формат не подходит")
                continue
            # Имя: fromNode__filename, без коллизий
            safe_src = "".join(c if c.isalnum() or c in "-_" else "_" for c in src)[:40]
            dest_name = f"{safe_src}__{src_path.name}"
            dest = dest_root / dest_name
            try:
                if dest.exists() and dest.stat().st_size == src_path.stat().st_size:
                    copied.append(dest_name)
                    continue
                shutil.copy2(src_path, dest)
                copied.append(dest_name)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{src_path.name}: {exc}")

    meta = dict(project.meta or {})
    nodes = dict(meta.get("storage_nodes") or {})
    cur = dict(nodes.get(node_key) or {})
    cur["lastSyncAt"] = datetime.now(timezone.utc).isoformat()
    cur["lastSyncCopied"] = len(copied)
    nodes[node_key] = cur
    meta["storage_nodes"] = nodes
    project.meta = meta

    files = list_stored_files(project, node_key)
    return {
        "nodeKey": node_key,
        "copied": copied,
        "skipped": skipped,
        "errors": errors,
        "files": files,
        "okFileCount": len(files),
        "formats": cfg.get("formats") or ["any"],
    }


def resolve_storage(project: Project, node_key: str, *, auto_sync: bool | None = None) -> dict[str, Any]:
    cfg = node_config(project, node_key)
    do_sync = cfg.get("autoSync", True) if auto_sync is None else bool(auto_sync)
    sync_info: dict[str, Any] | None = None
    if do_sync and _incoming_sources(project, node_key):
        sync_info = sync_from_edges(project, node_key)
        cfg = node_config(project, node_key)
    files = list_stored_files(project, node_key)
    incoming = _incoming_sources(project, node_key)
    return {
        "nodeKey": node_key,
        "nodeType": STORAGE_NODE_TYPE,
        "label": cfg.get("label") or "Хранилище",
        "formats": list(cfg.get("formats") or ["any"]),
        "autoSync": bool(cfg.get("autoSync", True)),
        "files": files,
        "okFileCount": len(files),
        "incomingSources": incoming,
        "storageDir": str(storage_dir(project, node_key)),
        "lastSyncAt": cfg.get("lastSyncAt"),
        "lastSyncCopied": sync_info.get("copied") if sync_info else None,
        "config": cfg,
    }


def clear_storage(project: Project, node_key: str) -> int:
    root = storage_dir(project, node_key)
    if not root.is_dir():
        return 0
    n = 0
    for p in list(root.iterdir()):
        if p.is_file():
            p.unlink(missing_ok=True)
            n += 1
    return n


def save_upload(project: Project, node_key: str, filename: str, data: bytes) -> Path:
    root = ensure_storage_dir(project, node_key)
    safe = Path(filename).name
    if not safe:
        raise ValueError("empty filename")
    dest = root / safe
    dest.write_bytes(data)
    return dest
