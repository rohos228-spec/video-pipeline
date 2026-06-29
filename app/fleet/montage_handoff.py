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


def is_fleet_hub_montage(project: Project) -> bool:
    """Проект импортирован с worker / в очереди монтажа на hub — без 11Labs локально."""
    meta = project.meta or {}
    return bool(
        meta.get("fleet_imported")
        or meta.get("fleet_imported_at")
        or meta.get("fleet_source_project_id")
        or meta.get("montage_ready")
        or meta.get("montage_queue_enqueued")
    )


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
    if not meta.get("fleet_montage_deferred") or not meta.get("montage_ready"):
        return False
    return should_defer_assemble_to_hub(project)


def is_montage_handoff_pending(project: Project) -> bool:
    """Проект ждёт забор bundle hub'ом (UI: сборка «в работе»)."""
    return is_montage_deferred_to_hub(project)


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
    """Agent → hub: немедленно запросить pull (не ждать 45 с цикл)."""
    if (settings.fleet_role or "hub").strip().lower() != "agent":
        return
    if not settings.fleet_enabled:
        return
    meta = project.meta or {}
    if meta.get("fleet_handoff_complete"):
        logger.info("[#{}] fleet: handoff уже complete — hub не уведомляем", project.id)
        return
    hub = settings.fleet_heartbeat_hub_url
    if not hub:
        logger.warning("[#{}] fleet: FLEET_HUB_URL пуст — hub не уведомлён", project.id)
        return
    _warn_unreachable_agent_url(project.id)
    body = {
        "project_id": project.id,
        "slug": project.slug,
        "node_name": settings.fleet_node_name or platform.node(),
        "montage_ready_at": meta.get("montage_ready_at"),
    }
    token = settings.fleet_agent_token or ""
    try:
        result = await agent_post(
            hub,
            token,
            "/api/fleet/montage-ready",
            json_body=body,
            timeout_sec=120,
        )
        logger.info("[#{}] fleet notify hub pull: {}", project.id, result)
    except FleetAgentError as exc:
        detail = exc.detail or ""
        if exc.status == 404 and "route not found" in detail.lower():
            logger.info(
                "[#{}] hub без /montage-ready (старый Studio) — "
                "забор через auto-pull ~45с или Fleet → «На монтаж» на hub",
                project.id,
            )
            return
        logger.warning(
            "[#{}] fleet notify hub pull failed (hub={}): {}",
            project.id,
            hub,
            detail,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[#{}] fleet notify hub pull error: {}", project.id, exc)


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
    meta = bundle_svc.mark_montage_ready(meta)
    meta["fleet_montage_deferred"] = True
    meta.pop("montage_queue_enqueued", None)
    if reason:
        meta["fleet_montage_defer_reason"] = reason[:500]
    project.meta = meta
    project.status = ProjectStatus.music_ready
    await session.flush()
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
    project.meta = meta
    await session.flush()
    logger.info("[#{}] fleet handoff complete ({})", project.id, via)
