"""Авто-заполнение `topics.xlsx` массового проекта через ChatGPT.

Юзер может загрузить файл с темами, где заполнен ТОЛЬКО заголовок (B), а
остальные карточные колонки (стиль / тип хука / эмоция / факт / логика /
интеграция / примечание / длительность ролика) — пустые. Перед стартом
очереди мы отправляем такой xlsx в ChatGPT с инструкцией «дозаполни
ТОЛЬКО пустые ячейки», получаем файл обратно и обновляем
`Project.meta["topic_card"]` для всех подпроектов.

Если все ячейки уже заполнены — шаг пропускается (юзер сам всё указал).
Если хотя бы ОДНА строка имеет пробел в одном из карточных полей —
запускаем round-trip.

Failure-режим: при любой ошибке GPT/скачивания файла шаг логирует
warning и НЕ блокирует старт очереди. Подпроекты пойдут с тем что есть.
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bots.browser import browser_session
from app.bots.chatgpt import ChatGPTBot
from app.models import BatchProject, Project
from app.storage.batch_sheet import (
    CARD_FIELDS,  # noqa: F401  (часть публичного API модуля; используется в тестах)
    VOICEOVER_CHARS_PER_SECOND,
    read_topics,
)

# Карточные поля, которые ХОТИМ чтобы юзер/GPT заполнил перед стартом.
# Это «исторический» набор полей одиночного xlsx-flow: они описывают
# содержательную часть ролика, GPT-автозаполнение в первую очередь
# подбирает значения для них (стиль/эмоция/факт/логика/...).
#
# Новые поля массовой xlsx v2 (image_generator, hero_combo, dropdown'ы
# и т.п.) НЕ включены — у них в схеме уже стоят дефолты, и автозаполнение
# их не должно ждать.
_REQUIRED_CARD_FIELDS = [
    "source",
    "style",
    "hook_type",
    "emotion",
    "fact",
    "logic",
    "integration",
    "shoot_note",
    "video_duration_sec",
]

# Сколько попыток round-trip к ChatGPT при ошибке (скачивание xlsx, парсинг и т.п.).
_MAX_RETRIES = 2

# Таймаут общения с ChatGPT (с) — long-running т.к. модель пишет файл.
_GPT_TIMEOUT = 1200.0


def has_empty_card_cells(rows: list[dict]) -> bool:
    """True если хотя бы в одной строке (с заголовком) есть пустая карточная ячейка.

    Используется чтобы решить, нужен ли вообще запуск GPT-автозаполнения.
    """
    for row in rows:
        if not (row.get("title") or "").strip():
            continue
        for field in _REQUIRED_CARD_FIELDS:
            val = row.get(field)
            if val is None or (isinstance(val, str) and not val.strip()):
                return True
    return False


def _build_autofill_prompt() -> str:
    """Дефолтный промт для ChatGPT.

    Юзер сможет переопределить его позже файлом
    `prompts/batch_autofill/default.md` (TODO), но пока этого нет —
    зашиваем дефолт.
    """
    return (
        "Тебе пришёл xlsx-файл `topics.xlsx` — список тем для коротких "
        "роликов (шортсов). Колонка B содержит название каждого ролика "
        "(она уже заполнена). Остальные карточные колонки (C..L) могут "
        "быть пустыми или частично заполненными.\n\n"
        "Твоя задача: для каждой строки, где B (Название) не пусто, "
        "ДОЗАПОЛНИТЬ ТОЛЬКО ПУСТЫЕ ячейки в колонках:\n"
        "  • C: Источник — откуда тема (свободный текст, можно «idea»)\n"
        "  • D: Стиль — формат подачи («Попаданец», «А что если», "
        "«Мини-разбор», «Спасение мира», и т.п.)\n"
        "  • E: Тип хука — визуальный приём («Фишай / сюрреал», "
        "«Эстетика / контраст», «Близкий кадр», и т.п.)\n"
        "  • F: Эмоциональный фон — общая эмоция ролика "
        "(«удивляющий», «ироничный», «тревожный», «трогательный», и т.п.)\n"
        "  • G: Научпоп ядро / факт — 1-2 предложения с ядром факта\n"
        "  • H: Логическое объяснение — 1-2 предложения почему это интересно\n"
        "  • I: Интеграция продукта — 1-2 предложения как органично "
        "вписать продукт (если он есть в чате — учти его, иначе пиши "
        "«в свободной форме» или универсальный шаблон)\n"
        "  • J: Примечание по съёмке — тех. требования "
        "(«Продукт в кадре 3+ раза», «Динамика», «Крупный план», и т.п.)\n"
        "  • K: hero_mode — оставь значение `auto` если пусто; если "
        "ролик ОДНОЗНАЧНО без персонажа (статика / абстракция / макро) "
        "поставь `no_hero`, если ОБЯЗАТЕЛЬНО нужен герой — `hero`\n"
        "  • L: Время ролика (сек) — целое число секунд, "
        f"оптимальная длительность шортса (типично 20-60 сек, "
        f"если не уверен — поставь 30)\n\n"
        f"Колонка M — формула `=L×{VOICEOVER_CHARS_PER_SECOND}`, она считается "
        "Excel'ом автоматически. НЕ трогай её.\n"
        "Колонки N..Q — служебные (slug/статус/прогресс/обновлён), их тоже "
        "НЕ трогай.\n\n"
        "ОЧЕНЬ ВАЖНО:\n"
        "  1. Если ячейка УЖЕ заполнена — оставь её как есть, ничего "
        "не переписывай.\n"
        "  2. Заполняй ВСЕ темы (все непустые строки колонки B), не "
        "пропускай ни одной.\n"
        "  3. Тексты в C..J пиши на русском, без эмодзи и markdown.\n"
        "  4. Не добавляй и не удаляй строки.\n"
        "  5. Не переименовывай колонки.\n\n"
        "Верни этот же `topics.xlsx` обратно как .xlsx файл. Кратко в "
        "чате напиши, для скольких строк что заполнил."
    )


async def autofill_topics_if_needed(
    session: AsyncSession,
    batch: BatchProject,
) -> dict:
    """Главная функция: проверяет topics.xlsx, запускает GPT-автозаполнение если нужно.

    Возвращает dict с информацией:
      - `triggered`: bool — был ли запущен GPT round-trip
      - `reason`: str — почему пропущено / запущено
      - `updated_subs`: int — сколько подпроектов получили обновлённую topic_card
      - `failed`: bool — была ли ошибка (НЕ блокирует старт батча)
      - `error`: str | None
    """
    topics_path: Path = batch.topics_xlsx_path
    if not topics_path.exists():
        logger.info(
            "batch_autofill: batch #{} — topics.xlsx нет ({}), пропускаем",
            batch.id, topics_path,
        )
        return {
            "triggered": False, "reason": "no_topics_xlsx",
            "updated_subs": 0, "failed": False, "error": None,
        }

    rows = read_topics(topics_path)
    if not rows:
        logger.info(
            "batch_autofill: batch #{} — topics.xlsx пустой, пропускаем",
            batch.id,
        )
        return {
            "triggered": False, "reason": "empty_xlsx",
            "updated_subs": 0, "failed": False, "error": None,
        }

    if not has_empty_card_cells(rows):
        logger.info(
            "batch_autofill: batch #{} — все карточные ячейки заполнены, "
            "GPT-автозаполнение не нужно",
            batch.id,
        )
        return {
            "triggered": False, "reason": "already_filled",
            "updated_subs": 0, "failed": False, "error": None,
        }

    logger.info(
        "batch_autofill: batch #{} — есть пустые ячейки, "
        "запускаю GPT round-trip (rows={})",
        batch.id, len(rows),
    )

    # Промт во временный .md-файл, чтобы прикрепить к чату как
    # вложение (как в enrich_xlsx).
    tmp_dir = topics_path.parent / "tmp_autofill"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    prompt_file = tmp_dir / "prompt_autofill.md"
    prompt_file.write_text(_build_autofill_prompt(), encoding="utf-8")

    accompanying = (
        f"Прикреплены 2 файла:\n"
        f"  1. prompt_autofill.md — инструкция, что делать.\n"
        f"  2. topics.xlsx — таблица тем массового проекта "
        f"«{batch.name or batch.slug}».\n\n"
        f"Сделай всё что написано в инструкции и пришли мне xlsx обратно."
    )

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            async with browser_session() as bs:
                gpt = ChatGPTBot(bs)
                await gpt.new_conversation()
                reply = await gpt.ask_with_files(
                    accompanying.strip(),
                    [prompt_file, topics_path],
                    timeout=_GPT_TIMEOUT,
                )
                logger.info(
                    "batch_autofill: batch #{} — GPT ответил len={} "
                    "(attempt {}/{})",
                    batch.id, len(reply or ""), attempt, _MAX_RETRIES,
                )
                # Скачиваем приложенный xlsx ПОВЕРХ исходного.
                target = await gpt.download_attachment_from_last_reply(
                    topics_path, timeout=600,
                )
                if not target.exists() or target.stat().st_size < 1024:
                    raise RuntimeError(
                        "скачанный topics.xlsx пустой / слишком маленький "
                        f"({target.stat().st_size if target.exists() else 0} байт)"
                    )
                logger.info(
                    "batch_autofill: batch #{} — topics.xlsx обновлён "
                    "({} байт)",
                    batch.id, target.stat().st_size,
                )
                break  # успех
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "batch_autofill: batch #{} attempt {}/{} FAILED: {}",
                batch.id, attempt, _MAX_RETRIES, exc,
            )
            if attempt >= _MAX_RETRIES:
                logger.error(
                    "batch_autofill: batch #{} все {} попытки failed — "
                    "пропускаем GPT-автозаполнение, идём со старым xlsx",
                    batch.id, _MAX_RETRIES,
                )
                return {
                    "triggered": True, "reason": "gpt_failed",
                    "updated_subs": 0, "failed": True,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            continue

    # 4. Перечитываем topics.xlsx и обновляем topic_card подпроектов.
    new_rows = read_topics(topics_path)
    updated_subs = await _update_subprojects_from_xlsx(
        session, batch, new_rows,
    )

    return {
        "triggered": True,
        "reason": "filled_via_gpt",
        "updated_subs": updated_subs,
        "failed": False,
        "error": None,
    }


async def _update_subprojects_from_xlsx(
    session: AsyncSession,
    batch: BatchProject,
    rows: list[dict],
) -> int:
    """Прокатывает свежие card-поля из rows → Project.meta["topic_card"].

    Сопоставление row ↔ project идёт по `batch_position` (колонка №).
    Если позиции нет — fallback на title (точное совпадение).

    Возвращает кол-во обновлённых подпроектов.
    """
    subs = (
        await session.execute(
            select(Project).where(Project.batch_id == batch.id)
        )
    ).scalars().all()

    # Build lookup: position -> row, title -> row
    by_pos: dict[int, dict] = {}
    by_title: dict[str, dict] = {}
    for r in rows:
        pos = r.get("position")
        if isinstance(pos, int):
            by_pos[pos] = r
        elif isinstance(pos, float) and pos.is_integer():
            by_pos[int(pos)] = r
        title = (r.get("title") or "").strip()
        if title:
            by_title.setdefault(title, r)

    updated = 0
    for p in subs:
        row = by_pos.get(p.batch_position or 0)
        if row is None and p.topic:
            row = by_title.get(p.topic.strip())
        if row is None:
            continue
        # Карточные поля → meta.topic_card (только непустые).
        new_card = {k: row.get(k) for k in CARD_FIELDS if row.get(k)}
        if not new_card:
            continue
        # Мердж: не теряем уже существующие поля если row не дозаполнил их.
        old_meta = p.meta or {}
        old_card = old_meta.get("topic_card") or {}
        merged_card = {**old_card, **new_card}
        if merged_card == old_card:
            continue
        new_meta = dict(old_meta)
        new_meta["topic_card"] = merged_card
        p.meta = new_meta
        updated += 1
        logger.info(
            "batch_autofill: project #{} (pos={}) topic_card "
            "обновлён, новых полей: {}",
            p.id, p.batch_position,
            [k for k in new_card if k not in old_card],
        )
    if updated:
        await session.flush()
    return updated
