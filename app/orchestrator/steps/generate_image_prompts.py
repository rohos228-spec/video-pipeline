"""Шаг 5: ОДНИМ запросом в ChatGPT — N image-промтов на N кадров.

С Push D логика изменилась:
  СТАРОЕ:  N кадров → N свежих чатов с ChatGPT (один промт за раз).
  НОВОЕ:   1 чат — отдаём в ChatGPT все закадровые блоки разделённые «-»,
           ChatGPT возвращает все image-промты тоже разделённые «-».

Формат входа:
  <IMAGE_SHORTS master-prompt — выбран в проекте>
  <tech-блок (генератор / aspect / 2K)>
  <hero-line — эталонное описание героя, если он есть>
  ---
  <блок1>-<блок2>-<блок3>-...-<блокN>

Формат ожидаемого ответа:
  <image_prompt1>-<image_prompt2>-...-<image_promptN>

Парсим reply по «-», чистим, кладём по кадрам в порядке номеров. Если
блоков меньше чем кадров — RuntimeError. Если больше — обрезаем до N
с warning'ом.

Входной статус: generating_image_prompts (выставляется бот-меню).
Выходной статус: image_prompts_ready (либо failed, если ChatGPT упал)."""

from __future__ import annotations

from aiogram import Bot
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bots.browser import browser_session
from app.bots.chatgpt import ChatGPTBot
from app.models import Frame, FrameStatus, Project, ProjectStatus
from app.services.prompt_library import get_project_prompt
from app.storage import for_project as _sheet_for_project

# Минимальная длина одного image-промта в ответе. Меньше — почти
# наверняка мусор/обрезок, не пишем в БД.
_MIN_PROMPT_CHARS = 40


def _parse_dash_prompts(reply: str) -> list[str]:
    """Разрезать ответ ChatGPT на промты по знаку «-».

    GPT просят: «верни всё одним сообщением, между промтами `-`, без
    нумерации и пояснений». На практике модель может:
    - нумеровать строки («1. …», «1) …») — снимаем.
    - использовать «—»/«–» вместо «-» внутри промтов — НЕ режем по ним.
    - вставить пустые строки между промтами — игнорируем.
    """
    text = (reply or "").strip()
    if not text:
        return []
    # Главный разделитель — именно ASCII «-» в окружении пробелов или
    # переводов строк, чтобы не порезать дефисные слова в самих промтах.
    # Простое .split("-") дало бы много ложных срабатываний на
    # «high-quality», «cyber-punk» и т.д. Поэтому делим только по
    # «вертикальным» вхождениям «-».
    import re
    parts = re.split(r"\s*\n\s*-\s*|^\s*-\s*", text, flags=re.MULTILINE)
    # Если split не нашёл — fallback на « - » (с пробелами вокруг).
    if len(parts) <= 1:
        parts = re.split(r"\s+-\s+", text)
    blocks: list[str] = []
    for raw in parts:
        b = (raw or "").strip()
        if not b:
            continue
        # Снять «1. », «1) » в начале.
        if len(b) >= 3 and b[0].isdigit() and b[1] in ".)" and b[2] == " ":
            b = b[3:].strip()
        # Снять кавычки/маркеры списка.
        b = b.lstrip("*•·»>-").strip()
        if not b:
            continue
        blocks.append(b)
    return blocks


