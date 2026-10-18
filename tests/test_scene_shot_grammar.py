"""Грамматика сцены → кадры: цепь действия, таблица камеры, закадр по шагу."""

from app.services.scene_shot_grammar import (
    SHOT_VO_MIN,
    align_shots_to_action_chain,
    apply_grammar_to_ops,
    bits_cover_vo,
    camera_pack,
    classify_object,
    expand_action_to_shots,
    fill_bit_spans,
    fill_kadry_scene_numbers,
    has_visible_verb,
    merge_same_place_scenes,
    object_matches_step,
    scene_script_text,
    shots_grammar_reason,
    split_scene_action,
)


def test_merge_trivial_shots_does_not_revive_inactive_head() -> None:
    """Неактивный шот не становится первым живым кадром после склейки соседей."""
    from app.services.frame_tombstones import shot_is_active
    from app.services.scene_shot_grammar import merge_trivial_shots

    dead = {
        "id": "dead",
        "место": "кухня",
        "кто": "c01",
        "действие": "c01 продолжает архивный шаг",
        "план": "средний",
        "ракурс": "фронт",
        "объект": "стол",
        "закадр": "Архив.",
        "активен": False,
        "карантин": True,
    }
    shots = [
        dead,
        {
            "id": "s1",
            "место": "кухня",
            "кто": "c01",
            "действие": "c01 сидит за столом",
            "план": "средний",
            "ракурс": "фронт",
            "объект": "стол",
            "закадр": "Он сидит молча.",
        },
        {
            "id": "s2",
            "место": "кухня",
            "кто": "c01",
            "действие": "c01 продолжая держать чашку",
            "план": "средний",
            "ракурс": "фронт",
            "объект": "стол",
            "закадр": "",
        },
    ]
    notes = merge_trivial_shots(shots)
    assert notes
    assert shot_is_active(shots[0])
    assert shots[0]["id"] == "s1"
    by_id = {shot["id"]: shot for shot in shots}
    assert by_id["dead"]["id"] == "dead"
    assert by_id["dead"]["активен"] is False
    assert by_id["dead"]["закадр"] == "Архив."
    assert "архивный" not in by_id["s1"]["действие"]
    assert shot_is_active(by_id["dead"]) is False


def test_merge_trivial_shots_keeps_absorbed_id_inactive() -> None:
    """Склеенный кадр не выпадает: его id остаётся в хвосте с активен: False."""
    from app.services.scene_shot_grammar import merge_trivial_shots

    shots = [
        {
            "id": "s1",
            "место": "кухня",
            "кто": "c01",
            "действие": "c01 сидит за столом",
            "план": "средний",
            "ракурс": "фронт",
            "объект": "стол",
            "закадр": "Он сидит молча.",
        },
        {
            "id": "s2",
            "место": "кухня",
            "кто": "c01",
            "действие": "c01 продолжая держать чашку",
            "план": "средний",
            "ракурс": "фронт",
            "объект": "стол",
            "закадр": "",
        },
    ]
    notes = merge_trivial_shots(shots)
    assert notes
    by_id = {shot["id"]: shot for shot in shots}
    assert "s1" in by_id and "s2" in by_id
    assert by_id["s1"].get("активен") is not False
    assert "чашку" in by_id["s1"]["действие"]
    assert "Он сидит молча." in by_id["s1"]["закадр"]
    assert by_id["s2"]["активен"] is False
    assert by_id["s2"]["id"] == "s2"


def test_split_and_classify() -> None:
    steps = split_scene_action("вошёл → сел к столу → открыл папку")
    assert steps == ["вошёл", "сел к столу", "открыл папку"]
    assert classify_object("вошёл") == "место"
    assert classify_object("сел к столу") == "тело"
    assert classify_object("открыл папку") == "предмет"
    assert object_matches_step("открыл папку", "предмет")
    assert not object_matches_step("открыл папку", "двое")
    assert has_visible_verb("вошёл, сел")
    assert not has_visible_verb("узнаёт правду")


def test_bit_spans_from_anchors() -> None:
    vo = "Он вошёл в кабинет следователя, сел к столу и открыл папку с жалобой."
    bits = fill_bit_spans(
        vo,
        [
            {"порядок": 1, "изменение": "снаружи → внутри", "якорь": "Он вошёл"},
            {
                "порядок": 2,
                "изменение": "стоит → сидит",
                "якорь": "сел к столу",
            },
            {
                "порядок": 3,
                "изменение": "папка закрыта → открыта",
                "якорь": "открыл папку",
            },
        ],
    )
    assert bits_cover_vo(vo, bits)
    assert "вошёл" in bits[0]["закадр"]
    assert "открыл" in bits[-1]["закадр"]


