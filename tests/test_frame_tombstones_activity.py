"""Один предикат активности.

Если ``activity_flags`` импортируется, ``frame_tombstones`` отдаёт те же
объекты. Иначе локальная матрица. Она расходится с blob ``e8a3284a``
только на правдивых строках «удалён» и «карантин»: список в
``test_fallback_diverges_from_activity_flags_blob``.
"""

from __future__ import annotations

import json
import logging
import sys
from types import SimpleNamespace

import pytest

from app.services.frame_tombstones import (
    ACTIVE_MARK,
    DELETED_MARK,
    QUARANTINE_MARK,
    frame_is_active,
    frame_marked_deleted,
    mark_frame_deleted,
    merge_kadry_keep_missing,
    shot_is_active,
)

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
    ("shot", {ACTIVE_MARK: "0"}, False, False),
    ("shot", {ACTIVE_MARK: "false"}, False, False),
    ("shot", {ACTIVE_MARK: "FALSE"}, False, False),
    ("shot", {ACTIVE_MARK: " нет "}, False, False),
    ("shot", {ACTIVE_MARK: "no"}, False, False),
    ("shot", {ACTIVE_MARK: " OFF "}, False, False),
    ("shot", {ACTIVE_MARK: True}, True, False),
    ("shot", {DELETED_MARK: True}, False, False),
    ("shot", {DELETED_MARK: "да"}, True, False),
    ("shot", {DELETED_MARK: 1}, False, False),
    ("shot", {DELETED_MARK: False}, True, False),
    ("shot", {DELETED_MARK: 0}, True, False),
    ("shot", {DELETED_MARK: ""}, True, False),
    ("shot", {DELETED_MARK: None}, True, False),
    ("shot", {DELETED_MARK: "false"}, True, False),
    ("shot", {DELETED_MARK: " нет "}, True, False),
    ("shot", {DELETED_MARK: "no"}, True, False),
    ("shot", {DELETED_MARK: "off"}, True, False),
    ("shot", {QUARANTINE_MARK: True}, False, False),
    ("shot", {QUARANTINE_MARK: "да"}, True, False),
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
    ("frame", {ACTIVE_MARK: "0"}, False, False),
    ("frame", {ACTIVE_MARK: "false"}, False, False),
    ("frame", {ACTIVE_MARK: "FALSE"}, False, False),
    ("frame", {ACTIVE_MARK: " нет "}, False, False),
    ("frame", {ACTIVE_MARK: "no"}, False, False),
    ("frame", {ACTIVE_MARK: "off"}, False, False),
    ("frame", {DELETED_MARK: True}, False, False),
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


def _as_frame(payload: object) -> object:
    if payload is None:
        return None
    return SimpleNamespace(attrs=payload)


def _project65_and_readers_stay_active() -> None:
    """Нет ключа «активен» — живые. Строка «false» не удаляет, карантин кадра гасит."""
    from tests.test_r0c_b_project65_e2e import FIXTURE, _cell_from_scene, _ns, _shot_id
    from app.services.shots_report import build_shots_report_model

    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    shots = [
        shot
        for scene in raw["scenes"]
        for shot in (scene.get("кадры") or [])
        if isinstance(shot, dict)
    ]
    assert len(shots) == 187
    assert all(ACTIVE_MARK not in shot for shot in shots)
    assert all(shot_is_active(shot) for shot in shots)
    live = [shot for shot in shots if shot_is_active(shot)]
    assert len(live) == 187

    frames = []
    for row in raw["frames_table"]:
        attrs = row.get("attrs") if isinstance(row.get("attrs"), dict) else {}
        assert ACTIVE_MARK not in attrs
        frames.append(SimpleNamespace(attrs=attrs, id=row["id"], number=row["number"]))
    assert len(frames) == 98
    assert all(frame_is_active(frame.attrs or {}) for frame in frames)

    report = build_shots_report_model(
        [_ns(_cell_from_scene(scene)) for scene in raw["scenes"]]
    )
    report_ids = {str(row.get("id") or "") for row in report["shots"]}
    assert {_shot_id(shot) for shot in shots} <= report_ids

    mixed = [
        {},
        {DELETED_MARK: "false"},
        {DELETED_MARK: "нет"},
        {DELETED_MARK: 0},
        {QUARANTINE_MARK: True},
        {ACTIVE_MARK: False},
    ]
    assert [shot for shot in mixed if shot_is_active(shot)] == mixed[:4]
    frame_rows = [_as_frame(dict(item)) for item in mixed]
    assert [frame for frame in frame_rows if frame_is_active(frame.attrs or {})] == frame_rows[:4]
    assert frame_marked_deleted(_as_frame({DELETED_MARK: "false"})) is False
    assert frame_marked_deleted(_as_frame({DELETED_MARK: "нет"})) is False
    assert frame_marked_deleted(_as_frame({DELETED_MARK: 0})) is False
    assert frame_is_active({QUARANTINE_MARK: True}) is False

    tomb = SimpleNamespace(attrs={}, id=1, number=1)
    mark_frame_deleted(tomb, None, "matrix")
    assert tomb.attrs[DELETED_MARK] is True
    assert tomb.attrs[QUARANTINE_MARK] is True
    assert tomb.attrs[ACTIVE_MARK] is False

    merged = merge_kadry_keep_missing([{"id": "k", "закадр": "текст"}], [])
    assert merged[0][ACTIVE_MARK] is False


