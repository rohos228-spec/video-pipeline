"""Папки сайдбара, порядок проектов и очередь генерации (data/sidebar_layout.json)."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from app.settings import settings


def _layout_path() -> Path:
    path = settings.data_dir / "sidebar_layout.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _empty_layout() -> dict[str, Any]:
    return {"folders": [], "project_layout": {}, "gen_queue": []}


def load_layout() -> dict[str, Any]:
    path = _layout_path()
    if not path.is_file():
        return _empty_layout()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        logger.warning("sidebar_layout: read failed: {}", e)
        return _empty_layout()
    if not isinstance(data, dict):
        return _empty_layout()
    folders = data.get("folders")
    project_layout = data.get("project_layout")
    gen_queue = data.get("gen_queue")
    return {
        "folders": folders if isinstance(folders, list) else [],
        "project_layout": project_layout if isinstance(project_layout, dict) else {},
        "gen_queue": gen_queue if isinstance(gen_queue, list) else [],
    }


def save_layout(data: dict[str, Any]) -> None:
    path = _layout_path()
    tmp = path.with_suffix(".json.tmp")
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, path)


def _normalize_gen_queue(raw: list[Any]) -> list[int]:
    """Уникальные ID в порядке добавления в очередь."""
    out: list[int] = []
    seen: set[int] = set()
    for item in raw:
        try:
            pid = int(item)
        except (TypeError, ValueError):
            continue
        if pid in seen:
            continue
        seen.add(pid)
        out.append(pid)
    return out


def get_gen_queue() -> list[int]:
    return _normalize_gen_queue(load_layout().get("gen_queue") or [])


def gen_queue_position(project_id: int) -> int | None:
    queue = get_gen_queue()
    try:
        idx = queue.index(project_id)
    except ValueError:
        return None
    return idx + 1


def toggle_gen_queue(project_id: int) -> list[int]:
    """Добавить в конец очереди или снять номер (остальные сдвигаются)."""
    data = load_layout()
    queue = _normalize_gen_queue(data.get("gen_queue") or [])
    if project_id in queue:
        queue = [p for p in queue if p != project_id]
    else:
        queue.append(project_id)
    data["gen_queue"] = queue
    save_layout(data)
    return queue


def set_gen_queue(queue: list[int]) -> list[int]:
    data = load_layout()
    data["gen_queue"] = _normalize_gen_queue(queue)
    save_layout(data)
    return data["gen_queue"]


def create_folder(name: str) -> dict[str, Any]:
    clean = (name or "").strip()
    if not clean:
        raise ValueError("name is required")
    data = load_layout()
    folders: list[dict[str, Any]] = list(data.get("folders") or [])
    folder_id = uuid.uuid4().hex[:12]
    order = max((int(f.get("order") or 0) for f in folders), default=-1) + 1
    record = {
        "id": folder_id,
        "name": clean,
        "order": order,
        "created_at": _now_iso(),
    }
    folders.append(record)
    data["folders"] = folders
    save_layout(data)
    return record


def rename_folder(folder_id: str, name: str) -> dict[str, Any]:
    clean = (name or "").strip()
    if not clean:
        raise ValueError("name is required")
    data = load_layout()
    folders: list[dict[str, Any]] = list(data.get("folders") or [])
    found: dict[str, Any] | None = None
    for f in folders:
        if str(f.get("id")) == folder_id:
            f["name"] = clean
            f["updated_at"] = _now_iso()
            found = f
            break
    if found is None:
        raise KeyError(f"folder not found: {folder_id}")
    data["folders"] = folders
    save_layout(data)
    return found


def delete_folder(folder_id: str) -> bool:
    data = load_layout()
    folders: list[dict[str, Any]] = list(data.get("folders") or [])
    new_folders = [f for f in folders if str(f.get("id")) != folder_id]
    if len(new_folders) == len(folders):
        return False
    layout: dict[str, Any] = dict(data.get("project_layout") or {})
    for key, val in list(layout.items()):
        if isinstance(val, dict) and str(val.get("folder_id") or "") == folder_id:
            val = dict(val)
            val["folder_id"] = None
            layout[key] = val
    data["folders"] = new_folders
    data["project_layout"] = layout
    save_layout(data)
    return True


def _project_layout_entry(
    layout: dict[str, Any], project_id: int
) -> dict[str, Any]:
    key = str(project_id)
    raw = layout.get(key)
    if not isinstance(raw, dict):
        return {"folder_id": None, "order": 999999}
    folder_id = raw.get("folder_id")
    try:
        order = int(raw.get("order"))
    except (TypeError, ValueError):
        order = 999999
    return {
        "folder_id": str(folder_id) if folder_id else None,
        "order": order,
    }


def ensure_project_layout(project_id: int, *, folder_id: str | None = None) -> None:
    """Добавить проект в layout при создании (в папку или в корень)."""
    data = load_layout()
    layout: dict[str, Any] = dict(data.get("project_layout") or {})
    key = str(project_id)
    if key in layout:
        return
    siblings = [
        _project_layout_entry(layout, int(k))
        for k in layout
        if _project_layout_entry(layout, int(k)).get("folder_id") == folder_id
    ]
    order = max((int(s.get("order") or 0) for s in siblings), default=-1) + 1
    layout[key] = {"folder_id": folder_id, "order": order}
    data["project_layout"] = layout
    save_layout(data)


def remove_project_from_layout(project_id: int) -> None:
    data = load_layout()
    layout: dict[str, Any] = dict(data.get("project_layout") or {})
    key = str(project_id)
    if key in layout:
        del layout[key]
    queue = _normalize_gen_queue(data.get("gen_queue") or [])
    if project_id in queue:
        queue = [p for p in queue if p != project_id]
    data["project_layout"] = layout
    data["gen_queue"] = queue
    save_layout(data)


def update_layout(
    *,
    folders: list[dict[str, Any]] | None = None,
    project_layout: dict[str, Any] | None = None,
    gen_queue: list[int] | None = None,
) -> dict[str, Any]:
    data = load_layout()
    if folders is not None:
        data["folders"] = folders
    if project_layout is not None:
        data["project_layout"] = project_layout
    if gen_queue is not None:
        data["gen_queue"] = _normalize_gen_queue(gen_queue)
    save_layout(data)
    return data


def sync_projects(
    project_ids: set[int],
    *,
    batch_subprojects: dict[int, tuple[int, int]] | None = None,
    batch_names: dict[int, str] | None = None,
) -> None:
    """Добавить отсутствующие root-проекты в layout (корень, конец списка).

    Принимает batch_* kwargs (вызов из list_projects после #113) — не падаем.
    Подпроекты батча в корень не кладём; папки батча — отдельный follow-up.
    """
    batch_subprojects = batch_subprojects or {}
    _ = batch_names
    data = load_layout()
    layout: dict[str, Any] = dict(data.get("project_layout") or {})
    root_orders = [
        int(v.get("order") or 0)
        for k, v in layout.items()
        if isinstance(v, dict) and not v.get("folder_id")
    ]
    next_order = max(root_orders, default=-1) + 1
    changed = False
    for pid in sorted(project_ids):
        if pid in batch_subprojects:
            continue
        key = str(pid)
        if key in layout:
            continue
        layout[key] = {"folder_id": None, "order": next_order}
        next_order += 1
        changed = True
    if changed:
        data["project_layout"] = layout
        save_layout(data)


def layout_for_api(project_ids: set[int] | None = None) -> dict[str, Any]:
    data = load_layout()
    layout: dict[str, Any] = dict(data.get("project_layout") or {})
    if project_ids is not None:
        layout = {k: v for k, v in layout.items() if int(k) in project_ids}
    gen_queue = _normalize_gen_queue(data.get("gen_queue") or [])
    if project_ids is not None:
        gen_queue = [p for p in gen_queue if p in project_ids]
    folders = sorted(
        [f for f in (data.get("folders") or []) if isinstance(f, dict)],
        key=lambda f: (int(f.get("order") or 0), str(f.get("name") or "").lower()),
    )
    queue_map = {pid: idx + 1 for idx, pid in enumerate(gen_queue)}
    return {
        "folders": folders,
        "project_layout": layout,
        "gen_queue": gen_queue,
        "gen_queue_positions": queue_map,
    }


PROMPTS_LOG_PATH = Path("logs/prompts.log")


def log_prompt_send(
    *,
    bot: str,
    project_id: int | None,
    node: str,
    source: str,
    text: str,
) -> None:
    try:
        PROMPTS_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        pid = project_id if project_id is not None else "?"
        snippet = (text or "").replace("\n", " ")[:80]
        line = f"{ts}\tproject={pid}\tbot={bot}\tnode={node}\tsource={source}\t{snippet}"
        with PROMPTS_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError as e:
        logger.warning("prompts.log write failed: {}", e)
