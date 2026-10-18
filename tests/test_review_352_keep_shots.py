"""П.3 ревью #352: шоты не вырезаются, пропавшие id остаются с активен False."""

from __future__ import annotations

from types import SimpleNamespace

from app.services.montage_board_frames import _rewrite_vo_cell
from app.services.montage_coverage_ops import _clear_shot_action
from app.services.montage_scene_improve import _trim_to_cap, repair_shots
from app.services.scene_plan import apply_scene_plan_cells
from app.services.scene_shot_grammar import merge_gpt_shots_with_action
from app.services.shot_templates import fill_kadry_from_catalog
from app.services.vo_shot_expand import (
    merge_flattened_coverage_members,
    promote_shots_to_vo_cells,
    relink_shot_roles_by_scene,
)


def test_trim_to_cap_marks_bridge_and_keeps_it() -> None:
    shots = [
        {"id": "a", "роль": "событие"},
        {"id": "b", "роль": "мост"},
        {"id": "c", "роль": "следствие"},
    ]
    out = _trim_to_cap(shots, 2)
    assert [row["id"] for row in out] == ["a", "b", "c"]
    assert out[1]["активен"] is False
    assert out[0].get("активен") is not False
    assert out[2].get("активен") is not False


def test_repair_shots_marks_duplicate_stem() -> None:
    shots = [
        {
            "id": "1-S1-K1",
            "якорь_n": 1,
            "действие": "идёт к двери",
            "объект": "тело",
            "план": "СРЕДНИЙ",
            "ракурс": "фронт",
            "движение": "статика",
            "стык": "cut",
            "закадр": "Он идёт к двери.",
        },
        {
            "id": "1-S1-K2",
            "якорь_n": 1,
            "действие": "идёт к двери",
            "объект": "тело",
            "план": "КРУПНЫЙ",
            "ракурс": "сбоку",
            "движение": "статика",
            "стык": "cut",
            "закадр": "Он идёт к двери.",
        },
    ]
    units = [{"n": 1, "закадр": "Он идёт к двери.", "бюджет": 2}]
    repair_shots(shots, units)
    assert len(shots) == 2
    marked = [row for row in shots if row.get("активен") is False]
    assert marked
    assert all(row.get("id") for row in shots)


def test_merge_gpt_over_limit_keeps_tail() -> None:
    action = "стоит у двери"
    shots = [
        {"id": "1-K1", "действие": "стоит у двери", "закадр": "Он стоит у двери."},
        {"id": "1-K2", "действие": "смотрит в сторону", "закадр": "Потом смотрит."},
        {"id": "1-K3", "действие": "берёт ключ", "закадр": "Берёт ключ со стола."},
    ]
    out, err = merge_gpt_shots_with_action(shots, action, vo="Он стоит у двери.")
    assert out is not None
    assert err is None
    ids = [row.get("id") for row in out]
    assert "1-K1" in ids and "1-K2" in ids and "1-K3" in ids
    inactive = [row for row in out if row.get("активен") is False]
    assert inactive


def test_fill_kadry_keeps_unparsed_scene_id() -> None:
    out = fill_kadry_from_catalog(
        [
            {"id": "1-S1-K1", "сцена": 1, "действие": "идёт к двери"},
            {"id": "lost-me", "действие": "хвост без номера сцены"},
        ],
        "1. комната — идёт к двери",
    )
    lost = next(row for row in out if row.get("id") == "lost-me")
    assert lost["активен"] is False


def test_apply_scene_plan_keeps_non_dict_row() -> None:
    shots: list = [
        {"id": "1-S1-K1", "порядок": 1, "закадр": "Он стоит у двери."},
        "not-a-dict",
    ]
    apply_scene_plan_cells([{"key": "a", "shots": shots, "plan": None, "owned": True}])
    assert "not-a-dict" in shots
    assert any(isinstance(row, dict) and row.get("id") == "1-S1-K1" for row in shots)


def test_clear_shot_action_marks_kadry() -> None:
    frame = SimpleNamespace(
        number=4,
        attrs={"кадры": [{"id": "4-K1", "действие": "старое"}], "shots": [{"id": "4-K2"}]},
    )
    _clear_shot_action(frame)
    assert frame.attrs["кадры"][0]["id"] == "4-K1"
    assert frame.attrs["кадры"][0]["активен"] is False
    assert frame.attrs["shots"][0]["активен"] is False


