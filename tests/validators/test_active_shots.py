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
    """Новая дыра в закадре ячейки — error. Та же дыра, что уже была, не блокирует."""

    before = _project(
        [
            {"id": "a", "закадр": "Первая фраза."},
            {"id": "b", "закадр": "Вторая фраза."},
        ],
        "Первая фраза. Вторая фраза.",
    )
    after = _project(
        [
            {"id": "a", "закадр": "Первая фраза."},
            {"id": "dead", "активен": False, "закадр": ""},
        ],
        "Первая фраза. Вторая фраза.",
    )
    violations = check_active_shots(before, after)
    assert _codes(violations) == [("vo_on_inactive", "error", "s1")]
    assert violations[0].path == "scenes/s1/закадр"

    same = check_active_shots(after, copy.deepcopy(after))
    assert ("vo_on_inactive", "error", "s1") not in _codes(same)


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


def test_restore_with_explicit_op_passes() -> None:
    """Явный restore/activate или флаг активен возвращает шот. Без op — отказ."""

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
    bare = check_active_shots(before, revived)
    assert ("inactive_shot_revived", "error", "dead") in _codes(bare)

    for ops in (
        [{"op": "restore", "shot_id": "dead"}],
        [{"op": "activate", "shot_id": "dead"}],
        [{"op": "update", "id": "dead", "активен": True}],
    ):
        assert check_active_shots(before, revived, ops=ops) == []
        assert run_preservation_validators(before, revived, ops, mode="reject") == []


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


def test_mark_inactive_with_scene_voiceover_passes_reject() -> None:
    """Пометка шота неактивным не рвёт закадр сцены: текст остаётся на шоте."""

    before = _project(
        [
            {"id": "a", "закадр": "Первая фраза."},
            {"id": "b", "закадр": "Хвост на месте."},
        ],
        "Первая фраза. Хвост на месте.",
    )
    marked = _project(
        [
            {"id": "a", "закадр": "Первая фраза."},
            {"id": "b", "активен": False, "закадр": "Хвост на месте."},
        ],
        "Первая фраза. Хвост на месте.",
    )
    assert run_preservation_validators(before, marked, mode="reject") == []
    assert marked["сцены"][0]["кадры"][1]["закадр"] == "Хвост на месте."


def test_mark_inactive_with_scene_voiceover_passes_soft() -> None:
    """Тот же кейс в soft: закадр сцены не даёт error и не переписывает шот."""

    before = _project(
        [
            {"id": "a", "закадр": "Первая фраза."},
            {"id": "b", "закадр": "Хвост на месте."},
        ],
        "Первая фраза. Хвост на месте.",
    )
    marked = _project(
        [
            {"id": "a", "закадр": "Первая фраза."},
            {"id": "b", "активен": False, "закадр": "Хвост на месте."},
        ],
        "Первая фраза. Хвост на месте.",
    )
    violations = run_preservation_validators(before, marked, mode="soft")
    assert all(item.severity != "error" for item in violations)
    assert all(item.code != "vo_on_inactive" for item in violations)
    assert marked["сцены"][0]["кадры"][1]["закадр"] == "Хвост на месте."


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
    assert ("vo_on_inactive", "error", "s1") not in _codes(violations)
    assert ("vo_on_inactive", "error", "b") not in _codes(violations)


def test_scene_vo_without_active_shots_flagged() -> None:
    """Сцена с закадром и только неактивными шотами — vo_on_inactive.

    Если так уже было на before, повторно не блокируем.
    """

    before = _project(
        [
            {"id": "a", "закадр": "x"},
            {"id": "b", "закадр": "y"},
        ],
        "x y",
    )
    after = _project(
        [
            {"id": "a", "активен": False, "закадр": "x"},
            {"id": "b", "активен": False, "закадр": "y"},
        ],
        "x y",
    )
    violations = check_active_shots(before, after)
    assert ("vo_on_inactive", "error", "s1") in _codes(violations)
    assert violations[0].path == "scenes/s1/закадр"

    parked = check_active_shots(after, copy.deepcopy(after))
    assert ("vo_on_inactive", "error", "s1") not in _codes(parked)


