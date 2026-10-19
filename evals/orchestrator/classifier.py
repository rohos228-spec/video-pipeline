"""Детерминированный классификатор контура A (порт WS5, не его код).

Спорные сообщения не угадываются: ``deterministic=false``, дальше ReplayRouter.
"""

from __future__ import annotations

import re

STOP_WORDS = frozenset({"стой", "отмена", "останови", "stop"})
STRONG_WORDS = frozenset({"план", "ревью", "промпт", "промт", "отчёт", "отчет"})
MUTATING_WORDS = frozenset({"запусти", "создай", "измени", "удали", "правь", "перепиши"})
QUESTION_PREFIXES = ("что", "как", "где", "когда", "сколько", "покажи", "статус", "кратко", "какой", "какие")
_TOKEN_RE = re.compile(r"\w+", flags=re.UNICODE)


def deterministic_classify(message: str) -> dict[str, object]:
    text = message.strip()
    lowered = text.lower()
    tokens = set(_TOKEN_RE.findall(lowered))
    if tokens & STOP_WORDS:
        return {"tier": "fast", "mode": "interrupt", "deterministic": True}
    long = len(text) > 800
    strong_word = bool(tokens & STRONG_WORDS)
    mutating = bool(tokens & MUTATING_WORDS)
    if long or strong_word or mutating:
        return {"tier": "strong", "mode": "steer", "deterministic": True}
    if lowered.endswith("?") or lowered.startswith(QUESTION_PREFIXES):
        return {"tier": "fast", "mode": "append", "deterministic": True}
    return {"tier": None, "mode": None, "deterministic": False}


def classifier_messages(message: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": "classifier"},
        {"role": "user", "content": message},
    ]
