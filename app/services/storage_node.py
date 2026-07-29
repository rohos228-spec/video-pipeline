"""Нода «Хранилище»: только хранит файлы, пришедшие со стрелок / загрузкой.

Изоляция по node_key:
  data/.../storage/<node_key>/
  meta.storage_nodes[node_key]

Имена при копировании со стрелок:
  {номер_или_id_источника}_{YYYYMMDD_HHMMSS}_{оригинал}
"""

from __future__ import annotations

import hashlib
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
    seen: set[str] = set()
    for e in cg.get("edges") or []:
        if not isinstance(e, dict):
            continue
        if str(e.get("target") or "") != node_key:
            continue
        src = str(e.get("source") or "").strip()
        if src and src not in seen:
            seen.add(src)
            sources.append(src)
    return sources


def _file_fingerprint(path: Path) -> str:
    """size + sha256(head+tail) — одинаковый project.xlsx с разных нод = один файл."""
    try:
        st = path.stat()
        size = int(st.st_size)
    except OSError:
        return ""
    h = hashlib.sha256()
    try:
        with path.open("rb") as fh:
            head = fh.read(65_536)
            h.update(head)
            if size > 65_536:
                fh.seek(max(0, size - 65_536))
                h.update(fh.read(65_536))
    except OSError:
        return f"{size}:"
    return f"{size}:{h.hexdigest()}"


def _already_have(
    index_files: list[dict[str, Any]],
    *,
    from_node: str,
    original_name: str,
    size: int,
    fingerprint: str = "",
) -> bool:
    for item in index_files:
        if fingerprint and str(item.get("fingerprint") or "") == fingerprint:
            return True
        if (
            str(item.get("fromNode") or "") == from_node
            and str(item.get("originalName") or "") == original_name
            and int(item.get("size") or 0) == size
        ):
            name = str(item.get("name") or "")
            if name:
                return True
        # Тот же basename+size уже лежит (другая нода отдала тот же project.xlsx)
        if (
            fingerprint
            and str(item.get("originalName") or "") == original_name
            and int(item.get("size") or 0) == size
        ):
            name = str(item.get("name") or "")
            if name:
                return True
    return False


def sync_from_edges(
    project: Project,
    node_key: str,
    *,
    limit_per_source: int = 200,
    touch_project_meta: bool = True,
) -> dict[str, Any]:
    """Скопировать все подходящие файлы с входящих нод.

    Имя: ``{метка_источника}_{YYYYMMDD_HHMMSS}_{оригинал}``.
    Дубликат: тот же fingerprint / realpath / (источник+имя+размер) не копируется.

    ``touch_project_meta=False`` — только диск/_index.json, без UPDATE projects.meta
    (GET storage/resolve не должен держать SQLite write-lock).
    """
    from app.services.gpt_operator import files_from_source_node

    cfg = node_config(project, node_key)
    # formats в конфиге устарели для sync: витрина фильтрует на клиенте.
    # При копировании со стрелок всегда принимаем все типы файлов.
    allow = _ACCEPT_ALL
    dest_root = ensure_storage_dir(project, node_key)
    index = _load_index(project, node_key)
    index_files: list[dict[str, Any]] = list(index.get("files") or [])
    # выкинуть записи о пропавших файлах
    alive = {p.name for p in dest_root.iterdir() if p.is_file()}
    index_files = [x for x in index_files if str(x.get("name") or "") in alive]

    copied: list[str] = []
    skipped: list[str] = []
    errors: list[str] = []
    seen_realpaths: set[str] = set()
    seen_fingerprints: set[str] = {
        str(x.get("fingerprint") or "")
        for x in index_files
        if str(x.get("fingerprint") or "")
    }

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
            try:
                real = str(src_path.resolve())
            except OSError:
                real = str(src_path)
            if real in seen_realpaths:
                skipped.append(f"{src_path.name}: тот же путь уже скопирован")
                continue
            size = int(src_path.stat().st_size)
            orig = src_path.name
            fp = _file_fingerprint(src_path)
            if fp and fp in seen_fingerprints:
                skipped.append(f"{orig}: дубликат содержимого (fingerprint)")
                continue
            if _already_have(
                index_files,
                from_node=src,
                original_name=orig,
                size=size,
                fingerprint=fp,
            ):
                skipped.append(f"{orig}: уже есть от {label} или того же содержимого")
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
            seen_realpaths.add(real)
            if fp:
                seen_fingerprints.add(fp)
            saved_at = datetime.now().astimezone().isoformat(timespec="seconds")
            entry = {
                "name": dest_name,
                "fromNode": src,
                "fromLabel": label,
                "originalName": orig,
                "savedAt": saved_at,
                "size": size,
                "fingerprint": fp,
            }
            index_files.append(entry)
            copied.append(dest_name)

    index["files"] = index_files
    _save_index(project, node_key, index)

    last_sync_at = datetime.now(timezone.utc).isoformat()
    if touch_project_meta:
        meta = dict(project.meta or {})
        nodes = dict(meta.get("storage_nodes") or {})
        cur = dict(nodes.get(node_key) or {})
        cur["lastSyncAt"] = last_sync_at
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
        "lastSyncAt": last_sync_at if touch_project_meta else cfg.get("lastSyncAt"),
        "lastSyncCopied": len(copied),
    }


