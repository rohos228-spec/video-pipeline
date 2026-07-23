"""Привязка Excel-снимка к ноде канваса.

После успешного обновления project.xlsx копируем результат в ``old/``
с уникальным именем ``<ts>_<node_key>_result_project.xlsx`` и пишем
привязку в ``project.meta["xlsx_snapshots_by_node"][node_key]`` (+ NodeRun.meta).

UI preview/download по ``node_key`` читает этот снимок, а не общий live-файл.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.models import Project
from app.services.xlsx_versioning import snapshot_node_result_xlsx

META_KEY = "xlsx_snapshots_by_node"


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def snapshots_map(project: Project) -> dict[str, dict[str, Any]]:
    meta = project.meta if isinstance(project.meta, dict) else {}
    raw = meta.get(META_KEY)
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for key, val in raw.items():
        if isinstance(val, dict) and val.get("rel"):
            out[str(key)] = dict(val)
        elif isinstance(val, str) and val.strip():
            out[str(key)] = {"rel": val.strip()}
    return out


def _latest_snapshot_by_filename(project: Project, node_key: str) -> Path | None:
    """Fallback: найти последний ``old/*_<node_key>_result_*.xlsx`` по имени."""
    from app.services.xlsx_versioning import _safe_name_token

    old_dir = project.data_dir / "old"
    if not old_dir.is_dir():
        return None
    token = _safe_name_token(node_key)
    matches = sorted(
        old_dir.glob(f"*_{token}_result_*.xlsx"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for path in matches:
        if path.is_file() and path.stat().st_size >= 512:
            return path
    return None


def resolve_bound_xlsx_path(project: Project, node_key: str | None) -> Path | None:
    """Абсолютный путь к привязанному снимку ноды или None."""
    if not node_key:
        return None
    entry = snapshots_map(project).get(str(node_key))
    if entry:
        rel = str(entry.get("rel") or "").strip().replace("\\", "/")
        if rel and ".." not in rel.split("/"):
            path = (project.data_dir / rel).resolve()
            try:
                path.relative_to(project.data_dir.resolve())
            except ValueError:
                path = None  # type: ignore[assignment]
            if path is not None and path.is_file():
                return path
    return _latest_snapshot_by_filename(project, str(node_key))


def bind_snapshot_entry(
    project: Project,
    node_key: str,
    snapshot: Path,
) -> dict[str, Any]:
    """Записать привязку в project.meta (без flush)."""
    data_root = project.data_dir.resolve()
    snap = Path(snapshot).resolve()
    try:
        rel = snap.relative_to(data_root).as_posix()
    except ValueError:
        rel = f"old/{snap.name}"
    entry = {
        "rel": rel,
        "name": snap.name,
        "bound_at": _utcnow_iso(),
        "node_key": str(node_key),
    }
    meta = dict(project.meta or {})
    by_node = dict(meta.get(META_KEY) or {})
    by_node[str(node_key)] = entry
    meta[META_KEY] = by_node
    project.meta = meta
    flag_modified(project, "meta")
    return entry


async def _write_noderun_meta(
    session: AsyncSession,
    project: Project,
    node_key: str,
    entry: dict[str, Any],
) -> None:
    try:
        from app.services.run_sync import _workflow_run_with_nodes

        run = await _workflow_run_with_nodes(session, project.id)
        if run is None:
            return
        nr = next(
            (c for c in (run.node_runs or []) if c.node_key == node_key),
            None,
        )
        if nr is None:
            return
        nmeta = dict(nr.meta or {})
        nmeta["xlsx_snapshot"] = entry
        nr.meta = nmeta
        flag_modified(nr, "meta")
    except Exception:  # noqa: BLE001
        logger.debug(
            "[#{}] node_xlsx_snapshot: NodeRun.meta write failed for {}",
            project.id,
            node_key,
            exc_info=True,
        )


async def find_node_key_for_type(
    session: AsyncSession,
    project: Project,
    node_type: str,
) -> str | None:
    """Первый node_key данного типа в активном WorkflowRun."""
    from app.services.run_sync import _workflow_run_with_nodes

    run = await _workflow_run_with_nodes(session, project.id)
    if run is None:
        return None
    # Предпочитаем running, затем любой.
    running = None
    any_key = None
    for nr in run.node_runs or []:
        if nr.node_type != node_type:
            continue
        any_key = nr.node_key
        if str(getattr(nr.status, "value", nr.status)) == "running":
            running = nr.node_key
            break
    return running or any_key


async def snapshot_and_bind_node_xlsx(
    session: AsyncSession,
    project: Project,
    *,
    node_key: str | None = None,
    node_type: str | None = None,
) -> dict[str, Any] | None:
    """Снять post-update снимок project.xlsx и привязать к ноде.

    Нужен ``node_key`` или ``node_type`` (тогда ключ ищется в run).
    """
    key = (node_key or "").strip() or None
    if key is None and node_type:
        key = await find_node_key_for_type(session, project, node_type)
    if not key:
        logger.debug(
            "[#{}] node_xlsx_snapshot: нет node_key (type={})",
            project.id,
            node_type,
        )
        return None
    xlsx = project.data_dir / "project.xlsx"
    snap = snapshot_node_result_xlsx(xlsx, node_key=key)
    if snap is None:
        return None
    entry = bind_snapshot_entry(project, key, snap)
    await _write_noderun_meta(session, project, key, entry)
    await session.flush()
    logger.info(
        "[#{}] node_xlsx_snapshot: {} → {}",
        project.id,
        key,
        entry.get("name"),
    )
    return entry
