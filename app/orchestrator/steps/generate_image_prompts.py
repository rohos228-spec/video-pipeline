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
from app.services.xlsx_v8_import import (
    read_v8_active_frame_count,
    read_v8_image_prompts_from_path,
)
from app.storage import for_project as _sheet_for_project


def _frames_needing_image_prompt(
    frames: list[Frame],
    *,
    xlsx_path: Path | None = None,
    project_id: int | None = None,
) -> list[Frame]:
    """Кадры, для которых обязателен image_prompt после img_pr.

    Если в xlsx N колонок voiceover — проверяем только кадры 1..N.
    Лишние Frame в БД (старый split) не должны валить шаг.
    """
    if xlsx_path is not None:
        n = read_v8_active_frame_count(xlsx_path)
        if n > 0:
            active = [fr for fr in frames if fr.number <= n]
            extra = [
                fr.number
                for fr in frames
                if fr.number > n and (fr.voiceover_text or "").strip()
            ]
            if extra:
                logger.warning(
                    "[#{}] img_pr: в БД есть кадры {}, в xlsx только {} колонок — "
                    "image_prompt для них не требуем",
                    project_id or "?",
                    extra,
                    n,
                )
            return active
    return [fr for fr in frames if (fr.voiceover_text or "").strip()]


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

    n_xlsx = read_v8_active_frame_count(xlsx_path)
    n_work = n_xlsx if n_xlsx > 0 else len(frames)
    if n_xlsx and n_xlsx < len(frames):
        logger.info(
            "[#{}] generate_image_prompts: xlsx {} кадров, в БД {} — "
            "работаем по xlsx",
            project.id,
            n_xlsx,
            len(frames),
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
                project, n_frames=n_work, project_id=project.id
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
            need = _frames_needing_image_prompt(frames, xlsx_path=xlsx_path)
            if need and all((fr.image_prompt or "").strip() for fr in need):
                break
            missing = [
                fr.number
                for fr in need
                if not (fr.image_prompt or "").strip()
            ]
            filled_r45 = len(read_v8_image_prompts_from_path(xlsx_path))
            raise RuntimeError(
                f"после xlsx-sync промты не заполнены (попытка {attempt}): "
                f"кадры {missing}; в xlsx {n_xlsx or '?'} колонок voiceover, "
                f"строка R45 заполнена для {filled_r45}"
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

    need = _frames_needing_image_prompt(
        frames, xlsx_path=xlsx_path, project_id=project.id
    )
    missing = [
        fr.number for fr in need if not (fr.image_prompt or "").strip()
    ]
    if missing:
        raise RuntimeError(
            f"GPT не заполнил image_prompt для кадров: {missing}"
        )

    for fr in need:
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
