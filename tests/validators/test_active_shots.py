"""Неактивный шот не входит в закадр, QC и рендер. Живой набор берётся из shots_of."""

from __future__ import annotations

import copy

import pytest

from app.orchestrator.validators.grammar import shots_of
from app.orchestrator.validators.preservation import run_preservation_validators
from app.orchestrator.validators.shot_activity import check_active_shots


def _project(shots: list[dict], vo: str) -> dict:
    return {
        "сцены": [
            {
                "id": "s1",
                "номер": 1,
                "закадр": vo,
                "кадры": shots,
            }
        ]
    }


def _codes(violations: list) -> list[tuple[str, str, str]]:
    return [(item.code, item.severity, item.id) for item in violations]


def test_voiceover_on_active_shots_passes() -> None:
    shots = [
        {"id": "a", "закадр": "Первая фраза."},
        {"id": "b", "закадр": "Вторая фраза."},
        {"id": "dead", "активен": False, "закадр": ""},
    ]
    project = _project(shots, "Первая фраза. Вторая фраза.")
    before = copy.deepcopy(project)
    after = copy.deepcopy(project)
    frozen = copy.deepcopy((before, after))
    assert check_active_shots(before, after, ops=[]) == []
    assert (before, after) == frozen
    assert [shot["id"] for shot in shots_of({"shots": shots})] == ["a", "b"]


def test_voiceover_qc_and_render_skip_inactive_shot() -> None:
    shots = [
        {"id": "a", "закадр": "Первая фраза."},
        {"id": "dead", "активен": False, "закадр": "хвост", "qc": True},
    ]
    project = _project(shots, "Первая фраза.")
    voiced = check_active_shots(project, copy.deepcopy(project))
    assert ("vo_on_inactive", "error", "dead") not in _codes(voiced)
    assert ("inactive_shot_used", "error", "dead") in _codes(voiced)

    quiet = [
        {"id": "a", "закадр": "Первая фраза."},
        {"id": "dead", "активен": False, "закадр": ""},
    ]
    base = _project(quiet, "Первая фраза.")
    rendered = check_active_shots(
        base,
        copy.deepcopy(base),
        ops=[{"op": "render", "shot_id": "dead"}, {"op": "qc", "id": "a"}],
    )
    assert _codes(rendered) == [("inactive_shot_used", "error", "dead")]


def test_cell_voiceover_must_sit_on_active_shots() -> None:
    shots = [
        {"id": "a", "закадр": "Первая фраза."},
        {"id": "dead", "активен": False, "закадр": ""},
    ]
    project = _project(shots, "Первая фраза. Вторая фраза.")
    violations = check_active_shots(project, copy.deepcopy(project))
    assert _codes(violations) == [("vo_on_inactive", "error", "s1")]
    assert violations[0].path == "scenes/s1/закадр"


def test_incoming_inactive_shot_does_not_revive_by_itself() -> None:
    before = _project(
        [
            {"id": "a", "закадр": "Первая фраза."},
            {"id": "dead", "активен": False, "закадр": ""},
        ],
        "Первая фраза.",
    )
    revived = _project(
        [
            {"id": "a", "закадр": "Первая фраза."},
            {"id": "dead", "активен": True, "закадр": ""},
        ],
        "Первая фраза.",
    )
    violations = check_active_shots(before, revived)
    assert _codes(violations) == [("inactive_shot_revived", "error", "dead")]

    allowed = check_active_shots(
        before,
        revived,
        ops=[{"op": "activate", "shot_id": "dead"}],
    )
    assert allowed == []


def test_op_cannot_drop_the_last_active_shot_while_vo_remains() -> None:
    before = _project([{"id": "a", "закадр": "Первая фраза."}], "Первая фраза.")
    after = _project([{"id": "a", "активен": False, "закадр": ""}], "Первая фраза.")
    ops = [{"op": "update", "id": "a", "fields": {"активен": False}}]
    codes = {item.code for item in check_active_shots(before, after, ops=ops)}
    assert "last_active_deactivated" in codes
    assert "vo_on_inactive" in codes

    kept = _project(
        [
            {"id": "a", "активен": False, "закадр": ""},
            {"id": "b", "закадр": "Первая фраза. Вторая фраза."},
        ],
        "Первая фраза. Вторая фраза.",
    )
    still = check_active_shots(
        _project(
            [
                {"id": "a", "закадр": "Первая фраза."},
                {"id": "b", "закадр": "Вторая фраза."},
            ],
            "Первая фраза. Вторая фраза.",
        ),
        kept,
        ops=[{"op": "deactivate", "shot_id": "a"}],
    )
    assert still == []

    empty_vo = _project([{"id": "a", "активен": False, "закадр": ""}], "")
    assert check_active_shots(
        _project([{"id": "a", "закадр": ""}], ""),
        empty_vo,
        ops=[{"op": "deactivate", "id": "a"}],
    ) == []


def test_mark_inactive_keeps_vo_text_passes_reject() -> None:
    """Неактивный шот оставляет закадр на месте. reject это не ошибка."""

    def scene(shots: list[dict]) -> dict:
        return {"сцены": [{"id": "s1", "номер": 1, "кадры": shots}]}

    active = {"id": "a", "закадр": "Первая фраза."}
    parked = {"id": "b", "закадр": "Хвост на месте."}
    before = scene([active, parked])
    marked = scene([active, {**parked, "активен": False}])
    violations = run_preservation_validators(before, marked, mode="reject")
    assert all(item.code != "vo_on_inactive" for item in violations)
    kept = marked["сцены"][0]["кадры"][1]
    assert kept["закадр"] == "Хвост на месте."
    assert "закадр_архив" not in kept

    again = copy.deepcopy(marked)
    repeated = run_preservation_validators(
        marked,
        again,
        ops=[{"op": "deactivate", "shot_id": "b", "fields": {"активен": False}}],
        mode="reject",
    )
    assert all(item.code != "vo_on_inactive" for item in repeated)
    assert again["сцены"][0]["кадры"][1]["закадр"] == "Хвост на месте."

    tail = run_preservation_validators(
        scene([{**parked, "активен": False}, active]),
        scene([active, {**parked, "активен": False}]),
        mode="reject",
    )
    assert all(item.code != "vo_on_inactive" for item in tail)


def test_participation_follows_shots_of(monkeypatch: pytest.MonkeyPatch) -> None:
    def only_first(context: dict) -> list:
        shots = context["shots"]
        return [shots[0]]

    monkeypatch.setattr(
        "app.orchestrator.validators.shot_activity.shots_of",
        only_first,
    )
    project = _project(
        [
            {"id": "a", "закадр": "Первая фраза."},
            {"id": "b", "закадр": "Вторая фраза."},
        ],
        "Первая фраза. Вторая фраза.",
    )
    violations = check_active_shots(project, copy.deepcopy(project))
    assert ("vo_on_inactive", "error", "s1") in _codes(violations)
    assert ("vo_on_inactive", "error", "b") not in _codes(violations)
