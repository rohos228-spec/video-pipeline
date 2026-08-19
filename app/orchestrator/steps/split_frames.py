"""Шаг 3: разбивка — GPT → replace_frames (DB SoT), Excel только экспорт."""

from __future__ import annotations

from aiogram import Bot
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Frame, Project, ProjectStatus
from app.services import xlsx_step_runners as xsr
from app.storage import for_project as _sheet_for_project


async def run(session: AsyncSession, project: Project, bot: Bot | None = None) -> None:
    if project.status is not ProjectStatus.splitting:
        return
    logger.info("[#{}] split_frames (db-first) starting", project.id)

    existing_frames = (
        await session.execute(
            select(Frame)
            .where(Frame.project_id == project.id)
            .order_by(Frame.number)
        )
    ).scalars().all()
    meta = dict(project.meta or {})
    if len(existing_frames) >= 2 and meta.get("split_completed"):
        logger.info(
            "[#{}] split_frames: split_completed + {} кадров — пропуск GPT",
            project.id,
            len(existing_frames),
        )
        project.status = ProjectStatus.frames_ready
        await session.flush()
        return

    result = await xsr.run_split_xlsx(project)
    ops = list(result.apply_ops or [])
    if not ops and result.frames_spec:
        ops = [{"target": "replace_frames", "frames": result.frames_spec}]
    if not ops:
        raise RuntimeError("split_frames: пустой replace_frames после GPT")

    from app.services import db_apply

    applied = await db_apply.apply_ops(session, project, ops, export_xlsx=False)
    logger.info(
        "[#{}] split_frames: replace_frames → {} кадров",
        project.id,
        applied.get("replace_frames") or applied.get("updated"),
    )

    # UI (SplitRowView / База excel_rows) и node snapshot читают project.xlsx R49.
    # Без DB→xlsx после replace_frames снимок остаётся на старых ~N колонках
    # (проект #33: DB=133, xlsx/UI=80).
    try:
        frames_for_export = (
            await session.execute(
                select(Frame)
                .where(Frame.project_id == project.id)
                .order_by(Frame.number)
            )
        ).scalars().all()
        if len(frames_for_export) >= 2:
            exported = db_apply.export_project_xlsx(project, list(frames_for_export))
            logger.info(
                "[#{}] split_frames: export_project_xlsx → {} кадров (cells={})",
                project.id,
                exported.get("frames"),
                exported.get("cells"),
            )
    except Exception as e:  # noqa: BLE001
        logger.warning("[#{}] split_frames: export_project_xlsx failed: {}", project.id, e)

    try:
        from app.services.node_xlsx_snapshot import snapshot_and_bind_node_xlsx

        await snapshot_and_bind_node_xlsx(session, project, node_type="split")
    except Exception as e:  # noqa: BLE001
        logger.warning("[#{}] split xlsx snapshot bind failed: {}", project.id, e)

    try:
        from app.services.storage_step_sync import sync_storage_after_step

        await sync_storage_after_step(
            session, project, "split", log_prefix="split_frames"
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "[#{}] split_frames: sync downstream storage failed: {}",
            project.id,
            e,
        )

    frames = (
        await session.execute(
            select(Frame)
            .where(Frame.project_id == project.id)
            .order_by(Frame.number)
        )
    ).scalars().all()
    if len(frames) < 2:
        raise RuntimeError(
            f"после replace_frames в БД кадров {len(frames)} (нужно ≥2)"
        )

    meta = dict(project.meta or {})
    for key in (
        "enrich_completed_slots",
        "excel_gpt_completed_keys",
        "active_excel_gpt_node_key",
        # Кадры пересозданы — чекпоинты/реестр scene_design невалидны.
        "scene_design",
        "scene_registry",
    ):
        meta.pop(key, None)
    meta["split_completed"] = True
    from app.services.node_step_params import split_params_fingerprint

    meta["split_params_applied"] = split_params_fingerprint(project)
    project.meta = meta
    project.status = ProjectStatus.frames_ready
    await session.flush()
    logger.info("[#{}] split_frames: {} кадров в DB", project.id, len(frames))

    try:
        _sheet_for_project(project).write_general(status=project.status.value)
    except Exception as e:  # noqa: BLE001
        logger.warning("[#{}] project_sheet split write failed: {}", project.id, e)

    await session.commit()
    from app.services.agent_harness import harness_gate_or_raise

    await harness_gate_or_raise(session, project, step="split")
    await session.commit()
