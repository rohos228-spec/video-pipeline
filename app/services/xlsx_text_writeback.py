"""Запись project.xlsx из текстового ответа GPT API (без браузерного download).

Модель не отдаёт бинарный .xlsx — поэтому:
  1) если в ответе/скачанных файлах есть .xlsx — накладываем НЕПУСТЫЕ ячейки
     на существующий шаблон (full replace только если шаблона ещё нет);
  2) иначе TSV `# Лист:` + `@row=N` накладываем на шаблон БЕЗ wipe строк/merge;
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
_ROW_MARK_RE = re.compile(r"^@row=(\d+)$", re.IGNORECASE)

# Совпадает с app.services.xlsx_v8_import.SHEET_GENERAL_V8 / plan_validation.
_GENERAL_PLAN_SHEET = "Общий план"
_MIN_PROSE_PLAN_CHARS = 200


def extract_sheet_blocks(text: str) -> dict[str, list[list[str]]]:
    """Разобрать `# Лист: Name` + TSV-строки → {sheet_name: rows}.

    Строки могут начинаться с `@row=N` (абсолютный номер Excel).
    """
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


def _parse_row_mark(row: list[str]) -> tuple[int | None, list[str]]:
    """Если первая ячейка `@row=N` — вернуть (N, остальные ячейки)."""
    if not row:
        return None, []
    first = str(row[0] or "").strip()
    m = _ROW_MARK_RE.match(first)
    if not m:
        return None, list(row)
    return int(m.group(1)), list(row[1:])


def _label_row_map(ws) -> dict[str, int]:
    """Подпись колонки A → номер строки (для legacy TSV без @row=)."""
    out: dict[str, int] = {}
    max_r = int(ws.max_row or 0)
    for r in range(1, max_r + 1):
        from app.services.xlsx_v8_import import _resolve_plan_cell

        cell = _resolve_plan_cell(ws, r, 1)
        if type(cell).__name__ == "MergedCell":
            continue
        lab = str(cell.value or "").strip()
        if lab:
            out.setdefault(lab.casefold(), r)
    return out


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

    Абсолютные строки: префикс `@row=N` в первой колонке TSV.
    Без префикса: sequential (legacy); для листа «план» / «Общий план»
    пробуем сопоставить подпись A с шаблоном, чтобы не сдвинуть R45/R49.
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

        has_row_marks = any(_parse_row_mark(r)[0] is not None for r in rows)
        label_map: dict[str, int] = {}
        use_label_fallback = (
            not has_row_marks
            and key in {"план", "общий план", "общий план ролика"}
        )
        if use_label_fallback:
            label_map = _label_row_map(ws)

        sequential_idx = 0
        for row in rows:
            abs_row, cells = _parse_row_mark(row)
            if abs_row is None:
                sequential_idx += 1
                abs_row = sequential_idx
                if use_label_fallback and cells:
                    lab = str(cells[0] or "").strip().casefold()
                    if lab and lab in label_map:
                        abs_row = label_map[lab]
            if abs_row < 1:
                continue
            for c_idx, val in enumerate(cells, start=1):
                if val is None:
                    continue
                s = str(val)
                # Пустая ячейка в TSV — не затираем подпись/merge шаблона
                if not s.strip():
                    continue
                _set_cell_value(ws, abs_row, c_idx, s)

    dest_xlsx.parent.mkdir(parents=True, exist_ok=True)
    wb.save(str(dest_xlsx))
    wb.close()
    return dest_xlsx


def _is_v8_project_workbook(path: Path) -> bool:
    if not path.exists() or path.stat().st_size < 64:
        return False
    try:
        from openpyxl import load_workbook

        wb = load_workbook(filename=str(path), read_only=True)
        try:
            names = {n.lower() for n in wb.sheetnames}
            return "план" in names or "общий план" in names
        finally:
            wb.close()
    except Exception:  # noqa: BLE001
        return False


def merge_xlsx_nonempty_overlay(
    project_xlsx: Path,
    gpt_xlsx: Path,
    dest_xlsx: Path | None = None,
) -> Path:
    """Скопировать непустые ячейки из gpt_xlsx поверх project_xlsx.

    Пустые/отсутствующие в GPT ячейки НЕ затирают шаблон (enrich R2–44 и т.п.).
    Листы, которых нет в шаблоне, на v8-книге пропускаем.
    """
    from openpyxl import load_workbook

    dest = dest_xlsx or project_xlsx
    wb_proj = load_workbook(filename=str(project_xlsx))
    wb_gpt = load_workbook(filename=str(gpt_xlsx), data_only=True)
    try:
        name_map = {n.lower(): n for n in wb_proj.sheetnames}
        strict = "план" in name_map or "общий план" in name_map
        written = 0
        for gpt_name in wb_gpt.sheetnames:
            key = gpt_name.lower()
            if key not in name_map:
                if strict:
                    logger.warning(
                        "xlsx_overlay: skip unknown sheet {!r}", gpt_name
                    )
                    continue
                ws_p = wb_proj.create_sheet(title=gpt_name[:31])
                name_map[key] = ws_p.title
            else:
                ws_p = wb_proj[name_map[key]]
            ws_g = wb_gpt[gpt_name]
            max_r = int(ws_g.max_row or 0)
            max_c = min(int(ws_g.max_column or 0), 80)
            for r in range(1, max_r + 1):
                for c in range(1, max_c + 1):
                    val = ws_g.cell(row=r, column=c).value
                    if val is None:
                        continue
                    s = str(val)
                    if not s.strip():
                        continue
                    _set_cell_value(ws_p, r, c, s)
                    written += 1
        dest.parent.mkdir(parents=True, exist_ok=True)
        wb_proj.save(str(dest))
        logger.info(
            "xlsx_overlay: {} ← {} cells={} → {}",
            project_xlsx.name,
            gpt_xlsx.name,
            written,
            dest.name,
        )
    finally:
        wb_gpt.close()
        wb_proj.close()
    return dest


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
    reply = reply_text or ""

    # Отчёт проверки (JSON/TXT) нельзя заливать в «Общий план» через prose/TSV.
    try:
        from app.services.check_analysis import looks_like_check_payload
    except Exception:  # noqa: BLE001
        looks_like_check_payload = None  # type: ignore[assignment]

    if callable(looks_like_check_payload) and looks_like_check_payload(reply):
        has_xlsx = any(
            p.suffix.lower() in {".xlsx", ".xlsm", ".xls"}
            and p.exists()
            and p.stat().st_size > 64
            for p in downloaded_paths
        )
        logger.warning(
            "xlsx_writeback: skip text — reply looks like check report "
            "(binary_xlsx={})",
            has_xlsx,
        )
        if not has_xlsx:
            return None
        reply = ""

    for p in downloaded_paths:
        if p.suffix.lower() in {".xlsx", ".xlsm", ".xls"} and p.exists() and p.stat().st_size > 64:
            project_xlsx.parent.mkdir(parents=True, exist_ok=True)
            if p.resolve() == project_xlsx.resolve():
                return project_xlsx
            if _is_v8_project_workbook(project_xlsx):
                # Не подменяем весь файл — GPT часто отдаёт «урезанную» книгу
                # и затирает enrich / чужие строки (как старый баг img_pr).
                try:
                    merge_xlsx_nonempty_overlay(project_xlsx, p, project_xlsx)
                    return project_xlsx
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        "xlsx_writeback: overlay failed ({}), fallback copy: {}",
                        p.name,
                        e,
                    )
            shutil.copy2(p, project_xlsx)
            logger.info(
                "xlsx_writeback: скопирован {} → {} ({} байт)",
                p.name,
                project_xlsx.name,
                project_xlsx.stat().st_size,
            )
            return project_xlsx

    blocks = extract_sheet_blocks(reply)
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
    prose = _strip_reply_noise(reply)
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
    "Каждая строка ОБЯЗАТЕЛЬНО начинается с `@row=<номер Excel>` как во входе "
    "(пример: `@row=49\\tзакадровый текст\\t...`) — не уплотняй пустые ряды "
    "и не сдвигай номера строк. "
    "Для шага план обязателен лист «Общий план» (`# Лист: Общий план`). "
    "Сохрани имена листов как во входе. Либо приложи прямую ссылку на .xlsx."
)
