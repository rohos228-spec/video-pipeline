"""Шаг 8: промты анимации через GPT API (пачки по 5 кадров + vision).

Схема:
  1) Один раз: сопр. промт + файл мастер-промта (без картинок).
  2) Дальше: PNG-лента (до 5 кадров) + ID и закадровый текст (vision).
  3) Парсим ответ → plan R48 (shot_01) и R64 (shot_02) + БД; повторяем 2–3.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

from aiogram import Bot  # noqa: F401
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Frame, FrameStatus, Project, ProjectStatus
from app.services import animation_prompt_gpt as apg
from app.services import db_apply, db_v2
from app.services.chatgpt_xlsx import tmp_gpt_dir, write_anim_pr_prompt_file
from app.services.animation_prompt_local import build_local_ops_for_missing
from app.services.gpt_api import GptApiError
from app.services.gpt_client import get_gpt_client
from app.services.step_cancel import StepCancelledError, consume_stop, raise_if_cancelled
from app.services.volume_batches import inferred_batch_size
from app.storage import for_project as _sheet_for_project

# kie maintenance / 5xx: не валим шаг — ждём провайдера и повторяем пачку.
_BATCH_BACKOFF_S = (45, 90, 180, 300, 300)
_PHASE1_MAX_RETRIES = 1
# После N outage на одной пачке — local Veo fallback на весь остаток.
_BATCH_OUTAGE_BEFORE_LOCAL = 3


class _LocalFallbackNeeded(RuntimeError):
    """kie outage слишком долгий — добиваем локальным composer."""


def _provider_outage(exc: BaseException) -> bool:
    if isinstance(exc, GptApiError) and exc.retryable:
        return True
    msg = str(exc).lower()
    return (
        "server exception" in msg
        or "being maintained" in msg
        or "try again later" in msg
        or "code=500" in msg
        or "code=502" in msg
        or "code=503" in msg
    )


async def _ask_batch_until_ok(
    gpt,
    batch_msg: str,
    strip_path: Path | None,
    *,
    project_id: int,
    system: str | None,
    label: str,
) -> str:
    """Пачка (vision или text-only): при outage kie — backoff; после лимита → local."""
    attempt = 0
    images = [strip_path] if strip_path is not None else []
    while True:
        raise_if_cancelled(project_id)
        try:
            return await gpt.ask_anim_pr_batch(
                batch_msg,
                images,
                timeout=600,
                project_id=project_id,
                system=system,
                max_retries=2,
            )
        except Exception as e:  # noqa: BLE001
            if not _provider_outage(e):
                raise
            attempt += 1
            if attempt >= _BATCH_OUTAGE_BEFORE_LOCAL:
                raise _LocalFallbackNeeded(str(e)) from e
            delay = _BATCH_BACKOFF_S[min(attempt - 1, len(_BATCH_BACKOFF_S) - 1)]
            logger.warning(
                "[#{}] anim_pr: {} GPT outage ({}) — повтор #{}/{}, жду {}с",
                project_id,
                label,
                e,
                attempt,
                _BATCH_OUTAGE_BEFORE_LOCAL,
                delay,
            )
            await asyncio.sleep(delay)


async def _fill_remaining_local(
    session: AsyncSession,
    project: Project,
    frames: list[Frame],
    sheet,
) -> int:
    """Добить пустые shot_01 промты локальным Veo-composer (без GPT)."""
    ops = build_local_ops_for_missing(frames, shot=1)
    if not ops:
        return 0
    logger.warning(
        "[#{}] anim_pr: local fallback — заполняю {} кадров без GPT",
        project.id,
        len(ops),
    )
    saved = 0
    for i in range(0, len(ops), 40):
        part = ops[i : i + 40]
        result = await db_apply.apply_ops(session, project, part, export_xlsx=True)
        saved += int(result.get("updated") or 0)
        await session.commit()
    for fr in frames:
        if (fr.animation_prompt or "").strip():
            fr.status = FrameStatus.animation_prompt_ready
            try:
                sheet.write_frame(fr.number, frame_status=fr.status.value)
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "[#{}] xlsx write_frame(status) failed: {}", project.id, e
                )
    await session.flush()
    return saved


async def run(session: AsyncSession, project: Project, bot: Bot) -> None:
    if project.status is not ProjectStatus.generating_animation_prompts:
        return
    await fill_animation_prompts(session, project, finalize_status=True)


async def fill_animation_prompts(
    session: AsyncSession,
    project: Project,
    *,
    finalize_status: bool = True,
    max_batches: int | None = None,
) -> dict[str, int]:
    """Ядро anim_pr: видеопромты из image_prompt (+ PNG-лента, если есть).

    finalize_status=False — для sidecar рядом с generating_images (статус не трогаем).
    """
    logger.info(
        "[#{}] make_animation_prompts starting (from image_prompt, finalize={})",
        project.id,
        finalize_status,
    )
    stats = {"batches": 0, "saved_ops": 0}

    # Не синкаем из R48 в DB — Excel только экспорт после apply-ops.
    await db_v2.backfill_project_v2(session, project)
    frames = (
        await session.execute(
            select(Frame).where(Frame.project_id == project.id).order_by(Frame.number)
        )
    ).scalars().all()

    # Один проход R49 — НЕ voiceover_for_frame() на каждый кадр (openpyxl×N).
    hydrated = apg.hydrate_voiceovers_from_plan(project, list(frames))
    if hydrated:
        logger.info(
            "[#{}] anim_pr: voiceover из R49 → {} кадров",
            project.id,
            hydrated,
        )
        await session.flush()

    # Старый парсер клал весь JSON apply-ops в первый кадр пачки — распакуем
    # уже оплаченные ответы GPT, иначе has_* считает их «пустыми» и шлёт заново.
    unpacked = apg.unpack_animation_prompt_blobs(list(frames))
    if unpacked:
        repair_ops = apg.build_apply_ops_from_pairs(
            [
                apg.ParsedAnimationPair(
                    image_id=(fr.uuid or f"F{fr.number}"),
                    animation_text=(fr.animation_prompt or "").strip(),
                    frame_number=fr.number,
                )
                for fr in frames
                if (fr.animation_prompt or "").strip()
                and not apg.is_apply_ops_blob(fr.animation_prompt or "")
            ],
            list(frames),
            shot=1,
        )
        if repair_ops:
            try:
                await db_apply.apply_ops(
                    session, project, repair_ops, export_xlsx=True
                )
            except db_apply.ApplyOpsError as e:
                logger.warning(
                    "[#{}] anim_pr: unpack→apply_ops: {} — оставляю DB",
                    project.id,
                    e,
                )
        await session.flush()
        logger.info(
            "[#{}] anim_pr: распаковано {} промтов из JSON-blob в DB/R48",
            project.id,
            unpacked,
        )

    pending = apg.collect_batch_items(project, frames)
    pending_shot2 = (
        apg.collect_shot2_batch_items(project, frames) if finalize_status else []
    )
    already_done, xlsx_filled, with_image = apg.count_animation_prompt_stats(
        project, frames
    )
    if not pending and not pending_shot2:
        # Не compute_actual_status: при готовых клипах уходит в videos_ready →
        # auto_advance стартует video, хотя юзер ждал anim_pr.
        if finalize_status:
            project.status = ProjectStatus.animation_prompts_ready
        logger.info(
            "[#{}] make_animation_prompts: nothing to do "
            "(db_ready={}, png={}) → status={}",
            project.id,
            already_done,
            with_image,
            project.status.value,
        )
        await session.flush()
        return stats

    logger.info(
        "[#{}] anim_pr: очередь shot_01={} shot_02={} (db_ready={}, png={}, "
        "from_image_prompt=1)",
        project.id,
        len(pending),
        len(pending_shot2),
        already_done,
        with_image,
    )

    skip_phase1 = already_done > 0 or (not pending and bool(pending_shot2))
    if skip_phase1:
        first_batch = pending if pending else pending_shot2
        first_pending = first_batch[0].frame.number
        logger.info(
            "[#{}] anim_pr: догонка — {} готово, первая пачка с кадра {} "
            "(shot_01={}, shot_02={})",
            project.id,
            already_done,
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

    # Master rules → system у каждой пачки (фаза 1 при maintenance kie часто жжёт 5 мин впустую).
    master_system = ""
    try:
        master_system = prompt_file.read_text(encoding="utf-8").strip()
    except Exception as e:  # noqa: BLE001
        logger.warning("[#{}] anim_pr: не прочитал master prompt: {}", project.id, e)
    if len(master_system) > 12_000:
        master_system = master_system[:12_000] + "\n…"

    logger.info("[#{}] anim_pr: GPT API…", project.id)
    gpt = get_gpt_client()
    try:
        logger.info("[#{}] anim_pr: новый API-сеанс…", project.id)
        await gpt.new_conversation()
        raise_if_cancelled(project.id)
        if not skip_phase1:
            logger.info(
                "[#{}] anim_pr: ФАЗА 1 текст+файл {} ({} байт), {} симв. (max_retries={})",
                project.id,
                prompt_file.name,
                prompt_file.stat().st_size,
                len(initial),
                _PHASE1_MAX_RETRIES,
            )
            try:
                initial_reply = await gpt.ask_anim_pr_initial(
                    initial,
                    prompt_file,
                    timeout=180,
                    project_id=project.id,
                    max_retries=_PHASE1_MAX_RETRIES,
                )
                if not (initial_reply or "").strip():
                    logger.warning(
                        "[#{}] anim_pr: пустой ответ на ФАЗУ 1 — всё равно шлём пачки фото",
                        project.id,
                    )
            except Exception as e:  # noqa: BLE001
                # kie 500 / maintenance — не валим шаг: system=master на пачках.
                logger.warning(
                    "[#{}] anim_pr: ФАЗА 1 упала ({}) — продолжаю пачки shot_01",
                    project.id,
                    e,
                )
            raise_if_cancelled(project.id)
        else:
            logger.info(
                "[#{}] anim_pr: ФАЗА 1 пропущена — {} промтов уже в xlsx/БД",
                project.id,
                already_done,
            )

        # Частичный ответ = объём превышен: добор остатка или пачки ≈80%.
        shot1_cap: int | None = None
        shot1_force: int | None = None
        while True:
            raise_if_cancelled(project.id)
            pending = apg.collect_batch_items(project, frames)
            if not pending:
                break
            if max_batches is not None and stats["batches"] >= max_batches:
                break

            take = shot1_force or shot1_cap or apg.BATCH_SIZE
            shot1_force = None
            batch = pending[: max(1, take)]
            use_strip = apg.batch_has_full_strip(batch)
            strip_path: Path | None = None
            if use_strip:
                strip_path = await asyncio.to_thread(
                    apg.build_batch_strip_path, batch, tmp_dir
                )
            batch_msg = apg.build_batch_message(batch)
            logger.info(
                "[#{}] anim_pr: ФАЗА 2 shot_01 batch {} кадров {} — {} ({} симв.)",
                project.id,
                len(batch),
                [it.frame.number for it in batch],
                f"лента {strip_path.name}" if strip_path else "text←image_prompt",
                len(batch_msg),
            )
            try:
                reply = await _ask_batch_until_ok(
                    gpt,
                    batch_msg,
                    strip_path,
                    project_id=project.id,
                    system=master_system or None,
                    label=f"shot_01 {[it.frame.number for it in batch]}",
                )
            except _LocalFallbackNeeded as e:
                logger.warning(
                    "[#{}] anim_pr: GPT outage лимит — local fallback ({})",
                    project.id,
                    e,
                )
                await _fill_remaining_local(session, project, list(frames), sheet)
                break
            delivered = await _save_anim_pr_batch(
                session,
                project,
                frames,
                batch,
                reply,
                sheet,
                shot=1,
            )
            asked = len(batch)
            if 0 < delivered < asked:
                rem = asked - delivered
                shot1_cap = inferred_batch_size(delivered)
                if rem <= delivered:
                    shot1_force = rem
                logger.warning(
                    "[#{}] anim_pr: volume partial shot_01 got={}/{} → "
                    "cap={} force_next={}",
                    project.id,
                    delivered,
                    asked,
                    shot1_cap,
                    shot1_force,
                )
            stats["batches"] += 1

        shot2_cap: int | None = None
        shot2_force: int | None = None
        while finalize_status:
            raise_if_cancelled(project.id)
            pending2 = apg.collect_shot2_batch_items(project, frames)
            if not pending2:
                break

            take2 = shot2_force or shot2_cap or apg.BATCH_SIZE
            shot2_force = None
            batch2 = pending2[: max(1, take2)]
            use_strip2 = apg.batch_has_full_strip(batch2)
            strip2: Path | None = None
            if use_strip2:
                strip2 = await asyncio.to_thread(
                    apg.build_batch_strip_path, batch2, tmp_dir
                )
            batch_msg2 = apg.build_batch_message_shot2(batch2)
            logger.info(
                "[#{}] anim_pr: ФАЗА 2 shot_02 batch {} кадров {} — {} ({} симв.)",
                project.id,
                len(batch2),
                [it.frame.number for it in batch2],
                f"лента {strip2.name}" if strip2 else "text-only",
                len(batch_msg2),
            )
            try:
                reply2 = await _ask_batch_until_ok(
                    gpt,
                    batch_msg2,
                    strip2,
                    project_id=project.id,
                    system=master_system or None,
                    label=f"shot_02 {[it.frame.number for it in batch2]}",
                )
            except _LocalFallbackNeeded as e:
                logger.warning(
                    "[#{}] anim_pr: shot_02 GPT outage — пропускаю остаток shot_02 ({})",
                    project.id,
                    e,
                )
                break
            delivered2 = await _save_anim_pr_batch(
                session,
                project,
                frames,
                batch2,
                reply2,
                sheet,
                shot=2,
            )
            asked2 = len(batch2)
            if 0 < delivered2 < asked2:
                rem2 = asked2 - delivered2
                shot2_cap = inferred_batch_size(delivered2)
                if rem2 <= delivered2:
                    shot2_force = rem2
                logger.warning(
                    "[#{}] anim_pr: volume partial shot_02 got={}/{} → "
                    "cap={} force_next={}",
                    project.id,
                    delivered2,
                    asked2,
                    shot2_cap,
                    shot2_force,
                )
            stats["batches"] += 1

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
        return stats

    if not finalize_status:
        await session.flush()
        return stats

    project.status = ProjectStatus.animation_prompts_ready
    await session.flush()
    try:
        sheet.write_general(status=project.status.value)
    except Exception as e:  # noqa: BLE001
        logger.warning("[#{}] xlsx write_general(status) failed: {}", project.id, e)

    # Harness-гейт (обязателен по NODE_SYSTEM): шаг не done, пока проверки не ok.
    await session.commit()
    from app.services.agent_harness import harness_gate_or_raise

    await harness_gate_or_raise(session, project, step="anim_pr")
    await session.commit()  # ops-телеметрия в project.meta
    return stats


async def _save_anim_pr_batch(
    session: AsyncSession,
    project: Project,
    frames: list[Frame],
    batch: list[apg.FrameImageBatchItem],
    reply: str,
    sheet,
    *,
    shot: int,
) -> int:
    # Целый JSON-blob в reply не должен попасть в R48 одним куском.
    if apg.is_apply_ops_blob(reply) and not apg.parse_apply_ops_animation_reply(
        reply, frames
    ):
        raise RuntimeError(
            f"anim_pr: ответ — битый JSON apply-ops для кадров "
            f"{[it.frame.number for it in batch]} (shot_0{shot})"
        )

    pairs = apg.parse_animation_reply(reply, frames, batch_items=batch)
    if not pairs:
        raise RuntimeError(
            "ChatGPT не вернул пары «ID изображения» / «текст анимации» "
            f"для кадров {[it.frame.number for it in batch]} (shot_0{shot})"
        )
    # Защита: никогда не писать сырой JSON в промт кадра.
    pairs = [
        p
        for p in pairs
        if not apg.is_apply_ops_blob(p.animation_text)
        and len(p.animation_text.strip()) >= apg.MIN_ANIM_PROMPT_LEN
    ]
    if not pairs:
        raise RuntimeError(
            f"anim_pr: после фильтра blob/коротких текстов нет валидных пар "
            f"для {[it.frame.number for it in batch]} (shot_0{shot})"
        )

    plan_row = 48 if shot == 1 else 64
    ops = apg.build_apply_ops_from_pairs(pairs, frames, shot=shot)
    if not ops:
        raise RuntimeError(
            f"anim_pr: нет валидных операций для кадров "
            f"{[it.frame.number for it in batch]} (shot_0{shot}) — uuid/длина?"
        )
    try:
        result = await db_apply.apply_ops(session, project, ops, export_xlsx=True)
    except db_apply.ApplyOpsError as e:
        raise RuntimeError(f"anim_pr apply_ops отклонён: {e}") from None
    saved = int(result.get("updated") or 0)

    for pair in pairs:
        if pair.frame_number is None:
            continue
        fr = next((f for f in frames if f.number == pair.frame_number), None)
        if fr is None:
            continue
        text = pair.animation_text.strip()
        # apply_ops пишет в те же ORM-объекты; дублируем, чтобы while-collect
        # сразу видел прогресс (без re-fetch / soft-retry).
        if shot == 1 and len(text) >= apg.MIN_ANIM_PROMPT_LEN:
            fr.animation_prompt = text
            fr.status = FrameStatus.animation_prompt_ready
        elif shot == 2:
            apg.save_animation_prompt_shot2(fr, project, text)
        try:
            sheet.write_frame(fr.number, frame_status=fr.status.value)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "[#{}] xlsx write_frame(status) failed: {}", project.id, e
            )
        logger.info(
            "[#{}] anim_pr: frame {} shot_0{} prompt len={} (apply-ops, plan R{})",
            project.id,
            fr.number,
            shot,
            len(text),
            plan_row,
        )

    await session.flush()
    await session.commit()
    logger.info(
        "[#{}] anim_pr: пачка shot_0{} — {} промтов через apply-ops (plan R{})",
        project.id,
        shot,
        saved,
        plan_row,
    )

    # GPT часто отдаёт 1 из BATCH_SIZE — это не hard-fail: сохраняем частичный
    # прогресс и крутим while True дальше по remaining (без soft-retry/30м паузы).
    batch_nums = {it.frame.number for it in batch}
    filled_from_batch = {
        p.frame_number
        for p in pairs
        if p.frame_number is not None and p.frame_number in batch_nums
    }
    still_missing = sorted(batch_nums - filled_from_batch)
    if still_missing and not filled_from_batch:
        raise RuntimeError(
            f"не получены animation_prompt shot_0{shot} для кадров {still_missing}"
        )
    if still_missing:
        logger.warning(
            "[#{}] anim_pr: частичный ответ shot_0{} — сохранено {}, "
            "остались {} (продолжаю без fail)",
            project.id,
            shot,
            sorted(filled_from_batch),
            still_missing,
        )
    return len(filled_from_batch)
