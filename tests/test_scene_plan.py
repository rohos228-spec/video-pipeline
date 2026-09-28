"""План площадки: двери, порог, ось 180°, скачок ракурса, контекст соседних ячеек."""

from __future__ import annotations

import copy

from app.services.apply_ops_batches import apply_scene_plan_ops
from app.services.scene_plan import (
    _split_vo,
    accusative,
    apply_scene_plan,
    apply_scene_plan_cells,
    needs_freeze,
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
    assert shots[0]["план"] != shots[1]["план"] or shots[1]["камера"]["где"] in {
        "запад",
        "восток",
    }
    assert any(it["вид"] in {"ракурс", "план"} for it in issues)


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


def test_plan_svg_filters_zones_and_staggers_cameras() -> None:
    import re

    from app.services.scene_plan import plan_svg

    extra = [{"id": f"лишняя {i}", "предметы": []} for i in range(20)]
    plan = {"зоны": PLAN["зоны"] + extra, "проходы": PLAN["проходы"]}
    svg = plan_svg(plan, [_street("1-S1-K1"), _street("1-S1-K4")])
    assert "лишняя 0" not in svg
    assert "улица у дома" in svg
    assert "прихожая" in svg
    assert "кухня" not in svg
    assert 'width="260"' in svg
    assert "К1↑" in svg
    assert "К4↑" in svg
    by = {
        n: (int(x), int(y))
        for x, y, n in re.findall(r'<text x="(\d+)" y="(\d+)"[^>]*>К(\d+)↑', svg)
    }
    assert by["1"] != by["4"]
    assert "север ↑" in svg


def test_plan_svg_wraps_many_zones() -> None:
    from app.services.scene_plan import plan_svg

    plan = {"зоны": [{"id": f"z{i}", "предметы": []} for i in range(12)]}
    svg = plan_svg(plan, None)
    assert 'width="650"' in svg
    assert "z0" in svg
    assert "z11" in svg


LAB_PLAN = {
    "зоны": [
        {
            "id": "кабинет криминалиста",
            "что": "рабочий кабинет с набором для осмотра следов",
            "предметы": [
                {"id": "шкаф с пакетами", "где": "запад"},
                {"id": "лампа", "где": "восток"},
                {
                    "id": "рабочий стол",
                    "где": "центр",
                    "что": "кисточки, дактилоскопический порошок, лупа, пинцет, бланки учёта",
                },
            ],
        }
    ],
    "проходы": [],
}


def test_detail_layout_names_tools_and_hides_room() -> None:
    shots = [
        {
            "id": "1-S5-K1",
            "зона": "кабинет криминалиста",
            "план": "ДЕТАЛЬ",
            "действие": "Ткач рассматривает отпечаток лупой",
            "объект": "предмет",
            "камера": {"где": "юг", "смотрит": "север"},
            "люди": [{"кто": "Ткач", "где": "юг"}],
        }
    ]
    apply_scene_plan(shots, LAB_PLAN)
    lay = shots[0]["раскладка"]
    assert "ДЕЙСТВИЕ (процесс в одном кадре):" in lay
    assert "Ткач рассматривает отпечаток лупой" in lay
    assert "жест завершён" not in lay
    assert "СТАРТ (первый кадр, до действия):" not in lay
    assert "лупа" in lay
    assert "кисточки" in lay or "лупа" in lay
    before_hidden = lay.split("Не видно")[0]
    assert "шкаф" not in before_hidden
    assert "Не видно при этой крупности: шкаф с пакетами" in lay
    assert "не общий вид" in lay


def test_wide_layout_lists_whole_room() -> None:
    shots = [
        {
            "id": "1-S5-K1",
            "зона": "кабинет криминалиста",
            "план": "ОБЩИЙ",
            "действие": "Ткач начинает входить в лабораторию",
            "объект": "место",
            "камера": {"где": "юг", "смотрит": "север"},
            "люди": [{"кто": "Ткач", "где": "юг"}],
        }
    ]
    apply_scene_plan(shots, LAB_PLAN)
    lay = shots[0]["раскладка"]
    assert "шкаф с пакетами" in lay
    assert "кисточки" in lay
    assert "Не видно" not in lay


def test_repeated_detail_plan_is_shifted() -> None:
    shots = [
        {
            "id": "1-S5-K1",
            "зона": "кабинет криминалиста",
            "план": "ДЕТАЛЬ",
            "действие": "надевает перчатки",
            "объект": "тело",
            "камера": {"где": "юг", "смотрит": "север"},
        },
        {
            "id": "1-S5-K2",
            "зона": "кабинет криминалиста",
            "план": "ДЕТАЛЬ",
            "действие": "рассматривает отпечаток",
            "объект": "предмет",
            "камера": {"где": "юг", "смотрит": "север"},
        },
    ]
    apply_scene_plan(shots, LAB_PLAN)
    assert shots[0]["план"] != shots[1]["план"]


def test_shots_ops_hook_writes_layout_and_fixes_plan() -> None:
    """Хук fw_shots/fw_qc: раскладка и смена плана пишутся в fields.кадры."""
    shots = [
        {
            "id": "1-S5-K1",
            "зона": "кабинет криминалиста",
            "план": "ДЕТАЛЬ",
            "действие": "Ткач рассматривает отпечаток лупой",
            "объект": "предмет",
            "камера": {"где": "юг", "смотрит": "север"},
            "люди": [{"кто": "Ткач", "где": "юг"}],
        },
        {
            "id": "1-S5-K2",
            "зона": "кабинет криминалиста",
            "план": "ДЕТАЛЬ",
            "действие": "Ткач смотрит на тот же отпечаток",
            "объект": "предмет",
            "камера": {"где": "юг", "смотрит": "север"},
            "люди": [{"кто": "Ткач", "где": "юг"}],
        },
    ]
    ops = [{
        "frame_uuid": "u-lab",
        "fields": {"кадры": copy.deepcopy(shots), "площадка": LAB_PLAN},
    }]
    apply_scene_plan_ops(ops, [{"uuid": "u-lab"}])
    out = ops[0]["fields"]["кадры"]
    assert out[0]["раскладка"]
    assert "лупа" in out[0]["раскладка"]
    assert out[1]["план"] != "ДЕТАЛЬ"
    assert "шкаф" not in out[0]["раскладка"].split("Не видно")[0]


def test_plan_change_turns_camera_and_layout_has_start_end_ref() -> None:
    shots = [
        {
            "id": "1-S5-K1",
            "зона": "кабинет криминалиста",
            "план": "СРЕДНИЙ",
            "ракурс": "фронт",
            "действие": "Ткач стоит у стола",
            "старт": "стоит прямо, руки вдоль тела, лупа на столе",
            "конец": "наклонился к столу",
            "объект": "тело",
            "камера": {"где": "юг", "смотрит": "север"},
            "люди": [{"кто": "Ткач", "где": "центр"}],
        },
        {
            "id": "1-S5-K2",
            "зона": "кабинет криминалиста",
            "план": "КРУПНЫЙ",
            "ракурс": "фронт",
            "действие": "Ткач берёт лупу",
            "старт": "рука над лупой, ещё не взял",
            "конец": "лупа в руке у лица",
            "объект": "тело",
            "камера": {"где": "юг", "смотрит": "север"},
            "люди": [{"кто": "Ткач", "где": "центр"}],
        },
    ]
    apply_scene_plan(shots, LAB_PLAN)
    assert shots[1]["камера"] != shots[0]["камера"]
    assert shots[1]["ракурс"] != "фронт"
    lay0 = shots[0]["раскладка"]
    lay1 = shots[1]["раскладка"]
    assert lay0.startswith("СТАРТ (первый кадр, до действия):")
    assert "Референса нет — первый кадр места." in lay0
    assert "рука над лупой, ещё не взял" in lay1
    assert "лупа в руке у лица" in lay1
    assert "В референсе:" in lay1
    assert "Угол камеры относительно референса ≥30°." in lay1
    assert "Предметы на старте:" in lay1
    assert "рабочий стол" in lay1 or "лупа" in lay1


ARCHIVE_DESK = {
    "зоны": [
        {
            "id": "архивный стол",
            "что": "стол с папкой, портретом и служебной книгой",
            "предметы": [
                {"id": "папка Сергея Ткача", "где": "центр", "состояние": "закрыта"},
                {"id": "служебная книга", "где": "восток"},
            ],
        }
    ],
    "проходы": [],
    "люди": [{"кто": "архивист", "зона": "архивный стол", "где": "юг"}],
}


def test_put_folder_start_is_empty_table_end_has_folder() -> None:
    """Кладёт на стол: старт без папки, конец папка уже лежит с руками."""
    shots = [
        {
            "id": "1-S1-K1",
            "зона": "архивный стол",
            "план": "ОБЩИЙ",
            "действие": "архивист кладёт папку с надписью «Сергей Ткач» на стол",
            "объект": "предмет",
            "камера": {"где": "юг", "смотрит": "север"},
            "люди": [{"кто": "архивист", "где": "юг"}],
            "меняет": [{"предмет": "папка Сергея Ткача", "состояние": "лежит на столе"}],
        },
        {
            "id": "1-S1-K2",
            "зона": "архивный стол",
            "план": "СРЕДНИЙ",
            "действие": "папка уже лежит на столе, архивист открывает папку",
            "объект": "предмет",
            "камера": {"где": "восток", "смотрит": "запад"},
            "люди": [{"кто": "архивист", "где": "юг"}],
            "меняет": [{"предмет": "папка Сергея Ткача", "состояние": "открыта"}],
        },
    ]
    apply_scene_plan(shots, ARCHIVE_DESK)
    lay0 = shots[0]["раскладка"]
    lay1 = shots[1]["раскладка"]
    start0, end0 = shots[0]["старт"], shots[0]["конец"]
    start1, end1 = shots[1]["старт"], shots[1]["конец"]
    assert start0 != end0
    assert "стола без" in start0
    assert "кладёт папку" not in start0
    assert "лежит на столе" in end0
    assert "руки" in end0
    start_block = lay0.split("КОНЕЦ")[0]
    assert "В центре: папка Сергея Ткача" not in start_block
    assert "папка Сергея Ткача" in lay0.split("КОНЕЦ")[-1]
    assert start1 != end1
    assert "открывает папку" not in start1
    assert "ещё закрыта" in start1 or "не трогают" in start1
    assert "открыта" in end1
    assert shots[0]["старт"] in lay0
    assert shots[1]["конец"] in lay1
    assert "жест завершён" not in lay0
    assert "листы дела" in shots[1]["конец"] or "фотография" in shots[1]["конец"]


def test_process_shot_skips_start_end_pair() -> None:
    shot = {
        "id": "1-S1-K1",
        "зона": "архивный стол",
        "план": "СРЕДНИЙ",
        "действие": "архивист идёт вдоль стеллажей",
        "объект": "тело",
        "камера": {"где": "юг", "смотрит": "север"},
        "люди": [{"кто": "архивист", "где": "юг", "движется": "север"}],
    }
    assert needs_freeze(shot) is False
    apply_scene_plan([shot], ARCHIVE_DESK)
    lay = shot["раскладка"]
    assert lay.startswith("ДЕЙСТВИЕ (процесс в одном кадре):")
    assert "СТАРТ (первый кадр, до действия):" not in lay
    assert "жест завершён" not in lay


def test_open_folder_freeze_names_photo() -> None:
    shot = {
        "id": "1-S1-K1",
        "зона": "архивный стол",
        "план": "СРЕДНИЙ",
        "действие": "архивист открывает папку, на листе фото Ткача",
        "закадр": "на верхнем листе фотография",
        "объект": "предмет",
        "камера": {"где": "юг", "смотрит": "север"},
        "люди": [{"кто": "архивист", "где": "юг"}],
        "меняет": [{"предмет": "папка Сергея Ткача", "состояние": "открыта"}],
    }
    assert needs_freeze(shot) is True
    apply_scene_plan([shot], ARCHIVE_DESK)
    assert "фотография" in shot["конец"]
    assert "жест завершён" not in shot["раскладка"]

