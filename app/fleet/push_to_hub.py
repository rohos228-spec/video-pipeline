"""Agent: упаковать проект и отправить bundle на hub (без pull с hub)."""

from __future__ import annotations

import asyncio

from loguru import logger

from app.db import session_scope
from app.fleet import bundle as bundle_svc
from app.fleet.client import FleetAgentError, agent_upload_file_path
from app.fleet.transfer_state import FleetTransferCancelled
from app.models import Project
from app.settings import settings


async def push_project_bundle_to_hub(project_id: int) -> dict:
    from app.fleet.transfer_state import check_transfer_cancelled

    check_transfer_cancelled(project_id)
    hub = (settings.fleet_hub_url or "").strip().rstrip("/")
    if not hub:
        raise ValueError("FLEET_HUB_URL пуст")
    if (settings.fleet_role or "hub").strip().lower() != "agent":
        raise ValueError("push-to-hub только на agent (NucBox)")

    async with session_scope() as session:
        project = await session.get(Project, project_id)
        if project is None:
            raise ValueError(f"project #{project_id} not found")
        data_dir = project.data_dir.resolve()
        meta = dict(project.meta or {})
        meta["fleet_send_requested"] = True
        meta.pop("fleet_transfer_aborted", None)
        project.meta = meta
        await session.flush()
        manifest = {
            "slug": project.slug,
            "topic": project.topic,
            "status": project.status.value
            if hasattr(project.status, "value")
            else str(project.status),
            "meta": meta,
        }
        slug = project.slug
        node_name = settings.fleet_node_name or "agent"
        ready_at = str(meta.get("montage_ready_at") or "")

    logger.info("[#{}] push-to-hub: packing bundle …", project_id)
    from app.fleet.transfer_state import update_fleet_transfer

    await update_fleet_transfer(
        project_id,
        phase="packing",
        direction="to_hub",
        percent=0,
        message="Упаковка bundle…",
        target=hub,
        slug=slug,
        source_node=node_name,
    )
    from app.fleet.transfer_state import check_transfer_cancelled

    check_transfer_cancelled(project_id)
    try:
        bundle_path, filename, _from_cache = await asyncio.to_thread(
            bundle_svc.get_or_build_bundle_file,
            project_id=project_id,
            slug=slug,
            data_dir=data_dir,
            manifest=manifest,
            ready_at=ready_at,
        )
    except FleetTransferCancelled:
        await update_fleet_transfer(
            project_id,
            phase="cancelled",
            direction="to_hub",
            percent=0,
            message="⏹ Остановлено пользователем",
            status="error",
        )
        raise
    try:
        check_transfer_cancelled(project_id)
        size_mb = bundle_path.stat().st_size / (1024 * 1024)
        if _from_cache:
            await update_fleet_transfer(
                project_id,
                phase="packing",
                direction="to_hub",
                percent=100,
                total_mb=size_mb,
                sent_mb=size_mb,
                message=f"Bundle готов ({size_mb:.0f} MB, из кэша)",
                target=hub,
                slug=slug,
                source_node=node_name,
            )
        logger.info(
            "[#{}] push-to-hub: uploading {:.0f} MB → {}",
            project_id,
            size_mb,
            hub,
        )
        token = settings.fleet_agent_token or ""
        result = await agent_upload_file_path(
            hub,
            token,
            "/api/fleet/import-bundle",
            bundle_path,
            filename=f"{slug}-fleet-bundle.tar.gz",
            timeout_sec=7200,
            progress_label=f"[#{project_id}] push-to-hub",
            project_id=project_id,
            extra_form={
                "run_assemble": "true",
                "source_node": node_name,
                "source_project_id": str(project_id),
            },
        )
        logger.info("[#{}] push-to-hub: hub response {}", project_id, result)
        async with session_scope() as session:
            project = await session.get(Project, project_id)
            if project is not None:
                from app.fleet.montage_handoff import mark_handoff_complete

                await mark_handoff_complete(session, project, via="push")
                await session.commit()
        await update_fleet_transfer(
            project_id,
            phase="done",
            direction="to_hub",
            percent=100,
            total_mb=size_mb,
            sent_mb=size_mb,
            message=f"Отправлено на hub ({size_mb:.0f} MB)",
            target=hub,
            slug=slug,
            source_node=node_name,
            status="done",
        )
        return {"ok": True, "hub": hub, "size_mb": round(size_mb, 1), **result}
    except FleetTransferCancelled:
        await update_fleet_transfer(
            project_id,
            phase="cancelled",
            direction="to_hub",
            percent=0,
            message="⏹ Остановлено пользователем",
            status="error",
        )
        raise
    except FleetAgentError as exc:
        await update_fleet_transfer(
            project_id,
            phase="error",
            direction="to_hub",
            percent=0,
            message=str(exc.detail)[:200],
            target=hub,
            slug=slug,
            status="error",
        )
        if exc.status == 404:
            raise ValueError(
                "hub без /api/fleet/import-bundle — на главном ПК запусти FLEET-HOTFIX.cmd"
            ) from exc
        raise ValueError(f"hub HTTP {exc.status}: {exc.detail[:300]}") from exc
