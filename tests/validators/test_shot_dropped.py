"""Шот исчез из сцены без ``активен: False`` — shot_dropped."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from app.orchestrator.validators.preservation import PreservationRejected, run_preservation_validators
from app.orchestrator.validators.shot_dropped import check_shot_dropped


def _scene(shots: list[dict]) -> dict:
    return {"сцены": [{"id": "s1", "номер": 1, "кадры": shots}]}


def _shot(sid: str, place: str, **extra: object) -> dict:
    row: dict[str, object] = {
        "id": sid,
        "сцена": 1,
        "место": place,
        "действие": "сидит",
    }
    row.update(extra)
    return row


def _inactive_ids(node: Any) -> set[str]:
    found: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            if value.get("активен") is False and value.get("id") not in (None, ""):
                found.add(str(value["id"]))
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(node)
    return found


def test_kept_shot_passes() -> None:
    shots = [_shot("a", "кабинет"), _shot("b", "кабинет")]
    before = _scene(shots)
    after = _scene(copy.deepcopy(shots))
    frozen = copy.deepcopy((before, after))
    assert check_shot_dropped(before, after) == []
    assert (before, after) == frozen


def test_shot_missing_without_inactive_flag_is_dropped() -> None:
    before = _scene([_shot("a", "кабинет"), _shot("b", "лаборатория")])
    after = _scene([_shot("a", "кабинет")])
    violations = check_shot_dropped(before, after)
    assert [(item.code, item.severity, item.id, item.path) for item in violations] == [
        ("shot_dropped", "error", "b", "scenes/1/кадры/b"),
    ]


def test_inactive_shot_may_leave_or_stay() -> None:
    before = _scene([_shot("a", "кабинет"), _shot("b", "лаборатория", активен=False)])
    after = _scene([_shot("a", "кабинет")])
    assert check_shot_dropped(before, after) == []

    stayed = _scene([_shot("a", "кабинет"), _shot("b", "лаборатория", активен=False)])
    assert check_shot_dropped(before, stayed) == []


def test_align_foreign_place_is_violation_or_inactive() -> None:
    """Настоящая align_shots_to_action_chain. После #365 шот может остаться неактивным."""

    from app.services.scene_shot_grammar import align_shots_to_action_chain

    action = (
        "1. кабинет следствия — следователь открывает папку\n"
        "(Следователь открыл папку.)"
    )
    ops = [
        {
            "frame_uuid": "u1",
            "fields": {
                "кадры": [
                    {
                        "id": "a",
                        "сцена": 1,
                        "место": "кабинет следствия",
                        "действие": "следователь открывает папку",
                    },
                    {
                        "id": "b",
                        "сцена": 1,
                        "место": "лаборатория криминалистики",
                        "действие": "криминалист снимает отпечаток",
                    },
                ]
            },
        }
    ]
    frames = [{"uuid": "u1", "number": 1, "attrs": {"главное_действие": action}}]
    before = copy.deepcopy(ops)
    align_shots_to_action_chain(ops, frames)
    violations = check_shot_dropped(before, ops)
    dropped = any(item.code == "shot_dropped" and item.id == "b" for item in violations)
    assert dropped or "b" in _inactive_ids(ops)


def _snap_cells(frames: list[Any]) -> dict:
    cells = []
    for frame in frames:
        attrs = getattr(frame, "attrs", None) or {}
        sub = attrs.get("camera_subdivide") if isinstance(attrs, dict) else {}
        sub = sub if isinstance(sub, dict) else {}
        cells.append(
            {
                "id": getattr(frame, "uuid", ""),
                "сцена": sub.get("сцена"),
                "кадры": copy.deepcopy(attrs.get("кадры")) if isinstance(attrs, dict) else None,
            }
        )
    return {"cells": cells}


def _ids_in(node: Any) -> set[str]:
    found: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            if value.get("id") not in (None, "") and "кадры" not in value:
                found.add(str(value["id"]))
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(node)
    return found


def _assert_missing_shots_are_caught(before: Any, after: Any) -> None:
    """Пропавший живой шот — нарушение, ``активен: False`` или он ещё в снимке."""

    violations = check_shot_dropped(before, after)
    caught = {item.id for item in violations if item.code == "shot_dropped"}
    inactive = _inactive_ids(after)
    before_ids = _ids_in(before)
    after_ids = _ids_in(after)
    silent = [
        fid
        for fid in sorted(before_ids - after_ids)
        if fid not in caught and fid not in inactive
    ]
    assert silent == []


