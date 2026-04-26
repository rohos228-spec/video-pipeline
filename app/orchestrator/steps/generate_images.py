"""Шаг 6: генерация картинок по уже готовым промтам (outsee nano-banana-2).

Промты должны быть подготовлены на шаге 5 (generate_image_prompts).
Этот шаг только генерит и валидирует картинки.

Входной статус: generating_images.
Выходной статус: images_ready.

Алгоритм (НЕ БЛОКИРУЕТСЯ на ожидании approve пользователя):
  1. Берёт следующий кадр в статусе image_prompt_ready.
  2. Генерит картинку в outsee, сохраняет файл, шлёт в TG карточку
     с кнопками ✅/🔁/❌/✏ — но НЕ ждёт решения, переходит дальше.
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
from app.bots.outsee import OutseeBot, OutseeImageError
from app.generation_options import (
    ASPECT_RATIOS_BY_ID,
    DEFAULTS,
    IMAGE_GENERATORS_BY_ID,
    IMAGE_RESOLUTIONS_BY_ID,
    build_gen_id_prefix,
)
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
)
from app.services.hitl import send_hitl_photo
from app.settings import settings
from app.storage import for_project as _sheet_for_project


async def run(session: AsyncSession, project: Project, bot: Bot) -> None:
    if project.status is not ProjectStatus.generating_images:
        return
    logger.info("[#{}] generate_images starting", project.id)

    frames = (
        await session.execute(
            select(Frame).where(Frame.project_id == project.id).order_by(Frame.number)
        )
    ).scalars().all()
    if not frames:
        raise RuntimeError("нет кадров — нечего генерировать")

    # Все кадры должны иметь image_prompt (шаг 5 уже выполнен).
    missing_prompts = [fr.number for fr in frames if not fr.image_prompt]
    if missing_prompts:
        raise RuntimeError(
            f"нет image_prompt у кадров: {missing_prompts}. "
            "Сначала запусти шаг 5 (Промты картинок)."
        )

    out_dir = Path(settings.data_dir) / "videos" / project.slug / "scenes"

    sheet = _sheet_for_project(project)
    try:
        sheet.ensure_frame_columns(len(frames))
    except Exception as e:  # noqa: BLE001
        logger.warning("[#{}] project_sheet ensure_frame_columns failed: {}", project.id, e)

    # Кадры, у которых нет картинки (статус не image_generated/image_approved)
    # → ставим в image_prompt_ready, чтобы цикл их подхватил.
    for fr in frames:
        if fr.status in (
            FrameStatus.image_approved,
            FrameStatus.failed,
            FrameStatus.image_generated,
        ):
            continue
        fr.status = FrameStatus.image_prompt_ready
    await session.flush()

    async with browser_session() as bs:
        outsee = OutseeBot(bs)
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

            # 3) все кадры обработаны? (approved / failed / image_generated)
            if await _all_frames_have_image_or_failed(session, project.id):
                break

            # 4) иначе ждём пока пользователь нажмёт кнопку в TG
            await asyncio.sleep(3)

    project.status = ProjectStatus.images_ready
    await session.flush()
    logger.info("[#{}] generate_images complete", project.id)


# ---------------------------------------------------------------------------


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


async def _all_frames_have_image_or_failed(
    session: AsyncSession, project_id: int
) -> bool:
    """True если у каждого кадра картинка сгенерирована/одобрена или статус
    failed. В ручном режиме мы не ждём явного approve, но если пользователь
    нажал ✅ — это тоже считается."""
    frames = (
        await session.execute(
            select(Frame)
            .where(Frame.project_id == project_id)
            .order_by(Frame.number)
        )
    ).scalars().all()
    for fr in frames:
        if fr.status not in (
            FrameStatus.image_approved,
            FrameStatus.image_generated,
            FrameStatus.failed,
        ):
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
    short_uuid = gen_id[:8]
    file_path = out_dir / f"frame_{frame.number:03d}_{short_uuid}.png"
    prompt_id_prefix = build_gen_id_prefix(project.id, frame.number, short_uuid)

    # Настройки картинки из проекта (с дефолтами).
    img_gen = IMAGE_GENERATORS_BY_ID.get(
        project.image_generator or DEFAULTS["image_generator"]
    )
    ar = ASPECT_RATIOS_BY_ID.get(
        project.aspect_ratio or DEFAULTS["aspect_ratio"]
    )
    ir = IMAGE_RESOLUTIONS_BY_ID.get(
        project.image_resolution or DEFAULTS["image_resolution"]
    )
    aspect_slug = ar.outsee_slug if ar else "9:16"
    model_slug = img_gen.outsee_slug if img_gen else None
    res_slug = ir.outsee_slug if ir else None
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
                    aspect_ratio=aspect_slug,
                    gen_id=gen_id,
                    model_slug=model_slug,
                    resolution=res_slug,
                    prompt_id_prefix=prompt_id_prefix,
                )
        else:
            result = await outsee.generate_image(
                frame.image_prompt,
                file_path,
                aspect_ratio=aspect_slug,
                gen_id=gen_id,
                model_slug=model_slug,
                resolution=res_slug,
                prompt_id_prefix=prompt_id_prefix,
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
        f"{prompt_id_prefix}\n"
        f"Кадр #{frame.number} / P{project.id}. Попытка {attempt_number}.\n"
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
            "prompt_id_prefix": prompt_id_prefix,
            "photo_path": str(result.file_path),
        },
        frame_id=frame.id,
        allow_edit=True,
    )
    # Коммитим сразу, чтобы callback-хендлер в другом таске видел HITL.
    await session.commit()


def _html_escape(s: str) -> str:
    import html as _h

    return _h.escape(s)
