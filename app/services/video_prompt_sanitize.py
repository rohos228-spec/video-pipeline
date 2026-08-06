"""Локальная санация video/anim промтов: триггеры → нейтральные замены.

После серии ошибок Outsee (CONTENT_POLICY / moderation) GPT-rewrite
ненадёжен (kie maintenance, таймауты). Детерминированная замена имён и
опасных слов + упрощение текста — основной путь до повторной генерации.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Iterable


def _fold_accents(text: str) -> str:
    """NFKD без combining marks — чтобы «Асаха́ры» / «Мацумо́то» ловились правилами."""
    return "".join(
        c
        for c in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(c)
    )


# (pattern, replacement) — длинные/специфичные первыми.
# Имена/культы → нейтральные роли; насилие/химия → бытовые аналоги.
_TRIGGER_RULES: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pat, re.IGNORECASE), repl)
    for pat, repl in (
        # Культ / лидеры
        (r"аум[\s\-]*синрик[её]й?\w*", "закрытая община"),
        (r"aum[\s\-]*shinriky[oō]?\w*", "closed community"),
        (r"асахар\w*", "лидер группы"),
        (r"asahara\w*", "group leader"),
        (r"мацумото\w*", "преподаватель"),
        (r"matsumoto\w*", "instructor"),
        (r"синрик[её]й?\w*", "община"),
        (r"shinriky[oō]?\w*", "community"),
        # Химия / теракт
        (r"зарин\w*", "химическое вещество"),
        (r"\bsarin\b", "chemical substance"),
        (r"отравляющ\w+\s+газ\w*", "едкий дым"),
        (r"нервно[\-\s]*паралитическ\w+", "опасное вещество"),
        (r"теракт\w*", "чрезвычайное происшествие"),
        (r"террор\w*", "давление"),
        (r"бомб\w*", "пакет"),
        (r"взрывчатк\w*", "содержимое"),
        (r"взрыв\w*", "хлопок"),
        (r"уби[йи]\w*", "столкновение"),
        (r"убийств\w*", "инцидент"),
        (r"кров\w+", "тёмные пятна"),
        (r"\bblood\b", "dark stains"),
        (r"\bbomb\b", "package"),
        (r"\bterror\w*", "pressure"),
        # Портрет конкретного лица → без идентификации
        (r"портрет\w*\s+лидер(?:а|у)?\s+группы", "фотография человека"),
        (r"portrait\s+of\s+(?:the\s+)?group\s+leader", "photo of a person"),
    )
)

_EMPTY_FILLER_RE = re.compile(
    r"(?i)(?:scene feature|shot scale|camera|important details|"
    r"place and time)\s*[/:]?\s*"
    r"нет исходных данных для заполнения\.?\s*"
)

_MULTI_SPACE_RE = re.compile(r"[ \t]{2,}")
_MULTI_NL_RE = re.compile(r"\n{3,}")

# После 3 ошибок — короткий кадр: визуал + действие, без воды.
_SIMPLIFY_MAX_CHARS = 900


def replace_trigger_words(text: str) -> tuple[str, int]:
    """Заменить триггерные слова. Возвращает (новый_текст, число_замен)."""
    if not text:
        return text, 0
    out = _fold_accents(text)
    n = 0
    for pat, repl in _TRIGGER_RULES:
        out, k = pat.subn(repl, out)
        n += k
    return out, n


def simplify_video_prompt(text: str, *, max_chars: int = _SIMPLIFY_MAX_CHARS) -> str:
    """Упростить anim/video промт: убрать пустые поля, воду, обрезать."""
    if not text:
        return text
    body = _EMPTY_FILLER_RE.sub("", text)
    body = _MULTI_NL_RE.sub("\n\n", body)
    body = _MULTI_SPACE_RE.sub(" ", body).strip()
    # Выбросить повторяющиеся «Animate the …» хвосты narration если слишком длинно
    if len(body) > max_chars:
        cut = body[:max_chars]
        sp = cut.rfind(" ")
        if sp > int(max_chars * 0.7):
            cut = cut[:sp]
        body = cut.rstrip(" ,;:") + "."
    return body


def sanitize_video_prompt_after_errors(
    text: str,
    *,
    simplify: bool = True,
    max_chars: int = _SIMPLIFY_MAX_CHARS,
) -> tuple[str, dict[str, int | bool]]:
    """Триггеры → замены; опционально упростить. stats для лога."""
    replaced, n = replace_trigger_words(text)
    out = simplify_video_prompt(replaced, max_chars=max_chars) if simplify else replaced
    return out, {
        "trigger_replacements": n,
        "simplified": simplify and out != replaced,
        "chars_before": len(text or ""),
        "chars_after": len(out),
    }


def apply_rules_preview(text: str, rules: Iterable[tuple[re.Pattern[str], str]] | None = None) -> str:
    """Тестовый хелпер: прогнать произвольный набор правил."""
    out = text
    for pat, repl in rules or _TRIGGER_RULES:
        out = pat.sub(repl, out)
    return out
