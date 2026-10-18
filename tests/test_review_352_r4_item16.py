"""Ревью #352 r4 п.16: чекпоинт не теряет ещё не применённые полные опы.

Недозакрыто после 9dd68fd8: stored = applied + rejected. Если apply
вернул [] и был хотя бы один короткий оп, полные промты выпадали.
"""

import pytest

from app.services import img_pr_batches as ipb
from app.services.xlsx_step_runners import apply_img_pr_checkpoint


@pytest.mark.asyncio
async def test_empty_apply_keeps_every_original_op(monkeypatch, tmp_path) -> None:
    long_id = "a" * 24
    short_id = "b" * 24
    long_prompt = "сцена " * 80
    ops = [
        {"frame_uuid": long_id, "fields": {"промт_картинки": long_prompt}},
        {"frame_uuid": short_id, "fields": {"промт_картинки": "слишком коротко"}},
    ]
    ipb.save_checkpoint(tmp_path, done_uuids=["stale"], ops=ops)

    async def _empty(*_a, **_k):
        return []

    monkeypatch.setattr(
        "app.services.xlsx_step_runners._apply_img_pr_ops_now",
        _empty,
    )

    class _Project:
        id = 13
        data_dir = tmp_path

    stored, done = await apply_img_pr_checkpoint(_Project())
    assert done == []
    saved = ipb.load_checkpoint(tmp_path)
    by = {op["frame_uuid"]: op for op in saved["ops"]}
    assert set(by) == {long_id, short_id}
    assert by[long_id]["fields"]["промт_картинки"] == long_prompt
    assert by[short_id]["fields"]["промт_картинки"] == "слишком коротко"
    assert {op["frame_uuid"] for op in stored} == {long_id, short_id}
    assert saved["done_uuids"] == []
