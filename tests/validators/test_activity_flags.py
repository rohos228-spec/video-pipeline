"""Матрица активности шота и кадра: xlsx-флаги, удалён, карантин."""

from __future__ import annotations

import logging

import numpy as np
import pytest

from app.orchestrator.validators import activity_flags
from app.orchestrator.validators.activity_flags import (
    ACTIVE_MARK,
    DELETED_MARK,
    HIDE_MARKS,
    QUARANTINE_MARK,
    frame_is_active,
    shot_is_active,
)

# NaN того типа, которым pandas заполняет пустую ячейку.
_NAN = np.float64("nan")

# (kind, payload, живой, WARN на неизвестную строку «активен»)
_CASES: list[tuple[str, object, bool, bool]] = [
    ("shot", None, False, False),
    ("shot", "не словарь", False, False),
    ("shot", {}, True, False),
    ("shot", {ACTIVE_MARK: None}, True, False),
    ("shot", {ACTIVE_MARK: ""}, True, False),
    ("shot", {ACTIVE_MARK: "   "}, True, False),
    ("shot", {ACTIVE_MARK: False}, False, False),
    ("shot", {ACTIVE_MARK: 0}, False, False),
    ("shot", {ACTIVE_MARK: 0.0}, False, False),
    ("shot", {ACTIVE_MARK: "0"}, False, False),
    ("shot", {ACTIVE_MARK: "false"}, False, False),
    ("shot", {ACTIVE_MARK: "FALSE"}, False, False),
    ("shot", {ACTIVE_MARK: " нет "}, False, False),
    ("shot", {ACTIVE_MARK: "no"}, False, False),
    ("shot", {ACTIVE_MARK: " OFF "}, False, False),
    ("shot", {ACTIVE_MARK: "ложь"}, False, False),
    ("shot", {ACTIVE_MARK: _NAN}, True, False),
    ("shot", {ACTIVE_MARK: True}, True, False),
    ("shot", {ACTIVE_MARK: "да?"}, True, True),
    ("shot", {DELETED_MARK: True}, False, False),
    ("shot", {DELETED_MARK: "да"}, False, False),
    ("shot", {DELETED_MARK: "-"}, True, True),
    ("shot", {DELETED_MARK: 1}, False, False),
    ("shot", {DELETED_MARK: False}, True, False),
    ("shot", {DELETED_MARK: 0}, True, False),
    ("shot", {DELETED_MARK: 0.0}, True, False),
    ("shot", {DELETED_MARK: ""}, True, False),
    ("shot", {DELETED_MARK: None}, True, False),
    ("shot", {DELETED_MARK: "false"}, True, False),
    ("shot", {DELETED_MARK: " нет "}, True, False),
    ("shot", {DELETED_MARK: "no"}, True, False),
    ("shot", {DELETED_MARK: "off"}, True, False),
    ("shot", {DELETED_MARK: "0.0"}, True, False),
    ("shot", {DELETED_MARK: "ложь"}, True, False),
    ("shot", {DELETED_MARK: _NAN}, True, False),
    ("shot", {QUARANTINE_MARK: True}, False, False),
    ("shot", {QUARANTINE_MARK: "да"}, False, False),
    ("shot", {QUARANTINE_MARK: _NAN}, True, False),
    ("shot", {QUARANTINE_MARK: 1}, False, False),
    ("shot", {QUARANTINE_MARK: False}, True, False),
    ("shot", {QUARANTINE_MARK: 0}, True, False),
    ("shot", {QUARANTINE_MARK: ""}, True, False),
    ("shot", {QUARANTINE_MARK: "off"}, True, False),
    ("shot", {DELETED_MARK: True, ACTIVE_MARK: True}, False, False),
    ("shot", {QUARANTINE_MARK: True, ACTIVE_MARK: True}, False, False),
    ("frame", None, False, False),
    ("frame", "не словарь", False, False),
    ("frame", {}, True, False),
    ("frame", {ACTIVE_MARK: None}, True, False),
    ("frame", {ACTIVE_MARK: ""}, True, False),
    ("frame", {ACTIVE_MARK: False}, False, False),
    ("frame", {ACTIVE_MARK: 0}, False, False),
    ("frame", {ACTIVE_MARK: 0.0}, False, False),
    ("frame", {ACTIVE_MARK: "0"}, False, False),
    ("frame", {ACTIVE_MARK: "false"}, False, False),
    ("frame", {ACTIVE_MARK: "FALSE"}, False, False),
    ("frame", {ACTIVE_MARK: " нет "}, False, False),
    ("frame", {ACTIVE_MARK: "no"}, False, False),
    ("frame", {ACTIVE_MARK: "off"}, False, False),
    ("frame", {ACTIVE_MARK: "ложь"}, False, False),
    ("frame", {ACTIVE_MARK: _NAN}, True, False),
    ("frame", {ACTIVE_MARK: "да?"}, True, True),
    ("frame", {DELETED_MARK: True}, False, False),
    ("frame", {DELETED_MARK: "да"}, False, False),
    ("frame", {DELETED_MARK: "0.0"}, True, False),
    ("frame", {DELETED_MARK: "ложь"}, True, False),
    ("frame", {DELETED_MARK: _NAN}, True, False),
    ("frame", {QUARANTINE_MARK: _NAN}, True, False),
    ("frame", {DELETED_MARK: "false"}, True, False),
    ("frame", {DELETED_MARK: 0}, True, False),
    ("frame", {DELETED_MARK: ""}, True, False),
    ("frame", {DELETED_MARK: None}, True, False),
    ("frame", {QUARANTINE_MARK: True}, False, False),
    ("frame", {QUARANTINE_MARK: "off"}, True, False),
    ("frame", {QUARANTINE_MARK: 0}, True, False),
    ("frame", {QUARANTINE_MARK: ""}, True, False),
    ("frame", {DELETED_MARK: True, ACTIVE_MARK: True}, False, False),
    ("frame", {QUARANTINE_MARK: 1, ACTIVE_MARK: False}, False, False),
]


