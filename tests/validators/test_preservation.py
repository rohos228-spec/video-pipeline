"""Общий прогон: before/after/ops → нарушения со стабильными кодами."""

from __future__ import annotations

import copy

from app.orchestrator.validators import run
from app.orchestrator.validators.preservation import collect_violations


def _before() -> dict:
    return {
        "сцены": [
            {
                "id": "s1",
                "номер": 1,
                "закадр": "Первая фраза.",
                "кадры": [
                    {"id": "k1", "закадр": "Первая фраза."},
                    {"id": "dead", "активен": False, "закадр": "хвост"},
                ],
            },
            {"id": "s2", "номер": 2, "кадры": [{"id": "k9"}]},
        ],
        "персонажи": [{"id": "c01", "имя": "Анна"}, {"id": "c02", "имя": "Борис"}],
    }


def _after() -> dict:
    return {
        "сцены": [
            {
                "id": "s1",
                "номер": 1,
                "закадр": "Первая фраза.",
                "кадры": [{"id": "dead", "активен": False, "закадр": "хвост"}],
            }
        ],
        "персонажи": [{"id": "c01", "имя": "Анна"}],
    }


def test_collect_reports_stable_codes() -> None:
    before = _before()
    after = _after()
    ops = [{"op": "render", "shot_id": "dead"}, {"op": "delete", "entity": "character", "id": "c02"}]
    frozen = copy.deepcopy((before, after, ops))
    violations = collect_violations(before, after, ops)
    assert violations == collect_violations(before, after, ops)
    assert (before, after, ops) == frozen
    codes = {(item.code, item.severity, item.id) for item in violations}
    assert ("scene_deleted", "error", "s2") in codes
    assert ("frame_deleted", "error", "k1") in codes
    assert ("frame_deleted", "error", "k9") in codes
    assert ("character_deleted", "error", "c02") in codes
    assert ("vo_on_inactive", "error", "dead") in codes
    assert ("inactive_shot_used", "error", "dead") in codes
    for item in violations:
        assert item.severity in {"error", "warn"}
        assert item.path
        assert isinstance(item.id, str)


def test_clean_project_and_inactive_shot_pass() -> None:
    project = {
        "сцены": [
            {
                "id": "s1",
                "номер": 1,
                "закадр": "Первая фраза.",
                "кадры": [
                    {"id": "k1", "закадр": "Первая фраза."},
                    {"id": "dead", "активен": False, "закадр": ""},
                ],
            }
        ],
        "персонажи": [{"id": "c01", "archived": True}],
    }
    assert collect_violations(project, copy.deepcopy(project), ops=[]) == []


def test_warn_does_not_block_the_registry() -> None:
    before = {"сцены": [{"id": "s1", "номер": 1, "кадры": [{"id": "k1"}]}]}
    after = {"сцены": [{"id": "s1", "номер": 1, "кадры": None}]}
    result = run("preservation", 1, {"before": before, "after": after})
    assert result.passed is True
    assert result.blocking is False
    assert result.details["violations"] == [
        {
            "code": "frames_no_data",
            "severity": "warn",
            "path": "scenes/s1/кадры",
            "id": "s1",
        }
    ]


def test_errors_block() -> None:
    result = run("preservation", 3, {"before": _before(), "after": _after(), "ops": []})
    assert result.passed is False
    assert result.blocking is True
    assert result.name == "preservation"
    assert any(item["code"] == "scene_deleted" for item in result.details["violations"])
