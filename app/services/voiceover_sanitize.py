"""Очистка ответа GPT для voiceover.txt: только закадровый текст."""

from __future__ import annotations

import re

# Контракт ответа шага script: код берёт только блок между маркерами.
VOICEOVER_START = "<<<VOICEOVER>>>"
VOICEOVER_END = "<<<END>>>"

VOICEOVER_OUTPUT_FORMAT = (
    "ОБЯЗАТЕЛЬНЫЙ ФОРМАТ ОТВЕТА:\n"
    "Весь готовый закадровый текст (только озвучка) оберни строго так:\n\n"
    f"{VOICEOVER_START}\n"
    "…только текст озвучки…\n"
    f"{VOICEOVER_END}\n\n"
    "Вне этих двух маркеров можно писать что угодно — в voiceover.txt попадёт "
    "только текст внутри.\n"
    "Внутри блока не пиши ИТОГО, комментарии, markdown и разбор плана."
)

_VOICEOVER_BLOCK_RE = re.compile(
    r"<<<\s*VOICEOVER\s*>>>\s*(.*?)\s*<<<\s*END\s*>>>",
    re.IGNORECASE | re.DOTALL,
)

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


def extract_voiceover_block(text: str) -> str | None:
    """Достать текст между <<<VOICEOVER>>> и <<<END>>>, иначе None."""
    m = _VOICEOVER_BLOCK_RE.search(text or "")
    if not m:
        return None
    body = (m.group(1) or "").strip()
    return body or None


def ensure_voiceover_format_instruction(chat_text: str) -> str:
    """Дописать контракт маркеров в chat_msg шага script (если ещё нет)."""
    body = (chat_text or "").rstrip()
    if "<<<VOICEOVER>>>" in body:
        return body
    return f"{body}\n\n{VOICEOVER_OUTPUT_FORMAT}".strip()


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
    """Вычленить закадровый текст: маркеры → иначе эвристика без мета/ИТОГО."""
    body = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not body:
        return ""

    extracted = extract_voiceover_block(body)
    if extracted is not None:
        body = extracted
    else:
        # Незакрытый блок: GPT открыл <<<VOICEOVER>>>, но не вернул <<<END>>> —
        # берём всё после START, мусор перед маркером выкидываем.
        m = re.search(r"<<<\s*VOICEOVER\s*>>>", body, re.IGNORECASE)
        if m:
            body = body[m.end() :].strip()
    body = _ITOGO_RE.sub("", body).strip()
    # Если маркеры уже вырезали блок — мета снаружи отброшена; внутри чистим ИТОГО.
    if extracted is not None:
        return body
    if not _has_process_preamble(body):
        return body
    return _cut_process_preamble(body)
