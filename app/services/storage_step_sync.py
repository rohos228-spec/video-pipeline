"""После шага plan/script/split — забрать артефакты в downstream storage."""

from __future__ import annotations

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Project
from app.services.canvas_graph import find_canvas_node_key_by_type
from app.services.node_xlsx_snapshot import find_node_key_for_type
from app.services.storage_node import sync_downstream_storage_from_node


async def sync_storage_after_step(
    session: AsyncSession,
    project: Project,
    node_type: str,
    *,
    log_prefix: str,
) -> list[dict]:
    """Refresh meta → найти ноду → sync storage; всегда логируем исход.

    Без refresh воркер может не видеть свежие стрелки UI (script→storage),
    и voiceover попадает в хранилище только позже через poll autoSync.
    """
    try:
        await session.refresh(project)
    except Exception:  # noqa: BLE001
        logger.debug(
            "[#{}] {}: refresh before storage sync failed",
            project.id,
            log_prefix,
            exc_info=True,
        )

    node_key = await find_node_key_for_type(session, project, node_type)
    if not node_key:
        node_key = find_canvas_node_key_by_type(
            project.meta if isinstance(project.meta, dict) else {},
            node_type,
        )
    if not node_key:
        logger.info(
            "[#{}] {}: storage sync skip — нет ноды type={}",
            project.id,
            log_prefix,
            node_type,
        )
        return []

    synced = sync_downstream_storage_from_node(project, node_key)
    if not synced:
        logger.info(
            "[#{}] {}: storage sync — нет стрелок {} → storage",
            project.id,
            log_prefix,
            node_key,
        )
        return []

    for info in synced:
        logger.info(
            "[#{}] {}: storage {} ← {} copied={} skipped={} errors={}",
            project.id,
            log_prefix,
            info.get("storageNode"),
            node_key,
            info.get("copied"),
            info.get("skipped"),
            info.get("errors"),
        )
    await session.flush()
    return synced
