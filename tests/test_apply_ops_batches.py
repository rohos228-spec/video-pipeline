from app.services.apply_ops_batches import (
    frames_per_batch,
    should_batch_apply_ops,
    split_frames,
    _frame_complete,
    _pending_frames,
)


def test_dense_32_pending_stays_one_batch() -> None:
    n, raw = 32, 24_000
    size = frames_per_batch(
        n_frames=n, json_bytes=raw, dense=True, target_batches=5
    )
    assert size == 32
    assert not should_batch_apply_ops(
        n_frames=n, json_bytes=raw, dense=True, target_batches=5
    )


def test_dense_160_is_five_batches() -> None:
    n, raw = 160, 120_000
    size = frames_per_batch(
        n_frames=n, json_bytes=raw, dense=True, target_batches=5
    )
    assert size == 32
    assert should_batch_apply_ops(
        n_frames=n, json_bytes=raw, dense=True, target_batches=5
    )
    chunks = split_frames(list(range(n)), size)
    assert [len(c) for c in chunks] == [32, 32, 32, 32, 32]


def test_dense_116_frames_split_like_xlsx_v8() -> None:
    n, raw = 116, 97570
    size = frames_per_batch(
        n_frames=n, json_bytes=raw, dense=True, target_batches=5
    )
    assert size == 24
    assert should_batch_apply_ops(
        n_frames=n, json_bytes=raw, dense=True, target_batches=5
    )
    chunks = split_frames(list(range(n)), size)
    assert [len(c) for c in chunks] == [24, 24, 24, 24, 20]


def test_light_analytics_keeps_116_in_one_call() -> None:
    n, raw = 116, 97570
    size = frames_per_batch(n_frames=n, json_bytes=raw, dense=False)
    assert size >= n
    assert not should_batch_apply_ops(n_frames=n, json_bytes=raw, dense=False)


def test_pending_skips_filled_shot01() -> None:
    frames = [
        {
            "uuid": "a" * 24,
            "voiceover_text": "one",
            "attrs": {"main_action": "x", "shot01_description": "y"},
        },
        {
            "uuid": "b" * 24,
            "voiceover_text": "two",
            "attrs": {},
        },
        {"uuid": "c" * 24, "voiceover_text": "", "attrs": {}},
    ]
    assert _frame_complete(frames[0], dense=True)
    pending = _pending_frames(frames, dense=True)
    assert [f["uuid"] for f in pending] == ["b" * 24]


def test_img_prompt_skip_and_small_batches() -> None:
    n, raw = 40, 80_000
    size = frames_per_batch(
        n_frames=n, json_bytes=raw, dense=False, skip_if_field="image_prompt"
    )
    assert size <= 8
    assert should_batch_apply_ops(
        n_frames=n, json_bytes=raw, dense=False, skip_if_field="image_prompt"
    )
    frames = [
        {"uuid": "a" * 24, "voiceover_text": "one", "image_prompt": "already"},
        {"uuid": "b" * 24, "voiceover_text": "two", "image_prompt": ""},
        {"uuid": "c" * 24, "attrs": {"промт_картинки": "old"}},
    ]
    pending = _pending_frames(frames, dense=False, skip_if_field="image_prompt")
    assert [f["uuid"] for f in pending] == ["b" * 24]
