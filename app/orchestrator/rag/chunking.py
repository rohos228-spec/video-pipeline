"""Нарезка корпусов: 1 правило = 1 чанк; промпт — до 8 частей; история — окна."""

from __future__ import annotations

import re
from typing import Any

from app.orchestrator.rag.types import SourceDoc, normalize_metadata

_RULE_HEADING = re.compile(r"^## Правило\s+(\d+)\b.*$", re.MULTILINE)
_H2 = re.compile(r"(?=^## )", re.MULTILINE)

# ~300–600 токенов для русского текста (грубо 2–3 символа на токен).
_HISTORY_WINDOW_CHARS = 1600


def split_rules(text: str, *, source_id: str) -> list[SourceDoc]:
    """Каждое «## Правило N» — отдельный чанк. Преамбула — свой чанк, если непустая."""
    matches = list(_RULE_HEADING.finditer(text))
    docs: list[SourceDoc] = []
    if not matches:
        body = text.strip()
        if body:
            docs.append(
                _doc(
                    "rules",
                    source_id,
                    "Правила пользователя",
                    body,
                    "full",
                    {},
                )
            )
        return docs
    preamble = text[: matches[0].start()].strip()
    if preamble:
        docs.append(_doc("rules", source_id, "Канон правил", preamble, "preamble", {}))
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        number = match.group(1)
        title = match.group(0).lstrip("#").strip()
        docs.append(_doc("rules", source_id, title, body, f"rule-{number}", {}))
    return docs


def section_windows(text: str, *, limit: int = 2000, window: int = 700) -> list[str]:
    """Длинную секцию режет по абзацам. Секция короче 2000 символов окон не получает.

    Порог 2000 оставляет окна только у самых длинных секций сборки и скелета.
    Секции около 1100–1300 символов в топ сильных семейств не подмешиваются.

    Родительский текст остаётся отдельным чанком: окна — дополнительные
    входы в ту же секцию, не замена part_key.
    """
    body = (text or "").strip()
    if limit < 1 or window < 1 or len(body) <= limit:
        return []
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", body) if part.strip()]
    if len(paragraphs) < 2:
        step = window
        paragraphs = [body[start : start + step] for start in range(0, len(body), step)]
    windows: list[str] = []
    current: list[str] = []
    current_len = 0
    for paragraph in paragraphs:
        extra = len(paragraph) + 2
        if current and current_len + extra > window:
            windows.append("\n\n".join(current))
            current = [paragraph]
            current_len = len(paragraph)
        else:
            current.append(paragraph)
            current_len += extra
    if current:
        windows.append("\n\n".join(current))
    if len(windows) < 2:
        return []
    return windows


def split_prompt(text: str, *, max_parts: int = 8) -> list[tuple[str, str]]:
    """Режет промпт по заголовкам ``##`` и укладывает в не больше ``max_parts`` частей."""
    body = text.strip()
    if not body:
        return []
    parts = [part.strip() for part in _H2.split(body) if part.strip()]
    if len(parts) <= max_parts:
        return [(f"s{i}", part) for i, part in enumerate(parts)]
    buckets: list[list[str]] = [[] for _ in range(max_parts)]
    for index, part in enumerate(parts):
        buckets[min(max_parts - 1, index * max_parts // len(parts))].append(part)
    packed = [(f"s{i}", "\n\n".join(items)) for i, items in enumerate(buckets) if items]
    return packed


def split_history_text(text: str, *, window_chars: int = _HISTORY_WINDOW_CHARS) -> list[str]:
    """Режет длинный текст истории на окна ~300–600 токенов по границам абзацев."""
    body = text.strip()
    if not body:
        return []
    if len(body) <= window_chars:
        return [body]
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
    if not paragraphs:
        paragraphs = [body]
    windows: list[str] = []
    current: list[str] = []
    current_len = 0
    for paragraph in paragraphs:
        extra = len(paragraph) + 2
        if current and current_len + extra > window_chars:
            windows.append("\n\n".join(current))
            current = [paragraph]
            current_len = len(paragraph)
        else:
            current.append(paragraph)
            current_len += extra
    if current:
        windows.append("\n\n".join(current))
    return windows


def window_messages(messages: list[dict[str, Any]], *, size: int = 10) -> list[list[dict[str, Any]]]:
    """Окна по 10 сообщений (корпус истории чатов)."""
    if size < 1:
        raise ValueError("size должен быть >= 1")
    return [messages[i : i + size] for i in range(0, len(messages), size)]


def _doc(
    corpus: str,
    source_id: str,
    title: str,
    text: str,
    part_key: str,
    metadata: dict[str, Any],
) -> SourceDoc:
    return SourceDoc(
        corpus=corpus,
        source_id=source_id,
        title=title,
        text=text,
        part_key=part_key,
        metadata=normalize_metadata(metadata),
    )
