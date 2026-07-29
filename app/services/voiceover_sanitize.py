"""Очистка ответа GPT для voiceover.txt: только закадровый текст."""

from __future__ import annotations

import re

# Промпты часто требуют самопроверку «ИТОГО: N символов» — в файл озвучки это не входит.
_ITOGO_RE = re.compile(
    r"(?im)(?:\n|\s)*итог[оа]?\s*:\s*\d+\s*символ\w*\.?\s*$"
)

# Мета-отчёт модели («Извлекаю…», «Каркас сохраняет…») перед самим рассказом.
_PROCESS_MARKERS: tuple[str, ...] = (
    "извлекаю",
    "затем проверю",
    "каркас сохраняет",
    "подготовлю текстовую",
    "подготовлю текстовую запись",
    "смещения номера",
    "без смещения номера",
    "формулировки про",
    "будут явно обозначены",
    "чтобы не выдавать",
    "сначала извлеку",
    "сейчас составлю",
    "сейчас напишу",
)

_MONTH = (
    r"(?:января|февраля|марта|апреля|мая|июня|июля|августа|"
    r"сентября|октября|ноября|декабря)"
)

# Начало похоже на закадровый рассказ (дата / событие), а не на план работы модели.
_NARRATION_START_RE = re.compile(
    rf"(?:"
    rf"(?:^|(?<=[.!?…]))\s*"
    rf"(?:"
    rf"(?:[А-ЯЁ][а-яё]+(?:ое|ье|го|ый)?\s+)?{_MONTH}\s+\d{{4}}"
    rf"|\d{{1,2}}\s+{_MONTH}\s+\d{{4}}"
    rf"|(?:В|Во)\s+\d{{4}}\s+год"
    rf"|(?:Однажды|Когда|После|До|Во время)\s+"
    rf")"
    rf")"
)


def _has_process_preamble(text: str) -> bool:
    head = (text or "")[:500].lower()
    hits = sum(1 for m in _PROCESS_MARKERS if m in head)
    return hits >= 2


def sanitize_voiceover_text(text: str) -> str:
    """Убрать ИТОГО/мета-отчёт GPT, оставить готовый закадровый текст."""
    body = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not body:
        return ""
    body = _ITOGO_RE.sub("", body).strip()
    if not _has_process_preamble(body):
        return body

    match = _NARRATION_START_RE.search(body)
    if match is not None and match.start() >= 40:
        cut = body[match.start() :].lstrip()
        # Мета обычно короче рассказа; не режем, если «хвост» крошечный.
        if len(cut) >= 80:
            return cut.strip()
    return body
