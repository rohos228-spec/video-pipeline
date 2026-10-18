"""Ревью #352 п.16: короткий промт остаётся в чекпоинте, не затирается фильтром."""

from app.services.img_pr_batches import partition_img_pr_checkpoint


def test_rejected_prompt_is_kept() -> None:
    long_prompt = "сцена " * 80
    ops = [
        {"frame_uuid": "abc123", "fields": {"промт_картинки": long_prompt}},
        {"frame_uuid": "short1", "fields": {"промт_картинки": "слишком коротко"}},
    ]
    writeable, rejected = partition_img_pr_checkpoint(ops)
    assert [op["frame_uuid"] for op in writeable] == ["abc123"]
    assert [op["frame_uuid"] for op in rejected] == ["short1"]
    stored = writeable + rejected
    assert len(stored) == 2
