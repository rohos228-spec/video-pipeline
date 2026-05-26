"""Шаг 5: промты картинок одним xlsx round-trip в ChatGPT.

Мастер-промт уходит файлом; в чат — только override или дефолт.
"""

from __future__ import annotations

from pathlib import Path

from aiogram import Bot
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Frame, FrameStatus, Project, ProjectStatus
from app.services import chatgpt_xlsx as cx
from app.services.step_cancel import StepCancelledError, raise_if_cancelled
from app.storage import for_project as _sheet_for_project


async def run(session: AsyncSession, project: Project, bot: Bot) -> None:
    if project.status is not ProjectStatus.generating_image_prompts:
        return
    logger.info(
        "[#{}] generate_image_prompts (xlsx-flow) starting", project.id
    )

    frames = (
        await session.execute(
            select(Frame).where(Frame.project_id == project.id).order_by(Frame.number)
        )
    ).scalars().all()
    if not frames:
        raise RuntimeError("нет кадров — нечего составлять промты")

    sheet = _sheet_for_project(project)
    try:
        sheet.ensure_frame_columns(len(frames))
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "[#{}] xlsx ensure_frame_columns failed: {}", project.id, e
        )

    xlsx_path: Path = sheet.ensure_initialized(
        project_id=project.id, slug=project.slug
    )
    if not xlsx_path.exists():
        raise RuntimeError(
            f"generate_image_prompts: project.xlsx не найден: {xlsx_path}"
        )

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
            from app.services import xlsx_step_runners as xsr

            await xsr.run_img_pr_xlsx(
                project, n_frames=len(frames), project_id=project.id
            )
            await xsr.sync_after_img_pr(session, project, xlsx_path)
            await session.refresh(project)
            frames = (
                await session.execute(
                    select(Frame)
                    .where(Frame.project_id == project.id)
                    .order_by(Frame.number)
                )
            ).scalars().all()
            if all(fr.image_prompt for fr in frames):
                break
            raise RuntimeError(
                f"после xlsx-sync промты не заполнены (попытка {attempt})"
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
            logger.warning(
                "[#{}] не смог refresh project после ⏹", project.id
            )
        return

    missing = [fr.number for fr in frames if not fr.image_prompt]
    if missing:
        raise RuntimeError(
            f"GPT не заполнил image_prompt для кадров: {missing}"
        )

    for fr in frames:
        fr.status = FrameStatus.image_prompt_ready
        await session.flush()
        try:
            sheet.write_frame(
                fr.number,
                image_prompt=fr.image_prompt,
                frame_status=fr.status.value,
                gen_type="image",
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "[#{}] xlsx write image_prompt frame {} failed: {}",
                project.id,
                fr.number,
                e,
            )
        logger.info(
            "[#{}] frame {}: image_prompt готов ({} симв)",
            project.id,
            fr.number,
            len(fr.image_prompt or ""),
        )

    project.status = ProjectStatus.image_prompts_ready
    await session.flush()
    logger.info(
        "[#{}] generate_image_prompts complete: {} промтов (xlsx-flow)",
        project.id,
        len(frames),
    )
    _ = last_err
