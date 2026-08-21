"""db_apply: near-miss uuid repair + salvage обрезанного apply-ops JSON."""

from __future__ import annotations

from app.services import db_apply
from app.services.db_apply import coerce_duration_seconds


def test_coerce_duration_seconds_numeric_and_human() -> None:
    assert coerce_duration_seconds(3) == 3.0
    assert coerce_duration_seconds("3.5") == 3.5
    assert coerce_duration_seconds("3,5") == 3.5
    assert coerce_duration_seconds(None) is None
    assert coerce_duration_seconds("3 сек") is None
    assert coerce_duration_seconds("5 секунд") is None
    assert coerce_duration_seconds("5 сек.") is None


def test_repair_near_miss_frame_uuids_one_hex_typo() -> None:
    known = "abc4bdef0123456789abcdef"
    typo = "abc4ddef0123456789abcdef"  # 4b → 4d
    ops = [{"frame_uuid": typo, "fields": {"закадр": "x"}}]
    remaps = db_apply.repair_near_miss_frame_uuids(ops, [known])
    assert remaps == [(typo, known)]
    assert ops[0]["frame_uuid"] == known


def test_repair_near_miss_skips_ambiguous() -> None:
    # два known на расстоянии 1 — не чиним
    a = "aaaaaaaaaaaaaaaaaaaaaaaa"
    b = "baaaaaaaaaaaaaaaaaaaaaaa"
    c = "caaaaaaaaaaaaaaaaaaaaaaa"
    ops = [{"frame_uuid": a, "fields": {"закадр": "x"}}]
    remaps = db_apply.repair_near_miss_frame_uuids(ops, [b, c])
    assert remaps == []
    assert ops[0]["frame_uuid"] == a


def test_repair_near_miss_skips_non_hex() -> None:
    ops = [{"frame_uuid": "u1", "fields": {"закадр": "x"}}]
    remaps = db_apply.repair_near_miss_frame_uuids(ops, ["u2"])
    assert remaps == []


def test_remap_frame_number_uuids_replaces_digit_id() -> None:
    """GPT часто пишет номер кадра вместо uuid — подставляем uuid по number."""
    ops = [
        {"frame_uuid": "78", "fields": {"место": "лес"}},
        {"frame_uuid": "abc4bdef0123456789abcdef", "fields": {"место": "ok"}},
    ]
    remaps = db_apply.remap_frame_number_uuids(
        ops,
        {78: "real-uuid-of-frame-78", 1: "other"},
    )
    assert remaps == [("78", "real-uuid-of-frame-78")]
    assert ops[0]["frame_uuid"] == "real-uuid-of-frame-78"
    assert ops[1]["frame_uuid"] == "abc4bdef0123456789abcdef"


def test_remap_frame_number_skips_unknown_and_ambiguous_text() -> None:
    ops = [
        {"frame_uuid": "999", "fields": {"место": "нет"}},
        {"frame_uuid": "78a", "fields": {"место": "не число"}},
        {"frame_uuid": " 42 ", "fields": {"место": "ok"}},
    ]
    remaps = db_apply.remap_frame_number_uuids(ops, {42: "uuid-42"})
    assert remaps == [("42", "uuid-42")]
    assert ops[0]["frame_uuid"] == "999"
    assert ops[1]["frame_uuid"] == "78a"
    assert ops[2]["frame_uuid"] == "uuid-42"


def test_salvage_ops_from_partial_json_truncated() -> None:
    # нет закрывающих ] } — как при обрыве длинного ответа
    text = (
        '{"ops":['
        '{"frame_uuid":"aaa","fields":{"место":"лес"}},'
        '{"frame_uuid":"bbb","fields":{"место":"город"}},'
        '{"frame_uuid":"ccc","fields":{"место":"обрез'
    )
    ops = db_apply.salvage_ops_from_partial_json(text)
    assert [o["frame_uuid"] for o in ops] == ["aaa", "bbb"]


def test_extract_apply_ops_json_uses_partial_salvage() -> None:
    text = (
        'Вот JSON:\n{"ops":['
        '{"frame_uuid":"f1","fields":{"закадр":"раз"}},'
        '{"frame_uuid":"f2","fields":{"закадр":"два"'
    )
    data = db_apply.extract_apply_ops_json(text)
    assert data is not None
    assert data.get("_salvaged_partial") is True
    assert len(data["ops"]) == 1
    assert data["ops"][0]["frame_uuid"] == "f1"
