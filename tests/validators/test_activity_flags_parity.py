"""Паритет activity_flags и фолбэка frame_tombstones на одной таблице."""

from __future__ import annotations

import importlib
import importlib.util
import sys

import pytest

from app.orchestrator.validators.activity_flags import (
    ACTIVE_MARK,
    HIDE_MARKS,
    frame_is_active,
    shot_is_active,
)

# NaN: сравнение с собой ложно, как у пустой ячейки pandas. stdlib, без numpy.
_NAN = float("nan")

# Положительные: пометка «удалён»/«карантин» стоит.
_POSITIVE = (
    True,
    "true",
    "True",
    "да",
    "Да",
    1,
    "1",
    "1.0",
    "yes",
    "on",
    "истина",
)
# Остальные значения таблицы, включая «-». Для удалён/карантин шот живой.
_REST = (
    "нет",
    0,
    "0",
    "false",
    False,
    "ложь",
    "0.0",
    " 0,0 ",
    _NAN,
    None,
    "-",
)
# «активен»: ложь, включая числовую строку «0.0». NaN и None — флага нет.
_ACTIVE_OFF = ("нет", 0, "0", "false", False, "ложь", "0.0", " 0,0 ")

_TOMBSTONE_MOD = "app.services.frame_tombstones"
_FLAGS_MOD = "app.orchestrator.validators.activity_flags"
_SKIP = (
    "в этом дереве нет app.services.frame_tombstones — "
    "сверку фолбэка сделает R0c-B, когда модуль появится"
)

# (функция шота, функция кадра, откуда взяли)
_Fallback = tuple[object, object, str]
_CACHED: dict[str, _Fallback | None] = {}


def _rows() -> list[tuple[str | None, object, bool]]:
    """Единая таблица: маркер, значение, ожидаемый «живой»."""

    rows: list[tuple[str | None, object, bool]] = []
    for mark in HIDE_MARKS:
        for value in _POSITIVE:
            rows.append((mark, value, False))
        for value in _REST:
            rows.append((mark, value, True))
    for value in _ACTIVE_OFF:
        rows.append((ACTIVE_MARK, value, False))
    for value in (*_POSITIVE, "1.0", _NAN, None, "-"):
        rows.append((ACTIVE_MARK, value, True))
    rows.append((None, None, True))
    return rows


def _row_id(row: tuple[str | None, object, bool]) -> str:
    mark, value, expected = row
    state = "живой" if expected else "скрыт"
    if mark is None:
        return f"нет-ключа-{state}"
    return f"{mark}={value!r}-{state}"


def _payload(mark: str | None, value: object) -> dict:
    if mark is None:
        return {}
    return {mark: value}


def _tombstone_in_tree() -> bool:
    spec = importlib.util.find_spec(_TOMBSTONE_MOD)
    return bool(spec and spec.origin and spec.origin != "namespace")


def _fallback(monkeypatch: pytest.MonkeyPatch) -> _Fallback | None:
    """Сначала именные фолбэки. Если имён нет — shot/frame после ImportError."""

    if "pair" in _CACHED:
        return _CACHED["pair"]
    if not _tombstone_in_tree():
        _CACHED["pair"] = None
        return None

    module = importlib.import_module(_TOMBSTONE_MOD)
    named_shot = getattr(module, "_fallback_shot_is_active", None)
    named_frame = getattr(module, "_fallback_frame_is_active", None)
    if callable(named_shot) and callable(named_frame):
        pair: _Fallback = (
            named_shot,
            named_frame,
            "_fallback_shot_is_active/_fallback_frame_is_active",
        )
        _CACHED["pair"] = pair
        return pair

    saved = sys.modules.get(_FLAGS_MOD)
    monkeypatch.setitem(sys.modules, _FLAGS_MOD, None)
    try:
        reloaded = importlib.reload(module)
        pair = (
            reloaded.shot_is_active,
            reloaded.frame_is_active,
            "ImportError→shot_is_active/frame_is_active",
        )
    finally:
        if saved is None:
            monkeypatch.delitem(sys.modules, _FLAGS_MOD, raising=False)
        else:
            monkeypatch.setitem(sys.modules, _FLAGS_MOD, saved)
        importlib.reload(module)
    _CACHED["pair"] = pair
    return pair


@pytest.mark.parametrize("row", _rows(), ids=_row_id)
def test_activity_flags_match_tombstone_fallback(
    row: tuple[str | None, object, bool],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ожидание, activity_flags и фолбэк frame_tombstones совпадают построчно."""

    mark, value, expected = row
    payload = _payload(mark, value)
    assert shot_is_active(payload) is expected
    assert frame_is_active(payload) is expected

    pair = _fallback(monkeypatch)
    if pair is None:
        pytest.skip(_SKIP)
    shot_fb, frame_fb, source = pair
    got_shot = shot_fb(payload)
    got_frame = frame_fb(payload)
    assert got_shot is expected and got_frame is expected, (
        f"{mark!r}={value!r} via {source}: "
        f"fallback shot={got_shot} frame={got_frame}, ожидался {expected}"
    )
