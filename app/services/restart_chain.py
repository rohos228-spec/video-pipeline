"""Единый потолок цепочки «брак -> перезапуск» на узел проекта (W40 C-12, C-11).

Платные перезапуски одного узла считали три независимых счётчика, и успех соседнего сбрасывал соседний:

* ``regen_budget`` (перегенерации по проверке файлов и по проверке картинок/клипов), его ``clear`` вызывается на каждом
  успешном проходе проверки;
* ``auto_retry_<ready>`` авто-ревью (``auto_advance``, 2 на шаг), сбрасывается одобрением;
* ``step_failure.total_fails`` (повторы после ошибки), сбрасывается успехом шага.

Здесь один счётчик ``project.meta['restart_chain'][<узел>]``: каждый платный перезапуск узла (любого происхождения)
прибавляет единицу; успех проверки или соседнего счётчика его НЕ сбрасывает. Сбрасывается он только
* одобрением шага (узел принят: цепочка закончилась) и
* решением человека снять паузу (``release``): продолжить, возобновить, сбросить лимиты.

Когда потолок достигнут, перезапуска нет: проект встаёт на паузу с причиной, пишется факт ``runtime_check`` /
``regen_budget`` (его превращает в инцидент «нужен человек» слой диагностики). Потолок: ``RESTART_CHAIN_MAX_PER_NODE``
(``0`` = выключен). Ручные действия человека тоже считаются (источник ``manual``), но не как «авто-регенерации».
"""
from __future__ import annotations

from loguru import logger
from sqlalchemy.orm.attributes import flag_modified

META_KEY = "restart_chain"

# regen_budget-виды и проверки -> узел (running-статус шага, чьи платные результаты они переделывают)
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
    except Exception:  # noqa: BLE001
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
    """Прибавить ``n`` перезапусков узла. Возвращает (разрешено, счётчик, потолок). При отказе счётчик не растёт."""
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
    """Шаг принят: цепочка этого узла закончилась."""
    state = dict(_state(project))
    if node not in state:
        return False
    state.pop(node)
    _save(project, state)
    return True


def release(project, node: str | None = None) -> bool:
    """Человек снял паузу (продолжить/возобновить/сбросить лимиты): цепочка начинается заново."""
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


async def stop(session, project, node: str, cur: int, cap: int, source: str) -> str:
    """Поставить проект на паузу с причиной и записать факт для инцидента. Возвращает текст причины."""
    from app.models import ProjectStatus
    from app.services import regen_budget

    text = summary(node, cur, cap, source)
    meta = dict(project.meta or {})
    meta[regen_budget.PAUSE_REASON_KEY] = text
    meta["restart_chain_stop"] = {"node": node, "count": cur, "cap": cap, "source": source}
    if project.status != ProjectStatus.paused:
        meta["paused_from_status"] = project.status.value  # «Продолжить» вернёт проект в этот шаг, а не в new
    project.meta = meta
    flag_modified(project, "meta")
    project.status = ProjectStatus.paused
    try:
        await session.flush()
    except Exception:  # noqa: BLE001
        logger.exception("[#{}] restart_chain: flush failed", getattr(project, "id", "?"))
    logger.warning("[#{}] restart_chain: {}", getattr(project, "id", "?"), text)
    try:
        from app.services.observation import record_fact
        await record_fact("runtime_check", {
            "check": "regen_budget", "performed": True, "status": "rejected", "kind": node,
            "restart_chain": True, "rounds": cur, "per_run_cap": cap, "source": source, "summary": text})
    except Exception:  # noqa: BLE001
        logger.debug("restart_chain: observation fact skipped", exc_info=True)
    return text


def release_for_human(project) -> bool:
    """Человек снял паузу: общая цепочка, счётчики regen_budget и авто-ревью начинаются заново (C-11, C-12).

    Вызывать только из ручных путей (кнопка «продолжить/возобновить»), никогда из автоматики."""
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
