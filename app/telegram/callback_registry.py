"""Реестр callback_data префиксов Telegram-бота.

Phase E.4 step 1: единое место, где зарегистрированы все callback-префиксы.
Новые callback'ы **обязательно** добавляются сюда первой строкой, иначе
тест `tests/test_callback_registry.py` фейлит.

Назначение:
1. Защита от коллизий: два разных handler'а не могут зарегистрировать
   одинаковый callback_data.
2. Документация: один взгляд → весь набор кнопок.
3. Гарантия 64-байт лимита: для каждого префикса оценим максимум.
4. Подготовка к Phase E (разбиение bot.py на handlers/) — handler'ы
   будут импортировать константы отсюда, не литералы.

ВНИМАНИЕ: значения констант — **префиксы** до первого `{var}`. Финальный
callback_data собирается через `f"{CB.PROJ_OPEN}:{project_id}"`.

ВСЕ существующие литералы в `app/telegram/*.py` пока остаются строковыми —
миграция на CB.X будет в отдельных PR'ах серии E (по одному файлу за PR).

См. AGENTS.md §10 и .cursor/rules/10-telegram.mdc.
"""

from __future__ import annotations

import re
from enum import Enum

# Лимит Telegram BotAPI: 64 байта на callback_data.
TG_CALLBACK_LIMIT = 64


class CB(str, Enum):
    """Префиксы callback_data всех inline-кнопок бота.

    Шаблон именования: `<scope>:<verb>[:<sub>]`, где scope — это:
        ai, mass, mprm, prm, menu, proj, hero_*, test, wiz, excel_prm,
        pov, reset, step_run.

    Финальный callback_data собирается так:
        f"{CB.PROJ_OPEN}:{project_id}"  → "proj:open:42"
        f"{CB.MASS_TOGGLE}:{batch_id}:{field}" → "mass:tog:7:auto_review"
    """

    # ───── AI-агент (Phase I.3) ────────────────────────────────────
    AI_APPROVE = "ai:approve"  # + ":{tool_call_db_id}"
    AI_REJECT = "ai:reject"
    AI_REGEN = "ai:regen"
    AI_CLARIFY = "ai:clarify"
    AI_CANCEL = "ai:cancel"  # + ":{session_id}"
    AI_STATUS = "ai:status"  # + ":{session_id}"
    AI_NOOP = "ai:noop"  # no callback, просто очищает loading

    # ───── Главное меню ────────────────────────────────────────────
    MENU_ROOT = "menu:root"
    MENU_NEW = "menu:new"
    MENU_LIST = "menu:list"
    MENU_MASS_PAUSE = "menu:mpause"
    MENU_MASS_RESUME = "menu:mresume"

    # ───── Меню проекта ────────────────────────────────────────────
    PROJ_MENU = "proj"  # + ":{pid}:menu" / ":delete_yes" / ":stop_running"
    STEP_RUN = "step_run"  # + ":{pid}:{step_code}"
    RESET_ASK = "reset_ask"  # + ":{pid}:{step_code}"
    RESET_DO = "reset_do"  # + ":{pid}:{step_code}"

    # ───── Hero / Items ────────────────────────────────────────────
    HERO_COUNT = "hero_cnt"  # + ":{pid}:{n}"
    HERO_RUN = "hero_run"  # + ":{pid}"
    HERO_MENU = "hero_menu"  # + ":{pid}:continue|reset_briefs|reset_all"
    HERO_VAR = "hero_var"  # + ":{pid}:{hero_idx}:{var_idx}"

    # ───── Массовая генерация ──────────────────────────────────────
    MASS_NEW = "mass:new"
    MASS_LIST = "mass:list"
    MASS_OPEN = "mass:open"  # + ":{batch_id}"
    MASS_START = "mass:start"  # + ":{batch_id}"
    MASS_PAUSE = "mass:pause"
    MASS_RESUME = "mass:resume"
    MASS_PROGRESS = "mass:progress"
    MASS_NOOP = "mass:noop"
    MASS_DELETE = "mass:delete"
    MASS_DELETE_YES = "mass:delete_yes"
    MASS_DELETE_KEEP = "mass:delete_keep"
    MASS_DL_XLSX = "mass:dl_xlsx"
    MASS_UPLOAD_XLSX = "mass:upload_xlsx"
    MASS_ADD_TEXT = "mass:add_text"
    MASS_TOPICS = "mass:topics"
    MASS_SUB = "mass:sub"  # + ":{batch_id}:{slug}"
    MASS_TOGGLE = "mass:tog"  # + ":{batch_id}:{field}"
    MASS_SET_NUM = "mass:setnum"  # + ":{batch_id}:{field}:{delta}"
    MASS_SETTINGS = "mass:settings"
    MASS_RETRY_PAUSED = "mass:retry_paused"
    # Product (для batch)
    MASS_PROD = "mass:prod"
    MASS_PROD_NAME = "mass:prod_name"
    MASS_PROD_DESC = "mass:prod_desc"
    MASS_PROD_PHOTO = "mass:prod_photo"
    MASS_PROD_CLEAR = "mass:prod_clear"

    # ───── Mass prompts (mprm:*) ───────────────────────────────────
    MPRM = "mprm"  # + ":{batch_id}:{step}:..."
    MPRM_SAVE = "mprm:save"
    MPRM_TXT_SAVE = "mprm:txtsave"

    # ───── Single project prompts (prm:*) ──────────────────────────
    PRM = "prm"
    EXCEL_PRM = "excel_prm"
    POV = "pov"

    # ───── Wizard ──────────────────────────────────────────────────
    WIZ = "wiz"  # + ":{batch_id}:start|reset|set:{k}:{v}|..."

    # ───── Test prompt (визуальные промты) ─────────────────────────
    TEST = "test"  # + ":{slug}:set_visual|set_system|delete|..."
    TEST_LIST = "test:list"
    TEST_NEW = "test:new"
    TEST_NOOP = "test:noop"

    # ───── HITL картинок/видео (app/services/hitl.py) ─────────────
    # Используется в bot.py для HITL-карточек кадров (image/video).
    # Формат callback: 'hitl:{hitl_id}:approve|regen|edit|original|reject'.
    HITL = "hitl"  # + ":{hitl_id}:{action}"

    # ───── Глобальный no-op (для disabled-плашек-кнопок) ──────────
    NOOP = "noop"

    # ───── /debug команды (Phase G) — пока без inline-кнопок ──────
    # (handlers/debug.py — только text commands, callback'ов нет)


