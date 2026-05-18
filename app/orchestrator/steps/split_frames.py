"""Шаг 3: разбить закадровый текст на блоки и создать записи Frame в БД.

В массовой генерации (batch sub'ы) — через xlsx-flow: прикладываем
`project.xlsx` + `voiceover.txt` + промт-файл, GPT возвращает обновлённый
xlsx, бот его подменяет и пересоздаёт фреймы из xlsx (то же, что
одиночный `_run_split_xlsx`). См. `app/services/xlsx_steps.py`.

Для одиночных проектов (если оркестратор всё-таки доходит до этого шага)
работает текстовый fallback ниже — RAZBIVKA_SLOV-промт + script_text.
Источник входного текста: `project.script_text`. ChatGPT возвращает блоки,
разделённые знаком «-». Каждый блок → один Frame. Длительность блока
распределяется пропорционально длине, окно 2-4 сек, сумма → 60-75 сек.

(Фаза 2) После разбивки — GPT-проверка через `gpt_check_text_artifact`,
ретраи до 3 раз при `regenerate`.
"""

from __future__ import annotations

from aiogram import Bot
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bots.browser import browser_session
from app.bots.chatgpt import ChatGPTBot
from app.models import Frame, HITLKind, Project, ProjectStatus
from app.services.gpt_check import (
    GptCheckDecision,
    gpt_check_text_artifact,
    load_check_prompt,
)
from app.services.hitl import send_hitl_text
from app.services.prompt_library import get_project_prompt
from app.services.xlsx_steps import run_split_xlsx_step
from app.storage import for_project as _sheet_for_project

MIN_FRAME = 2.0
MAX_FRAME = 4.0
TARGET_TOTAL = 65.0  # середина окна 60-75 сек

# Минимальная и максимальная длина блока — фильтруем мусор от модели,
# но без жёсткого реджекта (ChatGPT иногда чуть-чуть промахивается).
_MIN_BLOCK_CHARS = 5
_MAX_BLOCK_CHARS = 80

MAX_GPT_CHECK_RETRIES = 3


def _parse_dash_blocks(reply: str) -> list[str]:
    """Разбить ответ ChatGPT на блоки по знаку «-».

    Промт RAZBIVKA_SLOV требует ставить «-» между блоками. На практике
    модель может ставить « - » или переносить строки + «-» в начале строки.
    Чистим лишние пробелы и пустые куски.
    """
    text = (reply or "").strip()
    if not text:
        return []
    # Главный разделитель — «-». Дальше чистим строки, пустые/мусор отсеиваем.
    raw_blocks = text.split("-")
    blocks: list[str] = []
    for raw in raw_blocks:
        b = raw.strip().strip("·•—–").strip()
        if not b:
            continue
        # Иногда GPT ставит нумерацию «1. ...» — снимаем.
        if len(b) >= 3 and b[0].isdigit() and b[1] in ".)" and b[2] == " ":
            b = b[3:].strip()
        # Перевод строк внутри блока — заменяем на пробел.
        b = " ".join(b.split())
        if len(b) < _MIN_BLOCK_CHARS:
            continue
        if len(b) > _MAX_BLOCK_CHARS:
            # Слишком длинный — урезаем по последнему пробелу до лимита.
            cut = b[:_MAX_BLOCK_CHARS]
            sp = cut.rfind(" ")
            b = cut[: sp if sp > 20 else _MAX_BLOCK_CHARS]
        blocks.append(b)
    return blocks


def _distribute_durations(cells: list[str]) -> list[float]:
    if not cells:
        return []
    lengths = [max(len(c), 1) for c in cells]
    total_len = sum(lengths)
    raw = [TARGET_TOTAL * (length / total_len) for length in lengths]
    clamped = [min(max(x, MIN_FRAME), MAX_FRAME) for x in raw]
    s = sum(clamped)
    target = min(max(s, 60.0), 75.0)
    if s > 0:
        factor = target / s
        clamped = [min(max(x * factor, MIN_FRAME), MAX_FRAME) for x in clamped]
    return [round(x, 2) for x in clamped]


