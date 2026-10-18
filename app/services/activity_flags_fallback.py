"""Флаги активности шота и кадра. Только stdlib: без импортов из проекта."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

ACTIVE_MARK = "активен"
DELETED_MARK = "удалён"
QUARANTINE_MARK = "карантин"

_FALSE_TOKENS = frozenset({"0", "false", "нет", "no", "off"})


def flag_is_false(value: object) -> bool:
    """False, 0, ``0.0`` и строки ``0`` / ``false`` / ``нет`` / ``no`` / ``off``.

    Регистр и крайние пробелы у строк не важны.
    """

    if value is False or value == 0:
        return True
    if isinstance(value, str):
        return value.strip().casefold() in _FALSE_TOKENS
    return False


def flag_is_true(value: object) -> bool:
    """«удалён» и «карантин»: None, пустая строка и ложные флаги — не удалено."""

    if value is None:
        return False
    if isinstance(value, str) and not value.strip():
        return False
    if flag_is_false(value):
        return False
    return bool(value)


def _record_is_active(record: object) -> bool:
    if not isinstance(record, dict):
        return False
    if flag_is_true(record.get(DELETED_MARK)) or flag_is_true(record.get(QUARANTINE_MARK)):
        return False
    if ACTIVE_MARK not in record:
        return True
    value = record[ACTIVE_MARK]
    if value is None or (isinstance(value, str) and not value.strip()):
        return True
    if flag_is_false(value):
        return False
    if isinstance(value, str):
        logger.warning(
            "неизвестное значение «активен» %r — считаем активным",
            value,
        )
    return True


def shot_is_active(shot: object) -> bool:
    """Живой шот. Нет ключа ``активен`` и None — активен. Не-dict — нет."""

    return _record_is_active(shot)


def frame_is_active(attrs: object) -> bool:
    """Та же матрица, что у шота, включая карантин. ``attrs`` — словарь кадра."""

    return _record_is_active(attrs)
