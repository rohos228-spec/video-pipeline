"""Code-side validators для auto_review (anti-bullshit).

Идея: код проверяет факты, которые ChatGPT может «соврать» —
длину, наличие конкретных слов, цитаты-из-текста.

Используется парно с GPT-ревьюером: GPT даёт structured scores с
evidence_quote, код проверяет что цитаты действительно substring
артефакта и что числовые критерии в норме. Решение об approve/regen
делает код, а не GPT.
"""

from __future__ import annotations

import re
import unicodedata

# Минимальная длина evidence_quote, чтобы считать её содержательной.
MIN_QUOTE_LEN = 8


def _normalize_for_match(text: str) -> str:
    """NFC + сжать пробелы (включая неразрывные) + lower-case.

    Нужно для нечувствительного к мелочам substring-сравнения
    цитат GPT с оригиналом. ChatGPT часто:
      - меняет пробел на NBSP / тонкий пробел,
      - меняет «-» на «—»,
      - капитализирует слова в цитате (или наоборот).
    """
    if not text:
        return ""
    t = unicodedata.normalize("NFC", text)
    # Все виды пробелов → обычный пробел.
    t = re.sub(r"[\s\u00A0\u2002-\u200B\u2028\u2029]+", " ", t)
    # Все виды тире → обычный «-».
    t = re.sub(r"[\u2010-\u2015\u2212]", "-", t)
    return t.strip().lower()


def quote_in_text(quote: str, text: str) -> bool:
    """Проверка, что цитата буквально есть в тексте (с нормализацией).

    Слишком короткие цитаты (< MIN_QUOTE_LEN после нормализации) считаем
    «не цитата» и пропускаем — GPT мог дать односложное observation.
    """
    nq = _normalize_for_match(quote)
    if len(nq) < MIN_QUOTE_LEN:
        return True  # не считаем за провал — слишком короткое
    nt = _normalize_for_match(text)
    return nq in nt


def validate_evidence_quotes(artifact: str, criteria: dict) -> list[str]:
    """Из словаря criteria достаёт все evidence_quote и проверяет.

    Возвращает список ключей, для которых цитата НЕ найдена в артефакте.
    """
    bad = []
    for key, val in (criteria or {}).items():
        if not isinstance(val, dict):
            continue
        q = val.get("evidence_quote") or val.get("observation")
        if not isinstance(q, str) or not q.strip():
            continue
        # Для observation (картинки/видео) substring-чек не нужен.
        if "observation" in val and "evidence_quote" not in val:
            continue
        if not quote_in_text(q, artifact):
            bad.append(key)
    return bad


# ============================================================
# План
# ============================================================

# Регэксп для интервалов из 01_plan/default.md: «0–3 сек», «3-10 сек», и т.д.
# Допускаем дефис, тире, em-dash.
_TIME_INTERVAL_RE = re.compile(
    r"(\d{1,3})\s*[-\u2010-\u2015\u2212]\s*(\d{1,3})\s*сек",
    flags=re.IGNORECASE,
)

# 30-секундный формат вертикального ролика.
# Если будешь делать 60-сек / 12-мин — добавь соответствующий check_plan/вариант.md
# и prompt_overrides["plan"] = "вариант", auto_review подхватит через ImageOverrides.
EXPECTED_PLAN_INTERVALS: list[tuple[int, int]] = [
    (0, 3),
    (3, 7),
    (7, 15),
    (15, 22),
    (22, 27),
    (27, 30),
]


def validate_plan_numeric(
    plan_text: str,
    *,
    product_name: str | None = None,
) -> dict:
    """Возвращает технические факты о плане.

    Возвращаемые поля:
      - intervals_found: список (start, end) найденных интервалов
      - missing_intervals: список ожидаемых, которых нет
      - has_all_intervals: bool
      - char_count: int
      - product_required: bool — был ли задан постоянный продукт
      - product_mentioned: bool — упоминается ли продукт в плане (если задан)

    `product_name` — название постоянного продукта массового. Если задан
    и в плане НЕ упомянут хотя бы 1 раз (по quote_in_text-нормализации) —
    `product_mentioned=False`, что в `derive_final_decision` приведёт к
    regen.
    """
    if not plan_text:
        return {
            "intervals_found": [],
            "missing_intervals": list(EXPECTED_PLAN_INTERVALS),
            "has_all_intervals": False,
            "char_count": 0,
            "product_required": bool(product_name and product_name.strip()),
            "product_mentioned": False,
        }
    found: set[tuple[int, int]] = set()
    for m in _TIME_INTERVAL_RE.finditer(plan_text):
        try:
            a, b = int(m.group(1)), int(m.group(2))
            found.add((a, b))
        except (TypeError, ValueError):
            continue
    missing = [iv for iv in EXPECTED_PLAN_INTERVALS if iv not in found]
    product_required = bool(product_name and product_name.strip())
    product_mentioned = (
        quote_in_text(product_name.strip(), plan_text)
        if product_required else False
    )
    return {
        "intervals_found": sorted(found),
        "missing_intervals": missing,
        "has_all_intervals": not missing,
        "char_count": len(plan_text),
        "product_required": product_required,
        "product_mentioned": product_mentioned,
    }


