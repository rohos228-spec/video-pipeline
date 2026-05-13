"""xlsx-хранилище списка тем массового проекта.

Один файл `data/batches/<slug>/topics.xlsx`. Лист «Темы» — расширенная
структура (PR #3), каждая строка = карточка одного ролика:

  Колонка A: №
  Колонка B: Название ролика          (обязательная)
  Колонка C: Источник                  (свободный комментарий: «из файла» / «добавлено»)
  Колонка D: Стиль                     («Попаданец», «А что если», «мини-разбор», …)
  Колонка E: Тип хука                  («Фишай / сюрреал», «Эстетика / контраст», …)
  Колонка F: Эмоциональный фон         («удивляющий», «ироничный», «тревожный», …)
  Колонка G: Научпоп ядро / факт       (развёрнутый факт-зерно ролика)
  Колонка H: Логическое объяснение     (почему это интересно зрителю)
  Колонка I: Интеграция продукта       (как вписать постоянный продукт)
  Колонка J: Примечание по съёмке      (тех. требования: «Продукт в кадре 3+ раза»)
  Колонка K: hero_mode                 (опц.: hero | no_hero | auto)
  Колонка L: Подпроект (slug)          (заполняется автоматически)
  Колонка M: Статус                    (заполняется автоматически)
  Колонка N: Прогресс                  (заполняется автоматически)
  Колонка O: Обновлён                  (UTC, заполняется автоматически)

Обязательная колонка только B (Название). Остальные карточные поля
(C..J) — необязательные, но если заполнены, попадут в промпт плана/
сценария как контекст. Колонки L..O — сервисные.

Для импорта новых тем юзер заполняет колонку B (минимум) и опц. C..K
на чистых строках, заливает обратно — батч-сервис создаёт подпроекты
только для строк с непустой темой и пустым «Подпроект».
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from loguru import logger

SHEET_NAME = "Темы"

# Карточные поля — попадают в Project.meta["topic_card"] и в промпт.
CARD_FIELDS = [
    "title",         # B: Название ролика
    "source",        # C: Источник
    "style",         # D: Стиль
    "hook_type",     # E: Тип хука
    "emotion",       # F: Эмоциональный фон
    "fact",          # G: Научпоп ядро / факт
    "logic",         # H: Логическое объяснение
    "integration",   # I: Интеграция продукта
    "shoot_note",    # J: Примечание по съёмке
]

HEADERS = [
    "№",                          # A
    "Название ролика",            # B
    "Источник",                   # C
    "Стиль",                      # D
    "Тип хука",                   # E
    "Эмоциональный фон",          # F
    "Научпоп ядро / факт",        # G
    "Логическое объяснение",      # H
    "Интеграция продукта",        # I
    "Примечание по съёмке",       # J
    "hero_mode",                  # K
    "Подпроект",                  # L
    "Статус",                     # M
    "Прогресс",                   # N
    "Обновлён",                   # O
]

COL_WIDTHS = [4, 40, 14, 16, 22, 18, 50, 50, 50, 28, 12, 36, 16, 12, 20]


def _now_iso() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _header_count() -> int:
    return len(HEADERS)


def init_topics_xlsx(path: Path, batch_name: str) -> None:
    """Создаёт пустой xlsx с шапкой. Идемпотентно: если файл уже есть —
    не трогает.
    """
    if path.exists():
        return
    from openpyxl import Workbook
    from openpyxl.utils import get_column_letter

    path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.title = SHEET_NAME

    last_col = get_column_letter(_header_count())
    ws["A1"] = f"Массовый проект: {batch_name}"
    ws.merge_cells(f"A1:{last_col}1")

    for col_idx, header in enumerate(HEADERS, start=1):
        ws.cell(row=2, column=col_idx, value=header)

    for i, w in enumerate(COL_WIDTHS, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w

    wb.save(path)
    logger.info("batch topics.xlsx initialized: {}", path)


def write_subprojects_table(
    path: Path,
    rows: list[dict],
    batch_name: str,
) -> None:
    """Полностью переписывает таблицу подпроектов в xlsx.

    `rows` — список словарей со всеми полями (position, title/topic,
    source, style, hook_type, emotion, fact, logic, integration,
    shoot_note, hero_mode, slug, status, progress).
    """
    from openpyxl import Workbook, load_workbook
    from openpyxl.utils import get_column_letter

    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        wb = load_workbook(path)
        if SHEET_NAME in wb.sheetnames:
            del wb[SHEET_NAME]
        ws = wb.create_sheet(SHEET_NAME, 0)
    else:
        wb = Workbook()
        ws = wb.active
        ws.title = SHEET_NAME

    last_col = get_column_letter(_header_count())
    ws["A1"] = f"Массовый проект: {batch_name}"
    ws.merge_cells(f"A1:{last_col}1")
    for col_idx, header in enumerate(HEADERS, start=1):
        ws.cell(row=2, column=col_idx, value=header)

    for i, w in enumerate(COL_WIDTHS, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w

    for r, row in enumerate(rows, start=3):
        # Поддерживаем оба ключа: новый "title" и старый "topic".
        title = row.get("title") or row.get("topic")
        ws.cell(row=r, column=1, value=row.get("position"))
        ws.cell(row=r, column=2, value=title)
        ws.cell(row=r, column=3, value=row.get("source"))
        ws.cell(row=r, column=4, value=row.get("style"))
        ws.cell(row=r, column=5, value=row.get("hook_type"))
        ws.cell(row=r, column=6, value=row.get("emotion"))
        ws.cell(row=r, column=7, value=row.get("fact"))
        ws.cell(row=r, column=8, value=row.get("logic"))
        ws.cell(row=r, column=9, value=row.get("integration"))
        ws.cell(row=r, column=10, value=row.get("shoot_note"))
        ws.cell(row=r, column=11, value=row.get("hero_mode") or "auto")
        ws.cell(row=r, column=12, value=row.get("slug"))
        ws.cell(row=r, column=13, value=row.get("status"))
        ws.cell(row=r, column=14, value=row.get("progress"))
        ws.cell(row=r, column=15, value=_now_iso())

    wb.save(path)


def _s(v) -> str | None:
    """Чистая строка или None."""
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def read_topics(path: Path) -> list[dict]:
    """Читает темы из xlsx. Возвращает список словарей с полным набором
    карточных полей + сервисных.

    Поле topic (= title) — для обратной совместимости.
    """
    if not path.exists():
        return []
    from openpyxl import load_workbook

    wb = load_workbook(path, read_only=True, data_only=True)
    if SHEET_NAME not in wb.sheetnames:
        return []
    ws = wb[SHEET_NAME]

    out: list[dict] = []
    # Строки начиная с 3-й (1-я — заголовок батча, 2-я — заголовки колонок).
    for row in ws.iter_rows(min_row=3, values_only=True):
        if not row:
            continue
        # Дополняем до 15 колонок (вдруг старый файл с 7 колонками).
        padded = (row + (None,) * 15)[:15]
        (
            position, title, source, style, hook_type, emotion, fact,
            logic, integration, shoot_note, hero_mode, slug, status,
            progress, updated_at,
        ) = padded
        title_clean = _s(title) or ""
        if not title_clean:
            continue
        out.append({
            "position": position,
            "title": title_clean,
            # для обратной совместимости — старые потребители ждут "topic"
            "topic": title_clean,
            "source": _s(source),
            "style": _s(style),
            "hook_type": _s(hook_type),
            "emotion": _s(emotion),
            "fact": _s(fact),
            "logic": _s(logic),
            "integration": _s(integration),
            "shoot_note": _s(shoot_note),
            "hero_mode": (_s(hero_mode) or "").lower() or None,
            "slug": _s(slug),
            "status": _s(status),
            "progress": _s(progress),
            "updated_at": updated_at,
        })
    return out


def collect_new_topics(path: Path) -> list[dict]:
    """Возвращает только новые темы (без slug — ещё не созданы подпроекты).

    Каждый элемент — полный dict с карточными полями (title, source,
    style, hook_type, emotion, fact, logic, integration, shoot_note,
    hero_mode). Это позволяет передать всю карточку при создании
    подпроекта.
    """
    rows = read_topics(path)
    return [r for r in rows if not r.get("slug")]


def topic_card_from_row(row: dict) -> dict:
    """Извлекает только карточные поля из строки xlsx (для Project.meta)."""
    return {k: row.get(k) for k in CARD_FIELDS if row.get(k)}
