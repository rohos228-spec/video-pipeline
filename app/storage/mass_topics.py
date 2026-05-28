"""Парсинг Excel со списком тем (любой xlsx, построчно — название видео)."""

from __future__ import annotations

from pathlib import Path

from loguru import logger

_TOPIC_HEADER_KEYS = frozenset(
    {
        "название",
        "название ролика",
        "тема",
        "topic",
        "title",
        "видео",
        "name",
    }
)


def _cell_str(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _topics_from_batch_sheet(path: Path) -> list[str] | None:
    from app.storage import batch_sheet

    try:
        rows = batch_sheet.read_topics(path)
    except Exception as exc:  # noqa: BLE001
        logger.debug("mass_topics: batch_sheet failed {} — {}", path.name, exc)
        return None
    topics: list[str] = []
    for row in rows:
        title = (row.get("title") or row.get("topic") or "").strip()
        if title:
            topics.append(title)
    return topics if topics else None


def _topics_from_first_sheet(path: Path) -> list[str]:
    from openpyxl import load_workbook

    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb.active
        if ws is None:
            return []
        rows = list(ws.iter_rows(values_only=True))
    finally:
        wb.close()

    if not rows:
        return []

    ncol = max(len(r) for r in rows if r) if rows else 0
    if ncol <= 1:
        topics: list[str] = []
        seen: set[str] = set()
        for row in rows:
            if not row:
                continue
            title = _cell_str(row[0])
            if not title or title in seen:
                continue
            seen.add(title)
            topics.append(title)
        return topics

    header_row = rows[0]
    headers = [_cell_str(c).lower() for c in header_row]
    topic_col = 0
    for idx, h in enumerate(headers):
        if h in _TOPIC_HEADER_KEYS:
            topic_col = idx
            break
        if any(k in h for k in ("тема", "topic", "title", "назван")):
            topic_col = idx
            break

    data_rows = rows[1:] if any(headers) else rows
    if not any(headers):
        topic_col = 0

    topics: list[str] = []
    seen: set[str] = set()
    for row in data_rows:
        if not row:
            continue
        if topic_col >= len(row):
            continue
        title = _cell_str(row[topic_col])
        if not title or title in seen:
            continue
        seen.add(title)
        topics.append(title)
    return topics


def parse_topics_xlsx(path: Path) -> list[str]:
    """Вернёт список названий видео из xlsx (только текст темы)."""
    flex = _topics_from_first_sheet(path)
    if flex:
        return flex
    batch = _topics_from_batch_sheet(path)
    if batch:
        return batch
    return []
