"""Шаг 8: промты анимации через ChatGPT web (один диалог, пачки по 5 кадров).

Схема:
  1) Один раз: сопр. промт + файл мастер-промта (без картинок).
  2) Дальше в том же чате: одна PNG-лента (до 5 кадров слева→направо,
     между ними белые вертикальные разделители) + ID и закадровый текст.
  3) Парсим ответ → plan R48 (shot_01) и R64 (shot_02) + БД; повторяем 2–3.
"""

from __future__ import annotations

from datetime import datetime

from aiogram import Bot  # noqa: F401
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bots.browser import browser_session
from app.bots.chatgpt import ChatGPTBot
from app.models import Frame, FrameStatus, Project, ProjectStatus
from app.services import animation_prompt_gpt as apg
from app.services.chatgpt_xlsx import tmp_gpt_dir, write_anim_pr_prompt_file
from app.services.step_cancel import StepCancelledError, consume_stop, raise_if_cancelled
from app.storage import for_project as _sheet_for_project
from app.storage.plan_sheet_v8 import write_plan_animation_prompt


async def run(session: AsyncSession, project: Project, bot: Bot) -> None:
    if project.status is not ProjectStatus.generating_animation_prompts:
        return
    logger.info("[#{}] make_animation_prompts starting (batch GPT flow)", project.id)

    synced = await apg.sync_animation_prompts_from_xlsx(session, project)
    frames = (
        await session.execute(
            select(Frame).where(Frame.project_id == project.id).order_by(Frame.number)
        )
    ).scalars().all()

    for fr in frames:
        vo = apg.voiceover_for_frame(project, fr)
        if vo and not (fr.voiceover_text or "").strip():
            fr.voiceover_text = vo

    pending = apg.collect_batch_items(project, frames)
    pending_shot2 = apg.collect_shot2_batch_items(project, frames)
    already_done, xlsx_filled, with_image = apg.count_animation_prompt_stats(
        project, frames
    )
    if not pending and not pending_shot2:
        from app.services.project_state import compute_actual_status

        project.status = await compute_actual_status(session, project)
        logger.info(
            "[#{}] make_animation_prompts: nothing to do (synced={}, "
            "plan R48={}, картинок на диске={}) → status={}",
            project.id,
            synced,
            xlsx_filled,
            with_image,
            project.status.value,
        )
        await session.flush()
        return

    logger.info(
        "[#{}] anim_pr: очередь shot_01={} shot_02={} (synced={}, plan R48={}, png={})",
        project.id,
        len(pending),
        len(pending_shot2),
        synced,
        xlsx_filled,
        with_image,
    )

    skip_phase1 = already_done > 0 or (not pending and bool(pending_shot2))
    if skip_phase1:
        first_batch = pending if pending else pending_shot2
        first_pending = first_batch[0].frame.number
        logger.info(
            "[#{}] anim_pr: догонка — {} готово, synced={}, первая пачка с кадра {} "
            "(shot_01={}, shot_02={})",
            project.id,
            already_done,
            synced,
            first_pending,
            len(pending),
            len(pending_shot2),
        )

    tmp_dir = tmp_gpt_dir(project)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    prompt_file = write_anim_pr_prompt_file(project, tmp_dir, ts=ts)
    initial = apg.build_initial_message(
        project, frames, prompt_file_name=prompt_file.name
    )
    sheet = _sheet_for_project(project)

    logger.info("[#{}] anim_pr: подключаю Chrome (CDP)…", project.id)
    async with browser_session() as bs:
        gpt = ChatGPTBot(bs)
        try:
            logger.info("[#{}] anim_pr: новый чат ChatGPT…", project.id)
            await gpt.new_conversation()
            raise_if_cancelled(project.id)
            if not skip_phase1:
                logger.info(
                    "[#{}] anim_pr: новый чат → ФАЗА 1 только текст+файл {} ({} байт), {} симв.",
                    project.id,
                    prompt_file.name,
                    prompt_file.stat().st_size,
                    len(initial),
                )
                initial_reply = await gpt.ask_anim_pr_initial(
                    initial,
                    prompt_file,
                    timeout=300,
                    project_id=project.id,
                )
                if not (initial_reply or "").strip():
                    logger.warning(
                        "[#{}] anim_pr: пустой ответ на ФАЗУ 1 — всё равно шлём пачки фото",
                        project.id,
                    )
                raise_if_cancelled(project.id)
            else:
                logger.info(
                    "[#{}] anim_pr: ФАЗА 1 пропущена — {} промтов уже в xlsx/БД",
                    project.id,
                    already_done,
                )

            while True:
                raise_if_cancelled(project.id)
                pending = apg.collect_batch_items(project, frames)
                if not pending:
                    break

                batch = pending[: apg.BATCH_SIZE]
                strip_path = apg.build_batch_strip_path(batch, tmp_dir)
                batch_msg = apg.build_batch_message(batch)
                logger.info(
                    "[#{}] anim_pr: ФАЗА 2 shot_01 batch {} кадров {} — лента {} ({} симв.)",
                    project.id,
                    len(batch),
                    [it.frame.number for it in batch],
                    strip_path.name,
                    len(batch_msg),
                )
                reply = await gpt.ask_anim_pr_batch(
                    batch_msg,
                    [strip_path],
                    timeout=600,
                    project_id=project.id,
                )
                await _save_anim_pr_batch(
                    session,
                    project,
                    frames,
                    batch,
                    reply,
                    sheet,
                    shot=1,
                )

            while True:
                raise_if_cancelled(project.id)
                pending2 = apg.collect_shot2_batch_items(project, frames)
                if not pending2:
                    break

                batch2 = pending2[: apg.BATCH_SIZE]
                strip2 = apg.build_batch_strip_path(batch2, tmp_dir)
                batch_msg2 = apg.build_batch_message_shot2(batch2)
                logger.info(
                    "[#{}] anim_pr: ФАЗА 2 shot_02 batch {} кадров {} — лента {} ({} симв.)",
                    project.id,
                    len(batch2),
                    [it.frame.number for it in batch2],
                    strip2.name,
                    len(batch_msg2),
                )
                reply2 = await gpt.ask_anim_pr_batch(
                    batch_msg2,
                    [strip2],
                    timeout=600,
                    project_id=project.id,
                )
                await _save_anim_pr_batch(
                    session,
                    project,
                    frames,
                    batch2,
                    reply2,
                    sheet,
                    shot=2,
                )

        except StepCancelledError as e:
            consume_stop(project.id)
            logger.info(
                "[#{}] make_animation_prompts: {} — выхожу из цикла",
                project.id,
                e,
            )
            try:
                await session.refresh(project)
            except Exception:  # noqa: BLE001
                logger.warning("[#{}] не смог refresh project после ⏹", project.id)
            return

    project.status = ProjectStatus.animation_prompts_ready
    await session.flush()
    try:
        sheet.write_general(status=project.status.value)
    except Exception as e:  # noqa: BLE001
        logger.warning("[#{}] xlsx write_general(status) failed: {}", project.id, e)


