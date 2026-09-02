import pytest

from app.services.apply_ops_batches import (
    frames_per_batch,
    should_batch_apply_ops,
    split_frames,
    split_vo_units,
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


def test_analytics_without_chunk_size_is_one_pack() -> None:
    """54–59: chunk_size=None → все pending в одном вызове, не по 8."""
    pending = [{"uuid": f"{i:024d}", "voiceover_text": "x"} for i in range(155)]
    size = 0
    packs = split_frames(pending, size) if size else [pending]
    assert len(packs) == 1
    assert len(packs[0]) == 155


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


def test_ui_force_full_sends_already_filled_frames() -> None:
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
    pending = _pending_frames(frames, dense=True, force_full=True)
    assert [f["uuid"] for f in pending] == ["a" * 24, "b" * 24]


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


def test_img_prompt_skip_and_no_eight_packs() -> None:
    n, raw = 40, 80_000
    size = frames_per_batch(
        n_frames=n, json_bytes=raw, dense=False, skip_if_field="image_prompt"
    )
    assert size >= n
    assert not should_batch_apply_ops(
        n_frames=n, json_bytes=raw, dense=False, skip_if_field="image_prompt"
    )
    frames = [
        {"uuid": "a" * 24, "voiceover_text": "one", "image_prompt": "already"},
        {"uuid": "b" * 24, "voiceover_text": "two", "image_prompt": ""},
        {"uuid": "c" * 24, "attrs": {"промт_картинки": "old"}},
    ]
    pending = _pending_frames(frames, dense=False, skip_if_field="image_prompt")
    assert [f["uuid"] for f in pending] == ["b" * 24]
    no_vo = [
        {"uuid": "d" * 24, "voiceover_text": "", "image_prompt": ""},
        {"uuid": "e" * 24, "voiceover_text": "ok", "image_prompt": ""},
    ]
    pending2 = _pending_frames(no_vo, dense=False, skip_if_field="image_prompt")
    assert [f["uuid"] for f in pending2] == ["e" * 24]


def test_pending_keeps_child_with_vo_shot_copy() -> None:
    frames = [
        {
            "uuid": "c" * 24,
            "voiceover_text": "",
            "vo_shot": "фрагмент шота",
            "image_prompt": "",
        }
    ]
    pending = _pending_frames(frames, dense=False, skip_if_field="image_prompt")
    assert [f["uuid"] for f in pending] == ["c" * 24]


def test_prompts_and_action_skip_requires_all_three() -> None:
    from app.services.apply_ops_batches import SKIP_PROMPTS_AND_ACTION

    done = {
        "uuid": "a" * 24,
        "voiceover_text": "one",
        "image_prompt": "img",
        "animation_prompt": "mov",
        "attrs": {"shot01_action": "шаг"},
    }
    no_action = {
        "uuid": "b" * 24,
        "voiceover_text": "two",
        "image_prompt": "img",
        "animation_prompt": "mov",
        "attrs": {},
    }
    no_img = {
        "uuid": "c" * 24,
        "voiceover_text": "three",
        "image_prompt": "",
        "animation_prompt": "mov",
        "attrs": {"shot01_action": "шаг"},
    }
    assert _frame_complete(done, dense=False, skip_if_field=SKIP_PROMPTS_AND_ACTION)
    pending = _pending_frames(
        [done, no_action, no_img],
        dense=False,
        skip_if_field=SKIP_PROMPTS_AND_ACTION,
    )
    assert [f["uuid"] for f in pending] == ["b" * 24, "c" * 24]


def test_camera_menu_skip_requires_size_move_set() -> None:
    from app.services.apply_ops_batches import SKIP_CAMERA_MENU

    done = {
        "uuid": "a" * 24,
        "voiceover_text": "one",
        "attrs": {
            "camera_subdivide": {
                "крупность": "Средний план",
                "движение": "статика",
                "набор": "SET_08",
            }
        },
    }
    no_set = {
        "uuid": "b" * 24,
        "voiceover_text": "two",
        "attrs": {
            "camera_subdivide": {
                "крупность": "Средний план",
                "движение": "наезд",
            }
        },
    }
    assert _frame_complete(done, dense=False, skip_if_field=SKIP_CAMERA_MENU)
    pending = _pending_frames(
        [done, no_set],
        dense=False,
        skip_if_field=SKIP_CAMERA_MENU,
    )
    assert [f["uuid"] for f in pending] == ["b" * 24]


def test_prompt_footer_asks_shot_menu_fields() -> None:
    from app.services.apply_ops_batches import _batch_footer

    text = _batch_footer(1, 1, 8, footer_kind="prompts")
    assert "крупность" in text
    assert "движение" in text
    assert "набор" in text
    assert "Не пиши кадры" in text
    assert "промты_детей" in text
    cam = _batch_footer(1, 1, 8, footer_kind="camera_menu")
    assert "крупность" in cam
    assert "Не пиши промт_картинки" in cam


def test_prompts_ops_reason_rejects_kadry_only() -> None:
    from app.services.apply_ops_batches import prompts_ops_reason

    frames = [{"uuid": "a" * 24, "voiceover_text": "vo"}]
    kadry_only = [
        {
            "frame_uuid": "a" * 24,
            "fields": {"кадры": [{"id": "1-K1", "план": "средний"}]},
        }
    ]
    assert prompts_ops_reason(kadry_only, frames) == "uuid aaaaaaaa: нет промт_картинки"
    ok = [
        {
            "frame_uuid": "a" * 24,
            "fields": {"промт_картинки": "двор, средний план"},
        }
    ]
    assert prompts_ops_reason(ok, frames) is None


def _frame(i: int) -> dict:
    return {
        "uuid": f"{i:024d}",
        "voiceover_text": f"vo {i}",
    }


def test_split_vo_units_keeps_shots_in_cell_packs() -> None:
    """145 кадров = 115 ячеек → 4 пачки по 30 ячеек, не 5 по кадрам."""
    frames: list[dict] = []
    for i in range(115):
        uid = f"{i:024d}"
        frames.append(
            {
                "uuid": uid,
                "voiceover_text": f"vo {i}",
                "camera_subdivide": {"role": "vo_parent", "parent_uuid": uid},
            }
        )
        if i < 30:
            frames.append(
                {
                    "uuid": f"c{i:023d}",
                    "voiceover_text": "",
                    "vo_shot": f"shot {i}",
                    "camera_subdivide": {"role": "shot", "parent_uuid": uid},
                }
            )
    assert len(frames) == 145
    packs = split_vo_units(frames, 30)
    assert len(packs) == 4
    from app.services.apply_ops_batches import group_vo_units

    assert [len(group_vo_units(p)) for p in packs] == [30, 30, 30, 25]
    assert {fr["uuid"] for fr in packs[0]} >= {f"{0:024d}", f"c{0:023d}"}


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


@pytest.mark.asyncio
async def test_vo_chunk_size_9_packs(tmp_path, monkeypatch) -> None:
    """Сценарист: заранее пачки по 9 ячеек закадра, не все 66 разом."""
    import json

    from app.services.gpt_operator_client import OperatorApiResult

    calls: list[int] = []

    async def fake_run(**kwargs):
        path = kwargs["input_paths"][0]
        frames = json.loads(path.read_text(encoding="utf-8"))["frames"]
        calls.append(len(frames))
        ops = [
            {"frame_uuid": fr["uuid"], "fields": {"закадр": "x"}}
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
    frames = [_frame(i) for i in range(20)]
    ctx = tmp_path / "db_frames.json"
    ctx.write_text("{}", encoding="utf-8")
    res = await run_apply_ops_batched(
        project_dir=tmp_path,
        node_key="n_excel_gpt_fw_script",
        role="excel_gpt",
        output_mode="project_file",
        prompt="p",
        accompanying="",
        db_ctx={"frames": frames},
        ctx_path=ctx,
        project_id=1,
        dense=True,
        chunk_size=9,
        parallel_max=1,
    )
    assert calls == [9, 9, 2]
    assert len(res.apply_ops["ops"]) == 20


@pytest.mark.asyncio
async def test_vo_six_parallel_staggered(tmp_path, monkeypatch) -> None:
    """До 6 пачек параллельно, старт со сдвигом; вторая волна после первой."""
    import asyncio
    import json
    import time

    from app.services.gpt_operator_client import OperatorApiResult

    starts: list[float] = []
    inflight = 0
    max_inflight = 0
    lock = asyncio.Lock()
    calls: list[int] = []

    async def fake_run(**kwargs):
        nonlocal inflight, max_inflight
        async with lock:
            inflight += 1
            max_inflight = max(max_inflight, inflight)
            starts.append(time.monotonic())
            calls.append(
                len(json.loads(kwargs["input_paths"][0].read_text(encoding="utf-8"))["frames"])
            )
        await asyncio.sleep(0.25)
        path = kwargs["input_paths"][0]
        frames = json.loads(path.read_text(encoding="utf-8"))["frames"]
        async with lock:
            inflight -= 1
        return OperatorApiResult(
            reply_text='{"ops":[]}',
            output_paths=[path],
            apply_ops={
                "ops": [
                    {"frame_uuid": fr["uuid"], "fields": {"закадр": "x"}}
                    for fr in frames
                ]
            },
        )

    monkeypatch.setattr(
        "app.services.apply_ops_batches.run_operator_api", fake_run
    )
    frames = [_frame(i) for i in range(66)]
    ctx = tmp_path / "db_frames.json"
    ctx.write_text("{}", encoding="utf-8")
    res = await run_apply_ops_batched(
        project_dir=tmp_path,
        node_key="n_excel_gpt_fw_script",
        role="excel_gpt",
        output_mode="project_file",
        prompt="p",
        accompanying="",
        db_ctx={"frames": frames},
        ctx_path=ctx,
        project_id=1,
        dense=True,
        chunk_size=9,
        parallel_max=6,
        stagger_sec=0.03,
    )
    assert calls.count(9) == 7
    assert calls.count(3) == 1
    assert len(res.apply_ops["ops"]) == 66
    assert max_inflight == 6
    assert starts[6] >= starts[0] + 0.20


@pytest.mark.asyncio
async def test_incomplete_one_uuid_retries_that_frame(tmp_path, monkeypatch) -> None:
    """7/8 ops → добить один uuid, не валить пачку."""
    import json

    from app.services.gpt_operator_client import OperatorApiResult

    calls: list[int] = []

    async def fake_run(**kwargs):
        path = kwargs["input_paths"][0]
        frames = json.loads(path.read_text(encoding="utf-8"))["frames"]
        calls.append(len(frames))
        keep = frames if len(frames) == 1 else frames[:-1]
        return OperatorApiResult(
            reply_text='{"ops":[]}',
            output_paths=[path],
            apply_ops={
                "ops": [
                    {"frame_uuid": fr["uuid"], "fields": {"закадр": "x"}}
                    for fr in keep
                ]
            },
        )

    monkeypatch.setattr(
        "app.services.apply_ops_batches.run_operator_api", fake_run
    )
    frames = [_frame(i) for i in range(8)]
    ctx = tmp_path / "db_frames.json"
    ctx.write_text("{}", encoding="utf-8")
    res = await run_apply_ops_batched(
        project_dir=tmp_path,
        node_key="n_excel_gpt_fw_frames",
        role="excel_gpt",
        output_mode="project_file",
        prompt="p",
        accompanying="",
        db_ctx={"frames": frames},
        ctx_path=ctx,
        project_id=1,
        dense=False,
        chunk_size=8,
        parallel_max=1,
    )
    assert calls == [8, 1]
    assert len(res.apply_ops["ops"]) == 8


@pytest.mark.asyncio
async def test_qc_allow_empty_does_not_split_on_gpt_fail(tmp_path, monkeypatch) -> None:
    """QC: обрыв GPT не должен 1→2→4 крутить пачку до залипания."""
    import json

    calls: list[int] = []

    async def fake_run(**kwargs):
        path = kwargs["input_paths"][0]
        frames = json.loads(path.read_text(encoding="utf-8"))["frames"]
        calls.append(len(frames))
        raise RuntimeError("ConnectError: All connection attempts failed")

    monkeypatch.setattr(
        "app.services.apply_ops_batches.run_operator_api", fake_run
    )
    frames = [_frame(i) for i in range(8)]
    ctx = tmp_path / "db_frames.json"
    ctx.write_text("{}", encoding="utf-8")
    res = await run_apply_ops_batched(
        project_dir=tmp_path,
        node_key="n_excel_gpt_fw_qc",
        role="excel_gpt",
        output_mode="project_file",
        prompt="p",
        accompanying="",
        db_ctx={"frames": frames},
        ctx_path=ctx,
        project_id=1,
        dense=False,
        chunk_size=8,
        parallel_max=1,
        allow_empty_ops=True,
    )
    assert calls == [8]
    assert res.apply_ops["ops"] == []


def test_analytics_ops_rejects_copied_sense_and_place() -> None:
    from app.services.apply_ops_batches import analytics_ops_collapsed_reason

    frames = [
        {"uuid": "aaaa1111", "voiceover_text": "а с двух крестьянских жалоб."},
        {"uuid": "bbbb2222", "voiceover_text": "в её имениях."},
    ]
    ops = [
        {
            "frame_uuid": "aaaa1111",
            "fields": {
                "место": "ворота канцелярии XVIII века",
                "смысл_сцены": "двое передают прошение у ворот",
                "особенность_сцены": "Предметный кадр",
            },
        },
        {
            "frame_uuid": "bbbb2222",
            "fields": {
                "место": "ворота канцелярии XVIII века",
                "смысл_сцены": "двое передают прошение у ворот",
                "особенность_сцены": "Общий план локации",
            },
        },
    ]
    reason = analytics_ops_collapsed_reason(ops, frames)
    assert reason is not None
    assert "смысл_сцены" in reason or "место" in reason


def test_analytics_ops_allows_same_sense_for_same_voiceover() -> None:
    from app.services.apply_ops_batches import analytics_ops_collapsed_reason

    frames = [
        {"uuid": "aaaa1111", "voiceover_text": "один и тот же текст"},
        {"uuid": "bbbb2222", "voiceover_text": "один и тот же текст"},
    ]
    ops = [
        {
            "frame_uuid": "aaaa1111",
            "fields": {
                "место": "двор усадьбы",
                "смысл_сцены": "управляющий пишет в книгу",
            },
        },
        {
            "frame_uuid": "bbbb2222",
            "fields": {
                "место": "двор усадьбы",
                "смысл_сцены": "управляющий пишет в книгу",
            },
        },
    ]
    assert analytics_ops_collapsed_reason(ops, frames) is None


@pytest.mark.asyncio
async def test_analytics_collapsed_ops_not_applied(tmp_path, monkeypatch) -> None:
    from app.services.gpt_operator_client import OperatorApiResult

    applied: list[list] = []

    async def fake_run(**kwargs):
        return OperatorApiResult(
            reply_text='{"ops":[]}',
            output_paths=[],
            apply_ops={
                "ops": [
                    {
                        "frame_uuid": "aaaa1111",
                        "fields": {
                            "место": "ворота",
                            "смысл_сцены": "одно и то же",
                        },
                    },
                    {
                        "frame_uuid": "bbbb2222",
                        "fields": {
                            "место": "ворота",
                            "смысл_сцены": "одно и то же",
                        },
                    },
                ]
            },
        )

    async def apply_fn(payload):
        applied.append(list(payload.get("ops") or []))

    monkeypatch.setattr(
        "app.services.apply_ops_batches.run_operator_api", fake_run
    )
    monkeypatch.setattr(
        "app.services.adaptive_llm_batches.next_split_level", lambda _level: None
    )
    frames = [
        {"uuid": "aaaa1111", "voiceover_text": "жалобы"},
        {"uuid": "bbbb2222", "voiceover_text": "в имениях"},
    ]
    ctx = tmp_path / "db_frames.json"
    ctx.write_text("{}", encoding="utf-8")
    with pytest.raises(RuntimeError, match="скопирован"):
        await run_apply_ops_batched(
            project_dir=tmp_path,
            node_key="n_excel_gpt_2",
            role="excel_gpt",
            output_mode="project_file",
            prompt="p",
            accompanying="",
            db_ctx={"frames": frames},
            ctx_path=ctx,
            project_id=1,
            dense=False,
            chunk_size=2,
            footer_kind="analytics",
            apply_fn=apply_fn,
        )
    assert applied == []


def test_action_chain_rejects_slogan() -> None:
    from app.services.apply_ops_batches import action_chain_ops_reason

    ops = [
        {
            "frame_uuid": "aa" * 4,
            "fields": {"главное_действие": "Ткач в следственном отделе"},
        }
    ]
    assert action_chain_ops_reason(ops, [])


def test_action_chain_accepts_numbered() -> None:
    from app.services.apply_ops_batches import action_chain_ops_reason

    ops = [
        {
            "frame_uuid": "aa" * 4,
            "fields": {
                "главное_действие": (
                    "1. следственный отдел — отдел целиком\n"
                    "(Сергей Ткач парадокс.)"
                )
            },
        }
    ]
    assert action_chain_ops_reason(ops, []) is None


def test_shots_coverage_rejects_same_place_all_independent() -> None:
    from app.services.apply_ops_batches import shots_coverage_ops_reason

    ops = [
        {
            "frame_uuid": "aa" * 4,
            "fields": {
                "кадры": [
                    {
                        "id": "1-K1",
                        "parent_id": None,
                        "план": "ОБЩИЙ",
                        "ракурс": "фронт",
                        "место": "кухня",
                        "действие": "стоит у плиты",
                    },
                    {
                        "id": "1-K2",
                        "parent_id": None,
                        "план": "СРЕДНИЙ",
                        "ракурс": "3/4",
                        "место": "кухня",
                        "действие": "мешает кастрюлю",
                    },
                ]
            },
        }
    ]
    assert shots_coverage_ops_reason(ops, [])


def test_shots_coverage_accepts_one_logical_shot() -> None:
    from app.services.apply_ops_batches import shots_coverage_ops_reason

    long_vo = "A" * 90
    frames = [{"uuid": "aa" * 4, "voiceover_text": long_vo}]
    ops = [
        {
            "frame_uuid": "aa" * 4,
            "fields": {
                "кадры": [
                    {
                        "id": "1-K1",
                        "parent_id": None,
                        "план": "СРЕДНИЙ",
                        "ракурс": "фронт",
                        "место": "кухня",
                        "действие": "мешает кастрюлю",
                    }
                ]
            },
        }
    ]
    assert shots_coverage_ops_reason(ops, frames) is None


def test_shots_coverage_accepts_parent_on_same_place() -> None:
    from app.services.apply_ops_batches import shots_coverage_ops_reason

    ops = [
        {
            "frame_uuid": "aa" * 4,
            "fields": {
                "кадры": [
                    {
                        "id": "1-K1",
                        "parent_id": None,
                        "план": "ОБЩИЙ",
                        "ракурс": "фронт",
                        "место": "кухня",
                        "действие": "стоит у плиты",
                    },
                    {
                        "id": "1-K2",
                        "parent_id": "1-K1",
                        "план": "СРЕДНИЙ",
                        "ракурс": "3/4",
                        "место": "кухня",
                        "действие": "мешает кастрюлю",
                    },
                    {
                        "id": "1-K3",
                        "parent_id": None,
                        "план": "ОБЩИЙ",
                        "ракурс": "от двери",
                        "место": "двор",
                        "действие": "выходит во двор",
                    },
                ]
            },
        }
    ]
    assert shots_coverage_ops_reason(ops, []) is None


def test_bits_ops_rejects_slogan_string() -> None:
    from app.services.apply_ops_batches import bits_ops_reason

    vo = "Дарья Салтыкова. История началась не с процесса, а с двух жалоб."
    frames = [{"uuid": "aa" * 4, "voiceover_text": vo}]
    ops = [
        {
            "frame_uuid": "aa" * 4,
            "fields": {"биты": "Хук: история начинается с двух жалоб"},
        }
    ]
    assert bits_ops_reason(ops, frames)


def test_bits_ops_rejects_one_bit_for_two_clauses() -> None:
    from app.services.apply_ops_batches import bits_ops_reason

    vo = "Дарья Салтыкова. История началась не с процесса, а с двух жалоб."
    frames = [{"uuid": "aa" * 4, "voiceover_text": vo}]
    ops = [
        {
            "frame_uuid": "aa" * 4,
            "fields": {
                "биты": [
                    {
                        "порядок": 1,
                        "глагол": "сдвигает",
                        "изменение": "процесс → жалобы",
                        "якорь": "Дарья Салтыкова",
                    }
                ]
            },
        }
    ]
    assert bits_ops_reason(ops, frames)


def test_bits_ops_accepts_two_bits() -> None:
    from app.services.apply_ops_batches import bits_ops_reason

    vo = "Дарья Салтыкова. История началась не с процесса, а с двух жалоб."
    frames = [{"uuid": "aa" * 4, "voiceover_text": vo}]
    ops = [
        {
            "frame_uuid": "aa" * 4,
            "fields": {
                "биты": [
                    {
                        "порядок": 1,
                        "глагол": "называет",
                        "изменение": "никто → названа",
                        "якорь": "Дарья Салтыкова",
                    },
                    {
                        "порядок": 2,
                        "глагол": "сдвигает",
                        "изменение": "процесс → две жалобы",
                        "якорь": "История началась не с процесса",
                    },
                ]
            },
        }
    ]
    assert bits_ops_reason(ops, frames) is None


def test_shots_coverage_rejects_single_shot_on_two_clauses() -> None:
    from app.services.apply_ops_batches import shots_coverage_ops_reason

    vo = "Дарья Салтыкова. История началась не с процесса, а с двух жалоб."
    frames = [{"uuid": "aa" * 4, "voiceover_text": vo}]
    ops = [
        {
            "frame_uuid": "aa" * 4,
            "fields": {
                "кадры": [
                    {
                        "id": "1-K1",
                        "parent_id": None,
                        "план": "СРЕДНИЙ",
                        "ракурс": "фронт",
                        "место": "двор",
                        "действие": "стоит",
                    }
                ]
            },
        }
    ]
    reason = shots_coverage_ops_reason(ops, frames)
    assert reason and "один кадр" in reason


def test_shots_coverage_rejects_all_independent_invented_places() -> None:
    from app.services.apply_ops_batches import shots_coverage_ops_reason

    vo = "Дарья Салтыкова. История началась не с процесса, а с двух жалоб."
    frames = [{"uuid": "aa" * 4, "voiceover_text": vo}]
    ops = [
        {
            "frame_uuid": "aa" * 4,
            "fields": {
                "кадры": [
                    {
                        "id": "1-K1",
                        "parent_id": None,
                        "план": "ОБЩИЙ",
                        "ракурс": "фронт",
                        "место": "двор усадьбы",
                        "действие": "стоит у крыльца",
                    },
                    {
                        "id": "1-K2",
                        "parent_id": None,
                        "план": "СРЕДНИЙ",
                        "ракурс": "3/4",
                        "место": "канцелярия",
                        "действие": "две жалобы на столе",
                    },
                ]
            },
        }
    ]
    reason = shots_coverage_ops_reason(ops, frames)
    assert reason and "дочерн" in reason


def test_shots_coverage_accepts_master_and_child() -> None:
    from app.services.apply_ops_batches import shots_coverage_ops_reason

    vo = "Дарья Салтыкова. История началась не с процесса, а с двух жалоб."
    frames = [{"uuid": "aa" * 4, "voiceover_text": vo}]
    ops = [
        {
            "frame_uuid": "aa" * 4,
            "fields": {
                "кадры": [
                    {
                        "id": "1-K1",
                        "parent_id": None,
                        "план": "ОБЩИЙ",
                        "ракурс": "фронт",
                        "место": "двор усадьбы",
                        "действие": "двор целиком",
                    },
                    {
                        "id": "1-K2",
                        "parent_id": "1-K1",
                        "план": "ДЕТАЛЬ",
                        "ракурс": "сверху",
                        "место": "двор усадьбы",
                        "действие": "две жалобы",
                    },
                ]
            },
        }
    ]
    assert shots_coverage_ops_reason(ops, frames) is None


def test_shots_vo_rejects_comma_splinter() -> None:
    from app.services.apply_ops_batches import shots_coverage_ops_reason

    vo = (
        "Главный вопрос этой истории заключается в другом: "
        "почему крепостные люди так долго оставались без защиты, "
        "несмотря на существование чиновников, судов и государственной власти?"
    )
    frames = [{"uuid": "aa" * 4, "voiceover_text": vo}]
    ops = [
        {
            "frame_uuid": "aa" * 4,
            "fields": {
                "кадры": [
                    {
                        "id": "1-K1",
                        "parent_id": None,
                        "план": "СРЕДНИЙ",
                        "ракурс": "фронт",
                        "место": "канцелярия",
                        "действие": "чиновник",
                        "закадр": "несмотря на существование чиновников,",
                    },
                    {
                        "id": "1-K2",
                        "parent_id": "1-K1",
                        "план": "КРУПНЫЙ",
                        "ракурс": "сверху",
                        "место": "канцелярия",
                        "действие": "слово суды",
                        "закадр": "судов",
                    },
                ]
            },
        }
    ]
    reason = shots_coverage_ops_reason(ops, frames)
    assert reason and "симв" in reason


def test_shots_vo_accepts_name_title_and_normal_chunk() -> None:
    from app.services.apply_ops_batches import shots_coverage_ops_reason

    vo = "Дарья Салтыкова. История началась не с процесса, а с двух жалоб."
    frames = [{"uuid": "aa" * 4, "voiceover_text": vo}]
    ops = [
        {
            "frame_uuid": "aa" * 4,
            "fields": {
                "кадры": [
                    {
                        "id": "1-K1",
                        "parent_id": None,
                        "план": "ОБЩИЙ",
                        "ракурс": "фронт",
                        "место": "приёмная",
                        "действие": "титр имени",
                        "закадр": "Дарья Салтыкова.",
                    },
                    {
                        "id": "1-K2",
                        "parent_id": "1-K1",
                        "план": "СРЕДНИЙ",
                        "ракурс": "3/4",
                        "место": "приёмная",
                        "действие": "две жалобы на стол",
                        "закадр": " История началась не с процесса, а с двух жалоб.",
                    },
                ]
            },
        }
    ]
    assert shots_coverage_ops_reason(ops, frames) is None


@pytest.mark.asyncio
async def test_vo_chunk_size_8_packs(tmp_path, monkeypatch) -> None:
    """script_frames_qc: пачки по 8 отрезков закадра."""
    import json

    from app.services.apply_ops_batches import (
        FW_FRAMES_PER_BATCH,
        SCRIPT_FRAMES_QC_UNITS_PER_BATCH,
    )
    from app.services.gpt_operator_client import OperatorApiResult

    assert SCRIPT_FRAMES_QC_UNITS_PER_BATCH == 8
    assert FW_FRAMES_PER_BATCH == 6
    calls: list[int] = []

    async def fake_run(**kwargs):
        path = kwargs["input_paths"][0]
        frames = json.loads(path.read_text(encoding="utf-8"))["frames"]
        calls.append(len(frames))
        ops = [
            {
                "frame_uuid": fr["uuid"],
                "fields": {
                    "биты": [
                        {
                            "порядок": 1,
                            "глагол": "говорит",
                            "изменение": "молчит → сказано",
                            "якорь": fr["voiceover_text"],
                        }
                    ]
                },
            }
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
    frames = [_frame(i) for i in range(20)]
    ctx = tmp_path / "db_frames.json"
    ctx.write_text("{}", encoding="utf-8")
    res = await run_apply_ops_batched(
        project_dir=tmp_path,
        node_key="n_excel_gpt_fw_script",
        role="excel_gpt",
        output_mode="project_file",
        prompt="p",
        accompanying="",
        db_ctx={"frames": frames},
        ctx_path=ctx,
        project_id=1,
        dense=True,
        chunk_size=SCRIPT_FRAMES_QC_UNITS_PER_BATCH,
        parallel_max=1,
        footer_kind="bits",
    )
    assert calls == [8, 8, 4]
    assert len(res.apply_ops["ops"]) == 20
