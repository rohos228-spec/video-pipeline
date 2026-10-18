"""Неактивные шоты кадры[] не входят в подсчёт, VO, QC и рендер.

Фильтр только ``shot_is_active``. Список хранит неактивные целиком.
"""

from __future__ import annotations

import pytest
from loguru import logger


@pytest.fixture
def warn_log():
    lines: list[str] = []
    sink = logger.add(lambda message: lines.append(str(message)), level="WARNING")
    try:
        yield lines
    finally:
        logger.remove(sink)


def test_apply_grammar_splits_vo_only_onto_live_shots() -> None:
    """scene_shot_grammar.apply_grammar_to_ops: куски VO только живым."""
    from app.services.scene_shot_grammar import apply_grammar_to_ops

    dead = {"id": "dead", "закадр": "", "активен": False}
    ops = [{
        "frame_uuid": "u-vo",
        "fields": {"кадры": [
            {"id": "a", "закадр": ""},
            dead,
            {"id": "b", "закадр": ""},
        ]},
    }]
    frames = [{
        "uuid": "u-vo",
        "number": 1,
        "voiceover_text": (
            "Он вошёл в кабинет следователя и сел к столу. "
            "Затем он открыл толстую папку с жалобой горожан."
        ),
    }]
    apply_grammar_to_ops(ops, frames)
    shots = ops[0]["fields"]["кадры"]
    assert [s["id"] for s in shots] == ["a", "dead", "b"]
    assert shots[1]["закадр"] == ""
    assert shots[1]["активен"] is False
    assert shots[0]["закадр"].strip()
    assert shots[2]["закадр"].strip()
    assert shots[1] not in (shots[0], shots[2])


def test_repair_long_inactive_source_is_not_split() -> None:
    """repair_shot_vo_lengths: длинный неактивный источник не режется."""
    from app.services.scene_shot_grammar import repair_shot_vo_lengths, visible_len

    long = (
        "Первое длинное предложение про следователя в пыльном архиве. "
        "Второе длинное предложение про толстую папку на столе и сургучную печать."
    )
    assert visible_len(long) > 80
    shots = [
        {"id": "dead", "закадр": long, "удалён": True},
        {"id": "slot", "закадр": ""},
    ]
    repair_shot_vo_lengths(shots, long)
    assert shots[0]["закадр"] == long
    assert shots[1]["закадр"] == ""
    assert [s["id"] for s in shots] == ["dead", "slot"]


def test_repair_long_tail_skips_inactive_slot() -> None:
    """repair_shot_vo_lengths: хвост длинного куска не пишется в неактивный кадр."""
    from app.services.scene_shot_grammar import repair_shot_vo_lengths, visible_len

    long = (
        "Первое длинное предложение про следователя в пыльном архиве. "
        "Второе длинное предложение про толстую папку на столе и сургучную печать."
    )
    assert visible_len(long) > 80
    shots = [
        {"id": "src", "закадр": long},
        {"id": "dead", "закадр": "", "карантин": True, "активен": False},
        {"id": "slot", "закадр": ""},
    ]
    repair_shot_vo_lengths(shots, long)
    assert shots[1]["закадр"] == ""
    assert shots[1]["карантин"] is True
    assert shots[2]["закадр"].strip()
    assert shots[0]["закадр"] != long
    assert [s["id"] for s in shots] == ["src", "dead", "slot"]


def test_repair_short_inactive_source_stays() -> None:
    """repair_shot_vo_lengths: короткий неактивный закадр не переносится."""
    from app.services.scene_shot_grammar import repair_shot_vo_lengths, visible_len

    short = "Короткий кусок."
    live = "Живой кусок закадра нормальной длины остаётся."
    assert visible_len(short) < 26
    shots = [
        {"id": "dead", "закадр": short, "карантин": True},
        {"id": "live", "закадр": live},
    ]
    repair_shot_vo_lengths(shots, live)
    assert shots[0]["закадр"] == short
    assert shots[1]["закадр"] == live
    assert len(shots) == 2