def test_merge_same_place_and_expand() -> None:
    raw = (
        "1. кабинет следователя — вошёл\n"
        "(Он вошёл в кабинет следователя, )\n"
        "2. кабинет следователя — сел к столу → открыл папку\n"
        "(сел к столу и открыл папку с жалобой.)"
    )
    merged = merge_same_place_scenes(raw)
    assert merged.startswith("1. кабинет следователя —")
    assert merged.count("\n2.") == 0
    assert "вошёл → сел" in merged or "вошёл → сел к столу" in merged
    shots = expand_action_to_shots(merged, cell_number=5)
    assert len(shots) >= 2
    stems = [s["действие"] for s in shots]
    assert len(stems) == len(set(stems))
    assert shots[0]["parent_id"] is None
    assert shots[1]["parent_id"] == shots[0]["id"]
    assert shots[0]["линза_мм"] in (24, 35)
    if len(shots) >= 3:
        assert any(s["объект"] == "предмет" for s in shots)
    glued = " ".join(s["закадр"] for s in shots)
    assert "вошёл" in glued.lower() or "кабинет" in glued.lower()
    reason = shots_grammar_reason(shots, glued)
    assert reason is None, reason


def test_no_second_wide_same_place() -> None:
    pack_new = camera_pack(
        obj="место", place_new=True, position="вход", hod=True, prev=None
    )
    pack_old = camera_pack(
        obj="место", place_new=False, position="вход", hod=False, prev=None
    )
    assert pack_new["план"] == "ОБЩИЙ"
    assert pack_old["план"] == "СРЕДНИЙ"
    assert pack_old["линза_мм"] == 35


def test_duplicate_action_rejected() -> None:
    shots = [
        {
            "действие": "сел к столу",
            "объект": "тело",
            "план": "СРЕДНИЙ",
            "линза_мм": 50,
            "точка": "3/4",
            "закадр": "сел к столу в кабинете следователя",
        },
        {
            "действие": "сел к столу",
            "объект": "тело",
            "план": "СРЕДНИЙ",
            "линза_мм": 50,
            "точка": "3/4",
            "закадр": "ещё раз сел к столу здесь",
        },
    ]
    vo = " ".join(s["закадр"] for s in shots)
    assert shots_grammar_reason(shots, vo)


