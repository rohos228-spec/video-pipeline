"""Русские тексты ошибок слоя действий (коды остаются машинными и не меняются).

``validation_ru`` переводит сообщения ``schema.validate`` ("$.frame_number: required") для человека;
сам валидатор остаётся английским: его тексты идут в проверки и в повтор модели.
"""
from __future__ import annotations

import re

_PATTERNS: tuple[tuple[re.Pattern, str], ...] = tuple((re.compile(p), r) for p, r in (
    (r"^(?P<p>\S+): expected (?P<t>.+)$", r"\g<p>: ожидается тип \g<t>"),
    (r"^(?P<p>\S+): must equal (?P<v>.+)$", r"\g<p>: должно быть равно \g<v>"),
    (r"^(?P<p>\S+): must be one of (?P<v>.+)$", r"\g<p>: допустимые значения \g<v>"),
    (r"^(?P<p>\S+): shorter than (?P<n>\d+)$", r"\g<p>: короче \g<n> символов"),
    (r"^(?P<p>\S+): longer than (?P<n>\d+)$", r"\g<p>: длиннее \g<n> символов"),
    (r"^(?P<p>\S+): does not match pattern$", r"\g<p>: не подходит по формату"),
    (r"^(?P<p>\S+): below (?P<n>\S+)$", r"\g<p>: меньше допустимого (\g<n>)"),
    (r"^(?P<p>\S+): above (?P<n>\S+)$", r"\g<p>: больше допустимого (\g<n>)"),
    (r"^(?P<p>\S+): fewer than (?P<n>\d+) items$", r"\g<p>: меньше \g<n> элементов"),
    (r"^(?P<p>\S+): more than (?P<n>\d+) items$", r"\g<p>: больше \g<n> элементов"),
    (r"^(?P<p>\S+): required$", r"\g<p>: обязательное поле не заполнено"),
    (r"^(?P<p>\S+): unknown field$", r"\g<p>: лишнее поле"),
    (r"^\$: params must be an object$", "параметры должны быть объектом"),
))


def validation_ru(message: str) -> str:
    """Один текст ошибки проверки параметров по-русски; незнакомый возвращается как есть."""
    text = str(message)
    for pattern, repl in _PATTERNS:
        if pattern.match(text):
            return pattern.sub(repl, text)
    return text


def validation_list_ru(messages: list[str]) -> list[str]:
    return [validation_ru(m) for m in messages]



def spend_cap_ru(exc) -> str:
    """SpendCapExceeded по-русски (код остаётся в поле code)."""
    return (f"Локальный потолок расходов исчерпан ({exc.code}): потрачено {exc.spent:.2f} USD, "
            f"оценка {exc.estimate:.2f} USD, потолок {exc.cap:.2f} USD.")


# Журнал слоя действий (action_layer_history.event): код события -> фраза для оператора.
# Коды остаются машинными; этот словарь использует лента проекта (timeline_ru) и история в интерфейсе.
HISTORY_EVENT_RU: dict[str, str] = {
    "plan_created": "План действий создан",
    "plan_started": "План действий запущен",
    "plan_finished": "План действий завершён",
    "planner_call": "Планировщик обратился к модели",
    "planner_plan_linked": "Планировщик связал план с проектом",
    "approval_created": "Запрошено подтверждение плана",
    "approval_consumed": "Подтверждение плана использовано",
    "run_requested": "Оператор запросил выполнение плана",
    "resume_refused": "Продолжение плана отклонено",
    "stop_requested": "Оператор запросил остановку плана",
    "step_started": "Шаг плана начат",
    "step_succeeded": "Шаг плана выполнен",
    "step_failed": "Шаг плана завершился ошибкой",
    "step_unknown": "Итог шага плана не подтверждён",
    "step_skipped": "Шаг плана пропущен",
    "step_cancelled": "Шаг плана отменён",
    "budget_block": "Шаг остановлен лимитом расходов",
    "kill_switch_block": "Шаг остановлен аварийным стопом",
    "job_stop_after_failure": "Задача остановлена после сбоя",
    "job_orphan_released": "Серверная задача после сбоя завершилась, проект свободен",
    "reserve_unresolved": "Резерв расхода не закрыт: итог шага неизвестен",
    "reserve_released": "Незакрытый резерв расхода снят оператором",
    "snapshot_saved": "Снимок состояния перед шагом сохранён",
    "snapshot_restored": "Состояние восстановлено из снимка",
}


STEP_STATUS_RU: dict[str, str] = {
    "pending": "ждёт очереди", "running": "выполняется", "succeeded": "выполнен", "failed": "ошибка",
    "unknown": "итог не подтверждён", "skipped": "пропущен", "cancelled": "отменён",
}


def history_event_ru(code: str | None) -> str:
    """Фраза для события журнала слоя действий; незнакомый код возвращается как есть (не прячется)."""
    text = str(code or "")
    return HISTORY_EVENT_RU.get(text, text)
