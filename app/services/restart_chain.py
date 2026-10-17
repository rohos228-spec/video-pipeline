from __future__ import annotations

from loguru import logger
from sqlalchemy.orm.attributes import flag_modified

META_KEY = "restart_chain"

_KIND_TO_NODE = {
    "video": "generating_videos", "images": "generating_images", "music": "generating_music",
    "audio": "generating_audio", "vision:scenes": "generating_images", "vision:videos": "generating_videos",
    "vision:hero": "generating_hero",
}


def node_for_kind(kind: str) -> str:
    return _KIND_TO_NODE.get(kind, kind)


def max_per_node() -> int:
    try:
        from app.settings import settings
        return int(settings.restart_chain_max_per_node)
    except Exception:
        return 60


def _state(project) -> dict:
    meta = project.meta if isinstance(getattr(project, "meta", None), dict) else {}
    state = meta.get(META_KEY)
    return state if isinstance(state, dict) else {}


def _save(project, state: dict) -> None:
    meta = dict(project.meta or {})
    if state:
        meta[META_KEY] = state
    else:
        meta.pop(META_KEY, None)
    project.meta = meta
    flag_modified(project, "meta")


def count(project, node: str) -> int:
    entry = _state(project).get(node)
    return int(entry.get("count") or 0) if isinstance(entry, dict) else 0


def would_exceed(project, node: str, n: int = 1) -> bool:
    cap = max_per_node()
    return bool(cap) and count(project, node) + max(0, n) > cap


def charge(project, node: str, source: str, n: int = 1) -> tuple[bool, int, int]:
    cap = max_per_node()
    cur = count(project, node)
    if cap and cur + n > cap:
        return False, cur, cap
    state = {k: (dict(v) if isinstance(v, dict) else v) for k, v in _state(project).items()}
    entry = dict(state.get(node) or {})
    by = dict(entry.get("by") or {})
    by[source] = int(by.get(source) or 0) + n
    entry.update(count=cur + n, by=by)
    state[node] = entry
    _save(project, state)
    return True, cur + n, cap


def clear_node(project, node: str) -> bool:
    state = dict(_state(project))
    if node not in state:
        return False
    state.pop(node)
    _save(project, state)
    return True


def release(project, node: str | None = None) -> bool:
    state = dict(_state(project))
    gone = [k for k in state if node is None or k == node]
    changed = bool(gone)
    for k in gone:
        state.pop(k, None)
    if changed:
        _save(project, state)
    meta = dict(project.meta or {})
    if meta.get("restart_chain_stop"):
        meta.pop("restart_chain_stop", None)
        if "regen_budget_stop" not in meta:
            meta.pop("auto_paused_reason", None)
        project.meta = meta
        flag_modified(project, "meta")
        changed = True
    return changed


def summary(node: str, cur: int, cap: int, source: str) -> str:
    return (f"{node}: общий потолок перезапусков узла исчерпан ({cur} из {cap}, последний источник: {source}); "
            f"ещё один платный запуск без решения человека не делается")


def remember_pause_origin(project) -> None:
    from app.models import ProjectStatus

    if project.status != ProjectStatus.paused:
        meta = dict(project.meta or {})
        meta["paused_from_status"] = project.status.value
        project.meta = meta
        flag_modified(project, "meta")


async def stop(session, project, node: str, cur: int, cap: int, source: str) -> str:
    from app.models import ProjectStatus
    from app.services import regen_budget

    text = summary(node, cur, cap, source)
    meta = dict(project.meta or {})
    meta[regen_budget.PAUSE_REASON_KEY] = text
    meta["restart_chain_stop"] = {"node": node, "count": cur, "cap": cap, "source": source}
    if project.status != ProjectStatus.paused:
        meta["paused_from_status"] = project.status.value
    project.meta = meta
    flag_modified(project, "meta")
    project.status = ProjectStatus.paused
    try:
        await session.flush()
    except Exception:
        logger.exception("[#{}] restart_chain: flush failed", getattr(project, "id", "?"))
    logger.warning("[#{}] restart_chain: {}", getattr(project, "id", "?"), text)
    try:
        from app.services.observation import record_fact
        await record_fact("runtime_check", {
            "check": "regen_budget", "performed": True, "status": "rejected", "kind": node,
            "restart_chain": True, "rounds": cur, "per_run_cap": cap, "source": source, "summary": text})
    except Exception:
        logger.debug("restart_chain: observation fact skipped", exc_info=True)
    return text


async def release_for_human_everywhere(session, project) -> None:
    release_for_human(project)
    try:
        from app.project_db import run_on_replica_project

        async def _rel(_replica_session, replica) -> None:
            release_for_human(replica)

        await run_on_replica_project(session, project, _rel)
    except Exception:
        logger.warning("[#{}] restart_chain: сброс на зеркальной БД не удался", getattr(project, "id", "?"), exc_info=True)


def release_for_human(project) -> bool:
    from app.services import regen_budget

    changed = release(project)
    changed = regen_budget.reset(project) or changed
    meta = dict(project.meta or {})
    gone = [k for k in meta if k.startswith("auto_retry_")]
    for k in gone + ["auto_paused_fix_hints"]:
        if k in meta:
            meta.pop(k)
            changed = True
    if changed:
        project.meta = meta
        flag_modified(project, "meta")
    return changed
