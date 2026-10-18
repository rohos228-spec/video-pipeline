"""no_deletions: уменьшение числа или пропажа id сцен/кадров — blocking."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from app.orchestrator.validators import VALIDATOR_CONTEXT_DRY_RUN, run
from app.orchestrator.validators.snapshot import load_project_snapshot


def _side(scenes: list[str], frames: list[str]) -> dict:
    return {
        "scenes": [{"id": item} for item in scenes],
        "frames": [{"id": item} for item in frames],
    }


def test_frame_deletion_is_blocking() -> None:
    result = run(
        "no_deletions",
        7,
        {
            "before": _side(["s1"], ["f1", "f2"]),
            "after": _side(["s1"], ["f1"]),
        },
    )
    assert result.passed is False
    assert result.blocking is True
    assert result.details["reason"] == "deletion"
    assert result.details["frames"]["missing_ids"] == ["f2"]
    assert result.details["frames"]["count_decreased"] is True
    dumped = result.model_dump(by_alias=True)
    assert dumped["pass"] is False
    assert dumped["name"] == "no_deletions"


def test_scene_id_replaced_without_count_drop_is_blocking() -> None:
    result = run(
        "no_deletions",
        7,
        {"before": _side(["s1", "s2"], ["f1"]), "after": _side(["s1", "s3"], ["f1"])},
    )
    assert result.blocking is True
    assert result.details["scenes"]["missing_ids"] == ["s2"]
    assert result.details["scenes"]["count_decreased"] is False


def test_shot_alias_and_equal_snapshots_pass() -> None:
    before = {"scenes": [{"id": 1}], "shots": [{"id": "a"}, {"id": "b"}]}
    after = {"сцены": [{"id": 1}], "кадры": ["a", "b"]}
    result = run("no_deletions", 1, {"before": before, "after": after})
    assert result.passed is True
    assert result.blocking is False


def test_dry_run_does_not_mutate_snapshots() -> None:
    before = _side(["s1"], ["f1", "f2"])
    after = _side(["s1"], ["f1"])
    context = {"before": before, "after": after, VALIDATOR_CONTEXT_DRY_RUN: True}
    run("no_deletions", 1, context)
    assert [row["id"] for row in before["frames"]] == ["f1", "f2"]
    assert context[VALIDATOR_CONTEXT_DRY_RUN] is True


def test_no_snapshot_is_not_a_failure() -> None:
    result = run("no_deletions", 1, {})
    assert result.passed is True
    assert result.details["reason"] == "no_snapshot"


def test_incomplete_snapshot_blocks() -> None:
    result = run("no_deletions", 1, {"before": _side(["s1"], ["f1"])})
    assert result.blocking is True
    assert result.details["reason"] == "snapshot_incomplete"


def test_loader_supplies_after_and_is_only_read() -> None:
    calls: list[int] = []

    def loader(project_id: int) -> dict:
        calls.append(project_id)
        return _side(["s1"], ["f1"])

    result = run(
        "no_deletions",
        4,
        {"before": _side(["s1"], ["f1", "f2"]), "snapshot_loader": loader},
    )
    assert calls == [4]
    assert result.blocking is True
    assert result.details["frames"]["missing_ids"] == ["f2"]


def test_missing_db_with_baseline_blocks() -> None:
    result = run(
        "no_deletions",
        4,
        {"before": _side(["s1"], ["f1"]), "db_path": "/tmp/does-not-exist-ws1.db"},
    )
    assert result.blocking is True
    assert result.details["reason"] == "snapshot_unavailable"


def test_read_only_sqlite_snapshot(tmp_path: Path) -> None:
    path = tmp_path / "project.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE scenes (id INTEGER PRIMARY KEY, project_id INTEGER)")
    conn.execute("CREATE TABLE frames (id INTEGER PRIMARY KEY, project_id INTEGER)")
    conn.execute("INSERT INTO scenes (id, project_id) VALUES (1, 9), (2, 8)")
    conn.execute("INSERT INTO frames (id, project_id) VALUES (10, 9), (11, 9)")
    conn.commit()
    conn.close()
    before_bytes = path.read_bytes()
    snap = load_project_snapshot(9, db_path=path)
    assert snap["available"] is True
    assert [row["id"] for row in snap["scenes"]] == [1]
    assert [row["id"] for row in snap["frames"]] == [10, 11]
    assert path.read_bytes() == before_bytes
    result = run(
        "no_deletions",
        9,
        {"before": {"scenes": [{"id": 1}], "frames": [{"id": 10}, {"id": 11}]}, "db_path": str(path)},
    )
    assert result.passed is True


def test_db_without_tables_is_unavailable(tmp_path: Path) -> None:
    path = tmp_path / "empty.db"
    sqlite3.connect(path).close()
    snap = load_project_snapshot(1, db_path=path)
    assert snap["available"] is False
