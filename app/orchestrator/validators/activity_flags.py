"""Флаги активности шота и кадра. Только stdlib: без импортов из проекта."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

ACTIVE_MARK = "активен"
DELETED_MARK = "удалён"
QUARANTINE_MARK = "карантин"
# Маркеры, из-за которых шот или кадр не идёт в платную генерацию.
HIDE_MARKS = (DELETED_MARK, QUARANTINE_MARK)

_FALSE_TOKENS = frozenset({"0", "false", "нет", "no", "off", "ложь"})
_DELETED_FALSE_TOKENS = _FALSE_TOKENS | {"0.0"}
_HIDE_TRUE_TOKENS = frozenset({"1", "true", "да", "yes", "on", "истина"})
_WARNED_VALUES: set[str] = set()


def _is_nan(value: object) -> bool:
    """NaN pandas/numpy: сравнение с собой ложно. Не-числа сюда не попадают."""

    if isinstance(value, bool) or isinstance(value, (str, bytes, dict, list, tuple)):
        return False
    try:
        return bool(value != value)
    except Exception:
        return False


def _warn_once(mark: str, value: str) -> None:
    """Одно и то же значение пишем в лог один раз на процесс, не на каждый вызов."""

    token = value.strip().casefold()
    if not token or token in _WARNED_VALUES:
        return
    _WARNED_VALUES.add(token)
    logger.warning(
        "неизвестное значение «%s» %r — считаем, что флага нет",
        mark,
        value,
    )


def flag_is_false(value: object) -> bool:
    """False, 0, ``0.0`` и строки ``0`` / ``false`` / ``нет`` / ``no`` / ``off`` / ``ложь``.

    Регистр и крайние пробелы у строк не важны. NaN и None — не ложь, флага нет.
    """

    if value is None or _is_nan(value):
        return False
    if value is False or value == 0:
        return True
    if isinstance(value, str):
        return value.strip().casefold() in _FALSE_TOKENS
    return False


def flag_is_true(value: object, *, mark: str = DELETED_MARK) -> bool:
    """«удалён» и «карантин».

    None, NaN и пустая строка — флага нет. К ложным строкам добавлены ``0.0``
    и ``ложь``. ``да`` / ``true`` / ``1`` / ``yes`` / ``on`` / ``истина``
    скрывают шот. Неизвестная строка не удаляет: шот живой, в лог один WARN.
    """

    if value is None or _is_nan(value):
        return False
    if isinstance(value, str) and not value.strip():
        return False
    if isinstance(value, str):
        token = value.strip().casefold()
        if token in _DELETED_FALSE_TOKENS:
            return False
        if token in _HIDE_TRUE_TOKENS:
            return True
        _warn_once(mark, value)
        return False
    if flag_is_false(value):
        return False
    return bool(value)


def _record_is_active(record: object) -> bool:
    if not isinstance(record, dict):
        return False
    for mark in HIDE_MARKS:
        if flag_is_true(record.get(mark), mark=mark):
            return False
    if ACTIVE_MARK not in record:
        return True
    value = record[ACTIVE_MARK]
    if value is None or _is_nan(value) or (isinstance(value, str) and not value.strip()):
        return True
    if flag_is_false(value):
        return False
    if isinstance(value, str):
        _warn_once(ACTIVE_MARK, value)
    return True


def shot_is_active(shot: object) -> bool:
    """Живой шот. Нет ключа ``активен``, None и NaN — активен. Не-dict — нет."""

    return _record_is_active(shot)


def frame_is_active(attrs: object) -> bool:
    """Та же матрица, что у шота, включая карантин. ``attrs`` — словарь кадра."""

    return _record_is_active(attrs)