def test_sync_qc_mirror_is_caught_or_kept() -> None:
    """sync_qc подменяет кадры ребёнка одним шотом. Функцию не чиним."""

    from types import SimpleNamespace

    from app.models import FrameStatus
    from app.services.vo_shot_expand import sync_qc_kadry_onto_coverage_frames

    pu = "a" * 24
    ladder = [
        {"id": "1-S1-K1", "сцена": 1, "план": "ОБЩИЙ", "действие": "вошёл"},
        {"id": "1-S1-K2", "сцена": 1, "план": "СРЕДНИЙ", "действие": "открыл"},
        {"id": "1-S1-K3", "сцена": 1, "план": "КРУПНЫЙ", "действие": "ушёл"},
    ]

    def frame(number: int, uuid: str, role: str, index: int, kadry: list[dict]) -> SimpleNamespace:
        return SimpleNamespace(
            number=number,
            uuid=uuid,
            voiceover_text="кусок",
            image_prompt="",
            status=FrameStatus.planned,
            attrs={
                "кадры": kadry,
                "camera_subdivide": {
                    "role": role,
                    "parent_uuid": pu,
                    "shot_index": index,
                    "scene_split": 1,
                    "сцена": 1,
                    "shot_id": kadry[0]["id"],
                },
            },
        )

    parent = frame(1, pu, "vo_parent", 1, [dict(item) for item in ladder])
    child = frame(
        2,
        "b" * 24,
        "shot",
        2,
        [
            dict(ladder[1]),
            {"id": "1-S1-K4", "сцена": 1, "план": "ДЕТАЛЬ", "действие": "лишний"},
            {"id": "1-S1-K5", "сцена": 1, "план": "ДЕТАЛЬ", "действие": "ещё"},
        ],
    )
    frames = [parent, child]
    before = _snap_cells(frames)
    sync_qc_kadry_onto_coverage_frames(frames)
    _assert_missing_shots_are_caught(before, _snap_cells(frames))


def test_detach_replica_is_caught_or_kept() -> None:
    """detach реплики. ``planned[:1]`` здесь не чиним: шот либо на месте, либо пойман."""

    from types import SimpleNamespace

    from app.models import FrameStatus
    from app.services.vo_shot_expand import detach_duplicate_vo_replica_cells

    vo = "открытие ролика тем же полным закадром"
    p1, p2 = "c" * 24, "d" * 24

    def frame(number: int, uuid: str, role: str, parent: str, index: int, kadry: list[dict], text: str) -> SimpleNamespace:
        return SimpleNamespace(
            number=number,
            uuid=uuid,
            voiceover_text=text,
            image_prompt="p",
            status=FrameStatus.image_prompt_ready,
            attrs={
                "кадры": kadry,
                "camera_subdivide": {
                    "role": role,
                    "parent_uuid": parent,
                    "shot_index": index,
                    "shot_id": kadry[0]["id"],
                    "сцена": 9,
                },
            },
        )

    first = frame(
        1, p1, "vo_parent", p1, 1,
        [{"id": "1-K1", "сцена": 1, "действие": "а", "закадр": vo}],
        vo,
    )
    dup = frame(
        9, p2, "vo_parent", p2, 1,
        [
            {"id": "9-K1", "сцена": 9, "действие": "а", "закадр": vo},
            {"id": "9-K2", "сцена": 9, "действие": "б", "закадр": ""},
            {"id": "9-K3", "сцена": 9, "действие": "в", "закадр": ""},
        ],
        vo,
    )
    child = frame(
        10, "e" * 24, "shot", p2, 2,
        [
            {"id": "9-K2", "сцена": 9, "действие": "б"},
            {"id": "9-K4", "сцена": 9, "действие": "г"},
        ],
        "второй кусок",
    )
    frames = [first, dup, child]
    before = _snap_cells(frames)
    detach_duplicate_vo_replica_cells(frames)
    _assert_missing_shots_are_caught(before, _snap_cells(frames))


def test_add_shot_without_vo_key_passes() -> None:
    """Нет ключа «закадр» — пустой закадр, как "". Старые шоты не shot_dropped."""

    before = {
        "сцены": [
            {
                "id": "s1",
                "номер": 1,
                "закадр": "Первая фраза. Вторая фраза.",
                "кадры": [
                    {"id": "a", "закадр": "Первая фраза."},
                    {"id": "b", "закадр": "Вторая фраза."},
                ],
            }
        ]
    }

    def with_extra(extra: dict) -> dict:
        scene = copy.deepcopy(before["сцены"][0])
        scene["кадры"] = [*scene["кадры"], extra]
        return {"сцены": [scene]}

    empty = run_preservation_validators(before, with_extra({"id": "k9", "закадр": ""}), mode="reject")
    missing = run_preservation_validators(before, with_extra({"id": "k9"}), mode="reject")
    assert empty == []
    assert missing == []
    assert all(item.code != "shot_dropped" for item in missing)


