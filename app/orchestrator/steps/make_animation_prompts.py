"""Шаг 8: промты анимации через ChatGPT web (один диалог, пачки по 5 картинок).

Схема (как в TG-боте / ручном процессе):
  1) Новый чат → мастер-промт + закадровый текст (все кадры).
  2) В том же чате → до 5 изображений + для каждого ID и закадровый текст.
  3) Парсим «ID изображения» / «текст анимации» → план R48 + БД.
  4) Повторяем 2–3, пока есть картинки без animation_prompt.
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
    if not pending:
        logger.info("[#{}] make_animation_prompts: nothing to do", project.id)
        project.status = ProjectStatus.animation_prompts_ready
        await session.flush()
        return

    already_done = sum(1 for f in frames if (f.animation_prompt or "").strip())
    need_initial_chat = already_done == 0

    tmp_dir = tmp_gpt_dir(project)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    prompt_file = write_anim_pr_prompt_file(project, tmp_dir, ts=ts)
    initial = apg.build_initial_message(
        project, frames, prompt_file_name=prompt_file.name
    )
    sheet = _sheet_for_project(project)

    async with browser_session() as bs:
        gpt = ChatGPTBot(bs)
        try:
            if need_initial_chat:
                await gpt.new_conversation()
                raise_if_cancelled(project.id)
                logger.info(
                    "[#{}] anim_pr: новый чат → prompt_file={} ({} байт), chat {} симв.",
                    project.id,
                    prompt_file.name,
                    prompt_file.stat().st_size,
                    len(initial),
                )
                await gpt.ask_with_files(
                    initial,
                    [prompt_file],
                    timeout=300,
                    project_id=project.id,
                )
            else:
                logger.info(
                    "[#{}] anim_pr: тот же чат ChatGPT (уже {} промтов в БД), "
                    "пропускаю initial — следующая пачка картинок",
                    project.id,
                    already_done,
                )
                await gpt._page_ready()
                raise_if_cancelled(project.id)

            while True:
                raise_if_cancelled(project.id)
                pending = apg.collect_batch_items(project, frames)
                if not pending:
                    break

                batch = pending[: apg.BATCH_SIZE]
                paths = [it.image_path for it in batch]
                batch_msg = apg.build_batch_message(batch)
                logger.info(
                    "[#{}] anim_pr: batch {} frames ({}) — тот же диалог, msg {} симв.",
                    project.id,
                    len(batch),
                    [it.frame.number for it in batch],
                    len(batch_msg),
                )
                for it in batch:
                    logger.debug(
                        "[#{}] anim_pr batch item F{} id={} vo={}…",
                        project.id,
                        it.frame.number,
                        it.image_id,
                        (it.voiceover[:40] + "…")
                        if len(it.voiceover) > 40
                        else it.voiceover,
                    )
                reply = await gpt.ask_with_files(
                    batch_msg,
                    paths,
                    timeout=600,
                    project_id=project.id,
                )
                pairs = apg.parse_animation_reply(reply, frames, batch_items=batch)
                if not pairs:
                    raise RuntimeError(
                        "ChatGPT не вернул пары «ID изображения» / «текст анимации» "
                        f"для кадров {[it.frame.number for it in batch]}"
                    )

                saved = 0
                for pair in pairs:
                    if pair.frame_number is None:
                        continue
                    fr = next((f for f in frames if f.number == pair.frame_number), None)
                    if fr is None:
                        continue
                    text = pair.animation_text.strip()
                    if len(text) < 10:
                        continue
                    fr.animation_prompt = text
                    fr.status = FrameStatus.animation_prompt_ready
                    xlsx_ok = write_plan_animation_prompt(project, fr.number, text)
                    if xlsx_ok:
                        saved += 1
                    else:
                        logger.warning(
                            "[#{}] anim_pr: не записан plan R48 для кадра {}",
                            project.id,
                            fr.number,
                        )
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
                    logger.info(
                        "[#{}] anim_pr: frame {} prompt len={} (plan R48={})",
                        project.id,
                        fr.number,
                        len(text),
                        xlsx_ok,
                    )

                await session.flush()
                await session.commit()
                logger.info(
                    "[#{}] anim_pr: пачка сохранена — {} промтов в project.xlsx (план R48)",
                    project.id,
                    saved,
                )

                # Если GPT вернул не все кадры пачки — не зацикливаемся на тех же файлах
                still_missing = {it.frame.number for it in batch} - {
                    p.frame_number for p in pairs if p.frame_number is not None
                }
                if still_missing:
                    raise RuntimeError(
                        f"не получены animation_prompt для кадров {sorted(still_missing)}"
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
