# -*- coding: utf-8 -*-
"""Character-based batch planning for img_pr and VO steps."""

from __future__ import annotations

import json

from app.services.output_batch_plan import (
    IMG_PR_BATCH_CHAR_BUDGET,
    IMG_PR_CHARS_PER_FRAME,
    VO_CHARS_PER_BATCH,
    batch_count_by_voiceover,
    batch_count_img_pr,
    pack_frames_by_voiceover,
    pack_frames_img_pr,
    plan_db_frames_slices,
    split_into_n_batches,
)


def test_img_pr_batch_count_formula() -> None:
    # ceil(171 * 4000 / 228000) = ceil(3.0) = 3
    assert IMG_PR_CHARS_PER_FRAME == 4_000
    assert IMG_PR_BATCH_CHAR_BUDGET == 228_000
    assert batch_count_img_pr(171) == 3
    assert batch_count_img_pr(1) == 1
    assert batch_count_img_pr(57) == 1  # 57*4000 = 228000
    assert batch_count_img_pr(58) == 2


def test_vo_batch_count_formula() -> None:
    assert VO_CHARS_PER_BATCH == 3_500
    assert batch_count_by_voiceover(0) == 1
    assert batch_count_by_voiceover(3500) == 1
    assert batch_count_by_voiceover(3501) == 2
    assert batch_count_by_voiceover(10_500) == 3


def test_img_pr_pack_splits_171() -> None:
    frames = [{"uuid": f"u{i:03d}", "number": i} for i in range(1, 172)]
    batches = pack_frames_img_pr(frames)
    assert len(batches) == 3
    assert [len(b) for b in batches] == [57, 57, 57]
    assert sum(len(b) for b in batches) == 171
    assert [fr["uuid"] for b in batches for fr in b] == [fr["uuid"] for fr in frames]


def test_vo_pack_splits_by_voiceover_len() -> None:
    frames = [{"uuid": f"u{i:03d}"} for i in range(1, 21)]
    vo = "а" * 10_500  # 10500/3500 = 3 batches
    batches = pack_frames_by_voiceover(frames, vo)
    assert len(batches) == 3
    assert sum(len(b) for b in batches) == 20


def test_split_into_n_preserves_order() -> None:
    items = list(range(10))
    batches = split_into_n_batches(items, 3)
    assert [x for b in batches for x in b] == items
    assert len(batches) == 3


def test_plan_db_frames_slices_img_pr(tmp_path) -> None:
    frames = [{"uuid": f"u{i:03d}", "number": i} for i in range(1, 172)]
    path = tmp_path / "db_frames.json"
    path.write_text(
        json.dumps({"source": "db_v2", "frames": frames}, ensure_ascii=False),
        encoding="utf-8",
    )
    prompt = tmp_path / "prompt_img_pr_x.txt"
    prompt.write_text("img", encoding="utf-8")
    slices = plan_db_frames_slices([prompt, path])
    assert slices is not None
    assert len(slices) == 3
    total = 0
    for p in slices:
        data = json.loads(p.read_text(encoding="utf-8"))
        total += len(data["frames"])
    assert total == 171


def test_plan_db_frames_slices_by_vo_file(tmp_path) -> None:
    frames = [{"uuid": f"u{i:03d}", "number": i} for i in range(1, 11)]
    path = tmp_path / "db_frames.json"
    path.write_text(
        json.dumps({"frames": frames}, ensure_ascii=False),
        encoding="utf-8",
    )
    vo = tmp_path / "voiceover.txt"
    vo.write_text("б" * 7_000, encoding="utf-8")  # 7000/3500 = 2 batches
    slices = plan_db_frames_slices([path, vo])
    assert slices is not None
    assert len(slices) == 2


def test_img_pr_beats_voiceover_when_pack_kind_set(tmp_path) -> None:
    """prompt_img_pr уходит в master и не в input_paths — pack_kind обязателен."""
    frames = [{"uuid": f"u{i:03d}", "number": i} for i in range(1, 172)]
    path = tmp_path / "db_frames.json"
    path.write_text(
        json.dumps({"frames": frames}, ensure_ascii=False),
        encoding="utf-8",
    )
    vo = tmp_path / "voiceover.txt"
    vo.write_text("в" * 20_000, encoding="utf-8")  # VO → 6 батчей
    slices_vo = plan_db_frames_slices([path, vo])
    assert slices_vo is not None
    assert len(slices_vo) == 6
    slices_img = plan_db_frames_slices([path, vo], pack_kind="img_pr")
    assert slices_img is not None
    assert len(slices_img) == 3
    sizes = [
        len(json.loads(p.read_text(encoding="utf-8"))["frames"]) for p in slices_img
    ]
    assert sizes == [57, 57, 57]


def test_img_pr_detected_from_prompt_text_even_with_voiceover(tmp_path) -> None:
    frames = [{"uuid": f"u{i:03d}", "number": i} for i in range(1, 172)]
    path = tmp_path / "db_frames.json"
    path.write_text(
        json.dumps({"frames": frames}, ensure_ascii=False),
        encoding="utf-8",
    )
    vo = tmp_path / "voiceover.txt"
    vo.write_text("г" * 20_000, encoding="utf-8")  # VO → ceil(20000/3500)=6
    slices = plan_db_frames_slices(
        [path, vo],
        prompt="# img_pr\nМастер промт картинок",
        accompanying="промт_картинки в ops",
    )
    assert slices is not None
    assert len(slices) == 3