def sync_downstream_storage_from_node(
    project: Project, from_node_key: str
) -> list[dict[str, Any]]:
    """После завершения ноды — подтянуть файлы во все storage со стрелки.

    Учитывает вердикт pass/fail: заблокированные ветки не синкаем.
    Возвращает список результатов sync_from_edges (для логов).
    """
    from app.services.gpt_operator import (
        edge_kind_of,
        is_verdict_edge_kind,
        verdict_edge_blocks,
    )

    key = (from_node_key or "").strip()
    if not key:
        return []
    meta = project.meta if isinstance(project.meta, dict) else {}
    cg = canvas_graph_from_meta(meta) or {}
    by_id: dict[str, dict[str, Any]] = {}
    for n in cg.get("nodes") or []:
        if isinstance(n, dict) and n.get("id"):
            by_id[str(n["id"])] = n

    results: list[dict[str, Any]] = []
    for e in cg.get("edges") or []:
        if not isinstance(e, dict):
            continue
        if str(e.get("source") or "").strip() != key:
            continue
        tgt = str(e.get("target") or "").strip()
        if not tgt:
            continue
        typ = str((by_id.get(tgt) or {}).get("type") or "")
        if typ != STORAGE_NODE_TYPE:
            continue
        kind = edge_kind_of(e)
        if is_verdict_edge_kind(kind):
            blocked = verdict_edge_blocks(project, key, kind)
            if blocked:
                continue
        cfg = node_config(project, tgt)
        if cfg.get("autoSync", True) is False:
            continue
        try:
            info = sync_from_edges(project, tgt)
            info = dict(info)
            info["storageNode"] = tgt
            results.append(info)
        except Exception as exc:  # noqa: BLE001
            results.append(
                {
                    "storageNode": tgt,
                    "copied": [],
                    "skipped": [],
                    "errors": [str(exc)],
                    "okFileCount": 0,
                }
            )
    return results


def resolve_storage(
    project: Project,
    node_key: str,
    *,
    auto_sync: bool | None = None,
    touch_project_meta: bool = True,
) -> dict[str, Any]:
    cfg = node_config(project, node_key)
    do_sync = cfg.get("autoSync", True) if auto_sync is None else bool(auto_sync)
    sync_info: dict[str, Any] | None = None
    if do_sync and _incoming_sources(project, node_key):
        sync_info = sync_from_edges(
            project, node_key, touch_project_meta=touch_project_meta
        )
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
        "lastSyncAt": (sync_info or {}).get("lastSyncAt") or cfg.get("lastSyncAt"),
        "lastSyncCopied": (sync_info or {}).get("lastSyncCopied")
        if sync_info is not None
        else cfg.get("lastSyncCopied"),
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
