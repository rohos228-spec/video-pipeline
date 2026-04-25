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
from app.bots.outsee import OutseeBot, OutseeImageError
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
from app.storage import for_project as _sheet_for_project


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

    sheet = _sheet_for_project(project)
    try:
        sheet.ensure_frame_columns(len(frames))
    except Exception as e:  # noqa: BLE001
        logger.warning("[#{}] project_sheet ensure_frame_columns failed: {}", project.id, e)

    async with browser_session() as bs:
        gpt = ChatGPTBot(bs)
        outsee = OutseeBot(bs)

        # ---- Phase 1: промты для всех кадров -------------------------------
        for fr in frames:
            if fr.status in (FrameStatus.image_approved, FrameStatus.failed):
                continue
            if fr.image_prompt:
                # уже есть в БД — но xlsx могли не успеть записать раньше;
                # синканём (не страшно, это просто запись в человекочитаемое).
                try:
                    sheet.write_frame(fr.number, image_prompt=fr.image_prompt)
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        "[#{}] xlsx write_frame(prompt sync) failed: {}",
                        project.id,
                        e,
                    )
                continue
            prompt_ask = _build_prompt_ask(image_master, hero_line, fr)
            image_prompt = await gpt.ask_fresh(prompt_ask, timeout=240)
            if not image_prompt or len(image_prompt) < 40:
                raise RuntimeError(f"пустой image_prompt на кадре {fr.number}")
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
                    "[#{}] xlsx write_frame(image_prompt) failed: {}",
                    project.id,
                    e,
                )
            logger.info(
                "[#{}] frame {}: prompt готов ({} симв)",
                project.id,
                fr.number,
                len(image_prompt),
            )
        await session.commit()

        # ---- Phase 2+3: генерация картинок + обработка regen/edit ----------
        # Главный цикл не блокируется на HITL-решениях пользователя. Он:
        #   - берёт следующий кадр, которому нужна генерация (image_prompt_ready),
        #   - генерит, сохраняет, шлёт HITL-карточку, коммит,
        #   - переходит к следующему такому кадру,
        #   - когда все кадры сгенерированы — ждёт решений пользователя;
        #     при 🔁/✏️ возвращает кадр в image_prompt_ready и снова его гонит.
        #   - выходит когда каждый кадр либо image_approved, либо failed.
        while True:
            # 1) подхватить HITL-решения, требующие перегенерации
            await _apply_pending_regens(session, project.id)

            # 2) взять следующий кадр к обработке
            target = await _next_frame_to_process(session, project.id)
            if target is not None:
                await _generate_and_send(
                    session, bot, outsee, project, target, out_dir
                )
                continue

            # 3) все кадры обработаны? (approved / failed)
            if await _all_frames_settled(session, project.id):
                break

            # 4) иначе ждём пока пользователь нажмёт кнопку в TG
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


async def _next_frame_to_process(
    session: AsyncSession, project_id: int
) -> Frame | None:
    """Ищет первый кадр в статусе image_prompt_ready — т.е. «готов к outsee»."""
    frames = (
        await session.execute(
            select(Frame)
            .where(Frame.project_id == project_id)
            .order_by(Frame.number)
        )
    ).scalars().all()
    for fr in frames:
        if fr.status == FrameStatus.image_prompt_ready:
            return fr
    return None


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

    gen_id = uuid.uuid4().hex
    file_path = out_dir / f"frame_{frame.number:03d}_{gen_id[:8]}.png"
    logger.info(
        "[#{}] frame {} attempt {} gen_id={}: outsee {}",
        project.id,
        frame.number,
        attempt_number,
        gen_id[:8],
        "regenerate" if use_regen_button else "generate",
    )
    sheet = _sheet_for_project(project)
    try:
        sheet.write_frame(
            frame.number,
            image_gen_id=gen_id,
            attempt=attempt_number,
            frame_status="image_generating",
            last_error="",
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("[#{}] xlsx write_frame(gen_id) failed: {}", project.id, e)

    try:
        if use_regen_button:
            try:
                result = await outsee.regenerate_image(file_path, gen_id=gen_id)
            except OutseeImageError:
                # Если на странице нет предыдущего результата (или другая
                # «структурная» ошибка regenerate) — падаем на полноценный
                # generate с тем же gen_id, чтобы не плодить ложных файлов.
                logger.warning(
                    "[#{}] frame {}: «Повторить» не сработала — падаю на generate",
                    project.id,
                    frame.number,
                )
                result = await outsee.generate_image(
                    frame.image_prompt,
                    file_path,
                    aspect_ratio="9:16",
                    gen_id=gen_id,
                )
        else:
            result = await outsee.generate_image(
                frame.image_prompt,
                file_path,
                aspect_ratio="9:16",
                gen_id=gen_id,
            )
    except OutseeImageError as e:
        # Не «возьму последнюю картинку», не silent retry: помечаем кадр
        # failed и шлём в TG понятное описание ошибки (с gen_id, baseline-ом
        # и тем что нашли). Пайплайн пойдёт к следующему кадру; общая логика
        # анти-зацикливания (MAX_FAIL=3) защитит проект целиком.
        logger.exception(
            "[#{}] frame {}: outsee fail (gen_id={})",
            project.id,
            frame.number,
            gen_id[:8],
        )
        frame.status = FrameStatus.failed
        try:
            sheet.write_frame(
                frame.number,
                frame_status=frame.status.value,
                last_error=e.format_text()[:1500],
            )
        except Exception:  # noqa: BLE001
            pass
        await session.flush()
        try:
            await bot.send_message(
                settings.telegram_owner_chat_id,
                (
                    f"⚠️ Кадр #{frame.number} проекта #{project.id}: "
                    f"картинку поймать не удалось.\n\n"
                    f"<pre>{_html_escape(e.format_text())}</pre>"
                )[:3800],
                parse_mode="HTML",
            )
        except Exception:  # noqa: BLE001
            pass
        await session.commit()
        return

    art = Artifact(
        project_id=project.id,
        frame_id=frame.id,
        kind=ArtifactKind.scene_image,
        uuid=uuid.uuid4().hex,
        path=str(result.file_path),
        meta={"gen_id": gen_id, "raw_url": result.raw_url or ""},
    )
    session.add(art)
    frame.status = FrameStatus.image_generated
    await session.flush()

    try:
        sheet.write_frame(
            frame.number,
            image_path=str(result.file_path),
            image_url=result.raw_url,
            frame_status=frame.status.value,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("[#{}] xlsx write_frame(image_path) failed: {}", project.id, e)

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
            "gen_id": gen_id,
        },
        frame_id=frame.id,
        allow_edit=True,
    )
    # Коммитим сразу, чтобы callback-хендлер в другом таске видел HITL.
    await session.commit()


def _html_escape(s: str) -> str:
    import html as _h

    return _h.escape(s)
