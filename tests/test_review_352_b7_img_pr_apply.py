"""П.7: падение apply не помечает uuid сделанными и не отдаёт inline done."""

from __future__ import annotations

from app.services.xlsx_step_runners import drop_unapplied_img_pr_uuids


def test_failed_apply_drops_done_uuids() -> None:
    done = ["u-1", "u-2"]
    done_set = set(done)
    ops = [
        {"frame_uuid": "u-2", "fields": {"промт_картинки": "x"}},
    ]
    applied = drop_unapplied_img_pr_uuids(done, done_set, ops)
    assert applied is False
    assert done == ["u-1"]
    assert done_set == {"u-1"}
