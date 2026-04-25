"""Шаг 6–7: для каждого кадра — промт картинки (ChatGPT web) + генерация
картинки (outsee nano-banana-2). Генерация и HITL-проверка работают
ПАРАЛЛЕЛЬНО:

  1. Сначала бот получает в ChatGPT промты для всех кадров, у которых их
     ещё нет.
  2. Затем по очереди генерирует картинки в outsee (nano-banana одна
     очередь — параллельно нельзя), каждую сразу шлёт в TG как HITL-
     карточку с 4 кнопками и НЕ БЛОКИРУЕТСЯ на ожидании решения —
     продолжает генерить следующую.
  3. После того как все кадры «выпущены» в TG, loop ждёт пока каждый
     из них станет либо approved, либо failed. Параллельно обрабатывает
     возникающие 🔁 / ✏️ решения — ставит соответствующий кадр на
     повторную генерацию и запускает новый outsee-проход.

Таким образом пока ты одобряешь кадр N, бот уже может генерить кадр N+1.
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
    logger.info("[#{}] generate_images starting (parallel mode)", project.id)

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

        # ---- Главный цикл: интерливинг промт↔картинка, без ожидания HITL ---
        # На каждой итерации:
        #   1) Подхватываем решения regenerate/edit_prompt → возвращаем кадр
        #      в image_prompt_ready.
        #   2) Ищем первый «необработанный» кадр (planned или image_prompt_ready).
        #      - planned без prompt → просим у ChatGPT промт → image_prompt_ready;
        #      - image_prompt_ready → гоним в outsee → image_generated + HITL.
        #   3) Если необработанных нет — ждём решений пользователя.
        #   4) Выход, когда каждый кадр approved или failed.
        while True:
            await _apply_pending_regens(session, project.id)

            target, action = await _next_frame_and_action(session, project.id)
            if action == "prompt" and target is not None:
                prompt_ask = _build_prompt_ask(image_master, hero_line, target)
                image_prompt = await gpt.ask_fresh(prompt_ask, timeout=240)
                if not image_prompt or len(image_prompt) < 40:
                    raise RuntimeError(
                        f"пустой image_prompt на кадре {target.number}"
                    )
                target.image_prompt = image_prompt
                target.status = FrameStatus.image_prompt_ready
                await session.commit()
                logger.info(
                    "[#{}] frame {}: prompt готов ({} симв) → передаю в outsee",
                    project.id,
                    target.number,
                    len(image_prompt),
                )
                continue

            if action == "generate" and target is not None:
                await _generate_and_send(
                    session, bot, outsee, project, target, out_dir
                )
                continue

            # нечего делать — все сгенерены, ждём кнопок пользователя
            if await _all_frames_settled(session, project.id):
                break
            await asyncio.sleep(3)

    project.status = ProjectStatus.images_ready
    await session.flush()
    logger.info("[#{}] generate_images complete", project.id)


# ---------------------------------------------------------------------------


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


async def _next_frame_and_action(
    session: AsyncSession, project_id: int
) -> tuple[Frame | None, str]:
    """Ищет следующий кадр и тип действия:
      - ("prompt", fr) — кадру нужен image_prompt (planned без promt или
        image_prompt пуст);
      - ("generate", fr) — кадру нужен outsee-прогон (image_prompt_ready);
      - (None, "") — активных задач нет, ждём решений.
    Выбираем первый кадр, которому нужен promt; если таких нет —
    первый, которому нужен outsee."""
    frames = (
        await session.execute(
            select(Frame)
            .where(Frame.project_id == project_id)
            .order_by(Frame.number)
        )
    ).scalars().all()
    # 1) prompt-потребители
    for fr in frames:
        if fr.status in (FrameStatus.image_approved, FrameStatus.failed):
            continue
        if not fr.image_prompt:
            return fr, "prompt"
        if fr.status == FrameStatus.image_prompt_ready:
            # сразу за проптом идёт генерация — возвращаем тот же кадр
            return fr, "generate"
    # 2) generate-потребители (на случай если prompt уже есть, а status
    # image_prompt_ready — например, после regen).
    for fr in frames:
        if fr.status == FrameStatus.image_prompt_ready:
            return fr, "generate"
    return None, ""


async def _all_frames_settled(session: AsyncSession, project_id: int) -> bool:
    """True если у каждого кадра статус image_approved или failed."""
    frames = (
        await session.execute(
            select(Frame)
            .where(Frame.project_id == project_id)
            .order_by(Frame.number)
        )
    ).scalars().all()
    for fr in frames:
        if fr.status not in (FrameStatus.image_approved, FrameStatus.failed):
            return False
    return True


async def _apply_pending_regens(session: AsyncSession, project_id: int) -> None:
    """Находит HITL-решения regenerate/edit_prompt, которые ещё не
    «потреблены», возвращает соответствующие кадры в image_prompt_ready
    и помечает HITL как consumed."""
    hitls = (
        await session.execute(
            select(HITLRequest)
            .where(HITLRequest.project_id == project_id)
            .where(HITLRequest.kind == HITLKind.approve_images)
            .where(
                HITLRequest.decision.in_(
                    [HITLDecision.regenerate, HITLDecision.edit_prompt]
                )
            )
            .order_by(HITLRequest.id.desc())
        )
    ).scalars().all()
    for h in hitls:
        payload = dict(h.payload or {})
        if payload.get("consumed"):
            continue
        if h.frame_id is None:
            payload["consumed"] = True
            h.payload = payload
            continue
        frame = (
            await session.execute(select(Frame).where(Frame.id == h.frame_id))
        ).scalar_one_or_none()
        if frame is None:
            payload["consumed"] = True
            h.payload = payload
            continue
        # Возвращаем кадр в очередь на outsee. Выбор «Повторить» vs
        # заполнение промта делается в _generate_and_send на основе
        # последнего решения пользователя.
        frame.status = FrameStatus.image_prompt_ready
        payload["consumed"] = True
        h.payload = payload
        logger.info(
            "[#{}] frame {}: повторная генерация по решению '{}' (HITL #{})",
            project_id,
            frame.number,
            h.decision.value,
            h.id,
        )
    await session.flush()


async def _generate_and_send(
    session: AsyncSession,
    bot: Bot,
    outsee: OutseeBot,
    project: Project,
    frame: Frame,
    out_dir: Path,
) -> None:
    """Один прогон outsee → сохранение артефакта → HITL-карточка."""
    # Проверяем последний HITL: если последнее решение было regenerate —
    # используем кнопку «Повторить» (без перезаполнения промта); иначе —
    # обычная генерация с текущим image_prompt.
    last_hitl = (
        await session.execute(
            select(HITLRequest)
            .where(HITLRequest.frame_id == frame.id)
            .where(HITLRequest.kind == HITLKind.approve_images)
            .order_by(HITLRequest.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    use_regen_button = (
        last_hitl is not None
        and last_hitl.decision is HITLDecision.regenerate
    )

    attempt = (
        await session.execute(
            select(HITLRequest)
            .where(HITLRequest.frame_id == frame.id)
            .where(HITLRequest.kind == HITLKind.approve_images)
        )
    ).scalars().all()
    attempt_number = len(attempt) + 1

    file_path = out_dir / f"frame_{frame.number:03d}_{uuid.uuid4().hex[:8]}.png"
    logger.info(
        "[#{}] frame {} attempt {}: outsee {}",
        project.id,
        frame.number,
        attempt_number,
        "regenerate" if use_regen_button else "generate",
    )
    if use_regen_button:
        try:
            result = await outsee.regenerate_image(file_path)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "[#{}] frame {}: «Повторить» не сработала ({}) — падаем на generate",
                project.id,
                frame.number,
                e,
            )
            result = await outsee.generate_image(
                frame.image_prompt, file_path, aspect_ratio="9:16"
            )
    else:
        result = await outsee.generate_image(
            frame.image_prompt, file_path, aspect_ratio="9:16"
        )

    art = Artifact(
        project_id=project.id,
        frame_id=frame.id,
        kind=ArtifactKind.scene_image,
        uuid=uuid.uuid4().hex,
        path=str(result.file_path),
    )
    session.add(art)
    frame.status = FrameStatus.image_generated
    await session.flush()

    caption = (
        f"Кадр #{frame.number} / {project.id}. Попытка {attempt_number}.\n"
        f"{(frame.voiceover_text or '')[:600]}"
    )
    await send_hitl_photo(
        bot,
        session,
        project,
        kind=HITLKind.approve_images,
        photo_path=str(result.file_path),
        caption=caption,
        payload={
            "step": "image",
            "frame_id": frame.id,
            "attempt": attempt_number,
        },
        frame_id=frame.id,
        allow_edit=True,
    )
    # Коммитим сразу, чтобы callback-хендлер в другом таске видел HITL.
    await session.commit()