# ────────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────────


def all_prefixes() -> list[str]:
    """Список всех зарегистрированных префиксов."""
    return [c.value for c in CB]


def is_registered(callback_data: str) -> bool:
    """Проверка: callback_data использует один из зарегистрированных префиксов?

    Логика: callback_data должен либо точно равняться prefix, либо начинаться
    с `prefix:`. Например, `"ai:approve:42"` — ок для `CB.AI_APPROVE`.
    """
    if not callback_data:
        return False
    for prefix in all_prefixes():
        if callback_data == prefix:
            return True
        if callback_data.startswith(prefix + ":"):
            return True
    return False


def find_prefix(callback_data: str) -> str | None:
    """Найти лучший (самый длинный) префикс для callback_data."""
    if not callback_data:
        return None
    best = ""
    for prefix in all_prefixes():
        if callback_data == prefix or callback_data.startswith(prefix + ":"):
            if len(prefix) > len(best):
                best = prefix
    return best or None


def estimate_max_length(prefix: str, *, var_max_len: int = 20) -> int:
    """Грубая оценка максимальной длины callback_data для префикса.

    Используется в test_callback_registry для гарантии что
    "{prefix}:{var}:{var}:..." не превысит 64 байта при разумных значениях
    переменных.
    """
    # Считаем количество разделителей `:` после prefix — это нижняя оценка
    # количества переменных, которые могут идти после.
    # Реально мы не знаем сколько vars будет — это эвристика.
    return len(prefix.encode("utf-8")) + var_max_len * 3


__all__ = [
    "CB",
    "TG_CALLBACK_LIMIT",
    "all_prefixes",
    "estimate_max_length",
    "find_prefix",
    "is_registered",
]


# ────────────────────────────────────────────────────────────────────────────
# Sanity-check на impore — все префиксы должны быть ASCII, начинаться с
# буквы и содержать только [a-z0-9_:] для предсказуемого matching.
# ────────────────────────────────────────────────────────────────────────────

_VALID_PREFIX_RE = re.compile(r"^[a-z][a-z0-9_]*(:[a-z][a-z0-9_]*)*$")
for _cb in CB:
    if not _VALID_PREFIX_RE.match(_cb.value):
        raise ValueError(
            f"Invalid callback prefix in CB enum: {_cb.name}={_cb.value!r}. "
            f"Must match {_VALID_PREFIX_RE.pattern}"
        )
    if len(_cb.value.encode("utf-8")) >= TG_CALLBACK_LIMIT // 2:
        raise ValueError(
            f"Префикс {_cb.value!r} занимает > {TG_CALLBACK_LIMIT // 2} байт, "
            "не оставляет места под переменные. Сократи."
        )
