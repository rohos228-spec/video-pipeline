"""Запись project.xlsx из текстового ответа GPT API (без браузерного download).

Модель не отдаёт бинарный .xlsx — поэтому:
  1) если в ответе/скачанных файлах есть .xlsx — копируем поверх project.xlsx;
  2) иначе парсим блоки `# Лист: <имя>` + TSV (тот же формат, что `xlsx_to_text`)
     и пишем в копию исходного workbook;
  3) если TSV нет, но ответ — длинная проза, а в книге есть «Общий план» —
     дописываем план туда (fallback для шага plan).
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from loguru import logger

_SHEET_HEADER_RE = re.compile(
    r"^\s*#\s*Лист\s*:\s*(.+?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_FENCE_RE = re.compile(
    r"```(?:tsv|csv|text|xlsx)?\s*\n(.*?)```",
    re.IGNORECASE | re.DOTALL,
)

# Совпадает с app.services.xlsx_v8_import.SHEET_GENERAL_V8 / plan_validation.
_GENERAL_PLAN_SHEET = "Общий план"
_MIN_PROSE_PLAN_CHARS = 200


def extract_sheet_blocks(text: str) -> dict[str, list[list[str]]]:
    """Разобрать `# Лист: Name` + TSV-строки → {sheet_name: rows}."""
    if not (text or "").strip():
        return {}
    # Сначала пробуем fenced-блоки; если пусто — весь текст.
    chunks: list[str] = []
    for m in _FENCE_RE.finditer(text):
        body = (m.group(1) or "").strip()
        if body:
            chunks.append(body)
    if not chunks:
        chunks = [text]

    out: dict[str, list[list[str]]] = {}
    for chunk in chunks:
        current: str | None = None
        for line in chunk.splitlines():
            hm = _SHEET_HEADER_RE.match(line)
            if hm:
                current = hm.group(1).strip()
                out.setdefault(current, [])
                continue
            if current is None:
                # Без `# Лист:` — только явный TSV (табы). Запятые в русской
                # прозе (. «армия, право, дороги») нельзя считать CSV.
                if "\t" in line:
                    current = "Данные"
                    out.setdefault(current, [])
                else:
                    continue
            if not line.strip() or line.strip().startswith("…"):
                continue
            if "\t" in line:
                cells = line.split("\t")
            elif "|" in line and not line.strip().startswith("|--"):
                # markdown table row
                raw = [c.strip() for c in line.strip().strip("|").split("|")]
                if raw and all(re.fullmatch(r":?-{2,}:?", c or "") for c in raw):
                    continue
                cells = raw
            else:
                cells = [line]
            out[current].append(cells)
    # Убрать пустые листы
    return {k: v for k, v in out.items() if v}


def apply_sheet_blocks_to_xlsx(
    src_xlsx: Path,
    blocks: dict[str, list[list[str]]],
    dest_xlsx: Path,
) -> Path:
    """Записать TSV-блоки в workbook (по имени листа) → dest_xlsx."""
    from openpyxl import Workbook, load_workbook

    if not blocks:
        raise ValueError("нет блоков листов для записи")

    if src_xlsx.exists() and src_xlsx.stat().st_size > 0:
        wb = load_workbook(filename=str(src_xlsx))
    else:
        wb = Workbook()
        # Удалим дефолтный лист, если будем создавать свои.
        if wb.sheetnames == ["Sheet"] and "Данные" in blocks:
            std = wb.active
            if std is not None:
                wb.remove(std)

    name_map = {n.lower(): n for n in wb.sheetnames}
    for sheet_name, rows in blocks.items():
        key = sheet_name.lower()
        if key in name_map:
            ws = wb[name_map[key]]
        else:
            ws = wb.create_sheet(title=sheet_name[:31] or "Данные")
            name_map[key] = ws.title
        # Очистить используемую область и записать новые значения.
        if ws.max_row and ws.max_column:
            for r in ws.iter_rows(
                min_row=1,
                max_row=ws.max_row,
                max_col=ws.max_column,
            ):
                for cell in r:
                    cell.value = None
        for r_idx, row in enumerate(rows, start=1):
            for c_idx, val in enumerate(row, start=1):
                ws.cell(row=r_idx, column=c_idx, value=val)

    dest_xlsx.parent.mkdir(parents=True, exist_ok=True)
    wb.save(str(dest_xlsx))
    wb.close()
    return dest_xlsx


def _strip_reply_noise(text: str) -> str:
    t = (text or "").strip()
    if not t:
        return ""
    t = re.sub(r"```[\w]*\n?", "", t)
    t = t.replace("```", "").strip()
    return t


def write_prose_into_general_plan(
    src_xlsx: Path,
    dest_xlsx: Path,
    prose: str,
) -> Path:
    """Дописать длинный текстовый план на лист «Общий план» (без wipe других листов)."""
    from openpyxl import load_workbook

    cleaned = _strip_reply_noise(prose)
    if len(cleaned) < _MIN_PROSE_PLAN_CHARS:
        raise ValueError(
            f"проза слишком короткая для плана ({len(cleaned)} < {_MIN_PROSE_PLAN_CHARS})"
        )
    if not src_xlsx.exists():
        raise FileNotFoundError(str(src_xlsx))

    wb = load_workbook(filename=str(src_xlsx))
    try:
        if _GENERAL_PLAN_SHEET not in wb.sheetnames:
            raise ValueError(f"нет листа «{_GENERAL_PLAN_SHEET}»")
        ws = wb[_GENERAL_PLAN_SHEET]
        # Не чистим шаблон целиком — добавляем пару label/value в конец.
        row = (ws.max_row or 0) + 2
        if row < 1:
            row = 1
        ws.cell(row=row, column=1, value="План (GPT)")
        # Excel cell limit ~32767
        ws.cell(row=row, column=2, value=cleaned[:32000])
        dest_xlsx.parent.mkdir(parents=True, exist_ok=True)
        wb.save(str(dest_xlsx))
    finally:
        wb.close()
    return dest_xlsx


def writeback_project_xlsx(
    *,
    project_xlsx: Path,
    reply_text: str,
    downloaded_paths: list[Path] | None = None,
) -> Path | None:
    """Обновить project.xlsx из скачанного .xlsx или TSV-ответа.

    Возвращает путь к project.xlsx при успехе, иначе None.
    """
    downloaded_paths = list(downloaded_paths or [])
    for p in downloaded_paths:
        if p.suffix.lower() in {".xlsx", ".xlsm", ".xls"} and p.exists() and p.stat().st_size > 64:
            project_xlsx.parent.mkdir(parents=True, exist_ok=True)
            if p.resolve() != project_xlsx.resolve():
                shutil.copy2(p, project_xlsx)
            logger.info(
                "xlsx_writeback: скопирован {} → {} ({} байт)",
                p.name,
                project_xlsx.name,
                project_xlsx.stat().st_size,
            )
            return project_xlsx

    blocks = extract_sheet_blocks(reply_text or "")
    if blocks:
        tmp = project_xlsx.with_suffix(".writeback.tmp.xlsx")
        try:
            apply_sheet_blocks_to_xlsx(project_xlsx, blocks, tmp)
            shutil.move(str(tmp), str(project_xlsx))
            logger.info(
                "xlsx_writeback: TSV→{} листов={}, size={}",
                project_xlsx.name,
                list(blocks.keys()),
                project_xlsx.stat().st_size,
            )
            return project_xlsx
        except Exception as e:  # noqa: BLE001
            logger.warning("xlsx_writeback failed: {}", e)
            if tmp.exists():
                tmp.unlink(missing_ok=True)
            # fall through to prose fallback

    # Fallback: GPT отдал прозу без `# Лист:` / TSV
    prose = _strip_reply_noise(reply_text or "")
    if len(prose) >= _MIN_PROSE_PLAN_CHARS and project_xlsx.exists():
        tmp = project_xlsx.with_suffix(".writeback.prose.tmp.xlsx")
        try:
            write_prose_into_general_plan(project_xlsx, tmp, prose)
            shutil.move(str(tmp), str(project_xlsx))
            logger.info(
                "xlsx_writeback: prose→«{}» ({} симв) → {}",
                _GENERAL_PLAN_SHEET,
                len(prose),
                project_xlsx.name,
            )
            return project_xlsx
        except Exception as e:  # noqa: BLE001
            logger.warning("xlsx_writeback prose fallback failed: {}", e)
            if tmp.exists():
                tmp.unlink(missing_ok=True)

    logger.debug(
        "xlsx_writeback: в ответе нет TSV/листов и prose-fallback не сработал"
    )
    return None


WRITEBACK_HINT = (
    "Верни обновлённый Excel в текстовом формате: для каждого листа строка "
    "`# Лист: <имя>` и далее строки TSV (ячейки через табуляцию). "
    "Для шага план обязателен лист «Общий план» (`# Лист: Общий план`). "
    "Сохрани имена листов как во входе. Либо приложи прямую ссылку на .xlsx."
)
