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
    "сначала пойму",
    "сначала создам",
    "рабочую мысль",
    "саморедактур",
    "перед выдачей",
    "готовым закадровым",
    "напишу цельный",
    "проверю ритм",
    "уберу метафор",
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

_SHEET_HEADER_RE = re.compile(
    r"^\s*#\s*Лист\s*:\s*.+",
    re.IGNORECASE | re.MULTILINE,
)


def looks_like_xlsx_tsv_writeback(text: str) -> bool:
    """True, если текст — TSV writeback Excel (`# Лист:` / `@row=`), не закадр."""
    raw = (text or "").strip()
    if not raw:
        return False
    headers = _SHEET_HEADER_RE.findall(raw)
    if len(headers) >= 2:
        return True
    if headers and ("@row=" in raw.lower() or "\t" in raw):
        return True
    if raw.lower().count("@row=") >= 2:
        return True
    return False


def _has_process_preamble(text: str) -> bool:
    head = (text or "")[:800].lower()
    hits = sum(1 for m in _PROCESS_MARKERS if m in head)
    return hits >= 2


def _cut_process_preamble(body: str) -> str:
    match = _NARRATION_START_RE.search(body)
    if match is not None and match.start() >= 40:
        cut = body[match.start() :].lstrip()
        if len(cut) >= 80:
            return cut.strip()
    # Запасной путь: первый абзац после пустой строки, если голова — мета.
    parts = re.split(r"\n\s*\n", body, maxsplit=4)
    if len(parts) >= 2 and _has_process_preamble(parts[0]):
        for i in range(1, len(parts)):
            rest = "\n\n".join(parts[i:]).strip()
            if len(rest) >= 80 and not _has_process_preamble(rest[:200]):
                return rest
    return body


def sanitize_voiceover_text(text: str) -> str:
    """Убрать ИТОГО/мета-отчёт GPT, оставить готовый закадровый текст."""
    body = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not body:
        return ""
    body = _ITOGO_RE.sub("", body).strip()
    if not _has_process_preamble(body):
        return body
    return _cut_process_preamble(body)
