"""PR #388: «общий вид» не гасит сцену, закадр не теряется, правки не пропадают."""

from __future__ import annotations

from app.orchestrator.validators.activity_flags import shot_is_active
from app.services.montage_op_status import op_is_open, op_status_after_result
from app.services.scene_shot_grammar import (
    fold_inactive_shot_voiceover,
    merge_gpt_shots_with_action,
)


def test_all_wide_shots_keep_first_live() -> None:
    shots = [
        {"id": "s1", "действие": "Общий вид двора", "закадр": "Утро."},
        {"id": "s2", "действие": "общий вид улицы", "закадр": "Тишина."},
    ]
    out, _ = merge_gpt_shots_with_action(shots, "")
    assert out is not None
    by_id = {s.get("id"): s for s in out}
    assert shot_is_active(by_id["s1"])
    assert not shot_is_active(by_id["s2"])
    assert len([s for s in out if shot_is_active(s)]) >= 1


def test_wide_shot_quarantined_when_other_live() -> None:
    shots = [
        {"id": "s1", "действие": "Общий вид двора", "закадр": "Утро."},
        {"id": "s2", "действие": "Кот прыгает на забор", "закадр": "Кот."},
    ]
    out, _ = merge_gpt_shots_with_action(shots, "")
    assert out is not None
    by_id = {s.get("id"): s for s in out}
    assert not shot_is_active(by_id["s1"])
    assert by_id["s1"]["карантин"] is True
    assert shot_is_active(by_id["s2"])


def test_fold_without_host_keeps_voiceover() -> None:
    shots = [{"id": "s1", "активен": False, "закадр": "Последняя фраза."}]
    assert fold_inactive_shot_voiceover(shots) == []
    assert shots[0]["закадр"] == "Последняя фраза."
    assert "закадр_перенесён" not in shots[0]


def test_fold_substring_not_word_is_appended() -> None:
    host = {"id": "s1", "закадр": "Котлета остыла."}
    dead = {"id": "s2", "активен": False, "закадр": "Кот"}
    fold_inactive_shot_voiceover([host, dead])
    assert host["закадр"] == "Котлета остыла. Кот"
    assert dead["закадр"] == ""
    assert dead["закадр_перенесён"] == "Кот"


def test_fold_already_present_not_duplicated_but_saved() -> None:
    host = {"id": "s1", "закадр": "Кот спит. Утро."}
    dead = {"id": "s2", "активен": False, "закадр": "кот спит"}
    fold_inactive_shot_voiceover([host, dead])
    assert host["закадр"] == "Кот спит. Утро."
    assert dead["закадр"] == ""
    assert dead["закадр_перенесён"] == "кот спит"


def test_paid_frame_skip_keeps_op_pending() -> None:
    status = op_status_after_result({"paid_frame_skipped": True}, False)
    assert status == "held"
    assert op_is_open(status)


def test_op_status_ok_and_fail() -> None:
    assert op_status_after_result({}, True) == "ok"
    assert not op_is_open("ok")
    assert op_status_after_result({}, False) == "fail"
    assert op_is_open("fail")
