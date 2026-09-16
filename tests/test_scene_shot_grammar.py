"""Грамматика сцены → кадры: цепь действия, таблица камеры, 13–80."""

from app.services.scene_shot_grammar import (
    SHOT_VO_IDEAL,
    SHOT_VO_MAX,
    SHOT_VO_MIN,
    apply_grammar_to_ops,
    bits_cover_vo,
    camera_pack,
    classify_object,
    expand_action_to_shots,
    fill_bit_spans,
    has_visible_verb,
    merge_same_place_scenes,
    object_matches_step,
    shots_grammar_reason,
    split_scene_action,
    visible_len,
)
from app.services.scene_shot_grammar import _split_vo_for_steps


def test_split_and_classify() -> None:
    steps = split_scene_action("вошёл → сел к столу → открыл папку")
    assert steps == ["вошёл", "сел к столу", "открыл папку"]
    assert classify_object("вошёл") == "место"
    assert classify_object("сел к столу") == "тело"
    assert classify_object("открыл папку") == "предмет"
    assert object_matches_step("открыл папку", "предмет")
    assert not object_matches_step("открыл папку", "двое")
    assert has_visible_verb("вошёл, сел")
    assert has_visible_verb("стоят у перил подъезда")
    assert has_visible_verb("мать встала между ним и дверью")
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
    later_place = [
        s for s in shots[1:]
        if s.get("объект") == "место" and s.get("место") == shots[0].get("место")
    ]
    assert all(s.get("план") != "ОБЩИЙ" or int(s.get("линза_мм") or 0) != 24 for s in later_place)
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


def test_apply_grammar_fills_empty_shots() -> None:
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
    shots = ops[0]["fields"]["кадры"]
    assert len(shots) >= 1
    assert all(s.get("линза_мм") for s in shots)
    glued = " ".join(s["закадр"] for s in shots)
    assert "вошёл" in glued.lower()
    assert SHOT_VO_MIN >= 13


def test_long_vo_gets_more_shots() -> None:
    vo = (
        "Для окружающих семья выглядела тяжёлой, конфликтной и неблагополучной, "
        "однако само по себе это ещё не означало, что внутри совершаются преступления."
    )
    action = f"1. подъезд — соседи смотрят на семью\n({vo})"
    shots = expand_action_to_shots(action, cell_number=1)
    assert len(shots) >= 2
    assert all(SHOT_VO_MIN <= visible_len(s["закадр"]) <= 80 for s in shots)
    glued = " ".join(s["закадр"] for s in shots)
    assert " ".join(glued.split()) == " ".join(vo.split())
    stems = [s["действие"] for s in shots]
    assert len(stems) == len(set(stems))
    assert shots[0]["parent_id"] is None
    assert shots_grammar_reason(shots, vo) is None


def test_new_place_parent_is_null() -> None:
    action = (
        "1. квартира — стоит у матери → отец идёт к стене\n"
        "(Александр стоит у матери, отец идёт к стене.)\n"
        "2. подъезд — соседи смотрят на семью\n"
        "(Соседи смотрят на семью в подъезде.)"
    )
    shots = expand_action_to_shots(action, cell_number=1)
    ops = [{"frame_uuid": "u1", "fields": {"главное_действие": action, "кадры": shots}}]
    apply_grammar_to_ops(
        ops,
        [{"uuid": "u1", "number": 1, "main_action": action, "voiceover_text": ""}],
    )
    shots = ops[0]["fields"]["кадры"]
    places: dict[str, list] = {}
    for s in shots:
        places.setdefault(s["место"], []).append(s)
    assert shots[0]["parent_id"] is None
    pod = places["подъезд"][0]
    assert pod["parent_id"] is None


