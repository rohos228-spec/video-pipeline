"""Эвристический выбор модели для AI-агента в зависимости от запроса.

Правила (от дешёвого к дорогому):
- Простой запрос (приветствие, короткий вопрос, всё ≤ 200 символов и
  без code-keywords) → `gpt-4o-mini` (~$0.15/1M tokens in).
- Запрос на анализ архитектуры / длинный (> 500 знаков) / стратегия /
  планирование → `gpt-4o` (~$2.50/1M tokens in).
- Запрос про код / рефакторинг / отладку / архитектурный refactor /
  поиск багов → `claude-opus-4.1` (~$15/1M tokens in, но лучший на код).

Escape-hatch (override эвристики явным префиксом):
- "!pro <запрос>" → принудительно `pro_model` (gpt-4o).
- "!claude <запрос>" → принудительно `code_model` (claude-opus-4.1).
- "!mini <запрос>" → принудительно `default_model` (gpt-4o-mini).

Используется и в команде `/ai`, и в autoreply (`msg_autoreply`).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.ai_agent.config import AIAgentConfig

# Ключевые слова → намекают что нужна продвинутая модель для кода
_CODE_KEYWORDS = (
    # русские
    "рефактор",
    "архитектур",
    "найди баг",
    "почему падает",
    "почему не работает",
    "перепиши",
    "выдели в отдельн",
    "разбей на",
    "оптимизируй",
    "сломалось",
    "исправь",
    "почини",
    "не запускается",
    "ошибка типа",
    "почему не отвечает",
    "зациклил",
    "висит",
    "вылетает",
    "падает",
    "крашит",
    "thrown",
    # английские
    "refactor",
    "architect",
    "fix bug",
    "debug",
    "improve",
    "redesign",
    "code review",
    "type error",
    "stack trace",
    "traceback",
)

# Ключевые слова → намекают что нужна pro-модель (большой контекст / анализ)
_PRO_KEYWORDS = (
    # русские
    "проанализируй весь",
    "сделай план",
    "стратегия",
    "почему именно так",
    "сравни подходы",
    "что лучше",
    "общая картина",
    "целиком",
    # английские
    "design",
    "plan ",
    "strategy",
    "compare",
    "analyze",
    "deep dive",
    "overall",
)

# Жёсткий лимит длины запроса для дефолтной gpt-4o-mini.
# Выше → gpt-4o (больше context window и качество).
_LONG_QUERY_BYTES_THRESHOLD = 500


@dataclass(frozen=True)
class ModelChoice:
    """Результат выбора: какую модель использовать + очищенный запрос."""

    model: str
    cleaned_query: str  # запрос без !pro/!claude/!mini префикса
    reason: str  # для логирования / debug


def strip_override_prefix(query: str) -> tuple[str | None, str]:
    """Если запрос начинается с !pro/!claude/!mini — вернуть (override, остаток).

    >>> strip_override_prefix("!pro analyze project")
    ('pro', 'analyze project')
    >>> strip_override_prefix("normal query")
    (None, 'normal query')
    """
    q = query.lstrip()
    for prefix, key in (("!pro ", "pro"), ("!claude ", "claude"), ("!mini ", "mini")):
        if q.lower().startswith(prefix):
            return key, q[len(prefix):].lstrip()
    return None, query


def pick_model(query: str, cfg: AIAgentConfig) -> ModelChoice:
    """Выбрать модель для запроса.

    Логика:
        1. !pro/!claude/!mini override (явный выбор пользователя).
        2. Эвристика по ключевым словам и длине.
        3. Дефолт = cfg.default_model.
    """
    override, cleaned = strip_override_prefix(query)
    if override == "pro":
        return ModelChoice(
            model=cfg.pro_model,
            cleaned_query=cleaned,
            reason="explicit !pro override",
        )
    if override == "claude":
        return ModelChoice(
            model=cfg.code_model,
            cleaned_query=cleaned,
            reason="explicit !claude override",
        )
    if override == "mini":
        return ModelChoice(
            model=cfg.default_model,
            cleaned_query=cleaned,
            reason="explicit !mini override",
        )

    # Эвристика: проверка ключевых слов (case-insensitive).
    q_lower = cleaned.lower()

    matched_code = [kw for kw in _CODE_KEYWORDS if kw in q_lower]
    if matched_code:
        return ModelChoice(
            model=cfg.code_model,
            cleaned_query=cleaned,
            reason=f"code keyword: {matched_code[0]!r}",
        )

    matched_pro = [kw for kw in _PRO_KEYWORDS if kw in q_lower]
    if matched_pro:
        return ModelChoice(
            model=cfg.pro_model,
            cleaned_query=cleaned,
            reason=f"pro keyword: {matched_pro[0]!r}",
        )

    # Длинные запросы → pro
    if len(cleaned.encode("utf-8")) > _LONG_QUERY_BYTES_THRESHOLD:
        return ModelChoice(
            model=cfg.pro_model,
            cleaned_query=cleaned,
            reason=f"long query (>{_LONG_QUERY_BYTES_THRESHOLD} bytes)",
        )

    # Дефолт — самая дешёвая
    return ModelChoice(
        model=cfg.default_model,
        cleaned_query=cleaned,
        reason="default (short, no keywords)",
    )


__all__ = [
    "ModelChoice",
    "pick_model",
    "strip_override_prefix",
]
