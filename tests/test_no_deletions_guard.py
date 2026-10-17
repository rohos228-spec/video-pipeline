"""Контракт точки входа #379 и случаи, которые шим не должен отвергать.

Тесты идут через ``app.pipeline.no_deletions_guard``. Если подхватится
модуль #379 @ 40da86ed до правки P1–P3, эти случаи станут красными.
"""

from __future__ import annotations

import copy
import inspect

import pytest

from app.pipeline.no_deletions_guard import (
    PreservationRejected,
    Violation,
    run_preservation_validators,
)
from app.services.frame_tombstones import mark_shots_inactive, merge_kadry_keep_missing


def _scene(rows, *, sid="s1", number=1, **extra):
    scene = {"id": sid, "номер": number, "кадры": rows}
    scene.update(extra)
    return {"сцены": [scene]}


def test_signature_matches_379_entry_point() -> None:
    sig = inspect.signature(run_preservation_validators)
    assert list(sig.parameters) == ["before", "after", "ops", "mode"]
    assert sig.parameters["ops"].default is None
    assert sig.parameters["mode"].kind is inspect.Parameter.KEYWORD_ONLY
    assert sig.parameters["mode"].default == "reject"
    item = Violation(code="kept_inactive", severity="warn", path="scenes/s1/кадры/k1", id="k1")
    assert item.as_dict() == {
        "code": "kept_inactive",
        "severity": "warn",
        "path": "scenes/s1/кадры/k1",
        "id": "k1",
    }
    assert item.__dataclass_fields__.keys() == {"code", "severity", "path", "id"}


def test_reject_does_not_mutate_and_soft_keeps_shot() -> None:
    before = _scene(
        [{"id": "k1", "закадр": "а"}, {"id": "k2", "закадр": "фраза на месте"}]
    )
    after = _scene([{"id": "k1", "закадр": "а"}])
    frozen = copy.deepcopy(after)
    with pytest.raises(PreservationRejected) as raised:
        run_preservation_validators(before, after)
    assert any(item.code == "frame_deleted" and item.id == "k2" for item in raised.value.violations)
    assert after == frozen

    softened = run_preservation_validators(before, after, mode="soft")
    assert any(
        item.code == "kept_inactive" and item.severity == "warn" and item.id == "k2"
        for item in softened
    )
    assert all(item.severity != "error" for item in softened)
    kept = {item["id"]: item for item in after["сцены"][0]["кадры"]}
    assert kept["k2"]["активен"] is False
    assert kept["k2"]["закадр"] == "фраза на месте"
    assert "закадр_архив" not in kept["k2"]


def test_inactive_shot_keeps_vo_in_place() -> None:
    """Пометка не уносит закадр. Хранитель такой снимок пропускает."""
    original = {"id": "k1", "закадр": "фраза на месте", "активен": True}
    marked = mark_shots_inactive([original], where="test_inactive_shot_keeps_vo_in_place")
    assert marked[0]["закадр"] == "фраза на месте"
    assert marked[0]["активен"] is False
    assert "закадр_архив" not in marked[0]
    assert original["закадр"] == "фраза на месте"
    assert "закадр_архив" not in original

    merged = merge_kadry_keep_missing(
        [
            {"id": "k1", "закадр": "фраза на месте"},
            {"id": "k2", "закадр": "вторая фраза"},
        ],
        [{"id": "k1", "закадр": "фраза на месте"}],
    )
    by_id = {row["id"]: row for row in merged}
    assert by_id["k1"]["закадр"] == "фраза на месте"
    assert by_id["k2"]["закадр"] == "вторая фраза"
    assert by_id["k2"]["активен"] is False
    assert "закадр_архив" not in by_id["k1"]
    assert "закадр_архив" not in by_id["k2"]

    before = _scene(
        [
            {"id": "k1", "закадр": "фраза на месте", "активен": False},
            {"id": "k2", "закадр": "вторая фраза"},
        ]
    )
    after = _scene([{"id": "k1", "закадр": "фраза на месте", "активен": False}])
    violations = run_preservation_validators(before, after, mode="soft")
    kept = {row["id"]: row for row in after["сцены"][0]["кадры"]}
    assert kept["k1"]["закадр"] == "фраза на месте"
    assert kept["k1"]["активен"] is False
    assert "закадр_архив" not in kept["k1"]
    assert kept["k2"]["закадр"] == "вторая фраза"
    assert kept["k2"]["активен"] is False
    assert all(item.code != "vo_on_inactive" for item in violations)
    parked = run_preservation_validators(before, before, mode="reject")
    assert all(item.severity != "error" for item in parked)
    assert all(item.code != "vo_on_inactive" for item in parked)