async def run(session: AsyncSession, project: Project, bot: Bot) -> None:
    if project.status is not ProjectStatus.generating_image_prompts:
        return
    logger.info("[#{}] generate_image_prompts starting (single GPT call mode)", project.id)

    image_master = get_project_prompt(project, "img_pr")

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

    # Идемпотентность: если у ВСЕХ кадров уже есть image_prompt — просто
    # синканём xlsx и выходим. Если хоть у одного нет — будем заново
    # запрашивать GPT для всех (чтобы стилистика была единой; точечно
    # «дополнить» один кадр через тот же batch-запрос неудобно).
    if all(fr.image_prompt for fr in frames):
        for fr in frames:
            try:
                sheet.write_frame(fr.number, image_prompt=fr.image_prompt)
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "[#{}] xlsx sync image_prompt frame {} failed: {}",
                    project.id, fr.number, e,
                )
        project.status = ProjectStatus.image_prompts_ready
        await session.flush()
        logger.info("[#{}] generate_image_prompts: все промты уже есть, skip GPT", project.id)
        return

    # Hero-блок (если в проекте задан хотя бы один герой).
    hero_text = (project.hero_description or "").strip()
    descriptions = [d for d in (project.hero_descriptions or []) if d and d.strip()]
    if not hero_text and descriptions:
        hero_text = descriptions[0]
    hero_section = ""
    if hero_text:
        hero_section = (
            "\nЭталонное описание главного героя (использовать в кадрах "
            "где он появляется):\n"
            + hero_text
            + "\n"
        )

    # Собираем единое сообщение для ChatGPT.
    voiceover_line = "-".join(
        (fr.voiceover_text or "").strip() for fr in frames
    )
    full_prompt = (
        image_master.strip()
        + "\n\n"
        + hero_section
        + "\n---\n"
        + f"Кадров: {len(frames)}.\n"
        + "Закадровый текст по кадрам (между блоками знак «-»):\n"
        + voiceover_line
        + "\n\n"
        + "Верни одним сообщением ровно "
        + str(len(frames))
        + " промтов в том же порядке, разделяя их знаком «-». "
        + "Без нумерации, без пояснений, без заголовков. "
        + "Внутри самих промтов знак «-» не используй (если нужен дефис "
        + "— замени на пробел или подчёркивание)."
    )

    async with browser_session() as bs:
        gpt = ChatGPTBot(bs)
        last_reply = ""
        prompts: list[str] = []
        for attempt in range(1, 3):  # до 2 попыток на весь батч
            reply = await gpt.ask_fresh(full_prompt, timeout=900)
            last_reply = (reply or "").strip()
            blocks = _parse_dash_prompts(last_reply)
            # Отфильтруем заведомо короткий мусор.
            blocks = [b for b in blocks if len(b) >= _MIN_PROMPT_CHARS]
            if len(blocks) >= len(frames):
                prompts = blocks[: len(frames)]
                if len(blocks) > len(frames):
                    logger.warning(
                        "[#{}] GPT вернул {} промтов, ожидалось {} — обрезаю.",
                        project.id, len(blocks), len(frames),
                    )
                break
            logger.warning(
                "[#{}] попытка {}: GPT вернул {} блоков, ожидалось {}. "
                "Последние 200 симв: {!r}",
                project.id, attempt, len(blocks), len(frames),
                last_reply[-200:],
            )
        if len(prompts) != len(frames):
            raise RuntimeError(
                f"GPT не вернул нужное число промтов: "
                f"ожидалось {len(frames)}, получено "
                f"{len(_parse_dash_prompts(last_reply))} (фильтр {_MIN_PROMPT_CHARS}+ симв). "
                f"Последний ответ ({len(last_reply)} симв): {last_reply[:300]!r}"
            )

    # 4) Раскладываем по кадрам.
    for fr, img_prompt in zip(frames, prompts, strict=True):
        fr.image_prompt = img_prompt
        fr.status = FrameStatus.image_prompt_ready
        await session.flush()
        try:
            sheet.write_frame(
                fr.number,
                image_prompt=img_prompt,
                frame_status=fr.status.value,
                gen_type="image",
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "[#{}] xlsx write image_prompt frame {} failed: {}",
                project.id, fr.number, e,
            )
        logger.info(
            "[#{}] frame {}: image_prompt готов ({} симв)",
            project.id, fr.number, len(img_prompt),
        )

    project.status = ProjectStatus.image_prompts_ready
    await session.flush()
    logger.info(
        "[#{}] generate_image_prompts complete: {} промтов одним запросом",
        project.id, len(prompts),
    )
