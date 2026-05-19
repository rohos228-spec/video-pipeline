"""xlsx-хранилище списка тем массового проекта.

Один файл `data/batches/<slug>/topics.xlsx`. Лист «Темы» — расширенная
структура, каждая строка = карточка одного ролика:

  Колонка A:  №
  Колонка B:  Название ролика          (обязательная)
  Колонка C:  Источник                  (свободный комментарий: «из файла» / «добавлено»)
  Колонка D:  Стиль                     («Попаданец», «А что если», «мини-разбор», …)
  Колонка E:  Тип хука                  («Фишай / сюрреал», «Эстетика / контраст», …)
  Колонка F:  Эмоциональный фон         («удивляющий», «ироничный», «тревожный», …)
  Колонка G:  Научпоп ядро / факт       (развёрнутый факт-зерно ролика)
  Колонка H:  Логическое объяснение     (почему это интересно зрителю)
  Колонка I:  Интеграция продукта       (как вписать постоянный продукт)
  Колонка J:  Примечание по съёмке      (тех. требования: «Продукт в кадре 3+ раза»)
  Колонка K:  hero_mode                 (опц.: hero | no_hero | auto)
  Колонка L:  Время ролика (сек)        (числовое; используется как лимит)
  Колонка M:  Закадр. текст (символов)  (формула =L*13.5; авто)
  Колонка N:  Подпроект (slug)          (заполняется автоматически)
  Колонка O:  Статус                    (заполняется автоматически)
  Колонка P:  Прогресс                  (заполняется автоматически)
  Колонка Q:  Обновлён                  (UTC, заполняется автоматически)

Обязательная колонка только B (Название). Остальные карточные поля
(C..L) — необязательные, но если заполнены, попадут в промпт плана/
сценария как контекст. Колонка M — формула, считается Excel'ом.
Колонки N..Q — сервисные.

Для импорта новых тем юзер заполняет колонку B (минимум) и опц. C..L
на чистых строках, заливает обратно — батч-сервис создаёт подпроекты
только для строк с непустой темой и пустым «Подпроект».
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from loguru import logger

SHEET_NAME = "Темы"

# Карточные поля — попадают в Project.meta["topic_card"] и в промпт.
# video_duration_sec и voiceover_chars_target — для бюджета закадрового текста.
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
    "video_duration_sec",      # L: Время ролика (сек)
    "voiceover_chars_target",  # M: =L*13.5 — целевой объём закадра
]

# Множитель символов закадра на секунду ролика (1 сек ≈ 13.5 симв).
# Используется и в формуле Excel в колонке M, и в подсчёте бюджета в коде.
VOICEOVER_CHARS_PER_SECOND = 13.5

HEADERS = [
    "№",                                # A
    "Название ролика",                  # B
    "Источник",                         # C
    "Стиль",                            # D
    "Тип хука",                         # E
    "Эмоциональный фон",                # F
    "Научпоп ядро / факт",              # G
    "Логическое объяснение",            # H
    "Интеграция продукта",              # I
    "Примечание по съёмке",             # J
    "hero_mode",                        # K
    "Время ролика (сек)",               # L
    "Закадр. текст (символов = L×13,5)",  # M
    "⛔ СЛУЖ. НЕ ТРОГАТЬ — slug",         # N
    "⛔ СЛУЖ. НЕ ТРОГАТЬ — статус",       # O
    "⛔ СЛУЖ. НЕ ТРОГАТЬ — прогресс",     # P
    "⛔ СЛУЖ. НЕ ТРОГАТЬ — обновлён",     # Q
]

# Колонки N–Q — сервисные, в них бот сам записывает данные при выгрузке.
# Априори их не нужно редактировать вручную — collect_new_topics()
# игнорирует любые значения в N, которые не похожи на реальный slug.
SERVICE_COL_INDICES = (14, 15, 16, 17)  # N, O, P, Q (1-based)

# Индексы (1-based) ключевых колонок — чтоб не считать вручную.
COL_TITLE = 2          # B
COL_HERO_MODE = 11     # K
COL_DURATION = 12      # L
COL_VOICEOVER = 13     # M
COL_SLUG = 14          # N
COL_STATUS = 15        # O
COL_PROGRESS = 16      # P
COL_UPDATED = 17       # Q

COL_WIDTHS = [4, 40, 14, 16, 22, 18, 50, 50, 50, 28, 12, 16, 22, 32, 32, 28, 32]


def _now_iso() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _header_count() -> int:
    return len(HEADERS)


def _apply_service_styling(ws, last_row: int) -> None:
    """Покрасить сервисные колонки L–O в серый + локнуть их.

    Визуальный сигнал: "не трогать". Лок работает в Excel при
    включённой защите листа, но без пароля и опционально —
    просто подсказка.
    """
    from openpyxl.styles import PatternFill, Font, Alignment, Protection
    gray = PatternFill(start_color="FFE0E0E0", end_color="FFE0E0E0",
                       fill_type="solid")
    head_red = Font(color="FF990000", bold=True, size=10)
    centered = Alignment(horizontal="center", vertical="center",
                         wrap_text=True)
    for col_idx in SERVICE_COL_INDICES:
        h = ws.cell(row=2, column=col_idx)
        h.fill = gray
        h.font = head_red
        h.alignment = centered
        for r in range(3, max(last_row, 2) + 1):
            c = ws.cell(row=r, column=col_idx)
            c.fill = gray
            c.protection = Protection(locked=True)


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

    _apply_service_styling(ws, last_row=2)

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
        # L (duration), M (formula =L*13.5).
        # Если юзер уже вписал duration — пишем числом, иначе ставим
        # пустые и формулу в M — это место для ручного или GPT-заполнения.
        duration_val = row.get("video_duration_sec")
        if duration_val is not None and str(duration_val).strip() != "":
            try:
                ws.cell(row=r, column=COL_DURATION, value=float(duration_val))
            except (TypeError, ValueError):
                ws.cell(row=r, column=COL_DURATION, value=duration_val)
        # M — всегда формула (вычисляется Excel'ом при открытии).
        # OOXML хранит формулу с точкой как десятичный разделитель, Excel
        # сам переводит в локальный (запятая в ru_RU).
        ws.cell(
            row=r, column=COL_VOICEOVER,
            value=f"=L{r}*{VOICEOVER_CHARS_PER_SECOND}",
        )
        ws.cell(row=r, column=COL_SLUG, value=row.get("slug"))
        ws.cell(row=r, column=COL_STATUS, value=row.get("status"))
        ws.cell(row=r, column=COL_PROGRESS, value=row.get("progress"))
        ws.cell(row=r, column=COL_UPDATED, value=_now_iso())

    _apply_service_styling(ws, last_row=2 + len(rows))

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
    # Дополняем до 17 колонок (вдруг старый файл с 7 или 15 колонками) —
    # старые файлы без L/M просто получат None в этих полях.
    EXPECTED_COLS = _header_count()  # 17
    for row in ws.iter_rows(min_row=3, values_only=True):
        if not row:
            continue
        padded = (row + (None,) * EXPECTED_COLS)[:EXPECTED_COLS]
        (
            position, title, source, style, hook_type, emotion, fact,
            logic, integration, shoot_note, hero_mode,
            video_duration_sec, voiceover_chars_target,
            slug, status, progress, updated_at,
        ) = padded
        title_clean = _s(title) or ""
        if not title_clean:
            continue

        # duration — принимаем число или строку «30» / «30 сек» / «30.5».
        duration_num: float | None = None
        if video_duration_sec is not None:
            try:
                duration_num = float(
                    str(video_duration_sec).replace(",", ".")
                    .strip().split()[0]
                )
            except (ValueError, IndexError):
                duration_num = None

        # chars — в приорите фактическое значение из ячейки (вдруг GPT переписал
        # формулу числом). Иначе — вычисляем из duration_num * 13.5.
        chars_num: float | None = None
        if voiceover_chars_target is not None:
            try:
                chars_num = float(
                    str(voiceover_chars_target).replace(",", ".")
                    .strip().split()[0]
                )
            except (ValueError, IndexError):
                chars_num = None
        if chars_num is None and duration_num is not None:
            chars_num = round(duration_num * VOICEOVER_CHARS_PER_SECOND, 1)

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
            "video_duration_sec": duration_num,
            "voiceover_chars_target": chars_num,
            "slug": _s(slug),
            "status": _s(status),
            "progress": _s(progress),
            "updated_at": updated_at,
        })
    return out


def collect_new_topics(
    path: Path,
    known_slugs: set[str] | None = None,
) -> list[dict]:
    """Возвращает только новые темы (ещё не созданы подпроекты).

    `known_slugs` — множество реально существующих slug'ов подпроектов
    данного массового (из БД). Строка считается «уже привязанной»
    ТОЛЬКО если её slug-колонка содержит ровно один из этих slug'ов.
    Любая каша / случайный текст / название продукта в колонке L → строка
    идёт в новые темы.

    Если `known_slugs is None` — поведение как раньше: строка считается
    новой, если slug пустой. Используется в тестах.
    """
    rows = read_topics(path)
    if known_slugs is None:
        return [r for r in rows if not r.get("slug")]
    return [r for r in rows if (r.get("slug") or "") not in known_slugs]


def topic_card_from_row(row: dict) -> dict:
    """Извлекает только карточные поля из строки xlsx (для Project.meta)."""
    return {k: row.get(k) for k in CARD_FIELDS if row.get(k)}
