"""Матрица активности шота и кадра совпадает с WS1 activity_flags.

Локальная копия — ``app/services/activity_flags_fallback.py``, байт-в-байт
``d984a52d``. ``frame_is_active`` у вызывающих принимает кадр, матрица — ``attrs``.
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
    ("shot", {ACTIVE_MARK: "да?"}, True, True),
    ("shot", {DELETED_MARK: True}, False, False),
    ("shot", {DELETED_MARK: "да"}, False, False),
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
    ("frame", {ACTIVE_MARK: "да?"}, True, True),
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
    assert all(frame_is_active(frame) for frame in frames)

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
    assert [frame for frame in frame_rows if frame_is_active(frame)] == frame_rows[:4]
    assert frame_marked_deleted(_as_frame({DELETED_MARK: "false"})) is False
    assert frame_marked_deleted(_as_frame({DELETED_MARK: "нет"})) is False
    assert frame_marked_deleted(_as_frame({DELETED_MARK: 0})) is False
    assert frame_is_active(_as_frame({QUARANTINE_MARK: True})) is False

    tomb = SimpleNamespace(attrs={}, id=1, number=1)
    mark_frame_deleted(tomb, None, "matrix")
    assert tomb.attrs[DELETED_MARK] is True
    assert tomb.attrs[QUARANTINE_MARK] is True
    assert tomb.attrs[ACTIVE_MARK] is False

    merged = merge_kadry_keep_missing([{"id": "k", "закадр": "текст"}], [])
    assert merged[0][ACTIVE_MARK] is False


def _fallback_core_cases() -> None:
    """Крайние значения (NaN, ``0.0``, «ложь») задаёт модуль WS1, не этот тест."""
    assert shot_is_active({}) is True
    assert shot_is_active({ACTIVE_MARK: True}) is True
    assert shot_is_active({ACTIVE_MARK: False}) is False
    assert shot_is_active({DELETED_MARK: True}) is False
    assert shot_is_active({QUARANTINE_MARK: True}) is False
    assert frame_is_active(_as_frame({})) is True
    assert frame_is_active(_as_frame({ACTIVE_MARK: False})) is False
    assert frame_is_active(_as_frame({DELETED_MARK: True})) is False
    assert frame_is_active(_as_frame({QUARANTINE_MARK: True})) is False


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
        assert frame_is_active(_as_frame(payload)) is expected
    warned = any("активен" in record.getMessage() for record in caplog.records)
    assert warned is warns
    if kind == "shot" and payload == {}:
        _project65_and_readers_stay_active()
        _fallback_core_cases()
        assert shot_is_active({}) is True


_WS1_FLAGS_SHA = "d984a52d"


def test_activity_fallback_simulates_import_error() -> None:
    """ImportError оставляет локальную матрицу. Крайние значения сюда не входят."""
    import builtins
    import importlib

    import app.services.frame_tombstones as tomb

    original = builtins.__import__

    def _blocked(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "app.orchestrator.validators.activity_flags":
            raise ImportError(name)
        return original(name, globals, locals, fromlist, level)

    builtins.__import__ = _blocked
    sys.modules.pop("app.orchestrator.validators.activity_flags", None)
    try:
        importlib.reload(tomb)
        assert tomb.shot_is_active is tomb._fallback_shot_is_active
        assert tomb.frame_is_active is tomb._fallback_frame_is_active
        assert tomb.shot_is_active({}) is True
        assert tomb.shot_is_active({ACTIVE_MARK: True}) is True
        assert tomb.shot_is_active({ACTIVE_MARK: False}) is False
        assert tomb.shot_is_active({DELETED_MARK: True}) is False
        assert tomb.shot_is_active({QUARANTINE_MARK: True}) is False
        assert tomb.frame_is_active(SimpleNamespace(attrs={})) is True
        assert tomb.frame_is_active(SimpleNamespace(attrs={ACTIVE_MARK: False})) is False
        assert tomb.frame_is_active(SimpleNamespace(attrs={DELETED_MARK: True})) is False
        assert tomb.frame_is_active(SimpleNamespace(attrs={QUARANTINE_MARK: True})) is False
    finally:
        builtins.__import__ = original
        sys.modules.pop("app.orchestrator.validators.activity_flags", None)
        importlib.reload(tomb)


def test_activity_predicates_reexport_ws1_when_importable(tmp_path) -> None:
    """Когда модуль импортируется, предикаты — те же объекты, не обёртка.

    Файл #371 только во временном каталоге и в репозиторий не попадает.
    """
    import importlib
    import importlib.util
    import io
    import subprocess
    import tarfile

    import app.services.frame_tombstones as tomb

    repo = __import__("pathlib").Path(__file__).resolve().parents[1]
    archive = subprocess.run(
        ["git", "archive", _WS1_FLAGS_SHA, "app/orchestrator/validators/activity_flags.py"],
        cwd=repo,
        check=True,
        capture_output=True,
    ).stdout
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as tar:
        tar.extractall(tmp_path, filter="data")
    path = tmp_path / "app" / "orchestrator" / "validators" / "activity_flags.py"
    spec = importlib.util.spec_from_file_location(
        "app.orchestrator.validators.activity_flags", path
    )
    assert spec is not None and spec.loader is not None
    ws1 = importlib.util.module_from_spec(spec)
    sys.modules["app.orchestrator.validators.activity_flags"] = ws1
    spec.loader.exec_module(ws1)
    try:
        importlib.reload(tomb)
        assert tomb.shot_is_active is ws1.shot_is_active
        assert tomb.frame_is_active is ws1.frame_is_active
    finally:
        sys.modules.pop("app.orchestrator.validators.activity_flags", None)
        importlib.reload(tomb)


def _git_show(repo, spec: str) -> str:
    import subprocess

    result = subprocess.run(
        ["git", "show", spec],
        cwd=repo,
        capture_output=True,
    )
    if result.returncode != 0:
        subprocess.run(
            ["git", "fetch", "origin", "cursor/ws1-validators-inactive-shots"],
            cwd=repo,
            check=True,
        )
        result = subprocess.run(
            ["git", "show", spec],
            cwd=repo,
            capture_output=True,
            check=True,
        )
    return result.stdout.decode("utf-8")


def _ws1_matrix(source: str) -> list[tuple[str, object, bool, bool]]:
    import ast

    tree = ast.parse(source)
    segment = None
    for node in tree.body:
        target = node.target if isinstance(node, ast.AnnAssign) else None
        if isinstance(node, ast.Assign):
            target = next(
                (item for item in node.targets if isinstance(item, ast.Name) and item.id == "_CASES"),
                None,
            )
        if isinstance(target, ast.Name) and target.id == "_CASES" and node.value is not None:
            segment = ast.get_source_segment(source, node.value)
            break
    if not segment:
        raise AssertionError("в тесте WS1 нет матрицы _CASES")
    return eval(  # noqa: S307 — матрица из зафиксированного коммита репозитория
        segment,
        {
            "ACTIVE_MARK": "активен",
            "DELETED_MARK": "удалён",
            "QUARANTINE_MARK": "карантин",
        },
    )


def _load_ws1_flags(tmp_path, source: str):
    import importlib.util

    path = tmp_path / "activity_flags.py"
    path.write_text(source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("ws1_activity_flags_d984a52d", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _active_and_warn(fn, payload, caplog: pytest.LogCaptureFixture) -> tuple[bool, bool]:
    caplog.clear()
    value = fn(payload)
    warned = any("активен" in record.getMessage() for record in caplog.records)
    return value, warned


def test_activity_matrix_matches_ws1_flags_file(tmp_path, caplog: pytest.LogCaptureFixture) -> None:
    """Матрица #371 гоняется на локальной копии и на файле d984a52d из tmp.

    Расхождение ответа или WARN делает тест красным. Файл #371 в репозиторий
    этой ветки не коммитится.
    """
    from pathlib import Path

    import app.services.activity_flags_fallback as fallback
    import app.services.frame_tombstones as tomb

    repo = Path(__file__).resolve().parents[1]
    source = _git_show(repo, f"{_WS1_FLAGS_SHA}:app/orchestrator/validators/activity_flags.py")
    ours = (repo / "app/services/activity_flags_fallback.py").read_text(encoding="utf-8")
    assert ours == source
    cases = _ws1_matrix(
        _git_show(repo, f"{_WS1_FLAGS_SHA}:tests/validators/test_activity_flags.py")
    )
    assert cases
    ws1 = _load_ws1_flags(tmp_path, source)
    assert fallback.shot_is_active is tomb._fallback_shot_is_active
    assert fallback.frame_is_active is tomb._fallback_frame_attrs_is_active
    assert (fallback.ACTIVE_MARK, fallback.DELETED_MARK, fallback.QUARANTINE_MARK) == (
        ws1.ACTIVE_MARK,
        ws1.DELETED_MARK,
        ws1.QUARANTINE_MARK,
    )
    caplog.set_level(logging.WARNING)
    for kind, payload, expected, warns in cases:
        if kind == "shot":
            local = fallback.shot_is_active
            remote = ws1.shot_is_active
        else:
            local = fallback.frame_is_active
            remote = ws1.frame_is_active
        local_active, local_warn = _active_and_warn(local, payload, caplog)
        remote_active, remote_warn = _active_and_warn(remote, payload, caplog)
        assert local_active is remote_active is expected
        assert local_warn is remote_warn is warns
        if kind == "frame":
            wrapped, wrapped_warn = _active_and_warn(
                tomb._fallback_frame_is_active, _as_frame(payload), caplog
            )
            assert wrapped is remote_active
            assert wrapped_warn is warns