def test_rewrite_vo_cell_keeps_old_shot_id() -> None:
    parent = SimpleNamespace(
        number=3,
        voiceover_text="Короткий текст.",
        attrs={"кадры": [{"id": "old-K7", "закадр": "был"}]},
    )
    member = SimpleNamespace(number=3, voiceover_text="Короткий текст.", attrs={})
    _rewrite_vo_cell(parent, [member])
    ids = [row["id"] for row in parent.attrs["кадры"]]
    assert "3-K1" in ids
    assert "old-K7" in ids
    old = next(row for row in parent.attrs["кадры"] if row["id"] == "old-K7")
    assert old["активен"] is False


def test_merge_flattened_keeps_missing_shot() -> None:
    parent = SimpleNamespace(
        uuid="p" * 12,
        number=1,
        voiceover_text="Первая.",
        duration_seconds=0,
        attrs={"кадры": [{"id": "1-K1"}, {"id": "1-K9", "закадр": "хвост"}]},
    )
    child = SimpleNamespace(
        uuid="c" * 12,
        number=2,
        voiceover_text="Вторая.",
        duration_seconds=0,
        attrs={"кадры": [{"id": "1-K2"}]},
    )
    merge_flattened_coverage_members([parent, child])
    lost = next(row for row in parent.attrs["кадры"] if row["id"] == "1-K9")
    assert lost["активен"] is False


def test_promote_child_merges_old_ladder_and_keeps_empty() -> None:
    pu = "p" * 12
    full = "Первая фраза целиком здесь. Вторая фраза тоже длинная достаточно."
    parent = SimpleNamespace(
        uuid=pu,
        number=1,
        voiceover_text=full,
        image_prompt="",
        animation_prompt="",
        duration_seconds=0,
        attrs={
            "vo_cell_full": full,
            "camera_subdivide": {
                "role": "vo_parent",
                "parent_uuid": pu,
                "shot_index": 1,
            },
            "кадры": [
                {"id": "1-K1", "сцена": 1, "закадр": "Первая фраза целиком здесь."},
                {"id": "1-K2", "сцена": 1, "закадр": "Вторая фраза тоже длинная достаточно."},
            ],
        },
    )
    child = SimpleNamespace(
        uuid="c" * 12,
        number=2,
        voiceover_text="",
        image_prompt="",
        animation_prompt="",
        duration_seconds=0,
        attrs={
            "camera_subdivide": {"role": "shot", "parent_uuid": pu, "shot_index": 2},
            "кадры": [
                {"id": "1-K2", "закадр": "старый"},
                {"id": "1-K9", "закадр": "хвост который нельзя потерять здесь"},
            ],
        },
    )
    promote_shots_to_vo_cells([parent, child])
    lost = next(row for row in child.attrs["кадры"] if row.get("id") == "1-K9")
    assert lost["активен"] is False

    parent.attrs["кадры"] = [
        {"id": "1-K1", "сцена": 1, "закадр": "Первая фраза целиком здесь."},
        {"сцена": 1, "закадр": "Вторая фраза тоже длинная достаточно."},
    ]
    parent.attrs["camera_subdivide"] = {
        "role": "vo_parent",
        "parent_uuid": pu,
        "shot_index": 1,
    }
    bare = SimpleNamespace(
        uuid="d" * 12,
        number=2,
        voiceover_text="",
        image_prompt="",
        animation_prompt="",
        duration_seconds=0,
        attrs={
            "camera_subdivide": {"role": "shot", "parent_uuid": pu, "shot_index": 2},
            "кадры": [{"id": "keep-old", "закадр": "уже лежало на дочернем кадре"}],
        },
    )
    promote_shots_to_vo_cells([parent, bare])
    assert any(row.get("id") == "keep-old" for row in bare.attrs["кадры"])


def test_relink_keeps_shot_missing_from_new_ladder() -> None:
    first = SimpleNamespace(
        uuid="a" * 12,
        number=1,
        sort_key=1.0,
        voiceover_text="Первая фраза длинная.",
        attrs={
            "place": "комната",
            "кадры": [
                {"id": "1-K1", "место": "комната"},
                {"id": "1-K9", "место": "комната", "закадр": "хвост"},
            ],
        },
    )
    second = SimpleNamespace(
        uuid="b" * 12,
        number=2,
        sort_key=2.0,
        voiceover_text="Вторая фраза длинная.",
        attrs={"place": "комната", "кадры": [{"id": "1-K2", "место": "комната"}]},
    )
    relink_shot_roles_by_scene([first, second], resplit_places=True)
    ids = [row.get("id") for row in first.attrs["кадры"]]
    assert "1-K9" in ids
    lost = next(row for row in first.attrs["кадры"] if row.get("id") == "1-K9")
    assert lost["активен"] is False
