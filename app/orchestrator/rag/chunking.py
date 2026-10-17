"""Нарезка корпусов: 1 правило = 1 чанк; промпт — секции; история — окна."""

from __future__ import annotations

import re
from typing import Any

from app.orchestrator.rag.types import SourceDoc, normalize_metadata

_RULE_HEADING = re.compile(r"^## Правило\s+(\d+)\b.*$", re.MULTILINE)
_H2 = re.compile(r"(?=^## )", re.MULTILINE)
_HEADING_LINE = re.compile(r"^#{1,3}[ \t]+(.+?)\s*$", re.MULTILINE)

# Оценка токенов e5/XLM-R на этих промптах: ~3 символа на токен.
_CHARS_PER_TOKEN = 3
_SECTION_MAX_TOKENS = 512
_SECTION_OVERLAP_TOKENS = 64
# ~300–600 токенов для русского текста (грубо 2–3 символа на токен).
_HISTORY_WINDOW_CHARS = 1600


def estimate_tokens(text: str) -> int:
    """Грубая длина без загрузки токенайзера: CI не качает веса."""
    return max(1, (len(text) + _CHARS_PER_TOKEN - 1) // _CHARS_PER_TOKEN)


def section_prefix(*, node_key: str, title: str, role: str, heading: str) -> str:
    """Префикс секционного чанка: ключ, название, роль, заголовок секции."""
    role_text = (role or "").strip() or "роль не задана"
    head = (heading or "").strip() or "без заголовка"
    return f"Нода {node_key}. Название: {title}. Роль: {role_text}. Секция: {head}.\n"


def split_heading_sections(text: str) -> list[tuple[str, str]]:
    """Режет по заголовкам ``#``–``###``. Кусок до первого заголовка — своя секция."""
    body = (text or "").strip()
    if not body:
        return []
    matches = list(_HEADING_LINE.finditer(body))
    if not matches:
        return [("", body)]
    sections: list[tuple[str, str]] = []
    if matches[0].start() > 0:
        preamble = body[: matches[0].start()].strip()
        if preamble:
            sections.append(("", preamble))
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        chunk = body[start:end].strip()
        if not chunk:
            continue
        sections.append((match.group(1).strip(), chunk))
    return sections


def split_section_windows(
    text: str,
    *,
    prefix: str = "",
    max_tokens: int = _SECTION_MAX_TOKENS,
    overlap_tokens: int = _SECTION_OVERLAP_TOKENS,
) -> list[str]:
    """Длиннее ``max_tokens`` — окна с перекрытием. Короткая секция остаётся целиком.

    В бюджет входит префикс, чтобы пара с заголовком не вылезала за 512 токенов.
    """
    lead = prefix or ""
    # Запас на склейку абзацев: оценка токенов округляется вверх.
    budget = max_tokens - estimate_tokens(lead) - 16
    budget = max(128, budget)
    if estimate_tokens(text) <= budget:
        return [f"{lead}{text}".strip()]
    max_chars = budget * _CHARS_PER_TOKEN
    overlap_chars = min(overlap_tokens * _CHARS_PER_TOKEN, max_chars // 3)
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    if not paragraphs:
        paragraphs = [text.strip()]
    pieces: list[str] = []
    for paragraph in paragraphs:
        if len(paragraph) <= max_chars:
            pieces.append(paragraph)
            continue
        step = max(1, max_chars - overlap_chars)
        for start in range(0, len(paragraph), step):
            pieces.append(paragraph[start : start + max_chars])
            if start + max_chars >= len(paragraph):
                break
    windows: list[str] = []
    current: list[str] = []
    current_len = 0
    for piece in pieces:
        extra = len(piece) + 2
        if current and current_len + extra > max_chars:
            windows.append(f"{lead}" + "\n\n".join(current))
            tail = _overlap_tail(current, overlap_chars)
            tail_len = sum(len(part) + 2 for part in tail)
            if tail_len + extra <= max_chars:
                current = tail
                current_len = tail_len
            else:
                current = []
                current_len = 0
        current.append(piece)
        current_len += extra
    if current:
        windows.append(f"{lead}" + "\n\n".join(current))
    return windows


def _overlap_tail(parts: list[str], overlap_chars: int) -> list[str]:
    """Хвост окна не длиннее перекрытия. Длинный абзац обрезается с конца."""
    if overlap_chars <= 0 or not parts:
        return []
    kept: list[str] = []
    size = 0
    for part in reversed(parts):
        room = overlap_chars - size
        if room <= 0:
            break
        if len(part) + 2 <= room:
            kept.append(part)
            size += len(part) + 2
            continue
        kept.append(part[-room:])
        break
    return list(reversed(kept))


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


def split_prompt(text: str, *, max_parts: int = 3) -> list[tuple[str, str]]:
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
