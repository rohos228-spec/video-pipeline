"""План площадки: двери, порог, ось 180°, скачок ракурса, контекст соседних ячеек."""

from __future__ import annotations

import copy

from app.services.apply_ops_batches import apply_scene_plan_ops
from app.services.scene_plan import (
    _split_vo,
    accusative,
    apply_scene_plan,
    apply_scene_plan_cells,
    notes_for_shots,
    plan_hard_reasons,
    with_plan_notes,
)

PLAN = {
    "зоны": [
        {"id": "улица у дома", "предметы": [
            {"id": "входная дверь", "где": "север", "состояние": "закрыта"},
        ]},
        {"id": "прихожая", "предметы": [
            {"id": "входная дверь", "где": "юг"},
            {"id": "дверь туалета", "где": "север", "состояние": "закрыта"},
        ]},
        {"id": "кухня", "предметы": [{"id": "окно", "где": "север"}]},
    ],
    "проходы": [{"из": "улица у дома", "в": "прихожая", "через": "входная дверь"}],
}


def _street(sid: str = "1-S1-K1") -> dict:
    return {
        "id": sid,
        "зона": "улица у дома",
        "план": "ОБЩИЙ",
        "действие": "Иван бежит по улице к открытой двери дома",
        "закадр": "Он бежал по улице, не разбирая дороги, к своему дому.",
        "камера": {"где": "юг", "смотрит": "север"},
        "люди": [{"кто": "Иван", "где": "юг", "движется": "север"}],
    }


def _hall(sid: str = "1-S2-K1") -> dict:
    return {
        "id": sid,
        "зона": "прихожая",
        "план": "СРЕДНИЙ",
        "действие": "Иван уже в прихожей, тяжело дышит",
        "закадр": "Влетел в прихожую, тяжело дыша,",
        "люди": [{"кто": "Иван"}],
    }


def _kitchen() -> list[dict]:
    people = [{"кто": "следователь", "где": "запад"}, {"кто": "мать", "где": "восток"}]
    return [
        {"id": "1-S3-K1", "зона": "кухня", "план": "СРЕДНИЙ",
         "действие": "следователь садится напротив матери",
         "закадр": "Следователь сел напротив матери",
         "камера": {"где": "юг", "смотрит": "север"}, "люди": copy.deepcopy(people)},
        {"id": "1-S3-K2", "зона": "кухня", "план": "КРУПНЫЙ",
         "действие": "мать отводит взгляд",
         "закадр": "Она отвела взгляд и долго молчала.",
         "камера": {"где": "север", "смотрит": "юг"},
         "люди": [{"кто": "мать", "где": "восток", "лицом": "запад"}]},
    ]


def test_accusative_and_vo_split_at_punctuation() -> None:
    assert accusative("входная дверь") == "входную дверь"
    assert accusative("калитка") == "калитку"
    a, b = _split_vo("Он бежал по улице, не разбирая дороги, к своему дому.")
    assert a.endswith(",")
    assert f"{a} {b}" == "Он бежал по улице, не разбирая дороги, к своему дому."


def test_closed_door_gets_threshold_shot_and_entry_is_shown() -> None:
    shots = [_street(), _hall()]
    plan, issues = apply_scene_plan(shots, PLAN)
    texts = [s["действие"] for s in shots]
    assert texts[0] == "Иван бежит по улице к закрытой двери дома"
    assert texts[1] == "Иван открывает входную дверь"
    assert texts[2] == "Иван входит через входную дверь, тяжело дышит"
    assert shots[1]["меняет"] == [{"предмет": "входная дверь", "состояние": "открыта"}]
    assert shots[2]["стык"] == "cut_on_action"
    assert [s["id"] for s in shots] == ["1-S1-K1", "1-S1-K2", "1-S2-K1"]
    assert shots[1]["parent_id"] == "1-S1-K1"
    assert "входная дверь (закрыта)" in shots[1]["раскладка"]
    assert "входная дверь (открыта)" in shots[2]["раскладка"]
    assert not plan_hard_reasons(issues)
    assert any("[1-S1-K2]" in n for n in plan["исправлено_кодом"])