def test_attrs_kadry_wrapper_with_scene_is_not_a_shot() -> None:
    """Обёртка с ``attrs.кадры`` не шот, даже если у неё есть ``сцена``."""

    def wrapped(shots: list[dict], *, key: str) -> dict:
        return {
            key: [
                {
                    "id": "cell1",
                    "сцена": 1,
                    "attrs": {"кадры": shots},
                }
            ]
        }

    shots = [
        {"id": "k1", "закадр": "а"},
        {"id": "k2", "закадр": "б"},
    ]
    replaced = [
        {"id": "k1", "закадр": "а"},
        {"id": "k9", "закадр": "б"},
    ]
    for key in ("cells", "frames"):
        with pytest.raises(PreservationRejected) as raised:
            run_preservation_validators(wrapped(shots, key=key), wrapped(replaced, key=key), mode="reject")
        assert any(
            item.code == "shot_dropped" and item.id == "k2" for item in raised.value.violations
        )


def test_attrs_shots_and_cells_wrapper_is_not_a_shot() -> None:
    """Обёртка с ``attrs.shots`` или ``attrs.cells`` не шот, даже с ``сцена``."""

    shots = [
        {"id": "k1", "закадр": "а"},
        {"id": "k2", "закадр": "б"},
    ]
    replaced = [
        {"id": "k1", "закадр": "а"},
        {"id": "k9", "закадр": "б"},
    ]

    def wrapped(items: list[dict], *, key: str, attr_key: str) -> dict:
        return {
            key: [
                {
                    "id": "cell1",
                    "сцена": 1,
                    "attrs": {attr_key: items},
                }
            ]
        }

    for attr_key in ("shots", "cells"):
        for key in ("cells", "frames"):
            with pytest.raises(PreservationRejected) as raised:
                run_preservation_validators(
                    wrapped(shots, key=key, attr_key=attr_key),
                    wrapped(replaced, key=key, attr_key=attr_key),
                    mode="reject",
                )
            assert any(
                item.code == "shot_dropped" and item.id == "k2"
                for item in raised.value.violations
            )


def test_bare_named_shot_with_description_is_shot_dropped() -> None:
    """``{id, имя, описание}`` без ключей шота — всё равно шот. Замена того же числа — отказ."""

    before_shots = [
        {"id": "k1", "имя": "Анна", "описание": "стоит у окна"},
        {"id": "k2", "закадр": "б"},
    ]
    after_shots = [
        {"id": "k9", "закадр": "а"},
        {"id": "k2", "закадр": "б"},
    ]

    def wrapped(shots: list[dict], *, key: str) -> dict:
        if key == "cells":
            return {"cells": [{"id": "c", "сцена": "1", "кадры": shots}]}
        return {"frames": [{"id": "u1", "attrs": {"кадры": shots}}]}

    for key in ("cells", "frames"):
        with pytest.raises(PreservationRejected) as raised:
            run_preservation_validators(
                wrapped(before_shots, key=key),
                wrapped(after_shots, key=key),
                mode="reject",
            )
        assert any(
            item.code == "shot_dropped" and item.id == "k1" for item in raised.value.violations
        )


def test_add_idless_child_shot_passes() -> None:
    """Элемент без id не прячет список. reject и soft не дают shot_dropped."""

    before = {
        "сцены": [
            {
                "id": "s9",
                "номер": 9,
                "закадр": "кусок",
                "кадры": [{"id": "9-K1", "сцена": 9, "действие": "а", "закадр": "кусок"}],
            }
        ]
    }
    after = copy.deepcopy(before)
    after["сцены"][0]["кадры"].append({"id": "", "parent_id": "9-K1"})
    rejected = run_preservation_validators(before, copy.deepcopy(after), mode="reject")
    softened = run_preservation_validators(before, copy.deepcopy(after), mode="soft")
    assert rejected == []
    assert all(item.code != "shot_dropped" for item in softened)


