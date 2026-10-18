"""Ревью #352: db_apply не держит свою копию предиката активности."""

from __future__ import annotations

from app.services.db_apply import merge_kadry_keep_missing


def test_merge_keeps_xlsx_false_token_as_hidden() -> None:
    out = merge_kadry_keep_missing(
        [{"id": "K2", "активен": "нет", "план": "ОБЩИЙ"}],
        [{"id": "K2", "план": "КРУПНЫЙ", "активен": True}],
    )
    row = next(item for item in out if item.get("id") == "K2")
    assert row["план"] == "КРУПНЫЙ"
    assert row["активен"] == "нет"
