"""Шаг 6–7: для каждого кадра — промт картинки (ChatGPT web) + генерация
(outsee nano-banana-2). Каждый кадр отправляется в TG отдельной HITL-карточкой
с 4 кнопками (✅ Одобрить / 🔁 Перегенерировать / ✏️ Изменить промт / ❌ Отклонить).
Бот по одному кадру ждёт решение, и либо едет дальше, либо перегенерирует.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

from aiogram import Bot
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bots.browser import browser_session
from app.bots.chatgpt import ChatGPTBot
from app.bots.outsee import OutseeBot
from app.db import session_scope
from app.models import (
    Artifact,
    ArtifactKind,
    Frame,
    FrameStatus,
    HITLDecision,
    HITLKind,
    HITLRequest,
    Project,
    ProjectStatus,
    PromptKey,
)
from app.services.hitl import send_hitl_photo
from app.services.prompts import get_active_prompt
from app.settings import settings


async def run(session: AsyncSession, project: Project, bot: Bot) -> None:
    if project.status is not ProjectStatus.hero_ready:
        return
    logger.info("[#{}] generate_images starting", project.id)

    image_master = await get_active_prompt(session, PromptKey.IMAGE_SHORTS)
    frames = (
        await session.execute(
            select(Frame).where(Frame.project_id == project.id).order_by(Frame.number)
        )
    ).scalars().all()
    if not frames:
        raise RuntimeError("нет кадров — нечего генерировать")

    out_dir = Path(settings.data_dir) / "videos" / project.slug / "scenes"
    hero_line = ""
    if project.hero_description:
        hero_line = (
            "\n\nЭталонное описание главного героя (использовать, если он в кадре):\n"
            + project.hero_description
        )

    async with browser_session() as bs:
        gpt = ChatGPTBot(bs)
        outsee = OutseeBot(bs)

        for fr in frames:
            # Если кадр уже одобрен/отклонён ранее — пропускаем.
            if fr.status in (
                FrameStatus.image_approved,
                FrameStatus.animation_prompt_ready,
                FrameStatus.video_generated,
                FrameStatus.video_approved,
                FrameStatus.done,
                FrameStatus.failed,
            ):
                continue

            # 1) получаем промт (если ещё не получен)
            if not fr.image_prompt:
                prompt_ask = (
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
                image_prompt = await gpt.ask_fresh(prompt_ask, timeout=240)
                if not image_prompt or len(image_prompt) < 40:
                    raise RuntimeError(f"пустой image_prompt на кадре {fr.number}")
                fr.image_prompt = image_prompt
                fr.status = FrameStatus.image_prompt_ready
                await session.flush()

            # 2) per-frame цикл: генерим → карточка → ждём решение.
            await _review_frame(session, bot, outsee, project, fr, out_dir)

    project.status = ProjectStatus.images_ready
    await session.flush()
    logger.info("[#{}] generate_images complete, все кадры обработаны", project.id)


async def _review_frame(
    session: AsyncSession,
    bot: Bot,
    outsee: OutseeBot,
    project: Project,
    fr: Frame,
    out_dir: Path,
) -> None:
    """Генерирует картинку для кадра, шлёт HITL-карточку, ждёт решение.
    На 🔁 — регенерит тем же промтом через «Повторить»; на ✏️ — ждёт нового
    промта из TG-ответа и регенерит с ним; на ✅ — уходит; на ❌ — failed."""
    attempt = 0
    use_regenerate_button = False  # использовать ли кнопку «Повторить» на outsee

    while True:
        attempt += 1
        file_path = out_dir / f"frame_{fr.number:03d}_{uuid.uuid4().hex[:8]}.png"
        logger.info(
            "[#{}] frame {} attempt {}, regen={}",
            project.id,
            fr.number,
            attempt,
            use_regenerate_button,
        )
        if use_regenerate_button:
            # тот же промт, нажимаем «Повторить» на outsee
            result = await outsee.regenerate_image(file_path)
        else:
            # свежая генерация с заполнением textarea
            result = await outsee.generate_image(
                fr.image_prompt, file_path, aspect_ratio="9:16"
            )

        art = Artifact(
            project_id=project.id,
            frame_id=fr.id,
            kind=ArtifactKind.scene_image,
            uuid=uuid.uuid4().hex,
            path=str(result.file_path),
        )
        session.add(art)
        fr.status = FrameStatus.image_generated
        await session.flush()

        # HITL-карточка
        caption = (
            f"Кадр #{fr.number} / {project.id}. Попытка {attempt}.\n"
            f"{(fr.voiceover_text or '')[:600]}"
        )
        req = await send_hitl_photo(
            bot,
            session,
            project,
            kind=HITLKind.approve_images,
            photo_path=str(result.file_path),
            caption=caption,
            payload={"step": "image", "frame_id": fr.id, "attempt": attempt},
            frame_id=fr.id,
            allow_edit=True,
        )
        # Коммит, чтобы callback-handler в другом таске видел HITL.
        await session.commit()

        decision = await _wait_decision(req.id)
        # перезагружаем frame из БД (возможно image_prompt был обновлён)
        await session.refresh(fr)

        if decision is HITLDecision.approved:
            fr.status = FrameStatus.image_approved
            await session.flush()
            return
        if decision is HITLDecision.rejected:
            fr.status = FrameStatus.failed
            await session.flush()
            return
        if decision is HITLDecision.regenerate:
            use_regenerate_button = True
            continue
        if decision is HITLDecision.edit_prompt:
            # frame.image_prompt уже обновлён в on_owner_text_reply
            use_regenerate_button = False
            continue
        # pending / прочее — странная ситуация, выходим
        raise RuntimeError(f"неожиданное решение {decision} для HITL #{req.id}")


async def _wait_decision(hitl_id: int, *, poll: float = 2.0) -> HITLDecision:
    while True:
        async with session_scope() as s:
            req = (
                await s.execute(select(HITLRequest).where(HITLRequest.id == hitl_id))
            ).scalar_one_or_none()
            if req is None:
                raise RuntimeError(f"HITL #{hitl_id} исчез")
            if req.decision is not HITLDecision.pending:
                return req.decision
        await asyncio.sleep(poll)