def test_shots_grammar_reason_ignores_inactive() -> None:
    """shots_grammar_reason: брак неактивного кадра не валит ячейку."""
    from app.services.scene_shot_grammar import shots_grammar_reason, visible_len

    live_vo = "Он вошёл в кабинет следователя."
    assert 26 <= visible_len(live_vo) <= 80
    shots = [
        {
            "id": "live",
            "действие": "вошёл в кабинет",
            "объект": "место",
            "план": "ОБЩИЙ",
            "линза_мм": 24,
            "точка": "фронт",
            "закадр": live_vo,
        },
        {
            "id": "dead",
            "действие": "х",
            "объект": "лицо",
            "план": "КРУПНЫЙ",
            "линза_мм": 18,
            "закадр": "мало",
            "активен": False,
        },
    ]
    assert shots_grammar_reason(shots, live_vo) is None
    assert shots[1]["закадр"] == "мало"
    only_dead = [{"id": "dead", "закадр": "мало", "активен": False}]
    assert shots_grammar_reason(only_dead, live_vo) is None
    assert "пустые" in (shots_grammar_reason([], live_vo) or "")


def test_reflow_vo_does_not_rewrite_inactive_shot() -> None:
    """reflow_vo_by_sentences: неактивный кадр не слот раскладки."""
    from app.services.scene_shot_grammar import reflow_vo_by_sentences

    shots = [
        {"id": "a", "закадр": "Первое предложение."},
        {"id": "dead", "закадр": "хвост", "активен": False},
        {"id": "b", "закадр": "Второе предложение."},
    ]
    reflow_vo_by_sentences(shots, "Первое предложение. Второе предложение.")
    assert shots[1]["закадр"] == "хвост"
    assert shots[1]["активен"] is False
    assert [s["id"] for s in shots] == ["a", "dead", "b"]


def test_assign_scene_vo_skips_inactive_slots(warn_log) -> None:
    """_assign_scene_vo: куски и ранний выход считаются только по живым."""
    from app.services.shot_templates import _assign_scene_vo

    dead = {"id": "dead", "закадр": "СТАРОЕ", "активен": False, "карантин": True}
    out = _assign_scene_vo(
        [dead, {"id": "a", "закадр": ""}, {"id": "b", "закадр": ""}],
        "Первое предложение сцены. Второе предложение тоже тут.",
    )
    assert [s["id"] for s in out] == ["dead", "a", "b"]
    assert out[0]["закадр"] == "СТАРОЕ"
    assert out[0]["активен"] is False
    assert "СТАРОЕ" not in out[1]["закадр"]
    assert "СТАРОЕ" not in out[2]["закадр"]
    live_glue = f"{out[1]['закадр']} {out[2]['закадр']}"
    assert "Первое предложение" in live_glue
    assert "Второе предложение" in live_glue

    parked = [{"id": "d", "закадр": "СТАРОЕ", "активен": False}]
    same = _assign_scene_vo(parked, "Текст сцены остаётся нетронутым.")
    assert same[0]["закадр"] == "СТАРОЕ"
    assert any("нет живых" in line for line in warn_log)


def test_rebalance_shot_vo_leaves_inactive_text() -> None:
    """rebalance_shot_vo: неактивный кадр не донор и не приёмник закадра."""
    from app.services.apply_ops_batches import rebalance_shot_vo

    shots = [
        {"id": "a", "закадр": "Живой кусок остаётся здесь целиком и не растёт."},
        {"id": "dead", "закадр": "Чужой.", "реплика": "привет", "активен": False},
    ]
    rebalance_shot_vo(shots)
    assert shots[1]["закадр"] == "Чужой."
    assert "Чужой" not in shots[0]["закадр"]
    assert [s["id"] for s in shots] == ["a", "dead"]