async def _save_anim_pr_batch(
    session: AsyncSession,
    project: Project,
    frames: list[Frame],
    batch: list[apg.FrameImageBatchItem],
    reply: str,
    sheet,
    *,
    shot: int,
) -> None:
    pairs = apg.parse_animation_reply(reply, frames, batch_items=batch)
    if not pairs:
        raise RuntimeError(
            "ChatGPT не вернул пары «ID изображения» / «текст анимации» "
            f"для кадров {[it.frame.number for it in batch]} (shot_0{shot})"
        )

    saved = 0
    plan_row = 48 if shot == 1 else 64
    for pair in pairs:
        if pair.frame_number is None:
            continue
        fr = next((f for f in frames if f.number == pair.frame_number), None)
        if fr is None:
            continue
        text = pair.animation_text.strip()
        if len(text) < 10:
            continue
        if shot == 1:
            fr.animation_prompt = text
            fr.status = FrameStatus.animation_prompt_ready
            xlsx_ok = write_plan_animation_prompt(project, fr.number, text)
            try:
                sheet.write_frame(
                    fr.number,
                    animation_prompt=text,
                    frame_status=fr.status.value,
                )
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "[#{}] xlsx write_frame(animation_prompt) failed: {}",
                    project.id,
                    e,
                )
        else:
            xlsx_ok = apg.save_animation_prompt_shot2(fr, project, text)
        if xlsx_ok:
            saved += 1
        else:
            logger.warning(
                "[#{}] anim_pr: не записан plan R{} для кадра {} shot_0{}",
                project.id,
                plan_row,
                fr.number,
                shot,
            )
        logger.info(
            "[#{}] anim_pr: frame {} shot_0{} prompt len={} (plan R{}={})",
            project.id,
            fr.number,
            shot,
            len(text),
            plan_row,
            xlsx_ok,
        )

    await session.flush()
    await session.commit()
    logger.info(
        "[#{}] anim_pr: пачка shot_0{} — {} промтов в project.xlsx (plan R{})",
        project.id,
        shot,
        saved,
        plan_row,
    )

    still_missing = {it.frame.number for it in batch} - {
        p.frame_number for p in pairs if p.frame_number is not None
    }
    if still_missing:
        raise RuntimeError(
            f"не получены animation_prompt shot_0{shot} для кадров {sorted(still_missing)}"
        )
