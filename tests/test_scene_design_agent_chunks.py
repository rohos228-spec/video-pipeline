"""Adaptive /2 split action/camera при 524."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services.gpt_api import GptApiError
from app.services.scene_design import agent_chunks as ach
from app.services.scene_design import agents as ag
from app.services.scene_design import runner


def _fr(num: int, uuid: str, text: str):
    return SimpleNamespace(
        number=num,
        uuid=uuid,
        voiceover_text=text,
        duration_seconds=5.0,
    )


def _scene(i: int) -> dict:
    beats = ("setup", "develop", "develop", "turn", "payoff")
    return {
        "id_scene": f"scene_{i:02d}",
        "связь_с_прошлой": "продолжение" if i > 1 else "",
        "крючок_в_следующую": "дальше",
        "start_words": f"кусок {i} старт",
        "end_words": f"кусок {i} финиш",
        "цепь_действия": [
            {
                "phase_index": p,
                "beat": beats[p - 1],
                "action": f"жест {i}.{p}",
                "subject": "c01",
                "orientation": "face",
                "переход_к_следующей": "cut_on_action",
            }
            for p in range(1, 6)
        ],
    }


def test_is_capacity_failure_524() -> None:
    assert ach.is_capacity_failure(
        GptApiError("GPT HTTP 524: x", context={"status_code": 524, "retryable": True})
    )
    assert ach.is_capacity_failure(
        GptApiError("timeout", context={"error_kind": "timeout", "retryable": True})
    )
    assert not ach.is_capacity_failure(
        GptApiError("bad request", context={"status_code": 400, "retryable": False})
    )


def test_split_frames_half() -> None:
    frames = [_fr(i, f"u{i}", f"текст {i}") for i in range(1, 5)]
    left, right = ach.split_frames_half(frames)
    assert [f.number for f in left] == [1, 2]
    assert [f.number for f in right] == [3, 4]


def test_split_frames_batches() -> None:
    frames = [_fr(i, f"u{i}", f"текст {i}") for i in range(1, 6)]
    batches = ach.split_frames_batches(frames, max_frames=2)
    assert [[f.number for f in b] for b in batches] == [[1, 2], [3, 4], [5]]


def test_merge_action_renumbers() -> None:
    merged = ach.merge_agent_slices(
        "action",
        [{"scenes": [_scene(9)]}, {"scenes": [_scene(3)]}],
    )
    assert [s["id_scene"] for s in merged["scenes"]] == ["scene_01", "scene_02"]


@pytest.mark.asyncio
async def test_action_splits_twice_then_errors() -> None:
    frames = [_fr(i, f"u{i}", f"кусок {i} старт. кусок {i} финиш.") for i in range(1, 5)]
    project = SimpleNamespace(
        id=60,
        meta={"scene_design_variant": "chrono_dyn"},
        script_text="",
        general_plan="",
        data_dir=SimpleNamespace(),  # unused — load_prompt patched
    )
    boom = GptApiError(
        "GPT HTTP 524: cloudflare",
        context={"status_code": 524, "retryable": True},
    )
    depths_seen: list[int] = []

    async def fake_one(project, name, context, *, timeout, validate=True, max_retries=None):
        # depth inferred from chunk marker
        depth = 0
        if "split_depth=2" in context:
            depth = 2
        elif "split_depth=1" in context:
            depth = 1
        depths_seen.append(depth)
        raise boom

    with (
        patch.object(ag, "load_prompt", return_value="PROMPT"),
        patch.object(runner, "_run_one_agent", side_effect=fake_one),
        patch("asyncio.sleep", new_callable=AsyncMock),
        patch.object(runner.settings, "scene_design_agent_chunk_frames", 0),
    ):
        with pytest.raises(ach.CapacitySplitExhausted, match="после 2 дроблений"):
            await runner._run_one_agent_adaptive(
                project,
                "action",
                frames=frames,
                full_frames=frames,
                slice_extras=[],
                action_scenes=None,
                timeout=10,
            )
    assert max(depths_seen) >= 2


@pytest.mark.asyncio
async def test_action_split_once_then_merge() -> None:
    frames = [
        _fr(1, "a", "Альфа один. Бета два."),
        _fr(2, "b", "Гамма три. Дельта четыре."),
    ]
    project = SimpleNamespace(
        id=60,
        meta={"scene_design_variant": "chrono_dyn"},
        script_text="",
        general_plan="",
    )
    boom = GptApiError(
        "GPT HTTP 524: x",
        context={"status_code": 524, "retryable": True},
    )
    n = {"calls": 0}

    async def fake_one(project, name, context, *, timeout, validate=True, max_retries=None):
        n["calls"] += 1
        if "split_depth=" not in context:
            raise boom
        # один валидный кусок на половину
        idx = 1 if "часть «a»" in context else 2
        return {"scenes": [_scene(idx)]}

    with (
        patch.object(ag, "load_prompt", return_value="PROMPT"),
        patch.object(runner, "_run_one_agent", side_effect=fake_one),
        patch("asyncio.sleep", new_callable=AsyncMock),
        patch.object(runner.settings, "scene_design_agent_chunk_frames", 0),
    ):
        data = await runner._run_one_agent_adaptive(
            project,
            "action",
            frames=frames,
            full_frames=frames,
            slice_extras=[],
            action_scenes=None,
            timeout=10,
        )
    assert n["calls"] == 3  # full fail + 2 halves
    assert [s["id_scene"] for s in data["scenes"]] == ["scene_01", "scene_02"]


@pytest.mark.asyncio
async def test_proactive_chunks_skip_full_payload() -> None:
    """≤N кадров сразу — без попытки full на 65 кадров (не жечь 5 мин)."""
    frames = [_fr(i, f"u{i}", f"кусок {i} старт. кусок {i} финиш.") for i in range(1, 6)]
    project = SimpleNamespace(
        id=60,
        meta={"scene_design_variant": "chrono_dyn"},
        script_text="",
        general_plan="",
    )
    labels: list[str] = []

    async def fake_one(project, name, context, *, timeout, validate=True, max_retries=None):
        assert "split_depth=" in context  # всегда чанк, не full
        assert timeout <= 240  # attempt abort
        if "часть «p1»" in context:
            labels.append("p1")
            return {"scenes": [_scene(1)]}
        if "часть «p2»" in context:
            labels.append("p2")
            return {"scenes": [_scene(2)]}
        if "часть «p3»" in context:
            labels.append("p3")
            return {"scenes": [_scene(3)]}
        raise AssertionError(f"unexpected chunk context: {context[:200]}")

    with (
        patch.object(ag, "load_prompt", return_value="PROMPT"),
        patch.object(runner, "_run_one_agent", side_effect=fake_one),
        patch.object(runner.settings, "scene_design_agent_chunk_frames", 2),
        patch.object(runner.settings, "scene_design_agent_chunk_parallel", 2),
        patch.object(runner.settings, "scene_design_agent_attempt_timeout_s", 240.0),
    ):
        data = await runner._run_one_agent_adaptive(
            project,
            "action",
            frames=frames,
            full_frames=frames,
            slice_extras=[],
            action_scenes=None,
            timeout=900,
        )
    assert sorted(labels) == ["p1", "p2", "p3"]
    assert [s["id_scene"] for s in data["scenes"]] == [
        "scene_01",
        "scene_02",
        "scene_03",
    ]