def test_restore_scoped_to_frame_does_not_revive_copy() -> None:
    """Один shot_id в двух кадрах: restore по frame_uuid оживляет только свой."""

    before = {
        "сцены": [
            {
                "id": "frame-a",
                "кадры": [{"id": "k", "активен": False, "закадр": "a"}],
            },
            {
                "id": "frame-b",
                "кадры": [{"id": "k", "активен": False, "закадр": "b"}],
            },
        ]
    }
    after = {
        "сцены": [
            {
                "id": "frame-a",
                "кадры": [{"id": "k", "активен": True, "закадр": "a"}],
            },
            {
                "id": "frame-b",
                "кадры": [{"id": "k", "активен": True, "закадр": "b"}],
            },
        ]
    }
    violations = check_active_shots(
        before,
        after,
        ops=[{"op": "restore", "shot_id": "k", "frame_uuid": "frame-a"}],
    )
    revived = {item.path for item in violations if item.code == "inactive_shot_revived"}
    assert "scenes/frame-a/кадры/k" not in revived
    assert "scenes/frame-b/кадры/k" in revived


def test_reorder_with_scene_vo_passes() -> None:
    """Перестановка живых шотов не рвёт покрытие закадра сцены."""

    before = _project(
        [
            {"id": "a", "закадр": "x"},
            {"id": "b", "закадр": "y"},
        ],
        "x y",
    )
    after = _project(
        [
            {"id": "b", "закадр": "y"},
            {"id": "a", "закадр": "x"},
        ],
        "x y",
    )
    assert run_preservation_validators(before, after, mode="reject") == []


def test_replica_inactive_keeps_part_passes() -> None:
    """Активный шот несёт «x y», неактивный повторяет часть «y» — это не error."""

    before = _project(
        [
            {"id": "a", "закадр": "x"},
            {"id": "b", "закадр": "y"},
        ],
        "x y",
    )
    after = _project(
        [
            {"id": "a", "закадр": "x y"},
            {"id": "b", "активен": False, "закадр": "y"},
        ],
        "x y",
    )
    assert run_preservation_validators(before, after, mode="reject") == []
    soft = run_preservation_validators(before, copy.deepcopy(after), mode="soft")
    assert all(item.code != "vo_on_inactive" for item in soft)
    assert all(item.severity != "error" for item in soft)


def test_soft_kept_inactive_tail_passes_with_scene_vo() -> None:
    """Soft дописывает пропавший шот в хвост и не отвергает собственный результат."""

    before = _project(
        [
            {"id": "k1", "закадр": "x"},
            {"id": "k2", "закадр": "y"},
            {"id": "k3", "закадр": "z"},
        ],
        "x y z",
    )
    after = _project(
        [
            {"id": "k1", "закадр": "x"},
            {"id": "k3", "закадр": "z"},
        ],
        "x y z",
    )
    violations = run_preservation_validators(before, after, mode="soft")
    assert all(item.severity != "error" for item in violations)
    assert any(item.code == "kept_inactive" and item.id == "k2" for item in violations)
    shots = after["сцены"][0]["кадры"]
    assert shots[-1]["id"] == "k2"
    assert shots[-1]["активен"] is False
    assert ("vo_on_inactive", "error", "s1") not in _codes(violations)


def test_inactive_shot_moved_to_tail_passes_reject_and_soft() -> None:
    """Перенос неактивного шота в хвост (#365) не даёт vo_on_inactive."""

    before = _project(
        [
            {"id": "a", "закадр": "A."},
            {"id": "b", "активен": False, "закадр": "B."},
            {"id": "c", "закадр": "C."},
        ],
        "A. B. C.",
    )
    after = _project(
        [
            {"id": "a", "закадр": "A."},
            {"id": "c", "закадр": "C."},
            {"id": "b", "активен": False, "закадр": "B."},
        ],
        "A. B. C.",
    )
    assert run_preservation_validators(before, after, mode="reject") == []
    soft = run_preservation_validators(before, copy.deepcopy(after), mode="soft")
    assert all(item.severity != "error" for item in soft)
    assert all(item.code != "vo_on_inactive" for item in soft)


def test_soft_merge_trivial_restored_shot_text_twice_passes() -> None:
    """R-MERGE-TRIVIAL скопировал закадр, soft вернул шот: повтор текста допустим."""

    before = _project(
        [
            {"id": "a", "закадр": "A."},
            {"id": "b", "закадр": "B."},
        ],
        "A. B.",
    )
    merged = _project(
        [
            {"id": "a", "закадр": "A. B."},
        ],
        "A. B.",
    )
    violations = run_preservation_validators(before, merged, mode="soft")
    assert all(item.severity != "error" for item in violations)
    assert any(item.code == "kept_inactive" and item.id == "b" for item in violations)
    shots = merged["сцены"][0]["кадры"]
    assert shots[-1]["активен"] is False
    assert shots[-1]["закадр"] == "B."
    joined = " ".join(shot["закадр"] for shot in shots)
    assert joined == "A. B. B."
