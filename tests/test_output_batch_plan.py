# -*- coding: utf-8 -*-
"""Character-based batch planning for img_pr and VO steps."""

from __future__ import annotations

import json

from app.services.output_batch_plan import (
    IMG_PR_FRAMES_PER_BATCH,
    VO_CHARS_PER_BATCH,
    batch_count_by_voiceover,
    batch_count_img_pr,
    pack_frames_by_voiceover,
    pack_frames_img_pr,
    plan_db_frames_slices,
    split_into_n_batches,
)


def test_img_pr_batch_count_formula() -> None:
    assert IMG_PR_FRAMES_PER_BATCH == 8
    assert batch_count_img_pr(1) == 1
    assert batch_count_img_pr(8) == 1
    assert batch_count_img_pr(9) == 2
    assert batch_count_img_pr(155) == 20
    assert batch_count_img_pr(171) == 22


def test_vo_batch_count_formula() -> None:
    assert VO_CHARS_PER_BATCH == 2_500
    assert batch_count_by_voiceover(0) == 1
    assert batch_count_by_voiceover(2500) == 1
    assert batch_count_by_voiceover(2501) == 2
    assert batch_count_by_voiceover(10_500) == 5


def test_img_pr_pack_splits_155_by_8() -> None:
    frames = [{"uuid": f"u{i:03d}", "number": i} for i in range(1, 156)]
    batches = pack_frames_img_pr(frames)
    assert [len(b) for b in batches] == [8] * 19 + [3]
    assert sum(len(b) for b in batches) == 155
    assert [fr["uuid"] for b in batches for fr in b] == [fr["uuid"] for fr in frames]


def test_img_pr_pack_splits_171() -> None:
    frames = [{"uuid": f"u{i:03d}", "number": i} for i in range(1, 172)]
    batches = pack_frames_img_pr(frames)
    assert [len(b) for b in batches] == [8] * 21 + [3]
    assert sum(len(b) for b in batches) == 171
    assert [fr["uuid"] for b in batches for fr in b] == [fr["uuid"] for fr in frames]


def test_vo_pack_splits_by_voiceover_len() -> None:
    frames = [{"uuid": f"u{i:03d}"} for i in range(1, 21)]
    vo = "а" * 10_500  # 10500/2500 = 5 batches
    batches = pack_frames_by_voiceover(frames, vo)
    assert len(batches) == 5
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
    assert len(slices) == 22
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
    vo.write_text("б" * 7_000, encoding="utf-8")  # 7000/2500 = 3 batches
    slices = plan_db_frames_slices([path, vo])
    assert slices is not None
    assert len(slices) == 3


def test_img_pr_beats_voiceover_when_pack_kind_set(tmp_path) -> None:
    """prompt_img_pr уходит в master и не в input_paths — pack_kind обязателен."""
    frames = [{"uuid": f"u{i:03d}", "number": i} for i in range(1, 172)]
    path = tmp_path / "db_frames.json"
    path.write_text(
        json.dumps({"frames": frames}, ensure_ascii=False),
        encoding="utf-8",
    )
    vo = tmp_path / "voiceover.txt"
    vo.write_text("в" * 20_000, encoding="utf-8")  # VO → 8 батчей
    slices_vo = plan_db_frames_slices([path, vo])
    assert slices_vo is not None
    assert len(slices_vo) == 8
    slices_img = plan_db_frames_slices([path, vo], pack_kind="img_pr")
    assert slices_img is not None
    assert len(slices_img) == 22
    sizes = [
        len(json.loads(p.read_text(encoding="utf-8"))["frames"]) for p in slices_img
    ]
    assert sizes == [8] * 21 + [3]


def test_force_batches_2_and_4_ignore_vo_formula(tmp_path) -> None:
    frames = [{"uuid": f"u{i:03d}"} for i in range(1, 21)]
    path = tmp_path / "db_frames.json"
    path.write_text(
        json.dumps({"frames": frames}, ensure_ascii=False),
        encoding="utf-8",
    )
    vo = tmp_path / "voiceover.txt"
    vo.write_text("д" * 20_000, encoding="utf-8")  # VO-формула дала бы 6
    two = plan_db_frames_slices([path, vo], force_batches=2)
    assert two is not None and len(two) == 2
    assert [
        len(json.loads(p.read_text(encoding="utf-8"))["frames"]) for p in two
    ] == [10, 10]
    four = plan_db_frames_slices([path, vo], force_batches=4)
    assert four is not None and len(four) == 4
    assert [
        len(json.loads(p.read_text(encoding="utf-8"))["frames"]) for p in four
    ] == [5, 5, 5, 5]


def test_img_pr_detected_from_prompt_text_even_with_voiceover(tmp_path) -> None:
    frames = [{"uuid": f"u{i:03d}", "number": i} for i in range(1, 172)]
    path = tmp_path / "db_frames.json"
    path.write_text(
        json.dumps({"frames": frames}, ensure_ascii=False),
        encoding="utf-8",
    )
    vo = tmp_path / "voiceover.txt"
    vo.write_text("г" * 20_000, encoding="utf-8")  # VO → ceil(20000/2500)=8
    slices = plan_db_frames_slices(
        [path, vo],
        prompt="# img_pr\nМастер промт картинок",
        accompanying="промт_картинки в ops",
    )
    assert slices is not None
    assert len(slices) == 22