_HIDE_TRUE_VALUES = (
    "да",
    "Да",
    "true",
    "True",
    "1",
    1,
    True,
    "yes",
    "on",
    "истина",
    " да ",
)


@pytest.mark.parametrize("kind", ("shot", "frame"))
@pytest.mark.parametrize("mark", HIDE_MARKS)
@pytest.mark.parametrize("value", _HIDE_TRUE_VALUES, ids=lambda value: repr(value))
def test_hide_mark_affirmative_is_inactive(kind: str, mark: str, value: object) -> None:
    """«да»/«true»/«1» в удалён и карантин не отправляют шот в генерацию."""

    payload = {mark: value}
    active = shot_is_active(payload) if kind == "shot" else frame_is_active(payload)
    assert active is False


# Числовая строка равна числу: «0.0» и 0.0, «1.0» и 1, « 0,0 » и 0.
_NUMERIC_STRING_CASES = (
    (ACTIVE_MARK, "0.0", False),
    (ACTIVE_MARK, "1.0", True),
    (ACTIVE_MARK, " 0,0 ", False),
    (ACTIVE_MARK, 0.0, False),
    (DELETED_MARK, "0.0", True),
    (DELETED_MARK, "1.0", False),
    (DELETED_MARK, " 0,0 ", True),
    (QUARANTINE_MARK, "0.0", True),
    (QUARANTINE_MARK, "1.0", False),
    (QUARANTINE_MARK, " 0,0 ", True),
)


@pytest.mark.parametrize("kind", ("shot", "frame"))
@pytest.mark.parametrize(("mark", "value", "expected"), _NUMERIC_STRING_CASES)
def test_numeric_string_matches_number(
    kind: str,
    mark: str,
    value: object,
    expected: bool,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """«0.0» и «1.0» во всех полях ведут себя как числа, без WARN."""

    caplog.set_level(logging.WARNING)
    payload = {mark: value}
    active = shot_is_active(payload) if kind == "shot" else frame_is_active(payload)
    assert active is expected
    assert not caplog.records


# Таблица паритета. WARN только у неизвестной строки «-».
_TABLE_VALUES = (
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
_TABLE_MARKS = (ACTIVE_MARK, DELETED_MARK, QUARANTINE_MARK)


@pytest.mark.parametrize("kind", ("shot", "frame"))
@pytest.mark.parametrize("mark", _TABLE_MARKS)
@pytest.mark.parametrize("value", _TABLE_VALUES, ids=lambda value: repr(value))
def test_warn_only_for_unknown_strings(
    kind: str,
    mark: str,
    value: object,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Известные значения, включая «да» у ``активен``, не пишут WARN."""

    caplog.set_level(logging.WARNING)
    monkeypatch.setattr(activity_flags, "_WARNED_VALUES", set())
    payload = {mark: value}
    if kind == "shot":
        shot_is_active(payload)
    else:
        frame_is_active(payload)
    if isinstance(value, str) and value.strip() == "-":
        assert len(caplog.records) == 1
        return
    assert not caplog.records


def _unknown_tokens(payload: object) -> list[str]:
    if not isinstance(payload, dict):
        return []
    tokens: list[str] = []
    for mark in (ACTIVE_MARK, DELETED_MARK, QUARANTINE_MARK):
        value = payload.get(mark)
        if isinstance(value, str) and value.strip():
            tokens.append(value.strip().casefold())
    return tokens


@pytest.mark.parametrize(
    ("kind", "payload", "expected", "warns"),
    _CASES,
    ids=[f"{kind}-{index}" for index, (kind, *_) in enumerate(_CASES)],
)
def test_shot_activity_matches_frame_tombstones_matrix(
    kind: str,
    payload: object,
    expected: bool,
    warns: bool,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import builtins

    import app.orchestrator.validators.grammar as grammar

    caplog.set_level(logging.WARNING)
    if kind == "shot":
        assert shot_is_active(payload) is expected
    else:
        assert frame_is_active(payload) is expected
    assert grammar.shot_counts_as_active(payload) is expected

    imported: list[str] = []
    real_import = builtins.__import__

    def tracking(name, globals=None, locals=None, fromlist=(), level=0):  # noqa: A002
        if name == "app.services.frame_tombstones":
            imported.append(name)
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", tracking)
    assert grammar.shot_counts_as_active(payload) is expected
    assert imported == [], imported

    tokens = _unknown_tokens(payload)
    warned_values = getattr(activity_flags, "_WARNED_VALUES", ())
    seen = any(token in warned_values for token in tokens)
    warnings = [record for record in caplog.records if record.levelno >= logging.WARNING]
    if warns:
        assert seen or warnings
        assert len(warnings) <= 1
        assert any(token in getattr(activity_flags, "_WARNED_VALUES", ()) for token in tokens)
    else:
        assert not warnings

    tombstones = pytest.importorskip(
        "app.services.frame_tombstones",
        reason="в этом дереве нет frame_tombstones, сверку с activity_flags сделает R0c-B",
    )
    assert tombstones.shot_is_active(payload) is shot_is_active(payload)