def _fallback_core_cases() -> None:
    """True, False, нет ключа, ``удалён`` True, ``карантин`` True."""
    assert shot_is_active({}) is True
    assert shot_is_active({ACTIVE_MARK: True}) is True
    assert shot_is_active({ACTIVE_MARK: False}) is False
    assert shot_is_active({DELETED_MARK: True}) is False
    assert shot_is_active({QUARANTINE_MARK: True}) is False
    assert frame_is_active({}) is True
    assert frame_is_active({ACTIVE_MARK: False}) is False
    assert frame_is_active({DELETED_MARK: True}) is False
    assert frame_is_active({QUARANTINE_MARK: True}) is False


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
) -> None:
    caplog.set_level(logging.WARNING)
    if kind == "shot":
        assert shot_is_active(payload) is expected
    else:
        assert frame_is_active(payload) is expected
    warned = any("активен" in record.getMessage() for record in caplog.records)
    assert warned is warns
    if kind == "shot" and payload == {}:
        _project65_and_readers_stay_active()
        _fallback_core_cases()
        assert shot_is_active({}) is True


_FLAGS_BLOB = "e8a3284aa116f4fa4f8c2d9c07bfbd25a5fc62d0"


def _block_activity_flags_import():
    """ImportError на модуль WS1. Возвращает (tomb, restore)."""
    import builtins
    import importlib

    import app.orchestrator.validators as validators_pkg
    import app.services.frame_tombstones as tomb

    original = builtins.__import__
    had_attr = hasattr(validators_pkg, "activity_flags")
    saved_attr = getattr(validators_pkg, "activity_flags", None)
    if had_attr:
        delattr(validators_pkg, "activity_flags")

    def _blocked(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "app.orchestrator.validators.activity_flags" or (
            name == "app.orchestrator.validators" and "activity_flags" in (fromlist or ())
        ):
            raise ImportError(name)
        return original(name, globals, locals, fromlist, level)

    builtins.__import__ = _blocked
    sys.modules.pop("app.orchestrator.validators.activity_flags", None)
    importlib.reload(tomb)

    def _restore() -> None:
        builtins.__import__ = original
        if had_attr:
            setattr(validators_pkg, "activity_flags", saved_attr)
        sys.modules.pop("app.orchestrator.validators.activity_flags", None)
        importlib.reload(tomb)

    return tomb, _restore


def test_activity_fallback_simulates_import_error() -> None:
    """ImportError: удалённый кадр и неактивный шот остаются неактивными."""
    tomb, restore = _block_activity_flags_import()
    try:
        assert tomb.shot_is_active is tomb._fallback_shot_is_active
        assert tomb.frame_is_active is tomb._fallback_frame_attrs_is_active
        deleted = SimpleNamespace(number=2, attrs={DELETED_MARK: True, ACTIVE_MARK: False})
        inactive_shot = {ACTIVE_MARK: False, "id": "k"}
        assert tomb.frame_is_active(getattr(deleted, "attrs", None) or {}) is False
        assert tomb.shot_is_active(inactive_shot) is False
        assert tomb.shot_is_active({DELETED_MARK: True}) is False
        assert tomb.frame_is_active({}) is True
        assert tomb.shot_is_active({}) is True
    finally:
        restore()


def _activity_flags_blob_source() -> bytes | None:
    """Текст blob ``e8a3284a``. None — git или blob недоступны."""

    import subprocess
    from pathlib import Path

    repo = Path(__file__).resolve().parents[1]
    try:
        result = subprocess.run(
            ["git", "show", _FLAGS_BLOB],
            cwd=repo,
            capture_output=True,
            check=False,
        )
    except OSError:
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return result.stdout


def _load_activity_flags_blob(tmp_path, source: bytes, module_name: str):
    import importlib.util

    path = tmp_path / f"{module_name}.py"
    path.write_bytes(source)
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_activity_predicates_reexport_when_importable(tmp_path) -> None:
    """Когда модуль импортируется, предикаты — те же объекты, не обёртка.

    Файл #371 лежит только во временном каталоге и в репозиторий не попадает.
    """
    import importlib

    import app.services.frame_tombstones as tomb

    source = _activity_flags_blob_source()
    if source is None:
        pytest.skip("git или blob activity_flags недоступен")
    ws1 = _load_activity_flags_blob(
        tmp_path, source, "app.orchestrator.validators.activity_flags"
    )
    sys.modules["app.orchestrator.validators.activity_flags"] = ws1
    try:
        importlib.reload(tomb)
        assert tomb.shot_is_active is ws1.shot_is_active
        assert tomb.frame_is_active is ws1.frame_is_active
    finally:
        sys.modules.pop("app.orchestrator.validators.activity_flags", None)
        importlib.reload(tomb)


def test_fallback_signatures_match_ws1_blob(tmp_path) -> None:
    """Сигнатуры fallback совпадают с blob e8a3284a: те же параметры и тип аргумента."""
    import inspect

    import app.services.activity_flags_fallback as fallback

    source = _activity_flags_blob_source()
    if source is None:
        pytest.skip("git или blob activity_flags недоступен")
    remote = _load_activity_flags_blob(tmp_path, source, "activity_flags_blob_signatures")
    tomb, restore = _block_activity_flags_import()
    try:
        for name in ("shot_is_active", "frame_is_active"):
            local = inspect.signature(getattr(tomb, name))
            blob = inspect.signature(getattr(remote, name))
            assert local == blob
            assert local == inspect.signature(getattr(fallback, name))
            assert [p.kind for p in local.parameters.values()] == [
                p.kind for p in blob.parameters.values()
            ]
        assert list(inspect.signature(tomb.frame_is_active).parameters) == ["attrs"]
        assert list(inspect.signature(tomb.shot_is_active).parameters) == ["shot"]
        assert list(inspect.signature(remote.frame_is_active).parameters) == ["attrs"]
        assert list(inspect.signature(remote.shot_is_active).parameters) == ["shot"]
    finally:
        restore()


# Blob e8a3284a считает эти строки «флага нет» (кадр живой) и пишет WARN.
# Fallback считает пометку поставленной: кадр неактивен. Просьба к WS1.
_WS1_MARK_STRINGS_TREATED_AS_ABSENT = (
    "ложь",
    "ЛОЖЬ",
    "0.0",
    "да",
    "да?",
    "1",
    "true",
    "TRUE",
    " true ",
    "yes",
    "YES",
    " да ",
)


def test_fallback_diverges_from_activity_flags_blob(tmp_path) -> None:
    """Расхождения с blob e8a3284a — явный список, не общее равенство."""

    import app.services.activity_flags_fallback as fallback

    source = _activity_flags_blob_source()
    if source is None:
        pytest.skip("git или blob activity_flags недоступен")
    remote = _load_activity_flags_blob(tmp_path, source, "activity_flags_blob_e8a3284a")
    nan = float("nan")
    scalars = [
        None,
        True,
        False,
        0,
        0.0,
        1,
        "",
        "   ",
        "0",
        "false",
        "FALSE",
        " нет ",
        "no",
        " OFF ",
        "off",
        *_WS1_MARK_STRINGS_TREATED_AS_ABSENT,
        nan,
    ]
    flag_diverged = [
        value
        for value in scalars
        if fallback.flag_is_true(value) is not remote.flag_is_true(value)
    ]
    assert flag_diverged == list(_WS1_MARK_STRINGS_TREATED_AS_ABSENT)
    for value in _WS1_MARK_STRINGS_TREATED_AS_ABSENT:
        assert fallback.flag_is_true(value) is True
        assert remote.flag_is_true(value) is False
    for value in scalars:
        assert fallback.flag_is_false(value) is remote.flag_is_false(value)

    payloads: list[object] = [None, "не словарь", 1, [], {}]
    for value in scalars:
        payloads.append({ACTIVE_MARK: value})
        payloads.append({DELETED_MARK: value})
        payloads.append({QUARANTINE_MARK: value})
    payloads.extend(
        [
            {DELETED_MARK: True, ACTIVE_MARK: True},
            {DELETED_MARK: nan, ACTIVE_MARK: True},
            {DELETED_MARK: "0.0", ACTIVE_MARK: "ложь"},
            {DELETED_MARK: "ложь", QUARANTINE_MARK: "да?"},
            {QUARANTINE_MARK: nan, ACTIVE_MARK: "0.0"},
            {QUARANTINE_MARK: 1, ACTIVE_MARK: False},
            {ACTIVE_MARK: "да?", DELETED_MARK: ""},
        ]
    )
    expected_inactive = [
        {mark: value}
        for value in _WS1_MARK_STRINGS_TREATED_AS_ABSENT
        for mark in (DELETED_MARK, QUARANTINE_MARK)
    ]
    expected_inactive.append({DELETED_MARK: "ложь", QUARANTINE_MARK: "да?"})
    record_diverged = [
        payload
        for payload in payloads
        if fallback.shot_is_active(payload) is not remote.shot_is_active(payload)
        or fallback.frame_is_active(payload) is not remote.frame_is_active(payload)
    ]
    assert record_diverged == expected_inactive
    for payload in expected_inactive:
        assert fallback.shot_is_active(payload) is False
        assert fallback.frame_is_active(payload) is False
        assert remote.shot_is_active(payload) is True
        assert remote.frame_is_active(payload) is True


def test_fallback_keeps_user_deleted_frame_out_of_paid_and_import() -> None:
    """Пометка пользователя не доходит до платных шагов и импорта через fallback."""

    from app.orchestrator.steps.generate_images import log_paid_frame_skip as image_skip
    from app.orchestrator.steps.generate_videos import log_paid_frame_skip as video_skip
    from app.services.xlsx_v8_import import seal_import_skip

    tomb, restore = _block_activity_flags_import()
    try:
        project = SimpleNamespace(id=1, meta={})
        truthy = (True, 1, "1", "true", "да", "yes", "YES", " да ")
        for value in truthy:
            frame = SimpleNamespace(number=4, id=9, attrs={DELETED_MARK: value})
            assert image_skip(frame, where="generate_images")
            assert video_skip(frame, where="generate_videos")
            assert seal_import_skip(project, frame, 4)
            assert tomb.frame_is_active(frame.attrs) is False
            assert tomb.frame_marked_deleted(frame) is True
        quarantined = SimpleNamespace(number=5, id=10, attrs={QUARANTINE_MARK: "да"})
        assert tomb.frame_is_active(quarantined.attrs) is False
        assert image_skip(quarantined, where="generate_images")
        assert video_skip(quarantined, where="generate_videos")
        for value in (False, 0, "0", "false", "нет", "no", "off", None):
            attrs = {} if value is None else {DELETED_MARK: value}
            frame = SimpleNamespace(number=6, id=11, attrs=attrs)
            assert image_skip(frame, where="generate_images") is None
            assert video_skip(frame, where="generate_videos") is None
            assert seal_import_skip(project, frame, 6) is None
            assert tomb.frame_marked_deleted(frame) is False
        assert tomb.entity_is_marked(SimpleNamespace(attrs={DELETED_MARK: "нет"})) is False
        assert tomb.entity_is_marked(SimpleNamespace(attrs={DELETED_MARK: "да"})) is True
        assert tomb.entity_is_marked(SimpleNamespace(attrs={"archived": "нет"})) is False
        assert tomb.entity_is_marked(SimpleNamespace(attrs={"archived": "yes"})) is True
    finally:
        restore()
