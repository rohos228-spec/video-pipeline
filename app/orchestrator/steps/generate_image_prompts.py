"""Шаг 5: для каждого кадра — промт картинки (ChatGPT web).
Только промты, картинки не генерим (это шаг 6: generate_images).

Входной статус: generating_image_prompts (выставляется бот-меню).
Выходной статус: image_prompts_ready (либо falled, если ChatGPT упал).

Каждый промт пишется в БД (frame.image_prompt) И в xlsx
(строка «промт картинки», столбец = номер кадра)."""

from __future__ import annotations

from aiogram import Bot
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bots.browser import browser_session
from app.bots.chatgpt import ChatGPTBot
from app.models import Frame, FrameStatus, Project, ProjectStatus, PromptKey
from app.services.prompts import get_active_prompt
from app.storage import for_project as _sheet_for_project


async def run(session: AsyncSession, project: Project, bot: Bot) -> None:
    if project.status is not ProjectStatus.generating_image_prompts:
        return
    logger.info("[#{}] generate_image_prompts starting", project.id)

    image_master = await get_active_prompt(session, PromptKey.IMAGE_SHORTS)

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

    hero_line = ""
    if project.hero_description:
        hero_line = (
            "\n\nЭталонное описание главного героя (использовать, если он в кадре):\n"
            + project.hero_description
        )

    async with browser_session() as bs:
        gpt = ChatGPTBot(bs)
        for fr in frames:
            if fr.image_prompt:
                # уже есть в БД — синканём в xlsx и идём дальше
                try:
                    sheet.write_frame(fr.number, image_prompt=fr.image_prompt)
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        "[#{}] xlsx sync image_prompt frame {} failed: {}",
                        project.id,
                        fr.number,
                        e,
                    )
                continue

            prompt_ask = _build_prompt_ask(image_master, hero_line, fr)
            image_prompt = await gpt.ask_fresh(prompt_ask, timeout=240)
            if not image_prompt or len(image_prompt) < 40:
                raise RuntimeError(
                    f"пустой image_prompt на кадре {fr.number}"
                )
            fr.image_prompt = image_prompt
            fr.status = FrameStatus.image_prompt_ready
            await session.flush()
            try:
                sheet.write_frame(
                    fr.number,
                    image_prompt=image_prompt,
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
                len(image_prompt),
            )

    project.status = ProjectStatus.image_prompts_ready
    await session.flush()
    logger.info("[#{}] generate_image_prompts complete", project.id)


def _build_prompt_ask(image_master: str, hero_line: str, fr: Frame) -> str:
    return (
        image_master
        + hero_line
        + "\n\n---\n\nЗадача: составь ОДИН готовый текст промта для "
        + "генерации картинки этого кадра (на английском, строго по "
        + "правилам выше, включая блок `--no ...` в конце).\n\n"
        + f"Номер кадра: {fr.number}\n"
        + f"Длительность: {fr.duration_seconds} сек\n"
        + f"Закадровый текст: {fr.voiceover_text}\n"
        + (f"Смысл: {fr.meaning}\n" if fr.meaning else "")
    )