def test_split_vo_prefers_sentence_then_comma() -> None:
    vo = (
        "Алекса́ндр Спеси́вцев, сын Людми́лы Спеси́вцевой, рос в семье, "
        "где мать занимала центральное место. Она защищала его от внешнего мира, "
        "принимала решения за него и всё сильнее замыкала семейную жизнь "
        "вокруг себя и сына."
    )
    parts = _split_vo_for_steps(vo, 6)
    glued = " ".join(parts)
    assert " ".join(glued.split()) == " ".join(vo.split())
    assert all(SHOT_VO_MIN <= visible_len(p) <= SHOT_VO_MAX for p in parts)
    blob = "|".join(parts)
    assert "занимала|центральное" not in blob.replace(" ", "")
    assert not any(p.strip().casefold() in {"а", "и", "но"} for p in parts)
    for p in parts[:-1]:
        tail = p.rstrip()[-1:]
        if tail not in ".!?,:;":
            assert visible_len(p) >= SHOT_VO_MIN


def test_split_vo_keeps_a_with_next_clause() -> None:
    vo = (
        "В 1992 году Александра направили в Орло́вскую специализированную "
        "психиатрическую больницу, а в 1995 году он вернулся в Новокузне́цк."
    )
    parts = _split_vo_for_steps(vo, 4)
    glued = " ".join(parts)
    assert " ".join(glued.split()) == " ".join(vo.split())
    assert all(SHOT_VO_MIN <= visible_len(p) <= SHOT_VO_MAX for p in parts)
    assert not any(p.strip().casefold() in {"а", "а,"} for p in parts)
    assert any("1995" in p and p.strip().casefold().startswith("а ") for p in parts) or all(
        "а в 1995" in p for p in parts if "1995" in p
    )


def test_split_vo_targets_ideal_not_max() -> None:
    """13 — пол, 80 — потолок, цель ~45. Кусок < 80 не значит «оставить целиком»."""
    vo = (
        "Он вошёл в кабинет следователя, сел к столу и открыл папку с жалобой."
    )
    vis = visible_len(vo)
    assert vis < SHOT_VO_MAX
    assert vis > SHOT_VO_IDEAL
    parts = _split_vo_for_steps(vo, 1)
    glued = " ".join(parts)
    assert " ".join(glued.split()) == " ".join(vo.split())
    assert len(parts) >= 2
    lens = [visible_len(p) for p in parts]
    assert all(SHOT_VO_MIN <= x <= SHOT_VO_MAX for x in lens)
    avg = sum(lens) / len(lens)
    assert abs(avg - SHOT_VO_IDEAL) < abs(avg - SHOT_VO_MAX)
    assert abs(avg - SHOT_VO_IDEAL) < abs(avg - SHOT_VO_MIN)
    assert all(p.rstrip()[-1:] in ".!?,:;" or i == len(parts) - 1 for i, p in enumerate(parts))


def test_split_vo_rebalances_short_tail() -> None:
    """Не набивать первый кусок до 80, оставляя хвост у минимума."""
    vo = (
        "На практике ответственность оказалась разделена между больницей, "
        "семьёй и милицией."
    )
    parts = _split_vo_for_steps(vo, 1)
    glued = " ".join(parts)
    assert " ".join(glued.split()) == " ".join(vo.split())
    lens = [visible_len(p) for p in parts]
    assert all(SHOT_VO_MIN <= x <= SHOT_VO_MAX for x in lens)
    assert min(lens) > SHOT_VO_MIN + 5
    avg = sum(lens) / len(lens)
    assert abs(avg - SHOT_VO_IDEAL) < abs(avg - SHOT_VO_MAX)
    shots = [
        {
            "id": "1-K1",
            "действие": "вошёл",
            "объект": "место",
            "план": "ОБЩИЙ",
            "линза_мм": 24,
            "точка": "фронт",
            "закадр": "Он вошёл в кабинет следователя.",
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
    assert "пустой закадр" in (shots_grammar_reason(shots, "Он вошёл в кабинет следователя.") or "")
    shots[1]["закадр"] = "сел к столу и открыл папку."
    assert "склейка" in (
        shots_grammar_reason(shots, "Он вошёл в кабинет следователя, сел.") or ""
    )
