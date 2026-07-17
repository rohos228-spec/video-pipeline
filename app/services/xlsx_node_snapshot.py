"""Снимки Excel per-node: что нода создала (after) или использовала (before/input).

Живой `project.xlsx` перезаписывается следующими шагами — без снимка
превью внутри ноды всегда показывает «последнее» состояние пайплайна.
"""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from loguru import logger
from sqlalchemy.orm.attributes import flag_modified

from app.models import Project

XlsxRole = Literal["produce", "consume"]

_SNAPSHOT_DIRNAME = "xlsx_snapshots"
_BEFORE = "before.xlsx"
_AFTER = "after.xlsx"
_META_KEY = "xlsx_node_results"


def snapshot_dir(project: Project, node_key: str) -> Path:
    safe = Path(str(node_key)).name.strip() or "node"
    return project.data_dir / _SNAPSHOT_DIRNAME / safe


def _rel(project: Project, path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return str(path.resolve().relative_to(project.data_dir.resolve()))
    except ValueError:
        return str(path)


def _abs(project: Project, rel: str | None) -> Path | None:
    if not rel:
        return None
    p = Path(rel)
    if p.is_absolute():
        return p if p.is_file() else None
    cand = (project.data_dir / p).resolve()
    try:
        cand.relative_to(project.data_dir.resolve())
    except ValueError:
        return None
    return cand if cand.is_file() else None


def canvas_node_keys_of_type(project: Project, node_type: str) -> list[str]:
    """Все node_key на канвасе с данным type (n_plan, n_excel_gpt_2, …)."""
    from app.services.canvas_graph import canvas_graph_from_meta
    from app.services.excel_gpt_node import (
        excel_gpt_nodes_from_project,
        is_excel_gpt_node_type,
    )

    keys: list[str] = []
    seen: set[str] = set()
    meta = project.meta if isinstance(project.meta, dict) else {}
    cg = canvas_graph_from_meta(meta)
    nodes = list((cg or {}).get("nodes") or [])
    if not nodes and is_excel_gpt_node_type(node_type):
        nodes = excel_gpt_nodes_from_project(project)
    for n in nodes:
        ntype = str(n.get("type") or "")
        if ntype != node_type and not (
            node_type == "excel_gpt" and is_excel_gpt_node_type(ntype)
        ):
            continue
        nid = str(n.get("id") or "").strip()
        if nid and nid not in seen:
            seen.add(nid)
            keys.append(nid)
    # Стабильные дефолты, если граф ещё не синхронизирован.
    defaults = {
        "plan": "n_plan",
        "split": "n_split",
        "image_prompts": "n_image_prompts",
        "hero": "n_hero",
        "items": "n_items",
    }
    d = defaults.get(node_type)
    if d and d not in seen:
        keys.append(d)
    return keys


def save_node_xlsx_snapshot(
    project: Project,
    node_key: str,
    *,
    role: XlsxRole,
    before_path: Path | None = None,
    after_path: Path | None = None,
    source: str = "project_xlsx",
) -> dict[str, Any]:
    """Сохранить before/after и записать meta.xlsx_node_results[node_key]."""
    node_key = str(node_key).strip()
    if not node_key:
        return {}
    out_dir = snapshot_dir(project, node_key)
    out_dir.mkdir(parents=True, exist_ok=True)

    saved_before: Path | None = None
    saved_after: Path | None = None

    if before_path is not None and Path(before_path).is_file():
        dest = out_dir / _BEFORE
        shutil.copy2(before_path, dest)
        saved_before = dest

    if after_path is not None and Path(after_path).is_file():
        dest = out_dir / _AFTER
        shutil.copy2(after_path, dest)
        saved_after = dest

    # produce (plan/split/excel_gpt/…): на входе старый Excel, на выходе
    # обновлённый — в ноде показываем ТОЛЬКО обновлённый (after).
    # before храним для истории, но не подставляем в display.
    # consume (hero и т.п.): нода только читала файл — показываем его.
    if role == "produce":
        display = saved_after
    else:
        display = saved_before or saved_after

    info: dict[str, Any] = {
        "role": role,
        "source": source,
        "inputPath": _rel(project, saved_before),
        "outputPath": _rel(project, saved_after),
        "displayPath": _rel(project, display),
        "updatedAt": datetime.now(timezone.utc).isoformat(),
    }

    meta = dict(project.meta or {})
    bucket = dict(meta.get(_META_KEY) or {})
    bucket[node_key] = info
    meta[_META_KEY] = bucket
    project.meta = meta
    try:
        flag_modified(project, "meta")
    except Exception:  # noqa: BLE001
        pass

    logger.info(
        "xlsx_node_snapshot: #{} {} role={} display={}",
        project.id,
        node_key,
        role,
        info.get("displayPath"),
    )
    return info


def record_produce_for_node_keys(
    project: Project,
    node_keys: list[str],
    *,
    before_path: Path | None,
    after_path: Path,
    source: str = "project_xlsx",
) -> None:
    for key in node_keys:
        if not key:
            continue
        save_node_xlsx_snapshot(
            project,
            key,
            role="produce",
            before_path=before_path,
            after_path=after_path,
            source=source,
        )


def record_consume_for_node_keys(
    project: Project,
    node_keys: list[str],
    *,
    used_path: Path,
    source: str = "project_xlsx",
) -> None:
    """Нода только читала Excel — запомнить использованный файл."""
    for key in node_keys:
        if not key:
            continue
        save_node_xlsx_snapshot(
            project,
            key,
            role="consume",
            before_path=used_path,
            after_path=None,
            source=source,
        )


def node_xlsx_result(project: Project, node_key: str | None) -> dict[str, Any] | None:
    if not node_key:
        return None
    meta = project.meta if isinstance(project.meta, dict) else {}
    bucket = meta.get(_META_KEY) or {}
    if not isinstance(bucket, dict):
        return None
    raw = bucket.get(str(node_key))
    return dict(raw) if isinstance(raw, dict) else None


def resolve_display_xlsx(
    project: Project,
    *,
    node_key: str | None = None,
) -> tuple[Path, dict[str, Any]]:
    """Путь к xlsx для превью ноды + meta-инфо (или live project.xlsx).

    Ноды-обновлятели (role=produce) → after.xlsx (обновлённый выход).
    Ноды-читатели (role=consume) → before.xlsx (использованный вход).
    """
    live = project.data_dir / "project.xlsx"
    info = node_xlsx_result(project, node_key) or {}
    role = str(info.get("role") or "")

    # Явный displayPath из meta — но для produce игнорируем, если это before.
    display = _abs(project, str(info.get("displayPath") or "") or None)
    if display is not None:
        if role == "produce" and display.name == _BEFORE:
            display = None
        else:
            return display, {**info, "resolved": "snapshot"}

    if node_key:
        d = snapshot_dir(project, node_key)
        after = d / _AFTER
        before = d / _BEFORE
        if role == "produce" or (not role and after.is_file()):
            if after.is_file():
                return after, {
                    **info,
                    "role": role or "produce",
                    "resolved": "snapshot_file",
                    "displayPath": _rel(project, after),
                }
            # produce без after — не показываем старый before, лучше live
            return live, {
                **info,
                "role": role or "produce",
                "resolved": "live",
                "source": "project_xlsx",
            }
        if before.is_file():
            return before, {
                **info,
                "role": role or "consume",
                "resolved": "snapshot_file",
                "displayPath": _rel(project, before),
            }
        if after.is_file():
            return after, {
                **info,
                "resolved": "snapshot_file",
                "displayPath": _rel(project, after),
            }

    return live, {"resolved": "live", "role": info.get("role"), "source": "project_xlsx"}