# ============================================================
# Сценарий (закадровый текст)
# ============================================================

# 30-секундный формат: примерно 400-500 знаков дикторского чтения.
# Оставляем запас ±50 чтобы не рубить хороший текст из-за пары символов.
SCRIPT_MIN_CHARS = 350
SCRIPT_MAX_CHARS = 600

# Заезженные ИИ-шаблоны (из prompts/02_script/default.md, секция «нельзя»).
BANNED_PHRASES = [
    "всё изменилось в один момент",
    "никто не мог представить",
    "казалось, что",
    "но реальность оказалась иной",
    "именно тогда",
    "это стало поворотной точкой",
    "история на этом не закончилась",
    "всё было не так просто",
]

# Конструкции противопоставлений (учитываем только когда они в избытке).
ADVERSATIVE_TOKENS = ["однако,", "не просто,", "казалось,", "сначала,"]


def validate_script_numeric(
    script_text: str,
    *,
    product_name: str | None = None,
) -> dict:
    """Возвращает технические факты о сценарии.

    `product_name` — название постоянного продукта массового. Если задан,
    добавляет поля `product_required` / `product_mentioned`.
    """
    if not script_text:
        return {
            "char_count": 0,
            "char_count_in_range": False,
            "banned_phrases_found": [],
            "repeated_sentence_starts": [],
            "product_required": bool(product_name and product_name.strip()),
            "product_mentioned": False,
        }
    char_count = len(script_text)
    in_range = SCRIPT_MIN_CHARS <= char_count <= SCRIPT_MAX_CHARS

    low = script_text.lower()
    banned_found = [p for p in BANNED_PHRASES if p in low]

    # Поиск одинаковых начал у соседних предложений.
    # Делим по точке/!?/перевод строки, берём первые 2 слова.
    sentences = re.split(r"(?<=[.!?])\s+|\n+", script_text)
    starts = []
    for s in sentences:
        s = s.strip()
        if not s:
            continue
        words = s.split()
        if len(words) >= 2:
            starts.append(" ".join(words[:2]).lower())
    repeated = []
    for i in range(1, len(starts)):
        if starts[i] == starts[i - 1] and len(starts[i]) > 3:
            repeated.append(starts[i])

    product_required = bool(product_name and product_name.strip())
    product_mentioned = (
        quote_in_text(product_name.strip(), script_text)
        if product_required else False
    )

    return {
        "char_count": char_count,
        "char_count_in_range": in_range,
        "char_min": SCRIPT_MIN_CHARS,
        "char_max": SCRIPT_MAX_CHARS,
        "banned_phrases_found": banned_found,
        "repeated_sentence_starts": repeated,
        "product_required": product_required,
        "product_mentioned": product_mentioned,
    }


# ============================================================
# Финальное решение
# ============================================================


def derive_final_decision(
    gpt_decision: str,
    confidence: float,
    criteria: dict | None,
    fabricated_evidence: list[str],
    numeric_failures: list[str],
) -> tuple[str, list[str]]:
    """Применяем правила: GPT даёт скоры, код выносит решение.

    Возвращает (decision, reasons), где decision ∈ {approved, regen, rejected}.
    """
    reasons: list[str] = []

    # 1) Жёсткий красный флаг: код нашёл числовые провалы.
    if numeric_failures:
        reasons.extend(f"NUMERIC: {f}" for f in numeric_failures)
        return "regen", reasons

    # 2) GPT соврал в цитатах.
    if fabricated_evidence:
        reasons.append(
            "GPT_FABRICATED_EVIDENCE: "
            + ", ".join(fabricated_evidence)
        )
        return "regen", reasons

    # 3) Уверенность.
    if confidence < 0.7:
        reasons.append(f"LOW_CONFIDENCE: {confidence:.2f}")
        return "regen", reasons

    # 4) Любой fail-критерий.
    failed = []
    for k, v in (criteria or {}).items():
        if isinstance(v, dict) and v.get("verdict") == "fail":
            failed.append(k)
    if failed:
        reasons.append("FAILED_CRITERIA: " + ", ".join(failed))
        return "regen", reasons

    # 5) GPT хочет rejected — соглашаемся.
    if gpt_decision == "rejected":
        reasons.append("GPT_REJECTED")
        return "rejected", reasons

    # 6) GPT хочет regen — соглашаемся.
    if gpt_decision == "regen":
        reasons.append("GPT_REGEN")
        return "regen", reasons

    return "approved", reasons