def test_vo_sentence_problems_skips_inactive_piece() -> None:
    """vo_sentence_problems: оборванный неактивный кусок не брак ячейки."""
    from app.services.scene_shot_grammar import vo_sentence_problems

    shots = [
        {"id": "a", "закадр": "Первое предложение."},
        {"id": "dead", "закадр": "обрывок без точки", "активен": False},
        {"id": "b", "закадр": "Второе предложение."},
    ]
    assert vo_sentence_problems(
        shots, "Первое предложение. Второе предложение."
    ) == []
    assert shots[1]["закадр"] == "обрывок без точки"


def test_continuity_problems_skip_inactive_shot() -> None:
    """shots_continuity_problems: «заметил» на неактивном кадре не QC."""
    from app.services.scene_shot_grammar import shots_continuity_problems

    shots = [
        {"id": "ok", "действие": "стоит у стола", "кто": "герой", "место": "комната"},
        {
            "id": "dead",
            "действие": "заметил печать",
            "кто": "герой",
            "место": "комната",
            "активен": False,
        },
    ]
    assert shots_continuity_problems(shots) == []
    live_bad = [{"id": "bad", "действие": "заметил печать", "кто": "герой"}]
    assert shots_continuity_problems(live_bad)


def test_ensure_wide_does_not_count_inactive_speaker() -> None:
    """ensure_wide_before_dialogue: неактивная реплика не второй говорящий."""
    from app.services.scene_shot_grammar import ensure_wide_before_dialogue

    shots = [
        {"id": "1-K1", "план": "ДЕТАЛЬ", "реплика": "", "говорит": ""},
        {"id": "1-K2", "реплика": "да", "говорит": "Анна", "план": "КРУПНЫЙ"},
        {
            "id": "1-K3",
            "реплика": "нет",
            "говорит": "Борис",
            "план": "КРУПНЫЙ",
            "активен": False,
        },
    ]
    notes = ensure_wide_before_dialogue(shots)
    assert notes == []
    assert [s["id"] for s in shots] == ["1-K1", "1-K2", "1-K3"]
    assert shots[2]["активен"] is False
    assert shots[2]["говорит"] == "Борис"


def test_coverage_template_reason_ignores_inactive_duplicate() -> None:
    """coverage_template_reason: неактивный дубль действия не брак лестницы."""
    from app.services.shot_templates import coverage_template_reason

    shots = [
        {
            "сцена": 1,
            "шаблон": "T5",
            "план": "СРЕДНИЙ",
            "место": "отделение",
            "действие": "садится за стол и берёт перо",
        },
        {
            "сцена": 1,
            "шаблон": "T5",
            "план": "ДЕТАЛЬ",
            "место": "отделение",
            "действие": "садится за стол и берёт перо",
            "карантин": True,
        },
    ]
    assert coverage_template_reason(shots) is None
    assert len(shots) == 2
    assert shots[1]["карантин"] is True


def test_non_list_kadry_keeps_old_shots(warn_log) -> None:
    """{} и строка вместо списка не гасят старые шоты."""
    from app.services.frame_tombstones import merge_kadry_keep_missing

    old = [
        {"id": "a", "закадр": "живой", "активен": True},
        {"id": "b", "закадр": "без флага"},
        {"id": "c", "закадр": "скрыт", "активен": False},
    ]
    flags = [shot.get("активен", "missing") for shot in old]
    for bad in ({}, "не список"):
        got = merge_kadry_keep_missing(old, bad)
        assert got == old
        assert [shot.get("активен", "missing") for shot in got] == flags
        assert [shot.get("активен", "missing") for shot in old] == flags
    assert any("не список" in line for line in warn_log)

    assert merge_kadry_keep_missing({"id": "x"}, [{"id": "n", "закадр": "новый"}]) == [
        {"id": "n", "закадр": "новый"}
    ]
    assert merge_kadry_keep_missing("старое", "новое") == "старое"
    assert merge_kadry_keep_missing(None, None) is None