def test_add_shot_with_name_key_passes() -> None:
    """``имя`` на новом элементе не прячет старые шоты. Реальный шот с ``имя`` виден."""

    before = {
        "сцены": [
            {
                "id": "s1",
                "номер": 1,
                "закадр": "Первая фраза. Вторая фраза.",
                "кадры": [
                    {"id": "k1", "закадр": "Первая фраза."},
                    {"id": "k2", "закадр": "Вторая фраза."},
                ],
            }
        ]
    }
    added = copy.deepcopy(before)
    added["сцены"][0]["кадры"].append({"id": "k9", "имя": "Анна"})
    got = run_preservation_validators(before, added, mode="reject")
    assert got == []
    assert all(item.code != "shot_dropped" for item in got)

    named = {
        "сцены": [
            {
                "id": "s1",
                "номер": 1,
                "закадр": "Первая фраза.",
                "кадры": [{"id": "k1", "закадр": "Первая фраза.", "имя": "Анна"}],
            }
        ]
    }
    cleared = {
        "сцены": [
            {
                "id": "s1",
                "номер": 1,
                "закадр": "Первая фраза.",
                "кадры": [],
            }
        ]
    }
    with pytest.raises(PreservationRejected) as raised:
        run_preservation_validators(named, cleared, mode="reject")
    assert any(item.code == "shot_dropped" and item.id == "k1" for item in raised.value.violations)

    wrapped_before = {
        "frames": [
            {"id": "u1", "attrs": {"кадры": [{"id": "k1", "закадр": "а"}]}},
        ]
    }
    wrapped_after = {
        "frames": [
            {"id": "u2", "attrs": {"кадры": [{"id": "k1", "закадр": "а"}]}},
        ]
    }
    try:
        wrapped = run_preservation_validators(wrapped_before, wrapped_after, mode="reject")
    except PreservationRejected as exc:
        wrapped = exc.violations
    assert all(item.code != "shot_dropped" for item in wrapped)


def _shot_dropped_ids(violations: list) -> set[str]:
    return {item.id for item in violations if item.code == "shot_dropped"}


def test_null_kadry_rejects_voiced_shots() -> None:
    """``кадры: None`` у шотов с закадром — отказ, не прятание id."""

    before = {
        "сцены": [
            {
                "id": "s1",
                "номер": 1,
                "закадр": "Первая фраза.",
                "кадры": [{"id": "k1", "закадр": "Первая фраза."}],
            }
        ]
    }
    after = copy.deepcopy(before)
    after["сцены"][0]["кадры"] = None
    with pytest.raises(PreservationRejected) as raised:
        run_preservation_validators(before, after, mode="reject")
    assert "k1" in _shot_dropped_ids(raised.value.violations)


def test_scene_row_with_number_is_not_shot_dropped() -> None:
    """Строка сцены с номером не шот: появление ``кадры`` не даёт shot_dropped."""

    uid = "03ecb474-7628-4a21-b794-fef914ca3505"
    before = {"сцены": [{"id": uid, "номер": 1}]}
    after = {
        "сцены": [
            {
                "id": uid,
                "номер": 1,
                "кадры": [{"id": "k1", "закадр": "текст"}],
            }
        ]
    }
    try:
        violations = run_preservation_validators(before, after, mode="reject")
    except PreservationRejected as exc:
        violations = exc.violations
    assert uid not in _shot_dropped_ids(violations)


def test_character_name_is_not_shot_dropped() -> None:
    """Персонаж с ``имя`` не становится shot_dropped."""

    before = {
        "сцены": [{"id": "s1", "номер": 1, "кадры": [{"id": "k1", "закадр": "а"}]}],
        "персонажи": [{"id": "c01", "имя": "Анна"}, {"id": "c02", "code": "c02", "имя": "Борис"}],
    }
    after = copy.deepcopy(before)
    after["персонажи"] = [{"id": "c01", "имя": "Анна"}]
    with pytest.raises(PreservationRejected) as raised:
        run_preservation_validators(before, after, mode="reject")
    assert any(
        item.code == "character_deleted" and item.id == "c02" for item in raised.value.violations
    )
    assert _shot_dropped_ids(raised.value.violations) == set()


def test_frames_attrs_replace_and_null_kadry_reject() -> None:
    """``frames[].attrs.кадры``: замена шота и ``кадры: None`` — отказ."""

    before = {
        "frames": [
            {
                "id": "u1",
                "attrs": {
                    "кадры": [
                        {"id": "k1", "закадр": "а"},
                        {"id": "k2", "закадр": "б"},
                    ]
                },
            }
        ]
    }
    replaced = {
        "frames": [
            {"id": "u1", "attrs": {"кадры": [{"id": "k1", "закадр": "а"}]}},
        ]
    }
    with pytest.raises(PreservationRejected) as raised:
        run_preservation_validators(before, replaced, mode="reject")
    assert "k2" in _shot_dropped_ids(raised.value.violations)

    nulled = {"frames": [{"id": "u1", "attrs": {"кадры": None}}]}
    with pytest.raises(PreservationRejected) as raised_null:
        run_preservation_validators(before, nulled, mode="reject")
    assert {"k1", "k2"} <= _shot_dropped_ids(raised_null.value.violations)