def test_remark_tail_park_and_restore_pass() -> None:
    inactive = {"id": "k1", "закадр": "фраза на месте", "активен": False}
    remarked = run_preservation_validators(
        _scene([dict(inactive)]),
        _scene([{**inactive, "пометка": "ещё раз"}]),
        mode="reject",
    )
    assert remarked == []

    tail = run_preservation_validators(
        _scene([dict(inactive), {"id": "k2", "закадр": "живой"}]),
        _scene([{"id": "k2", "закадр": "живой"}, dict(inactive)]),
        mode="soft",
    )
    assert tail == []

    parked = run_preservation_validators(
        _scene([{"id": "k1", "закадр": "текст"}], number=2),
        _scene([{"id": "k1", "закадр": "текст"}], number=900000),
        mode="reject",
    )
    assert parked == []

    before = _scene([{"id": "k1", "закадр": "фраза на месте", "активен": False}])
    after = _scene([{"id": "k1", "закадр": "фраза на месте", "активен": True}])
    restored = run_preservation_validators(before, after, mode="reject")
    assert restored == []
    assert after["сцены"][0]["кадры"][0]["активен"] is True
    assert all(item.code != "inactive_shot_revived" for item in restored)


def test_scene_and_character_still_reject_in_soft_mode() -> None:
    with pytest.raises(PreservationRejected) as scene_exc:
        run_preservation_validators(
            _scene([{"id": "k1"}], sid="s1"),
            _scene([{"id": "k1"}], sid="s2"),
            mode="soft",
        )
    assert any(item.code == "scene_deleted" for item in scene_exc.value.violations)

    before = {"персонажи": [{"id": "c1", "name": "Анна"}], "сцены": []}
    after = {"персонажи": [], "сцены": []}
    with pytest.raises(PreservationRejected) as char_exc:
        run_preservation_validators(before, after, mode="soft")
    assert any(item.code == "character_deleted" and item.id == "c1" for item in char_exc.value.violations)


def test_pack_soft_keeps_missing_shot_and_soft_delete_passes() -> None:
    from app.services.apply_ops_batches import _preserve_pack_ops

    uid = "aa" * 4
    chunk = [
        {
            "uuid": uid,
            "number": 2,
            "attrs": {
                "кадры": [
                    {"id": "k1", "закадр": "живой"},
                    {"id": "k2", "закадр": "фраза на месте"},
                ]
            },
        }
    ]
    ops = [{"frame_uuid": uid, "fields": {"кадры": [{"id": "k1", "закадр": "живой"}]}}]
    _preserve_pack_ops(ops, chunk)
    rows = ops[0]["fields"]["кадры"]
    assert [row["id"] for row in rows] == ["k1", "k2"]
    assert rows[1]["активен"] is False
    assert rows[1]["закадр"] == "фраза на месте"
    assert "закадр_архив" not in rows[1]

    soft_ops = [
        {
            "frame_uuid": uid,
            "fields": {
                "кадры": [
                    {"id": "k1", "закадр": "живой"},
                    {"id": "k2", "закадр": "фраза на месте", "активен": False},
                ]
            },
        }
    ]
    _preserve_pack_ops(soft_ops, chunk)
    soft_rows = soft_ops[0]["fields"]["кадры"]
    assert [row["id"] for row in soft_rows] == ["k1", "k2"]
    assert soft_rows[1]["активен"] is False
    assert soft_rows[1]["закадр"] == "фраза на месте"


def test_repair_vo_keeps_inactive_zakadr(warn_log=None) -> None:
    del warn_log
    from app.services.apply_ops_batches import repair_shot_vo_ops

    inactive = {"id": "1-K0", "закадр": "фраза на месте", "активен": False, "действие": "пауза"}
    ops = [
        {
            "frame_uuid": "u1",
            "fields": {
                "кадры": [
                    dict(inactive),
                    {"id": "1-K1", "закадр": "", "действие": "шаг"},
                ]
            },
        }
    ]
    frames = [{"uuid": "u1", "number": 1, "voiceover_text": "Короткий шаг."}]
    repair_shot_vo_ops(ops, frames)
    rows = {row["id"]: row for row in ops[0]["fields"]["кадры"]}
    assert rows["1-K0"]["закадр"] == "фраза на месте"
    assert rows["1-K0"]["активен"] is False
    assert "закадр_архив" not in rows["1-K0"]
    assert rows["1-K1"]["id"] == "1-K1"


def test_montage_mirror_soft_keeps_dropped_shot() -> None:
    from types import SimpleNamespace

    from app.services.montage_coverage_ops import _preserve_parent_kadry

    parent = SimpleNamespace(uuid="parent-1", number=4, id=4)
    before = [
        {"id": "k1", "закадр": "первый"},
        {"id": "k2", "закадр": "фраза на месте"},
    ]
    kadry = [{"id": "k1", "закадр": "первый"}]
    kept = _preserve_parent_kadry(parent, before, kadry)
    assert [row["id"] for row in kept] == ["k1", "k2"]
    assert kept[1]["активен"] is False
    assert kept[1]["закадр"] == "фраза на месте"

    already = [
        {"id": "k1", "закадр": "первый"},
        {"id": "k2", "закадр": "фраза на месте", "активен": False},
    ]
    soft = _preserve_parent_kadry(parent, before, [dict(row) for row in already])
    assert [row["id"] for row in soft] == ["k1", "k2"]
    assert soft[1]["активен"] is False
    assert soft[1]["закадр"] == "фраза на месте"
