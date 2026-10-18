"""Неблокирующее ревью #352: uuid с дефисами и лог короткого промта img_pr."""

from __future__ import annotations


def test_dashed_frame_uuid_is_kept() -> None:
    from app.services.img_pr_batches import filter_prompt_ops

    uid = "12345678-1234-1234-1234-123456789abc"
    prompt = "сцена " * 80
    ops = filter_prompt_ops(
        [{"frame_uuid": uid, "fields": {"промт_картинки": prompt}}]
    )
    assert ops and ops[0]["frame_uuid"] == uid


def test_short_prompt_is_logged_and_dropped() -> None:
    from loguru import logger

    from app.services.img_pr_batches import writeable_img_pr_ops

    lines: list[str] = []
    sink = logger.add(lambda message: lines.append(str(message)), level="WARNING")
    try:
        kept = writeable_img_pr_ops(
            [
                {
                    "frame_uuid": "abc123",
                    "fields": {"промт_картинки": "слишком коротко"},
                }
            ]
        )
    finally:
        logger.remove(sink)
    assert kept == []
    assert any("img_pr: промт короче" in line and "abc123" in line for line in lines)
    assert all("слишком коротко" not in line for line in lines)
