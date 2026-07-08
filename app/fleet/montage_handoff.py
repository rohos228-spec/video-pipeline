"""Agent: не монтировать локально — отдать bundle на hub (send_to_main_pc)."""

from __future__ import annotations

import platform

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.fleet import bundle as bundle_svc
from app.fleet.client import FleetAgentError, agent_post
from app.models import Project, ProjectStatus
from app.services.node_step_params import send_to_main_pc_for_project
from app.settings import settings


def should_defer_assemble_to_hub(project: Project) -> bool:
    """True — этот узел не должен запускать assemble (монтаж на hub)."""
    if not settings.fleet_enabled:
        return False
    if not send_to_main_pc_for_project(project):
        return False
    role = (settings.fleet_role or "hub").strip().lower()
    if role == "agent":
        return True
    meta = project.meta or {}
    if role == "hub" and meta.get("fleet_imported"):
        return False
    return False


def is_montage_deferred_to_hub(project: Project) -> bool:
    """Montage уже отдан на hub — auto_advance не должен снова жать assemble."""
    meta = project.meta or {}
    if meta.get("fleet_handoff_complete"):
        return False
    if meta.get("fleet_transfer_aborted") or meta.get("user_stop"):
        return False
    if not meta.get("fleet_montage_deferred") or not meta.get("montage_ready"):
        return False
    return should_defer_assemble_to_hub(project)


def is_montage_handoff_pending(project: Project) -> bool:
    """Проект ждёт ручную отправку (UI: баннер «Отправить»)."""
    return is_montage_deferred_to_hub(project)


def is_hub_auto_pull_eligible(meta: dict) -> bool:
    """Hub auto-pull: только после явного «Отправить» на agent."""
    if meta.get("fleet_handoff_complete"):
        return False
    if meta.get("fleet_transfer_aborted") or meta.get("user_stop"):
        return False
    if not meta.get("fleet_send_requested"):
        return False
    if not meta.get("fleet_montage_deferred") or not meta.get("montage_ready"):
        return False
    return True


def clear_fleet_handoff_pending(meta: dict) -> dict:
    """STOP / cancel: убрать «ждёт отправки», не тянуть снова."""
    meta = dict(meta)
    for key in (
        "fleet_montage_deferred",
        "fleet_send_requested",
        "fleet_montage_defer_reason",
        "montage_ready",
        "montage_ready_at",
    ):
        meta.pop(key, None)
    return meta


def _warn_unreachable_agent_url(project_id: int) -> None:
    base = settings.fleet_agent_base_url
    if base.startswith("http://127.") or base.startswith("http://localhost"):
        logger.warning(
            "[#{}] fleet: FLEET_PUBLIC_URL не задан (agent URL={}) — "
            "hub не сможет скачать bundle с этого ПК",
            project_id,
            base,
        )


async def notify_hub_montage_ready(project: Project) -> None:
    """Отключено: авто-pull по montage-ready больше не используется."""
    logger.debug("[#{}] notify_hub_montage_ready ignored (manual push only)", project.id)


async def defer_assemble_to_hub(
    session: AsyncSession,
    project: Project,
    *,
    reason: str = "",
) -> bool:
    """Пометить montage_ready и вернуть music_ready — hub заберёт bundle."""
    if not should_defer_assemble_to_hub(project):
        return False

    meta = dict(project.meta or {})
    if meta.get("fleet_handoff_complete"):
        return False
    if meta.get("fleet_transfer_aborted") or meta.get("user_stop"):
        return False
    meta = bundle_svc.mark_montage_ready(meta)
    meta["fleet_montage_deferred"] = True
    meta.pop("montage_queue_enqueued", None)
    if reason:
        meta["fleet_montage_defer_reason"] = reason[:500]
    project.meta = meta
    project.status = ProjectStatus.music_ready
    await session.flush()
    from sqlalchemy.orm.attributes import flag_modified

    flag_modified(project, "meta")
    from app.fleet.transfer_state import is_transfer_blocked, update_fleet_transfer

    if not is_transfer_blocked(project.id) and not meta.get("user_stop"):
        await update_fleet_transfer(
            project.id,
            phase="waiting",
            direction="to_hub",
            percent=0,
            message="Нажми «Отправить на главный ПК» — авто-отправки нет",
            slug=project.slug,
            source_node=settings.fleet_node_name or "",
            target=(settings.fleet_hub_url or "").strip(),
        )
    logger.info(
        "[#{}] ✓ deferred → hub (music_ready). Отправка только вручную — кнопка на канвасе. {}",
        project.id,
        reason or "",
    )
    return True


async def mark_handoff_complete(
    session: AsyncSession,
    project: Project,
    *,
    via: str = "push",
) -> None:
    """После успешной передачи bundle — больше не тянуть auto-pull / не показывать «ждёт отправки»."""
    from datetime import datetime, timezone

    meta = dict(project.meta or {})
    meta["fleet_handoff_complete"] = True
    meta["fleet_handoff_at"] = datetime.now(timezone.utc).isoformat()
    meta["fleet_handoff_via"] = via
    meta.pop("fleet_montage_deferred", None)
    meta.pop("fleet_montage_defer_reason", None)
    meta.pop("fleet_send_requested", None)
    project.meta = meta
    await session.flush()
    logger.info("[#{}] fleet handoff complete ({})", project.id, via)
