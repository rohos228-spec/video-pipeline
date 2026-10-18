"""Ячейка не обнуляет кадры[], если id не уехали в ту же сцену."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from app.orchestrator.validators.frames_cleared import check_frames_cleared
from app.orchestrator.validators.preservation import PreservationRejected, run_preservation_validators


def _cells(*rows: dict) -> dict:
    return {"cells": list(rows)}


def _ids(violations: list) -> set[str]:
    return {item.id for item in violations if item.code == "frames_cleared"}


def test_same_list_passes() -> None:
    shots = [{"id": "k1", "сцена": 1, "закадр": "а"}, {"id": "k2", "сцена": 1, "закадр": "б"}]
    before = _cells({"id": "c", "сцена": "1", "кадры": shots})
    after = _cells({"id": "c", "сцена": "1", "кадры": copy.deepcopy(shots)})
    frozen = copy.deepcopy((before, after))
    assert check_frames_cleared(before, after) == []
    assert (before, after) == frozen


def test_shorter_list_without_transfer_is_cleared() -> None:
    before = _cells(
        {
            "id": "child",
            "сцена": "1",
            "кадры": [
                {"id": "k2", "сцена": 1},
                {"id": "k3", "сцена": 1},
                {"id": "k4", "сцена": 1},
            ],
        }
    )
    after = _cells({"id": "child", "сцена": "1", "кадры": [{"id": "k2", "сцена": 1}]})
    lost = check_frames_cleared(before, after)
    assert _ids(lost) == {"k3", "k4"}
    assert all(item.severity == "error" and item.code == "frames_cleared" for item in lost)

    empty = _cells({"id": "child", "сцена": "1", "кадры": []})
    assert _ids(check_frames_cleared(before, empty)) == {"k2", "k3", "k4"}


def test_transfer_inside_scene_and_inactive_pass() -> None:
    before = _cells(
        {"id": "parent", "сцена": "1", "кадры": [{"id": "k1", "сцена": 1}]},
        {
            "id": "child",
            "сцена": "1",
            "кадры": [{"id": "k2", "сцена": 1}, {"id": "k3", "сцена": 1}],
        },
    )
    moved = _cells(
        {
            "id": "parent",
            "сцена": "1",
            "кадры": [{"id": "k1", "сцена": 1}, {"id": "k3", "сцена": 1}],
        },
        {"id": "child", "сцена": "1", "кадры": [{"id": "k2", "сцена": 1}]},
    )
    assert check_frames_cleared(before, moved) == []

    other_scene = _cells(
        {"id": "parent", "сцена": "2", "кадры": [{"id": "k1", "сцена": 2}, {"id": "k3", "сцена": 2}]},
        {"id": "child", "сцена": "1", "кадры": [{"id": "k2", "сцена": 1}]},
    )
    assert "k3" in _ids(check_frames_cleared(before, other_scene))

    inactive = _cells(
        {
            "id": "child",
            "сцена": "1",
            "кадры": [
                {"id": "k2", "сцена": 1},
                {"id": "k3", "сцена": 1, "активен": False},
            ],
        }
    )
    left = _cells({"id": "child", "сцена": "1", "кадры": [{"id": "k2", "сцена": 1}]})
    assert check_frames_cleared(inactive, left) == []


def test_idless_shot_loss_is_frames_cleared() -> None:
    """Шот без id, пропавший из списка, — frames_cleared, не тихий пропуск."""

    kept = {"id": "k1", "закадр": "а"}
    lost_shot = {"id": "", "parent_id": "9-K1", "закадр": "кусок"}

    def pair(key: str) -> tuple[dict, dict]:
        if key == "cells":
            before = {"cells": [{"id": "c", "сцена": "1", "кадры": [kept, lost_shot]}]}
            after = {"cells": [{"id": "c", "сцена": "1", "кадры": [kept]}]}
            return before, after
        before = {"frames": [{"id": "u1", "attrs": {"кадры": [kept, lost_shot]}}]}
        after = {"frames": [{"id": "u1", "attrs": {"кадры": [kept]}}]}
        return before, after

    for key in ("cells", "frames"):
        before, after = pair(key)
        with pytest.raises(PreservationRejected) as raised:
            run_preservation_validators(before, after, mode="reject")
        assert any(
            item.code == "frames_cleared" and item.id == "кусок"
            for item in raised.value.violations
        )


def test_non_list_is_not_frames_cleared() -> None:
    before = _cells({"id": "child", "сцена": "1", "кадры": [{"id": "k1", "сцена": 1}]})
    after = _cells({"id": "child", "сцена": "1", "кадры": "нет"})
    assert check_frames_cleared(before, after) == []


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


def _scene_of(cells: dict, fid: str) -> str | None:
    for cell in cells.get("cells") or []:
        shots = cell.get("кадры")
        if not isinstance(shots, list):
            continue
        if any(isinstance(shot, dict) and str(shot.get("id")) == fid for shot in shots):
            return str(cell.get("сцена") or "")
    return None


def _assert_cleared_or_kept(before: dict, after: dict) -> None:
    """Пропажа — нарушение, активен False, или кадр уехал в ту же сцену."""

    caught = _ids(check_frames_cleared(before, after))
    inactive = _inactive_ids(after)
    silent: list[str] = []
    for cell in before.get("cells") or []:
        shots = cell.get("кадры")
        if not isinstance(shots, list):
            continue
        scene = str(cell.get("сцена") or "")
        cid = str(cell.get("id") or "")
        after_cell = next(
            (item for item in after.get("cells") or [] if str(item.get("id") or "") == cid),
            None,
        )
        after_shots = after_cell.get("кадры") if isinstance(after_cell, dict) else None
        if not isinstance(after_shots, list):
            continue
        kept = {
            str(shot.get("id"))
            for shot in after_shots
            if isinstance(shot, dict) and shot.get("id") not in (None, "")
        }
        for shot in shots:
            if not isinstance(shot, dict) or shot.get("активен") is False:
                continue
            fid = str(shot.get("id") or "")
            if not fid or fid in kept or fid in caught or fid in inactive:
                continue
            if _scene_of(after, fid) == scene:
                continue
            silent.append(fid)
    assert silent == []


def _snap(frames: list[Any]) -> dict:
    cells = []
    for frame in frames:
        attrs = getattr(frame, "attrs", None) or {}
        sub = attrs.get("camera_subdivide") if isinstance(attrs, dict) else {}
        sub = sub if isinstance(sub, dict) else {}
        cells.append(
            {
                "id": getattr(frame, "uuid", ""),
                "сцена": str(sub.get("сцена") or ""),
                "кадры": copy.deepcopy(attrs.get("кадры")) if isinstance(attrs, dict) else None,
            }
        )
    return {"cells": cells}


def test_promote_clears_child_or_keeps_shot() -> None:
    from types import SimpleNamespace

    from app.services.vo_shot_expand import promote_shots_to_vo_cells

    parent_uuid = "a" * 24
    ladder = [
        {"id": "1-K1", "сцена": 1, "план": "ОБЩИЙ", "действие": "вошёл"},
        {"id": "1-K2", "сцена": 1, "план": "СРЕДНИЙ", "действие": "открыл"},
    ]

    def frame(number: int, uuid: str, role: str, index: int, kadry: list[dict], text: str) -> SimpleNamespace:
        return SimpleNamespace(
            number=number,
            uuid=uuid,
            voiceover_text=text,
            image_prompt="",
            animation_prompt="",
            duration_seconds=None,
            status=None,
            attrs={
                "кадры": kadry,
                "vo_cell_full": "вошёл и открыл дверь",
                "camera_subdivide": {
                    "role": role,
                    "parent_uuid": parent_uuid,
                    "shot_index": index,
                    "сцена": 1,
                },
            },
        )

    parent = frame(1, parent_uuid, "vo_parent", 1, [dict(item) for item in ladder], "вошёл и открыл дверь")
    child = frame(
        2,
        "b" * 24,
        "shot",
        2,
        [
            dict(ladder[1]),
            {"id": "1-K3", "сцена": 1, "план": "КРУПНЫЙ", "действие": "смотрит"},
            {"id": "1-K4", "сцена": 1, "план": "ДЕТАЛЬ", "действие": "молчит"},
        ],
        "кусок",
    )
    frames = [parent, child]
    before = _snap(frames)
    promote_shots_to_vo_cells(frames)
    _assert_cleared_or_kept(before, _snap(frames))


def test_sync_qc_clears_mirror_or_keeps_shot() -> None:
    from types import SimpleNamespace

    from app.models import FrameStatus
    from app.services.vo_shot_expand import sync_qc_kadry_onto_coverage_frames

    pu = "c" * 24
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
        "d" * 24,
        "shot",
        2,
        [
            dict(ladder[1]),
            {"id": "1-S1-K4", "сцена": 1, "план": "ДЕТАЛЬ", "действие": "лишний"},
            {"id": "1-S1-K5", "сцена": 1, "план": "ДЕТАЛЬ", "действие": "ещё"},
        ],
    )
    frames = [parent, child]
    before = _snap(frames)
    sync_qc_kadry_onto_coverage_frames(frames)
    _assert_cleared_or_kept(before, _snap(frames))


def test_detach_replica_clears_or_keeps_shot() -> None:
    from types import SimpleNamespace

    from app.models import FrameStatus
    from app.services.vo_shot_expand import detach_duplicate_vo_replica_cells

    vo = "открытие ролика тем же полным закадром"
    p1, p2 = "e" * 24, "f" * 24

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

    first = frame(1, p1, "vo_parent", p1, 1, [{"id": "1-K1", "сцена": 1, "действие": "а", "закадр": vo}], vo)
    dup = frame(
        9,
        p2,
        "vo_parent",
        p2,
        1,
        [
            {"id": "9-K1", "сцена": 9, "действие": "а", "закадр": vo},
            {"id": "9-K2", "сцена": 9, "действие": "б", "закадр": ""},
            {"id": "9-K3", "сцена": 9, "действие": "в", "закадр": ""},
        ],
        vo,
    )
    child = frame(
        10,
        "1" * 24,
        "shot",
        p2,
        2,
        [
            {"id": "9-K2", "сцена": 9, "действие": "б"},
            {"id": "9-K4", "сцена": 9, "действие": "г"},
        ],
        vo,
    )
    frames = [first, dup, child]
    before = _snap(frames)
    detach_duplicate_vo_replica_cells(frames)
    _assert_cleared_or_kept(before, _snap(frames))
