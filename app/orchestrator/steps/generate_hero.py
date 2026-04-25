"""Шаг 5: генерация референса главного героя (антропоморфного кота).

Только если project.hero_mode in {"hero", "auto+...}. Решение о необходимости
ГГ — в project.hero_needed (проставляется шагом make_plan на основе плана от GPT)
либо в режиме "auto" ориентируемся на флаг hero_mode.

Сейчас: проверяем hero_mode. Если "no_hero" — шаг пропускается, проект сразу
движется к images_ready. В следующей итерации добавим поддержку hero_needed из
plan-вывода, когда вручную доведём парсер.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from aiogram import Bot
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.bots.browser import browser_session
from app.bots.chatgpt import ChatGPTBot
from app.bots.outsee import OutseeBot
from app.models import (
    Artifact,
    ArtifactKind,
    HITLKind,
    Project,
    ProjectStatus,
    PromptKey,
)
from app.services.hitl import send_hitl_photo
from app.services.prompts import get_active_prompt
from app.settings import settings


async def run(session: AsyncSession, project: Project, bot: Bot) -> None:
    if project.status is not ProjectStatus.frames_ready:
        return

    if project.hero_mode == "no_hero":
        logger.info("[#{}] hero skipped (hero_mode=no_hero)", project.id)
        project.status = ProjectStatus.hero_ready
        return

    logger.info("[#{}] generate_hero starting", project.id)

    async with browser_session() as bs:
        # 1) описание внешности героя (ChatGPT web) — берём из общего плана.
        #    Если уже есть в project.hero_description (retry после падения на
        #    outsee) — не дёргаем ChatGPT повторно.
        if project.hero_description and len(project.hero_description) >= 50:
            hero_prompt = project.hero_description
            logger.info(
                "[#{}] reuse cached hero_description ({} chars)",
                project.id,
                len(hero_prompt),
            )
        else:
            image_master = await get_active_prompt(
                session, PromptKey.IMAGE_SHORTS
            )
            hero_ask = (
                image_master
                + "\n\n---\n\nЗадача: на основе темы и общего плана ниже составь "
                + "ОДИН описательный промт для генерации эталонного референс-"
                + "изображения главного героя-кота. Только описание внешности, "
                + "позы и атмосферы; без указаний на конкретную сцену. "
                + "Формат 9:16.\n\n"
                + "Тема: " + (project.topic or "") + "\n\n"
                + "Общий план:\n" + (project.general_plan or "")
            )
            gpt = ChatGPTBot(bs)
            hero_prompt = await gpt.ask_fresh(hero_ask, timeout=300)
            if not hero_prompt or len(hero_prompt) < 50:
                raise RuntimeError("ChatGPT не вернул описание героя")
            project.hero_description = hero_prompt
            await session.flush()

        # 2) генерация референса в outsee nano-banana-2
        outsee = OutseeBot(bs)
        out_dir = Path(settings.data_dir) / "videos" / project.slug / "characters"
        file_name = f"hero_{uuid.uuid4().hex[:8]}.png"
        out_path = out_dir / file_name
        result = await outsee.generate_image(
            hero_prompt, out_path, aspect_ratio="9:16"
        )

    # 3) сохраняем в БД + HITL
    art = Artifact(
        project_id=project.id,
        kind=ArtifactKind.hero_reference,
        uuid=uuid.uuid4().hex,
        path=str(result.file_path),
    )
    session.add(art)
    project.status = ProjectStatus.hero_ready
    await session.flush()

    await send_hitl_photo(
        bot, session, project,
        kind=HITLKind.approve_hero,
        photo_path=str(result.file_path),
        caption=f"Референс ГГ для #{project.id}. Одобрить?",
        payload={"step": "hero", "artifact_id": art.id},
    )