def test_apply_grammar_does_not_silently_fill_empty_shots() -> None:
    action = (
        "1. кабинет следователя — вошёл → сел к столу → открыл папку\n"
        "(Он вошёл в кабинет, сел к столу и открыл папку с жалобой.)"
    )
    ops = [{"frame_uuid": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", "fields": {}}]
    frames = [
        {
            "uuid": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "number": 1,
            "main_action": action,
            "voiceover_text": (
                "Он вошёл в кабинет, сел к столу и открыл папку с жалобой."
            ),
        }
    ]
    apply_grammar_to_ops(ops, frames)
    assert not ops[0]["fields"].get("кадры")
    assert ops[0]["fields"].get("_shots_incomplete")
    assert SHOT_VO_MIN >= 26


def test_fill_kadry_scene_numbers_from_parent_id() -> None:
    shots = [
        {"id": "f001", "parent_id": None},
        {"id": "f002", "parent_id": "f001"},
        {"id": "f003", "parent_id": "f001"},
        {"id": "f011", "parent_id": None},
        {"id": "f014", "parent_id": None},
        {"id": "f015", "parent_id": "f014"},
    ]
    filled = fill_kadry_scene_numbers(shots)
    assert filled == 6
    assert [s["сцена"] for s in shots] == [1, 1, 1, 2, 3, 3]


def test_empty_cutaway_ok_glue_still_checked() -> None:
    vo = "Он вошёл в кабинет следователя."
    shots = [
        {
            "id": "1-K1",
            "действие": "вошёл",
            "объект": "место",
            "план": "ОБЩИЙ",
            "линза_мм": 24,
            "точка": "фронт",
            "закадр": vo,
        },
        {
            "id": "1-K2",
            "действие": "сел к столу",
            "объект": "тело",
            "план": "СРЕДНИЙ",
            "линза_мм": 50,
            "точка": "3/4",
            "закадр": "",
        },
    ]
    assert shots_grammar_reason(shots, vo) is None
    shots[1]["закадр"] = "сел к столу и открыл папку."
    assert "склейка" in (
        shots_grammar_reason(shots, "Он вошёл в кабинет следователя, сел.") or ""
    )
    empty = [{**shots[0], "закадр": ""}, {**shots[1], "закадр": ""}]
    assert "пустой закадр" in (shots_grammar_reason(empty, vo) or "")


def test_align_keeps_same_place_coverage_and_drops_other_room() -> None:
    action = (
        "1. кабинет следствия — следователь открывает папку → кладёт папку на стол\n"
        "(Следователь открыл толстую следственную папку. Затем он положил её на стол рядом с делами.)"
    )
    ops = [{
        "frame_uuid": "u1",
        "fields": {"кадры": [
            {
                "id": "a", "сцена": 1, "место": "кабинет следствия",
                "действие": "следователь открывает папку",
            },
            {
                "id": "mid", "сцена": 1, "место": "кабинет следствия",
                "действие": "следователь вынимает фото из папки",
            },
            {
                "id": "b", "сцена": 1, "место": "лаборатория криминалистики",
                "действие": "криминалист снимает отпечаток в лаборатории",
            },
            {
                "id": "c", "сцена": 1, "место": "кабинет следствия",
                "действие": "кладёт папку на стол",
            },
        ]},
    }]
    frames = [{"uuid": "u1", "number": 1, "attrs": {"главное_действие": action}}]
    assert align_shots_to_action_chain(ops, frames) == 1
    rows = ops[0]["fields"]["кадры"]
    live = [s for s in rows if s.get("активен") is not False]
    actions = [s["действие"] for s in live]
    assert actions == [
        "следователь открывает папку",
        "следователь вынимает фото из папки",
        "кладёт папку на стол",
    ]
    assert "лаборатор" not in " ".join(actions)
    lab = next(s for s in rows if s["id"] == "b")
    assert lab["активен"] is False
    assert align_shots_to_action_chain(ops, frames) == 0


def test_long_vo_must_be_split_not_dumped() -> None:
    chunk = (
        "Самый страшный парадокс этой истории заключается в том, "
        "что следствие искало его по своим же правилам."
    )
    assert len(chunk) > 80
    shots = [
        {
            "id": "1-K1",
            "действие": "идёт вдоль стеллажей архива",
            "объект": "тело",
            "план": "СРЕДНИЙ",
            "линза_мм": 50,
            "точка": "3/4",
            "закадр": chunk,
        }
    ]
    reason = shots_grammar_reason(shots, chunk) or ""
    assert "симв" in reason


def test_expand_no_auto_establishing_shot_keeps_long_vo() -> None:
    from app.services.scene_shot_grammar import expand_action_to_shots

    vo = (
        "Самый страшный парадокс этой истории заключается в том, "
        "что Сергея Ткача долгие годы искали по тем же правилам."
    )
    action = (
        "1. кабинет следственной группы — группа окружает доску розыска "
        "→ следователь сверяет схему с делом\n"
        f"({vo})"
    )
    shots = expand_action_to_shots(action, cell_number=1)
    assert not any(str(s.get("действие") or "").startswith("общий вид") for s in shots)
    lens = [len(s.get("закадр") or "") for s in shots]
    assert max(lens) == len(" ".join(vo.split()))
    assert sum(lens) >= max(lens)
    text = scene_script_text(
        "кабинет следственной группы",
        "группа окружает доску → сверяет схему",
    )
    assert "Сценарий сцены" in text
    assert "общий вид" not in text


def test_vo_limits_normal_and_cutaway() -> None:
    from app.services.scene_shot_grammar import (
        SHOT_VO_CUTAWAY_MAX,
        SHOT_VO_CUTAWAY_MIN,
        SHOT_VO_MAX,
        SHOT_VO_MIN,
        is_cutaway_shot,
    )

    assert SHOT_VO_MIN == 26
    assert SHOT_VO_MAX == 80
    assert SHOT_VO_CUTAWAY_MIN == 10
    assert SHOT_VO_CUTAWAY_MAX == 30

    normal_ok = [
        {
            "действие": "вошёл в кабинет следователя",
            "объект": "место",
            "план": "ОБЩИЙ",
            "линза_мм": 24,
            "точка": "фронт",
            "закадр": "Он вошёл в кабинет следователя утром.",
        }
    ]
    assert len(normal_ok[0]["закадр"]) >= 26
    assert shots_grammar_reason(normal_ok, normal_ok[0]["закадр"]) is None

    short_chunk = "сел к столу здесь."
    long_chunk = "Он вошёл в кабинет следователя утром рано."
    assert 10 <= len(short_chunk) < 26
    assert len(long_chunk) >= 26
    too_short = [
        {
            "действие": "вошёл в кабинет",
            "объект": "место",
            "план": "ОБЩИЙ",
            "линза_мм": 24,
            "точка": "фронт",
            "закадр": long_chunk,
        },
        {
            "действие": "сел к столу",
            "объект": "тело",
            "план": "СРЕДНИЙ",
            "линза_мм": 50,
            "точка": "3/4",
            "закадр": short_chunk,
        },
    ]
    vo = long_chunk + " " + short_chunk
    reason = shots_grammar_reason(too_short, vo) or ""
    assert "симв" in reason, reason

    cutaway = [
        {
            "действие": "посмотрел в реакции",
            "объект": "лицо",
            "план": "КРУПНЫЙ",
            "роль": "перебивка",
            "линза_мм": 85,
            "точка": "фронт",
            "закадр": "посмотрел кратко.",
        }
    ]
    assert is_cutaway_shot(cutaway[0])
    assert 10 <= len(cutaway[0]["закадр"]) <= 30
    assert shots_grammar_reason(cutaway, cutaway[0]["закадр"]) is None

    too_long = [
        {
            "действие": "идёт вдоль стеллажей архива документов",
            "объект": "тело",
            "план": "СРЕДНИЙ",
            "линза_мм": 50,
            "точка": "3/4",
            "закадр": (
                "Самый страшный парадокс этой истории заключается в том, "
                "что следствие искало его по своим же правилам долго."
            ),
        }
    ]
    assert len(too_long[0]["закадр"]) > 80
    # один кадр покрывает весь VO длиннее 80 — брак (не валить весь VO)
    assert shots_grammar_reason(too_long, too_long[0]["закадр"])
