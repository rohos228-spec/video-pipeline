"""xlsx-хранилище списка тем массового проекта.

Один файл `data/batches/<slug>/topics.xlsx`. Структура простая, чтобы
юзер мог открыть в Excel/LibreOffice/WPS и быстро заполнить:

  Лист «Темы»
    Колонка A: №
    Колонка B: Тема ролика (обязательная)
    Колонка C: hero_mode (опц.: hero | no_hero | auto)
    Колонка D: Подпроект (slug, заполняется автоматически)
    Колонка E: Статус (заполняется автоматически)
    Колонка F: Прогресс (заполняется автоматически)
    Колонка G: Обновлён (UTC, заполняется автоматически)

Для импорта новых тем юзер заполняет колонки B/C на чистых строках,
заливает обратно — батч-сервис создаёт подпроекты только для строк с
непустой темой и пустым «Подпроект».
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from loguru import logger

SHEET_NAME = "Темы"

HEADERS = [
    "№",
    "Тема ролика",
    "hero_mode",
    "Подпроект",
    "Статус",
    "Прогресс",
    "Обновлён",
]


def _now_iso() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def init_topics_xlsx(path: Path, batch_name: str) -> None:
    """Создаёт пустой xlsx с шапкой. Идемпотентно: если файл уже есть —
    не трогает."""
    if path.exists():
        return
    from openpyxl import Workbook

    path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.title = SHEET_NAME

    ws["A1"] = f"Массовый проект: {batch_name}"
    ws.merge_cells("A1:G1")

    for col_idx, header in enumerate(HEADERS, start=1):
        ws.cell(row=2, column=col_idx, value=header)

    # Авто-ширина колонок (примерная, openpyxl не умеет автосайз).
    widths = [4, 50, 12, 36, 16, 12, 20]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[ws.cell(row=2, column=i).column_letter].width = w

    wb.save(path)
    logger.info("batch topics.xlsx initialized: {}", path)


def write_subprojects_table(
    path: Path,
    rows: list[dict],
    batch_name: str,
) -> None:
    """Полностью переписывает таблицу подпроектов в xlsx.

    `rows` — список словарей с ключами: position, topic, hero_mode, slug,
    status, progress.
    """
    from openpyxl import Workbook, load_workbook

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

    ws["A1"] = f"Массовый проект: {batch_name}"
    ws.merge_cells("A1:G1")
    for col_idx, header in enumerate(HEADERS, start=1):
        ws.cell(row=2, column=col_idx, value=header)

    widths = [4, 50, 12, 36, 16, 12, 20]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[ws.cell(row=2, column=i).column_letter].width = w

    for r, row in enumerate(rows, start=3):
        ws.cell(row=r, column=1, value=row.get("position"))
        ws.cell(row=r, column=2, value=row.get("topic"))
        ws.cell(row=r, column=3, value=row.get("hero_mode") or "auto")
        ws.cell(row=r, column=4, value=row.get("slug"))
        ws.cell(row=r, column=5, value=row.get("status"))
        ws.cell(row=r, column=6, value=row.get("progress"))
        ws.cell(row=r, column=7, value=_now_iso())

    wb.save(path)


def read_topics(path: Path) -> list[dict]:
    """Читает темы из xlsx. Возвращает список словарей с темой/hero_mode/slug.

    Используется для импорта новых строк юзером: возвращает все непустые
    строки колонки «Тема». Поле slug подсказывает, какие строки уже
    стали подпроектами — новые строки имеют slug=None.
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
        position, topic, hero_mode, slug, status, progress, updated_at = (
            row + (None,) * 7
        )[:7]
        topic_clean = (str(topic).strip() if topic is not None else "")
        if not topic_clean:
            continue
        out.append({
            "position": position,
            "topic": topic_clean,
            "hero_mode": (
                str(hero_mode).strip().lower() if hero_mode else None
            ),
            "slug": (str(slug).strip() if slug else None),
            "status": (str(status).strip() if status else None),
            "progress": (str(progress).strip() if progress else None),
            "updated_at": updated_at,
        })
    return out


def collect_new_topics(path: Path) -> list[tuple[str, str | None]]:
    """Возвращает только новые темы (без slug — ещё не созданы подпроекты).

    Каждый элемент: (topic, hero_mode|None).
    """
    rows = read_topics(path)
    return [(r["topic"], r.get("hero_mode")) for r in rows if not r.get("slug")]
