"""Один предикат активности.

Если ``activity_flags`` импортируется, ``frame_tombstones`` отдаёт те же
объекты. Иначе локальная матрица. Она совпадает с blob ``56a6e8cf``
на всей матрице: ``test_fallback_matches_activity_flags_blob``.
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
_NAN = float("nan")
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
    ("shot", {DELETED_MARK: "да"}, False, False),
    ("shot", {DELETED_MARK: "Да"}, False, False),
    ("shot", {DELETED_MARK: " да "}, False, False),
    ("shot", {DELETED_MARK: "true"}, False, False),
    ("shot", {DELETED_MARK: "True"}, False, False),
    ("shot", {DELETED_MARK: "1"}, False, False),
    ("shot", {DELETED_MARK: "yes"}, False, False),
    ("shot", {DELETED_MARK: "on"}, False, False),
    ("shot", {DELETED_MARK: "истина"}, False, False),
    ("shot", {DELETED_MARK: "0.0"}, True, False),
    ("shot", {DELETED_MARK: " 0,0 "}, True, False),
    ("shot", {DELETED_MARK: "1.0"}, True, False),
    ("shot", {ACTIVE_MARK: "0.0"}, True, True),
    ("shot", {ACTIVE_MARK: " 0,0 "}, True, True),
    ("shot", {ACTIVE_MARK: "1.0"}, True, True),
    ("shot", {ACTIVE_MARK: "да"}, True, True),
    ("shot", {ACTIVE_MARK: "Да"}, True, True),
    ("shot", {ACTIVE_MARK: " да "}, True, True),
    ("shot", {ACTIVE_MARK: "true"}, True, True),
    ("shot", {ACTIVE_MARK: "True"}, True, True),
    ("shot", {ACTIVE_MARK: "1"}, True, True),
    ("shot", {ACTIVE_MARK: "yes"}, True, True),
    ("shot", {ACTIVE_MARK: "on"}, True, True),
    ("shot", {ACTIVE_MARK: "истина"}, True, True),
    ("shot", {ACTIVE_MARK: "да?"}, True, True),
    ("shot", {DELETED_MARK: "ложь"}, True, False),
    ("shot", {DELETED_MARK: "ЛОЖЬ"}, True, False),
    ("shot", {DELETED_MARK: _NAN}, True, False),
    ("shot", {DELETED_MARK: "-"}, True, False),
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
    ("shot", {QUARANTINE_MARK: "да"}, False, False),
    ("shot", {QUARANTINE_MARK: "0.0"}, True, False),
    ("shot", {QUARANTINE_MARK: "ложь"}, True, False),
    ("shot", {QUARANTINE_MARK: _NAN}, True, False),
    ("shot", {QUARANTINE_MARK: "-"}, True, False),
    ("shot", {ACTIVE_MARK: "ложь"}, False, False),
    ("shot", {ACTIVE_MARK: "-"}, True, True),
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
    ("frame", {DELETED_MARK: "да"}, False, False),
    ("frame", {DELETED_MARK: "true"}, False, False),
    ("frame", {DELETED_MARK: "1"}, False, False),
    ("frame", {DELETED_MARK: True}, False, False),
    ("frame", {DELETED_MARK: "0.0"}, True, False),
    ("frame", {DELETED_MARK: "1.0"}, True, False),
    ("frame", {ACTIVE_MARK: "0.0"}, True, True),
    ("frame", {ACTIVE_MARK: " 0,0 "}, True, True),
    ("frame", {DELETED_MARK: "ложь"}, True, False),
    ("frame", {DELETED_MARK: _NAN}, True, False),
    ("frame", {DELETED_MARK: "-"}, True, False),
    ("frame", {ACTIVE_MARK: "ложь"}, False, False),
    ("frame", {ACTIVE_MARK: "да"}, True, True),
    ("frame", {ACTIVE_MARK: "true"}, True, True),
    ("frame", {ACTIVE_MARK: "1"}, True, True),
    ("frame", {ACTIVE_MARK: "yes"}, True, True),
    ("frame", {ACTIVE_MARK: "on"}, True, True),
    ("frame", {ACTIVE_MARK: "истина"}, True, True),
    ("frame", {ACTIVE_MARK: "да?"}, True, True),
    ("frame", {ACTIVE_MARK: "-"}, True, True),
    ("frame", {QUARANTINE_MARK: "истина"}, False, False),
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


@pytest.fixture(scope="module")
def ws1_flags_blob(tmp_path_factory):
    """Blob ``56a6e8cf``. Модуль WS1 в дерево не копируется."""

    source = _activity_flags_blob_source()
    if source is None:
        pytest.skip("git или blob activity_flags недоступен")
    return _load_activity_flags_blob(
        tmp_path_factory.mktemp("flags56"),
        source,
        "activity_flags_blob_56a6e8cf",
    )


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
    ws1_flags_blob,
) -> None:
    """Матрица ImportError-пути совпадает с blob 56a6e8cf."""

    import app.services.activity_flags_fallback as fallback

    fallback._WARNED_VALUES.clear()
    ws1_flags_blob._WARNED_VALUES.clear()
    caplog.set_level(logging.WARNING)
    local = fallback.shot_is_active if kind == "shot" else fallback.frame_is_active
    remote = (
        ws1_flags_blob.shot_is_active if kind == "shot" else ws1_flags_blob.frame_is_active
    )
    assert local(payload) is expected
    assert remote(payload) is expected
    warned = any("активен" in record.getMessage() for record in caplog.records)
    assert warned is warns
    if kind == "shot" and payload == {}:
        _project65_and_readers_stay_active()
        _fallback_core_cases()
        assert shot_is_active({}) is True


_FLAGS_BLOB = "56a6e8cf104c0a0f6bc1ba1467d54e5844d38819"


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
        assert tomb.frame_is_active is tomb._fallback_frame_is_active
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
    """Текст blob ``56a6e8cf``. None — git или blob недоступны."""

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
        import inspect

        import app.services.activity_flags_fallback as fallback_mod

        assert tomb._fallback_shot_is_active is fallback_mod.shot_is_active
        assert tomb._fallback_frame_is_active is fallback_mod.frame_is_active
        assert tomb._fallback_entity_is_marked is tomb.entity_is_marked
        assert list(inspect.signature(tomb._fallback_shot_is_active).parameters) == ["shot"]
        assert list(inspect.signature(tomb._fallback_frame_is_active).parameters) == ["attrs"]
    finally:
        sys.modules.pop("app.orchestrator.validators.activity_flags", None)
        importlib.reload(tomb)


def test_fallback_signatures_match_ws1_blob(tmp_path) -> None:
    """Сигнатуры fallback совпадают с blob 56a6e8cf: те же параметры и тип аргумента."""
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


def test_fallback_matches_activity_flags_blob(tmp_path, caplog: pytest.LogCaptureFixture) -> None:
    """Расхождений с blob 56a6e8cf нет: один ответ на всей матрице."""

    import app.services.activity_flags_fallback as fallback

    source = _activity_flags_blob_source()
    if source is None:
        pytest.skip("git или blob activity_flags недоступен")
    remote = _load_activity_flags_blob(tmp_path, source, "activity_flags_blob_56a6e8cf_eq")
    nan = float("nan")
    hide_true = (
        "да",
        "Да",
        " да ",
        "true",
        "True",
        "1",
        "yes",
        "on",
        "истина",
        True,
        1,
    )
    for value in hide_true:
        assert fallback.flag_is_true(value) is True
        assert remote.flag_is_true(value) is True
        assert fallback.frame_is_active({DELETED_MARK: value}) is False
        assert fallback.shot_is_active({QUARANTINE_MARK: value}) is False
    for value in (nan, "0.0", " 0,0 ", "ложь", "ЛОЖЬ"):
        assert fallback.flag_is_true(value) is False
        assert remote.flag_is_true(value) is False
        assert fallback.frame_is_active({DELETED_MARK: value}) is True
        assert remote.shot_is_active({DELETED_MARK: value}) is True
    for value in ("1.0", "1,0", " 1.0 "):
        assert fallback.flag_is_true(value) is False
        assert remote.flag_is_true(value) is False
        assert fallback.frame_is_active({DELETED_MARK: value}) is True
        assert fallback.shot_is_active({QUARANTINE_MARK: value}) is True
    for value, expected in (("0.0", True), (" 0,0 ", True), ("0", False)):
        assert fallback.shot_is_active({ACTIVE_MARK: value}) is expected
        assert remote.frame_is_active({ACTIVE_MARK: value}) is expected
    assert fallback.shot_is_active({ACTIVE_MARK: "1.0"}) is True
    assert fallback.shot_is_active({ACTIVE_MARK: "ложь"}) is False
    assert remote.frame_is_active({ACTIVE_MARK: "ложь"}) is False

    for value in ("да", "Да", " да ", "true", "True", "1", "yes", "on", "истина", "1.0"):
        fallback._WARNED_VALUES.clear()
        remote._WARNED_VALUES.clear()
        caplog.clear()
        assert fallback.shot_is_active({ACTIVE_MARK: value}) is True
        fallback_warns = [
            record.getMessage()
            for record in caplog.records
            if record.levelno >= logging.WARNING
        ]
        caplog.clear()
        assert remote.frame_is_active({ACTIVE_MARK: value}) is True
        remote_warns = [
            record.getMessage()
            for record in caplog.records
            if record.levelno >= logging.WARNING
        ]
        assert fallback_warns == remote_warns
        assert len(fallback_warns) == 1
        assert "активен" in fallback_warns[0]

    fallback._WARNED_VALUES.clear()
    remote._WARNED_VALUES.clear()
    caplog.clear()
    assert fallback.shot_is_active({ACTIVE_MARK: "да?"}) is True
    fallback_unknown = [
        record.getMessage()
        for record in caplog.records
        if record.levelno >= logging.WARNING
    ]
    caplog.clear()
    assert remote.shot_is_active({ACTIVE_MARK: "да?"}) is True
    remote_unknown = [
        record.getMessage()
        for record in caplog.records
        if record.levelno >= logging.WARNING
    ]
    assert fallback_unknown == remote_unknown == [
        "неизвестное значение «активен» 'да?' — считаем, что флага нет"
    ]

    fallback._WARNED_VALUES.clear()
    remote._WARNED_VALUES.clear()
    caplog.clear()
    caplog.set_level(logging.WARNING)
    assert fallback.flag_is_true("-") is False
    assert remote.flag_is_true("-") is False
    assert fallback.frame_is_active({DELETED_MARK: "-"}) is True
    assert remote.shot_is_active({DELETED_MARK: "-"}) is True
    warnings = [
        record.getMessage()
        for record in caplog.records
        if record.name == "app.services.activity_flags_fallback"
        and "неизвестное значение" in record.getMessage()
    ]
    assert warnings == ["неизвестное значение «удалён» '-' — считаем, что флага нет"]
    assert fallback.flag_is_true("-") is False
    assert [
        record.getMessage()
        for record in caplog.records
        if record.name == "app.services.activity_flags_fallback"
    ] == warnings

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
        "ложь",
        "ЛОЖЬ",
        "0.0",
        " 0,0 ",
        "1.0",
        "1,0",
        "да",
        "Да",
        "да?",
        "1",
        "true",
        "TRUE",
        " true ",
        "yes",
        "YES",
        " да ",
        "on",
        "истина",
        "-",
        nan,
    ]
    diverged: list[object] = []
    for value in scalars:
        if fallback.flag_is_true(value) is not remote.flag_is_true(value):
            diverged.append(("flag_is_true", value))
        if fallback.flag_is_false(value) is not remote.flag_is_false(value):
            diverged.append(("flag_is_false", value))
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
            {DELETED_MARK: "-", ACTIVE_MARK: True},
            {QUARANTINE_MARK: "истина", ACTIVE_MARK: True},
        ]
    )
    for payload in payloads:
        if fallback.shot_is_active(payload) is not remote.shot_is_active(payload):
            diverged.append(("shot", payload))
        if fallback.frame_is_active(payload) is not remote.frame_is_active(payload):
            diverged.append(("frame", payload))
    assert diverged == []


def test_fallback_keeps_user_deleted_frame_out_of_paid_and_import() -> None:
    """Пометка пользователя не доходит до платных шагов и импорта через fallback."""

    from app.orchestrator.steps.generate_images import log_paid_frame_skip as image_skip
    from app.orchestrator.steps.generate_videos import log_paid_frame_skip as video_skip
    from app.services.xlsx_v8_import import seal_import_skip

    tomb, restore = _block_activity_flags_import()
    try:
        project = SimpleNamespace(id=1, meta={})
        truthy = ("да", "true", "1", True, 1, "Да", " да ", "yes", "on", "истина")
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
        for value in (False, 0, "0", "false", "нет", "no", "off", None, "0.0", "ложь", "-"):
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


# Решение Heil: не fail-closed. Эталон — blob 56a6e8cf, не «любая левая строка скрывает».
_HEIL_BLOB = "56a6e8cf104c0a0f6bc1ba1467d54e5844d38819"
_MISSING = object()


def _heil_value_table() -> list[tuple[object, object, bool]]:
    """Одна таблица. Третий столбец — шот и кадр живые."""

    inactive = (
        True,
        1,
        "1",
        "true",
        "True",
        "да",
        "Да",
        " да ",
        "yes",
        "on",
        "истина",
    )
    alive = (False, 0, "0", "false", "нет", "ложь", "0.0", float("nan"), None)
    rows: list[tuple[object, object, bool]] = []
    for mark in (DELETED_MARK, QUARANTINE_MARK):
        for value in inactive:
            rows.append((mark, value, False))
        for value in alive:
            rows.append((mark, value, True))
        rows.append((mark, "-", True))
    rows.append((ACTIVE_MARK, "ложь", False))
    rows.append((_MISSING, _MISSING, True))
    return rows


def test_activity_parity_real_vs_fallback(tmp_path, caplog: pytest.LogCaptureFixture) -> None:
    """Решение Heil: не fail-closed, фолбэк совпадает с blob ``56a6e8cf``.

    Одна таблица идёт в модуль, загруженный из этого blob, и в именные
    фолбэки ``_fallback_shot_is_active(shot)``, ``_fallback_frame_is_active(attrs)``,
    ``_fallback_entity_is_marked``. «-» оставляет запись живой и пишет ровно один WARN.
    """

    import inspect
    import subprocess
    from pathlib import Path

    from app.services import frame_tombstones as tomb

    repo = Path(__file__).resolve().parents[1]
    loaded = subprocess.run(
        ["git", "show", _HEIL_BLOB],
        cwd=repo,
        capture_output=True,
        check=False,
    )
    assert loaded.returncode == 0 and loaded.stdout, loaded.stderr
    real = _load_activity_flags_blob(tmp_path, loaded.stdout, "activity_flags_blob_56a6e8cf")

    def real_marked(entity: object) -> bool:
        attrs = getattr(entity, "attrs", None)
        if not isinstance(attrs, dict):
            return False
        return real.flag_is_true(attrs.get(DELETED_MARK), mark=DELETED_MARK) or real.flag_is_true(
            attrs.get("archived"), mark="archived"
        )

    def warnings() -> list[str]:
        return [
            record.getMessage()
            for record in caplog.records
            if record.levelno >= logging.WARNING
        ]

    assert list(inspect.signature(tomb._fallback_shot_is_active).parameters) == ["shot"]
    assert list(inspect.signature(tomb._fallback_frame_is_active).parameters) == ["attrs"]
    assert callable(tomb._fallback_entity_is_marked)
    sides = (
        ("real", real.shot_is_active, real.frame_is_active, real_marked),
        (
            "fallback",
            tomb._fallback_shot_is_active,
            tomb._fallback_frame_is_active,
            tomb._fallback_entity_is_marked,
        ),
    )
    rows = _heil_value_table()
    assert rows
    caplog.set_level(logging.WARNING)
    differences: list[object] = []
    for mark, value, expected_active in rows:
        payload = {} if mark is _MISSING else {mark: value}
        entity = SimpleNamespace(attrs=dict(payload))
        expect_marked = mark == DELETED_MARK and not expected_active
        expect_warn = value == "-"
        seen: dict[str, tuple[tuple[bool, bool, bool], list[str]]] = {}
        for label, shot, frame, marked in sides:
            shot.__globals__["_WARNED_VALUES"].clear()
            caplog.clear()
            got = (shot(payload), frame(payload), marked(entity))
            messages = warnings()
            seen[label] = (got, messages)
            if got != (expected_active, expected_active, expect_marked):
                differences.append((label, mark, value, got, expected_active, expect_marked))
            if expect_warn and len(messages) != 1:
                differences.append((label, mark, value, "warn", messages))
            if not expect_warn and messages:
                differences.append((label, mark, value, "warn", messages))
        if seen["real"] != seen["fallback"]:
            differences.append(("mismatch", mark, value, seen))
    assert differences == []