def test_rerun_on_own_output_changes_nothing() -> None:
    shots = [_street(), _hall()]
    plan, _ = apply_scene_plan(shots, PLAN)
    before = copy.deepcopy(shots)
    plan2, issues = apply_scene_plan(shots, plan)
    assert issues == []
    assert shots == before
    assert plan2["исправлено_кодом"] == plan["исправлено_кодом"]


def test_axis_single_person_close_up_keeps_screen_side() -> None:
    shots = _kitchen()
    _, issues = apply_scene_plan(shots, PLAN)
    assert shots[1]["камера"] == {"где": "юг", "смотрит": "север"}
    assert "на экране справа" in shots[1]["раскладка"]
    assert any(it["вид"] == "ось_180" for it in issues)


def test_repeated_camera_and_size_is_turned_90() -> None:
    shots = _kitchen()
    shots[1]["план"] = "СРЕДНИЙ"
    shots[1]["камера"] = {"где": "юг", "смотрит": "север"}
    shots[1]["люди"] = copy.deepcopy(shots[0]["люди"])
    _, issues = apply_scene_plan(shots, PLAN)
    assert shots[1]["камера"]["где"] in {"запад", "восток"}
    assert any(it["вид"] == "ракурс" for it in issues)


def test_neighbour_cell_is_read_only_context() -> None:
    street = [_street()]
    hall = [_hall()]
    hall_before = copy.deepcopy(hall)
    cells = [
        {"key": "a", "shots": street, "plan": PLAN, "owned": True},
        {"key": "b", "shots": hall, "plan": PLAN, "owned": False},
    ]
    _, issues = apply_scene_plan_cells(cells)
    assert [s["действие"] for s in street][-1] == "Иван открывает входную дверь"
    assert hall == hall_before
    assert issues["b"] == []


def test_door_not_opened_in_read_only_cell_is_hard_issue() -> None:
    street = [_street()]
    hall = [_hall()]
    cells = [
        {"key": "a", "shots": street, "plan": PLAN, "owned": False},
        {"key": "b", "shots": hall, "plan": PLAN, "owned": True},
    ]
    _, issues = apply_scene_plan_cells(cells)
    assert any("закрытую" in r for r in plan_hard_reasons(issues["b"]))


def test_ops_use_neighbour_frames_from_whole_context() -> None:
    frames = [
        {"uuid": "u1", "кадры": [_street()], "площадка": PLAN},
        {"uuid": "u2", "attrs": {"кадры": [_hall()], "площадка": PLAN}},
        {"uuid": "u3", "coverage_role": "child", "кадры": [_hall("1-S2-K2")]},
    ]
    ops = [{"frame_uuid": "u1", "fields": {"кадры": [_street()]}}]
    fixed, hard = apply_scene_plan_ops(ops, frames)
    shots = ops[0]["fields"]["кадры"]
    assert len(shots) == 2
    assert shots[1]["действие"] == "Иван открывает входную дверь"
    assert not hard
    assert all(f.startswith("uuid u1") for f in fixed)


def test_notes_follow_their_shots_after_split() -> None:
    notes = ["кадр 1 [1-S1-K1]: a", "кадр 6 [1-S3-K2]: b", "старое без id"]
    assert notes_for_shots(notes, {"1-S3-K2"}) == ["кадр 6 [1-S3-K2]: b", "старое без id"]
    out = with_plan_notes(
        {"зоны": []}, {"исправлено_кодом": notes}, [], [{"id": "1-S1-K1"}]
    )
    assert out["исправлено_кодом"] == ["кадр 1 [1-S1-K1]: a", "старое без id"]


def test_improve_scene_sees_next_scene_door() -> None:
    from app.services.montage_scene_improve import stage_scene_plan

    shots = [_street()]
    nodes = [{"node": "fw_qc", "status": "ok", "note": "qc"}]
    after = [{"key": "next", "shots": [_hall()], "plan": PLAN, "owned": False}]
    plan = stage_scene_plan(shots, PLAN, nodes, after=after)
    assert shots[-1]["действие"] == "Иван открывает входную дверь"
    assert nodes[0]["status"] == "fixed"
    assert "площадка:" in nodes[0]["note"]
    assert any("[1-S1-K2]" in n for n in plan["исправлено_кодом"])
