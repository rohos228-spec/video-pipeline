"""Запись project.xlsx из текстового ответа GPT API (без браузерного download).

Модель не отдаёт бинарный .xlsx — поэтому:
  1) если в ответе/скачанных файлах есть .xlsx — копируем поверх project.xlsx;
  2) иначе TSV `# Лист:` накладываем на шаблон БЕЗ wipe строк/merge;
  3) если TSV нет, но ответ длинный — пишем в B2 листа «Общий план»
     (подписи A и структура строк сохраняются).
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


def _set_cell_value(ws, row: int, col: int, value: str) -> None:
    """Записать значение с учётом merge (как в чате GPT: структура листа не ломается)."""
    from app.services.xlsx_v8_import import _resolve_plan_cell

    cell = _resolve_plan_cell(ws, row, col)
    # MergedCell без top-left — пропускаем
    if type(cell).__name__ == "MergedCell":
        return
    cell.value = value


def apply_sheet_blocks_to_xlsx(
    src_xlsx: Path,
    blocks: dict[str, list[list[str]]],
    dest_xlsx: Path,
) -> Path:
    """Наложить TSV на существующий workbook БЕЗ wipe строк/merge.

    Как скачанный .xlsx из чата: шаблон (merge, подписи, другие листы) сохраняем,
    пишем только пришедшие значения ячеек.
    """
    from openpyxl import Workbook, load_workbook

    if not blocks:
        raise ValueError("нет блоков листов для записи")

    if src_xlsx.exists() and src_xlsx.stat().st_size > 0:
        wb = load_workbook(filename=str(src_xlsx))
    else:
        wb = Workbook()
        if wb.sheetnames == ["Sheet"] and "Данные" in blocks:
            std = wb.active
            if std is not None:
                wb.remove(std)

    name_map = {n.lower(): n for n in wb.sheetnames}
    # Полный project.xlsx (v8) — нельзя плодить лишние листы (ломают validate).
    strict_layout = (
        _GENERAL_PLAN_SHEET in wb.sheetnames or "план" in name_map
    )

    for sheet_name, rows in blocks.items():
        key = sheet_name.lower()
        if key in name_map:
            ws = wb[name_map[key]]
        elif strict_layout:
            logger.warning(
                "xlsx_writeback: skip unknown sheet {!r} (сохраняем layout)",
                sheet_name,
            )
            continue
        else:
            ws = wb.create_sheet(title=sheet_name[:31] or "Данные")
            name_map[key] = ws.title

        for r_idx, row in enumerate(rows, start=1):
            for c_idx, val in enumerate(row, start=1):
                if val is None:
                    continue
                s = str(val)
                # Пустая ячейка в TSV — не затираем подпись/merge шаблона
                if not s.strip():
                    continue
                _set_cell_value(ws, r_idx, c_idx, s)

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
    """Записать длинный текст плана в value-ячейку шаблона «Общий план».

    Не добавляем новые строки в конец — пишем в B2 (типичный content merge),
    подписи колонки A и merge ranges не трогаем.
    """
    from openpyxl import load_workbook
    from app.services.xlsx_v8_import import _resolve_plan_cell

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
        # Пишем пару A2/B2: импортёр _read_general_plan берёт только a+b.
        # Подписи A1 и merge не ломаем; новые строки в конец не добавляем.
        label_cell = _resolve_plan_cell(ws, 2, 1)
        value_cell = _resolve_plan_cell(ws, 2, 2)
        if type(value_cell).__name__ == "MergedCell":
            value_cell = _resolve_plan_cell(ws, 3, 2)
        if type(value_cell).__name__ == "MergedCell":
            raise ValueError("не удалось записать в ячейки «Общий план» (merge)")
        if type(label_cell).__name__ != "MergedCell":
            if not (label_cell.value and str(label_cell.value).strip()):
                label_cell.value = "План (GPT)"
        value_cell.value = cleaned[:32000]
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
