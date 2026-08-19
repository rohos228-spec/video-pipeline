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
        from app.services.node_step_params import (
            split_cell_limits_from_project,
            split_params_fingerprint,
        )
        from app.services.voiceover_split_local import (
            enforce_split_cell_limits,
            frames_spec_cell_stats,
        )

        applied_fp = str(meta.get("split_params_applied") or "")
        current_fp = split_params_fingerprint(project)
        mn, mx, amn, amx = split_cell_limits_from_project(project)
        texts_now = [
            str(getattr(fr, "voiceover_text", None) or "").strip()
            for fr in existing_frames
        ]
        texts_now = [t for t in texts_now if t]
        stats_now = frames_spec_cell_stats([{"закадр": t} for t in texts_now])
        limits_ok = True
        if texts_now and (mn is not None or mx is not None):
            lo = mn if mn is not None else 1
            hi = mx if mx is not None else 10_000
            limits_ok = all(lo <= len(t) <= hi for t in texts_now)

        if applied_fp and applied_fp == current_fp and limits_ok:
            logger.info(
                "[#{}] split_frames: split_completed + {} кадров + "
                "params={} — пропуск GPT",
                project.id,
                len(existing_frames),
                current_fp,
            )
            project.status = ProjectStatus.frames_ready
            await session.flush()
            return

        if applied_fp and applied_fp == current_fp and not limits_ok:
            # Старый enforce оставлял хвосты < min — чиним локально без GPT.
            enforced = enforce_split_cell_limits(
                texts_now, min_chars=mn, max_chars=mx, avg_min=amn, avg_max=amx
            )
            if len(enforced) >= 2:
                from app.services import db_apply

                ops = [{"target": "replace_frames", "frames": [{"закадр": t} for t in enforced]}]
                applied = await db_apply.apply_ops(
                    session, project, ops, export_xlsx=False
                )
                after = frames_spec_cell_stats([{"закадр": t} for t in enforced])
                logger.info(
                    "[#{}] split_frames: local re-enforce {}-{} "
                    "frames {}→{} len {}/{}/{} → {}/{}/{} (без GPT)",
                    project.id,
                    mn,
                    mx,
                    stats_now.get("n"),
                    after.get("n"),
                    stats_now.get("min"),
                    stats_now.get("avg"),
                    stats_now.get("max"),
                    after.get("min"),
                    after.get("avg"),
                    after.get("max"),
                )
                meta = dict(project.meta or {})
                meta["split_completed"] = True
                meta["split_params_applied"] = current_fp
                project.meta = meta
                project.status = ProjectStatus.frames_ready
                await session.flush()
                await session.commit()
                logger.info(
                    "[#{}] split_frames: re-enforce → {} кадров",
                    project.id,
                    applied.get("replace_frames") or len(enforced),
                )
                return
            logger.warning(
                "[#{}] split_frames: limits broken but re-enforce failed — GPT",
                project.id,
            )

        logger.info(
            "[#{}] split_frames: settings changed ({} → {}) — re-run GPT",
            project.id,
            applied_fp or "—",
            current_fp,
        )

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
