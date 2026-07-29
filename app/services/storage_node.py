"""Нода «Хранилище»: только хранит файлы, пришедшие со стрелок / загрузкой.

Изоляция по node_key:
  data/.../storage/<node_key>/
  meta.storage_nodes[node_key]

Имена при копировании со стрелок:
  {номер_или_id_источника}_{YYYYMMDD_HHMMSS}_{оригинал}
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from app.models import Project
from app.services.canvas_graph import canvas_graph_from_meta

STORAGE_NODE_TYPE = "storage"
INDEX_NAME = "_index.json"

StorageFormat = Literal["image", "video", "xlsx", "text", "any"]

VALID_FORMATS: frozenset[str] = frozenset({"image", "video", "xlsx", "text", "any"})

_IMAGE = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif"})
_VIDEO = frozenset({".mp4", ".webm", ".mov", ".mkv"})
_XLSX = frozenset({".xlsx", ".xls"})
_TEXT = frozenset({".txt", ".md", ".json", ".csv", ".pdf"})
_ANY = _IMAGE | _VIDEO | _XLSX | _TEXT

# formats=any → любой файл (не только whitelist суффиксов)
_ACCEPT_ALL = object()

_SAFE_RE = re.compile(r"[^A-Za-z0-9._\-]+")


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


def _safe_token(raw: str, *, max_len: int = 48) -> str:
    s = _SAFE_RE.sub("_", str(raw or "").strip()).strip("._")
    return (s or "node")[:max_len]


def _source_label(project: Project, source_key: str) -> str:
    """Номер/метка ноды-источника для имени файла.

    excel_gpt → slot_N (или хвост id);
    иначе короткий id ноды.
    """
    meta = project.meta if isinstance(project.meta, dict) else {}
    cg = canvas_graph_from_meta(meta) or {}
    for n in cg.get("nodes") or []:
        if not isinstance(n, dict):
            continue
        if str(n.get("id") or "") != source_key:
            continue
        typ = str(n.get("type") or "")
        data = n.get("data") if isinstance(n.get("data"), dict) else {}
        if typ == "excel_gpt":
            slot = data.get("slotIndex")
            if slot is None:
                eg = meta.get("excel_gpt_nodes") if isinstance(meta.get("excel_gpt_nodes"), dict) else {}
                cfg = eg.get(source_key) if isinstance(eg, dict) else None
                if isinstance(cfg, dict) and cfg.get("slotIndex") is not None:
                    slot = cfg.get("slotIndex")
            if slot is not None and str(slot).strip().isdigit():
                return f"slot{int(slot)}"
            m = re.search(r"(\d+)$", source_key)
            if m:
                return f"n{m.group(1)}"
        m = re.search(r"(\d+)$", source_key)
        if m:
            return f"n{m.group(1)}"
        return _safe_token(source_key, max_len=24)
    m = re.search(r"(\d+)$", source_key)
    if m:
        return f"n{m.group(1)}"
    return _safe_token(source_key, max_len=24)


def _stamp_now() -> str:
    return datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")


def _index_path(project: Project, node_key: str) -> Path:
    return storage_dir(project, node_key) / INDEX_NAME


def _load_index(project: Project, node_key: str) -> dict[str, Any]:
    path = _index_path(project, node_key)
    if not path.is_file():
        return {"files": []}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"files": []}
    if not isinstance(raw, dict):
        return {"files": []}
    files = raw.get("files")
    if not isinstance(files, list):
        files = []
    return {"files": [x for x in files if isinstance(x, dict)]}


def _save_index(project: Project, node_key: str, index: dict[str, Any]) -> None:
    ensure_storage_dir(project, node_key)
    path = _index_path(project, node_key)
    path.write_text(
        json.dumps(index, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _entry_for_file(
    project: Project,
    node_key: str,
    path: Path,
    *,
    from_node: str | None = None,
    from_label: str | None = None,
    saved_at: str | None = None,
    original_name: str | None = None,
) -> dict[str, Any]:
    kind = _file_kind(path)
    size = int(path.stat().st_size) if path.is_file() else 0
    q = str(path)
    return {
        "name": path.name,
        "path": q,
        "size": size,
        "kind": kind,
        "ok": path.is_file() and size > 0,
        "fromNode": from_node,
        "fromLabel": from_label,
        "savedAt": saved_at,
        "originalName": original_name or path.name,
        "preview_url": (
            f"/api/files?path={q}" if kind in ("image", "video", "text") else None
        ),
        "download_url": f"/api/files?path={q}&download=1",
    }


def list_stored_files(project: Project, node_key: str) -> list[dict[str, Any]]:
    """Все файлы в папке хранилища (+ метаданные из _index.json)."""
    root = storage_dir(project, node_key)
    if not root.is_dir():
        return []
    index = _load_index(project, node_key)
    by_name: dict[str, dict[str, Any]] = {}
    for item in index.get("files") or []:
        name = str(item.get("name") or "").strip()
        if name:
            by_name[name] = item

    files: list[dict[str, Any]] = []
    for p in sorted(root.iterdir(), key=lambda x: x.name.lower()):
        if not p.is_file() or p.stat().st_size < 1:
            continue
        # служебные: индекс и временные zip-экспорты
        if p.name == INDEX_NAME or p.name.startswith("_export_"):
            continue
        meta = by_name.get(p.name) or {}
        files.append(
            _entry_for_file(
                project,
                node_key,
                p,
                from_node=str(meta.get("fromNode") or "") or None,
                from_label=str(meta.get("fromLabel") or "") or None,
                saved_at=str(meta.get("savedAt") or "") or None,
                original_name=str(meta.get("originalName") or "") or None,
            )
        )
    # новые сверху по времени в имени / mtime
    def sort_key(f: dict[str, Any]) -> str:
        return str(f.get("savedAt") or f.get("name") or "")

    files.sort(key=sort_key, reverse=True)
    return files


def stored_paths(project: Project, node_key: str, *, limit: int = 200) -> list[Path]:
    root = storage_dir(project, node_key)
    if not root.is_dir():
        return []
    out: list[Path] = []
    for p in sorted(root.iterdir()):
        if not p.is_file() or p.name == INDEX_NAME or p.stat().st_size < 1:
            continue
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


def _already_have(
    index_files: list[dict[str, Any]],
    *,
    from_node: str,
    original_name: str,
    size: int,
) -> bool:
    for item in index_files:
        if (
            str(item.get("fromNode") or "") == from_node
            and str(item.get("originalName") or "") == original_name
            and int(item.get("size") or 0) == size
        ):
            # файл ещё на диске?
            name = str(item.get("name") or "")
            if name:
                return True
    return False


def sync_from_edges(project: Project, node_key: str, *, limit_per_source: int = 200) -> dict[str, Any]:
    """Скопировать все подходящие файлы с входящих нод.

    Имя: ``{метка_источника}_{YYYYMMDD_HHMMSS}_{оригинал}``.
    Дубликат (тот же источник + имя + размер) не копируется повторно.
    """
    from app.services.gpt_operator import files_from_source_node

    cfg = node_config(project, node_key)
    allow = _suffixes_for_formats(list(cfg.get("formats") or ["any"]))
    dest_root = ensure_storage_dir(project, node_key)
    index = _load_index(project, node_key)
    index_files: list[dict[str, Any]] = list(index.get("files") or [])
    # выкинуть записи о пропавших файлах
    alive = {p.name for p in dest_root.iterdir() if p.is_file()}
    index_files = [x for x in index_files if str(x.get("name") or "") in alive]

    copied: list[str] = []
    skipped: list[str] = []
    errors: list[str] = []

    for src in _incoming_sources(project, node_key):
        label = _source_label(project, src)
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
            size = int(src_path.stat().st_size)
            orig = src_path.name
            if _already_have(
                index_files, from_node=src, original_name=orig, size=size
            ):
                skipped.append(f"{orig}: уже есть от {label}")
                continue
            stamp = _stamp_now()
            safe_orig = _safe_token(orig, max_len=80)
            dest_name = f"{label}_{stamp}_{safe_orig}"
            dest = dest_root / dest_name
            # коллизия в ту же секунду
            n = 1
            while dest.exists():
                dest_name = f"{label}_{stamp}_{n}_{safe_orig}"
                dest = dest_root / dest_name
                n += 1
            try:
                shutil.copy2(src_path, dest)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{src_path.name}: {exc}")
                continue
            saved_at = datetime.now().astimezone().isoformat(timespec="seconds")
            entry = {
                "name": dest_name,
                "fromNode": src,
                "fromLabel": label,
                "originalName": orig,
                "savedAt": saved_at,
                "size": size,
            }
            index_files.append(entry)
            copied.append(dest_name)

    index["files"] = index_files
    _save_index(project, node_key, index)

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
        "lastSyncCopied": (sync_info or {}).get("copied"),
        "config": cfg,
    }


def clear_storage(project: Project, node_key: str) -> int:
    root = storage_dir(project, node_key)
    if not root.is_dir():
        return 0
    n = 0
    for p in list(root.iterdir()):
        if not p.is_file():
            continue
        is_index = p.name == INDEX_NAME
        p.unlink(missing_ok=True)
        if not is_index:
            n += 1
    return n


def build_storage_zip(project: Project, node_key: str) -> Path:
    """Собрать zip всех файлов хранилища (без _index.json и старых zip)."""
    import zipfile

    root = storage_dir(project, node_key)
    if not root.is_dir():
        raise FileNotFoundError("хранилище пусто")
    files = [
        p
        for p in sorted(root.iterdir())
        if p.is_file()
        and p.name != INDEX_NAME
        and not p.name.startswith("_export_")
        and p.stat().st_size > 0
    ]
    if not files:
        raise FileNotFoundError("хранилище пусто")
    stamp = _stamp_now()
    zip_path = root / f"_export_{stamp}.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for p in files:
            zf.write(p, arcname=p.name)
    return zip_path


def save_upload(project: Project, node_key: str, filename: str, data: bytes) -> Path:
    root = ensure_storage_dir(project, node_key)
    stamp = _stamp_now()
    safe = _safe_token(Path(filename).name, max_len=80)
    if not safe:
        raise ValueError("empty filename")
    dest_name = f"upload_{stamp}_{safe}"
    dest = root / dest_name
    n = 1
    while dest.exists():
        dest_name = f"upload_{stamp}_{n}_{safe}"
        dest = root / dest_name
        n += 1
    dest.write_bytes(data)
    index = _load_index(project, node_key)
    files = list(index.get("files") or [])
    files.append(
        {
            "name": dest_name,
            "fromNode": None,
            "fromLabel": "upload",
            "originalName": Path(filename).name,
            "savedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
            "size": len(data),
        }
    )
    index["files"] = files
    _save_index(project, node_key, index)
    return dest