async def run(session: AsyncSession, project: Project, bot: Bot | None = None) -> None:
    if project.status is not ProjectStatus.splitting:
        return

    # Массовый sub → xlsx-flow (как одиночный через TG-меню).
    if project.batch_id is not None and bot is not None:
        # Идемпотентность: если фреймы уже есть — не трогаем.
        existing = (
            await session.execute(
                select(Frame).where(Frame.project_id == project.id)
            )
        ).scalars().all()
        if existing:
            logger.info(
                "[#{}] frames уже есть ({}), пропуск", project.id, len(existing)
            )
            project.status = ProjectStatus.frames_ready
            return
        await run_split_xlsx_step(session, project, bot)
        return

    if not project.script_text:
        raise RuntimeError("script_text пуст — нечего разбивать")
    logger.info("[#{}] split_frames (RAZBIVKA_SLOV text-fallback) starting", project.id)

    # Идемпотентность: если фреймы уже есть — не трогаем.
    existing = (
        await session.execute(
            select(Frame).where(Frame.project_id == project.id)
        )
    ).scalars().all()
    if existing:
        logger.info("[#{}] frames уже есть ({}), пропуск", project.id, len(existing))
        project.status = ProjectStatus.frames_ready
        return

    # 1) Мастер-промт разбивки — выбранный в проекте вариант с диска.
    master = get_project_prompt(project, "split")

    # 2) Шлём в ChatGPT: <RAZBIVKA_SLOV>\n\n---\n\n<script_text>.
    full_prompt = f"{master}\n\n---\n\n{project.script_text.strip()}"

    reply: str | None = None
    cells: list[str] = []

    for attempt in range(1, MAX_GPT_CHECK_RETRIES + 1):
        async with browser_session() as bs:
            gpt = ChatGPTBot(bs)
            reply = await gpt.ask_fresh(full_prompt, timeout=300)

            if not reply or len(reply.strip()) < 10:
                raise RuntimeError(f"ChatGPT вернул пустую разбивку: {reply!r}")

            cells = _parse_dash_blocks(reply)
            if not cells:
                raise RuntimeError(
                    f"не удалось распарсить разбивку (нет «-» или все блоки пустые); "
                    f"ответ модели: {reply[:500]!r}"
                )
            logger.info("[#{}] RAZBIVKA_SLOV → {} блоков", project.id, len(cells))

            # (Фаза 2) GPT-проверка разбивки.
            try:
                check_prompt = load_check_prompt("blocks")
            except FileNotFoundError:
                logger.warning("[#{}] промт проверки разбивки не найден, пропускаю GPT-check", project.id)
                break

            blocks_text = "\n\n".join(
                f"— ({i+1}) {cell}" for i, cell in enumerate(cells)
            )
            check_result = await gpt_check_text_artifact(
                chatgpt_bot=gpt,
                check_prompt=check_prompt,
                artifact_text=blocks_text,
                new_conversation=True,
                timeout=1200.0,
            )
            logger.info(
                "[#{}] blocks GPT-check attempt {}/{}: decision={}",
                project.id, attempt, MAX_GPT_CHECK_RETRIES,
                check_result.decision.value,
            )

            if check_result.decision is GptCheckDecision.approved:
                break

            if check_result.decision is GptCheckDecision.replace_artifact:
                break

            if check_result.decision is GptCheckDecision.regenerate:
                if attempt < MAX_GPT_CHECK_RETRIES:
                    logger.info(
                        "[#{}] blocks: GPT просит перегенерацию (hint: {}), retry {}/{}",
                        project.id, check_result.hint[:100],
                        attempt, MAX_GPT_CHECK_RETRIES,
                    )
                    continue
                logger.warning(
                    "[#{}] blocks: {} ретраев исчерпано, оставляем последний вариант",
                    project.id, MAX_GPT_CHECK_RETRIES,
                )
                break

            # timeout / parse_error
            break

    durations = _distribute_durations(cells)
    t = 0.0
    for i, (cell, dur) in enumerate(zip(cells, durations, strict=True), start=1):
        start_ts = t
        end_ts = t + dur
        session.add(
            Frame(
                project_id=project.id,
                number=i,
                voiceover_text=cell,
                start_ts=start_ts,
                end_ts=end_ts,
                duration_seconds=dur,
            )
        )
        t = end_ts

    project.status = ProjectStatus.frames_ready
    await session.flush()
    logger.info("[#{}] split_frames: {} ячеек, итого {:.2f} сек", project.id, len(cells), t)

    # (Mass-gen Фаза 2) HITL approve_blocks — кнопка подтверждения
    # после шага 3 (разбивка на блоки). Без неё конвейер
    # не пойдёт в шаг 4 (hero) при MASS_GEN_REQUIRE_CONFIRMATION=1.
    if bot is not None:
        blocks_preview = "\n\n".join(
            f"— ({i+1}) {cell}"
            for i, cell in enumerate(cells)
        )
        try:
            await send_hitl_text(
                bot, session, project,
                kind=HITLKind.approve_blocks,
                title=f"Разбивка на блоки #{project.id}",
                text=blocks_preview,
                payload={"step": "blocks", "count": len(cells)},
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "[#{}] approve_blocks HITL send failed: {}", project.id, e
            )

    try:
        sheet = _sheet_for_project(project)
        sheet.ensure_frame_columns(len(cells))
        for i, (cell, dur) in enumerate(zip(cells, durations, strict=True), start=1):
            sheet.write_frame(
                i,
                voiceover_text=cell,
                duration_seconds=dur,
                char_count=len(cell),
                frame_status="planned",
            )
        sheet.write_general(status=project.status.value)
    except Exception as e:  # noqa: BLE001
        logger.warning("[#{}] project_sheet split write failed: {}", project.id, e)
