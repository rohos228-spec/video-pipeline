"""Повторное ревью #352 №11: обрезанный кадр не закрывается как целый."""

from __future__ import annotations

from app.services.db_apply import extract_apply_ops_json


def test_truncated_frame_op_is_not_accepted() -> None:
    text = '{"ops":[{"frame_uuid":"abc","fields":{"закадр":"кусок"}'
    assert extract_apply_ops_json(text) is None


def test_closed_frame_op_is_accepted() -> None:
    text = '{"ops":[{"frame_uuid":"abc","fields":{"закадр":"кусок"}}]}'
    data = extract_apply_ops_json(text)
    assert data is not None
    assert data["ops"][0]["frame_uuid"] == "abc"


def test_truncated_project_op_without_frame_uuid_may_close() -> None:
    text = '{"ops":[{"type":"note","fields":{"x":1}}'
    data = extract_apply_ops_json(text)
    if data is None:
        return
    assert all(not str(op.get("frame_uuid") or "").strip() for op in data["ops"])
