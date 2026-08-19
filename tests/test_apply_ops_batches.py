import pytest

from app.services.apply_ops_batches import (
    frames_per_batch,
    should_batch_apply_ops,
    split_frames,
    select_frames_for_batches,
    _frame_complete,
    _pending_frames,
    run_apply_ops_batched,
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


def test_pending_skips_top_level_shot_fields() -> None:
    frames = [
        {
            "uuid": "a" * 24,
            "voiceover_text": "one",
            "main_action": "x",
            "shot01_description": "y",
        },
        {
            "uuid": "b" * 24,
            "voiceover_text": "two",
        },
    ]
    assert _frame_complete(frames[0], dense=True)
    pending = _pending_frames(frames, dense=True)
    assert [f["uuid"] for f in pending] == ["b" * 24]


def test_dense_retry_skips_filled_and_keeps_one_tail_batch() -> None:
    frames = []
    for i in range(160):
        row = {
            "uuid": f"{i:024d}",
            "voiceover_text": f"vo {i}",
        }
        if i < 129:
            row["main_action"] = "x"
            row["shot01_description"] = "y"
        frames.append(row)
    selected = select_frames_for_batches(
        frames, dense=True, target_batches=5
    )
    assert len(selected) == 31
    size = frames_per_batch(
        n_frames=len(selected), json_bytes=24_000, dense=True, target_batches=5
    )
    assert size == 31
    chunks = split_frames(selected, size)
    assert [len(c) for c in chunks] == [31]


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


def _frame(i: int) -> dict:
    return {
        "uuid": f"{i:024d}",
        "voiceover_text": f"vo {i}",
    }


@pytest.mark.asyncio
async def test_run_apply_ops_starts_with_one_batch(
    tmp_path, monkeypatch
) -> None:
    """Успех с первого раза = ровно один LLM-вызов, не 5 пачек."""
    import json

    from app.services.gpt_operator_client import OperatorApiResult

    calls: list[int] = []

    async def fake_run(**kwargs):
        path = kwargs["input_paths"][0]
        frames = json.loads(path.read_text(encoding="utf-8"))["frames"]
        calls.append(len(frames))
        ops = [
            {"frame_uuid": fr["uuid"], "fields": {"main_action": "x"}}
            for fr in frames
        ]
        return OperatorApiResult(
            reply_text='{"ops":[]}',
            output_paths=[path],
            apply_ops={"ops": ops},
        )

    monkeypatch.setattr(
        "app.services.apply_ops_batches.run_operator_api", fake_run
    )
    frames = [_frame(i) for i in range(160)]
    ctx = tmp_path / "db_frames.json"
    ctx.write_text("{}", encoding="utf-8")
    res = await run_apply_ops_batched(
        project_dir=tmp_path,
        node_key="n_excel_gpt_2",
        role="excel_gpt",
        output_mode="project_file",
        prompt="p",
        accompanying="",
        db_ctx={"frames": frames},
        ctx_path=ctx,
        project_id=1,
        dense=True,
    )
    assert calls == [160]
    assert len(res.apply_ops["ops"]) == 160


@pytest.mark.asyncio
async def test_run_apply_ops_splits_1_to_2_on_error(
    tmp_path, monkeypatch
) -> None:
    """Полный батч падает → два полубатча, не заранее 5×32."""
    import json

    from app.services.gpt_operator_client import OperatorApiResult

    calls: list[int] = []

    async def fake_run(**kwargs):
        path = kwargs["input_paths"][0]
        frames = json.loads(path.read_text(encoding="utf-8"))["frames"]
        calls.append(len(frames))
        if len(frames) > 80:
            raise RuntimeError("too big")
        ops = [
            {"frame_uuid": fr["uuid"], "fields": {"main_action": "x"}}
            for fr in frames
        ]
        return OperatorApiResult(
            reply_text='{"ops":[]}',
            output_paths=[path],
            apply_ops={"ops": ops},
        )

    monkeypatch.setattr(
        "app.services.apply_ops_batches.run_operator_api", fake_run
    )
    frames = [_frame(i) for i in range(160)]
    ctx = tmp_path / "db_frames.json"
    ctx.write_text("{}", encoding="utf-8")
    res = await run_apply_ops_batched(
        project_dir=tmp_path,
        node_key="n_excel_gpt_2",
        role="excel_gpt",
        output_mode="project_file",
        prompt="p",
        accompanying="",
        db_ctx={"frames": frames},
        ctx_path=ctx,
        project_id=1,
        dense=True,
    )
    assert calls == [160, 80, 80]
    assert len(res.apply_ops["ops"]) == 160


@pytest.mark.asyncio
async def test_run_apply_ops_splits_2_to_4_on_second_error(
    tmp_path, monkeypatch
) -> None:
    """Полубатч тоже падает → ещё раз пополам (4 куска исходного)."""
    import json

    from app.services.gpt_operator_client import OperatorApiResult

    calls: list[int] = []

    async def fake_run(**kwargs):
        path = kwargs["input_paths"][0]
        frames = json.loads(path.read_text(encoding="utf-8"))["frames"]
        calls.append(len(frames))
        if len(frames) > 40:
            raise RuntimeError("still too big")
        ops = [
            {"frame_uuid": fr["uuid"], "fields": {"main_action": "x"}}
            for fr in frames
        ]
        return OperatorApiResult(
            reply_text='{"ops":[]}',
            output_paths=[path],
            apply_ops={"ops": ops},
        )

    monkeypatch.setattr(
        "app.services.apply_ops_batches.run_operator_api", fake_run
    )
    frames = [_frame(i) for i in range(160)]
    ctx = tmp_path / "db_frames.json"
    ctx.write_text("{}", encoding="utf-8")
    res = await run_apply_ops_batched(
        project_dir=tmp_path,
        node_key="n_excel_gpt_2",
        role="excel_gpt",
        output_mode="project_file",
        prompt="p",
        accompanying="",
        db_ctx={"frames": frames},
        ctx_path=ctx,
        project_id=1,
        dense=True,
    )
    # 160 fail, 80 fail → 40+40, other 80 fail → 40+40
    assert calls == [160, 80, 40, 40, 80, 40, 40]
    assert len(res.apply_ops["ops"]) == 160
