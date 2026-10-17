"""Детерминированные правила tier/mode до LLM.

Strong, если раньше сработало хоть одно:
ход planner/critic, мутация или риск ≥ medium, длина > 800,
ключевые слова план/ревью/промпт/отчёт (и англ. plan/review/prompt/report).

Fast: стоп-слово (если strong не сработал), подтверждение, read-only,
классификация, суммаризация.

Иначе tier спорный: его решает fast-классификатор, порог уверенности 0.8.
Ниже порога или при неразборчивом ответе — strong.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

_STRONG_RE = re.compile(
    r"(?iu)(?<![\w])(?:"
    r"план\w*|ревью|промпт\w*|отч[её]т\w*|"
    r"plans?|reviews?|prompts?|reports?"
    r")(?![\w])"
)
_FAST_RE = re.compile(
    r"(?iu)(?:"
    r"суммир\w*|перескаж\w*|кратк\w*|summar\w*|summary|"
    r"классифиц\w*|classify|"
    r"только\s+посмотр\w*|ничего\s+не\s+меняй|read-?only|"
    r"какой\s+статус|покажи\s+статус|"
    r"сколько\b|how\s+many\b"
    r")"
)
_CONFIRM_RE = re.compile(
    r"(?iu)^(?:"
    r"да|окей|okay|ок|ok|yes|yep|угу|ага|"
    r"подтверждаю|подтвердить|подтверди|"
    r"confirm|confirmed|хорошо|ладно|agreed|accept"
    r")(?:[.!,\s].*)?$"
)
_INTERRUPT_RE = re.compile(
    r"(?iu)(?<![\w])(?:"
    r"стой|стоп|останови(?:сь|те)?|отмена|отмени(?:те)?|"
    r"хватит|прерви(?:те)?|cancel|stop|abort"
    r")(?![\w])"
)
_STEER_RE = re.compile(
    r"(?iu)(?<![\w])(?:измени|поправь|вместо|скорректируй|steer)(?![\w])|не\s+так"
)
_QUESTION_RE = re.compile(r"\?\s*$")
_TASK_RE = re.compile(
    r"(?iu)(?<![\w])(?:"
    r"нод\w*|кадр\w*|сцен\w*|промпт\w*|план\w*|проект\w*|"
    r"задач\w*|чекпоинт\w*|заказр\w*|run_node|node"
    r")(?![\w])"
)
_RISK_RANK = {"none": 0, "low": 1, "medium": 2, "high": 3}
_MODES = {"interrupt", "steer", "append", "task"}

CLASSIFIER_SYSTEM = (
    "Ты классификатор внутреннего хода оркестратора. "
    "Ответь одним JSON-объектом без пояснений: "
    '{"tier":"fast"|"strong","mode":"interrupt"|"steer"|"append"|"task","confidence":0.0}. '
    "tier=strong для планов, ревью, промптов, отчётов и мутаций; "
    "tier=fast для чтения, подтверждения, суммаризации, классификации и короткого ответа. "
    "confidence — уверенность именно в tier."
)


@dataclass(frozen=True)
class RuleHit:
    tier: str | None
    mode: str
    mode_locked: bool
    reason: str


@dataclass(frozen=True)
class ParsedClassifier:
    tier: str
    mode: str
    confidence: float


def classifier_messages(message: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": CLASSIFIER_SYSTEM},
        {"role": "user", "content": message},
    ]


def _mode(message: str) -> tuple[str, bool, str]:
    if _INTERRUPT_RE.search(message):
        return "interrupt", True, "stop-word"
    if _STEER_RE.search(message):
        return "steer", True, "steer"
    if _QUESTION_RE.search(message) and not _TASK_RE.search(message):
        return "append", True, "question-without-task"
    return "task", False, "default-task"


def classify_rules(
    message: str,
    *,
    agent_role: str | None = None,
    risk: str | None = None,
    mutating: bool | None = None,
) -> RuleHit:
    text = message or ""
    mode, mode_locked, mode_reason = _mode(text)
    role = (agent_role or "").strip().lower()
    rank = _RISK_RANK.get((risk or "").strip().lower(), 0)
    if role in {"planner", "critic"}:
        return RuleHit("strong", mode, mode_locked, f"role:{role}")
    if mutating or rank >= _RISK_RANK["medium"]:
        return RuleHit("strong", mode, mode_locked, "mutating-or-risk")
    if len(text.strip()) > 800:
        return RuleHit("strong", mode, mode_locked, "length")
    if _STRONG_RE.search(text):
        return RuleHit("strong", mode, mode_locked, "keyword")
    if mode == "interrupt":
        return RuleHit("fast", mode, mode_locked, mode_reason)
    if _CONFIRM_RE.match(text.strip()):
        return RuleHit("fast", mode, mode_locked, "confirmation")
    if _FAST_RE.search(text):
        return RuleHit("fast", mode, mode_locked, "fast-keyword")
    return RuleHit(None, mode, mode_locked, "disputed")


def parse_classifier_output(text: str) -> ParsedClassifier:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw.removeprefix("json").strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        return ParsedClassifier("strong", "task", 0.0)
    try:
        payload = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return ParsedClassifier("strong", "task", 0.0)
    tier = str(payload.get("tier") or "").strip().lower()
    mode = str(payload.get("mode") or "task").strip().lower()
    if mode not in _MODES:
        mode = "task"
    try:
        confidence = float(payload.get("confidence"))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))
    if tier not in {"fast", "strong"}:
        return ParsedClassifier("strong", mode, 0.0)
    return ParsedClassifier(tier, mode, confidence)
