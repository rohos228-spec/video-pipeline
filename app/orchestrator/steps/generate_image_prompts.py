"""Шаг img_pr: промты картинок через apply-ops (DB SoT)."""

from __future__ import annotations

from aiogram import Bot
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Frame, FrameStatus, Project, ProjectStatus
from app.services.step_cancel import StepCancelledError, raise_if_cancelled
from app.storage import for_project as _sheet_for_project


def _frames_needing_image_prompt(frames: list[Frame]) -> list[Frame]:
    return [
        fr
        for fr in frames
        if (fr.voiceover_text or "").strip()
        and not (fr.image_prompt or "").strip()
    ]


async def run(session: AsyncSession, project: Project, bot: Bot) -> None:
    if project.status is not ProjectStatus.generating_image_prompts:
        return
    logger.info("[#{}] generate_image_prompts (db-first) starting", project.id)

    from app.services import db_v2

    await db_v2.backfill_project_v2(session, project)

    frames = (
        await session.execute(
            select(Frame).where(Frame.project_id == project.id).order_by(Frame.number)
        )
    ).scalars().all()
    if not frames:
        raise RuntimeError("нет кадров — нечего составлять промты")

    need = _frames_needing_image_prompt(frames)
    if not need:
        raise RuntimeError("нет кадров с закадром — нечего составлять промты")

    uuid_map = "\n".join(
        f"кадр {fr.number} = {fr.uuid}" for fr in need if fr.uuid
    )
    if not uuid_map.strip():
        raise RuntimeError("у кадров нет uuid — backfill не сработал")

    sheet = _sheet_for_project(project)
    try:
        sheet.ensure_initialized(project_id=project.id, slug=project.slug)
    except Exception as e:  # noqa: BLE001
        logger.warning("[#{}] ensure_initialized: {}", project.id, e)

    cancelled = False
    last_err: Exception | None = None
    for attempt in range(1, 3):
        try:
            raise_if_cancelled(project.id)
        except StepCancelledError as e:
            logger.info(
                "[#{}] generate_image_prompts: {} — выхожу", project.id, e
            )
            cancelled = True
            break
        try:
            from app.services import db_apply, xlsx_step_runners as xsr

            result = await xsr.run_img_pr_xlsx(
                project,
                n_frames=len(need),
                project_id=project.id,
                uuid_map_text=uuid_map,
            )
            ops = list(result.apply_ops or [])
            if not ops:
                raise RuntimeError("пустой apply-ops после GPT")
            await db_apply.apply_ops(session, project, ops, export_xlsx=True)
            await session.commit()

            try:
                from app.services.node_xlsx_snapshot import snapshot_and_bind_node_xlsx

                await snapshot_and_bind_node_xlsx(
                    session, project, node_type="image_prompts"
                )
            except Exception as snap_err:  # noqa: BLE001
                logger.warning(
                    "[#{}] image_prompts snapshot failed: {}",
                    project.id,
                    snap_err,
                )

            await session.refresh(project)
            frames = (
                await session.execute(
                    select(Frame)
                    .where(Frame.project_id == project.id)
                    .order_by(Frame.number)
                )
            ).scalars().all()
            need = _frames_needing_image_prompt(frames)
            if not need:
                from app.services import img_pr_batches as ipb

                ipb.clear_checkpoint(project.data_dir)
                break
            missing = [fr.number for fr in need]
            raise RuntimeError(
                f"после apply-ops промты не заполнены (попытка {attempt}): "
                f"кадры {missing[:20]}{'…' if len(missing) > 20 else ''} "
                f"(осталось {len(missing)}; чекпоинт img_pr сохранит прогресс)"
            )
        except Exception as e:  # noqa: BLE001
            last_err = e
            logger.warning(
                "[#{}] generate_image_prompts attempt {} failed: {}",
                project.id,
                attempt,
                e,
            )
            if attempt >= 2:
                raise RuntimeError(
                    f"generate_image_prompts: не удалось получить промты: {e}"
                ) from e

    if cancelled:
        try:
            await session.refresh(project)
        except Exception:  # noqa: BLE001
            pass
        return

    need = _frames_needing_image_prompt(frames)
    missing = [fr.number for fr in need if not (fr.image_prompt or "").strip()]
    if missing:
        raise RuntimeError(
            f"GPT не заполнил image_prompt для кадров: {missing}"
        )

    for fr in need:
        fr.status = FrameStatus.image_prompt_ready
    project.status = ProjectStatus.image_prompts_ready
    await session.flush()

    await session.commit()
    from app.services.agent_harness import harness_gate_or_raise

    await harness_gate_or_raise(session, project, step="img_pr")
    await session.commit()

    logger.info(
        "[#{}] generate_image_prompts complete: {} промтов (DB)",
        project.id,
        len(need),
    )
    _ = last_err
