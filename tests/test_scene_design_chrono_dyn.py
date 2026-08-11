"""chrono_dyn: волны агентов, required_shots, варианты промптов."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.node_groups import NODE_GROUPS, get_node_group
from app.services.scene_design import agents as ag
from app.services.scene_design.camera_expand import required_shots_for_beat


def test_chrono_dyn_group_in_palette() -> None:
    g = get_node_group("scene_design_fanout_chrono_dyn")
    assert g is not None
    assert g.group_id in NODE_GROUPS
    variants = {n.prompt_variant for n in g.nodes if n.prompt_variant}
    assert "sd_action_chrono_dyn" in variants
    assert "sd_camera_chrono_dyn" in variants
    assert "sd_assemble_chrono_dyn" in variants
    assert g.project_meta.get("scene_design_variant") == "chrono_dyn"
    assert g.exit_key == "assemble"
    assert all(n.operator_config is None for n in g.nodes)
    assert len(g.nodes) == 6
    edge_pairs = {(a, b) for a, b, _k in g.internal_edges}
    assert ("action", "camera") in edge_pairs
    assert ("camera", "assemble") in edge_pairs
    assert not any(a.startswith("check_") or b.startswith("check_") for a, b in edge_pairs)


def test_uses_chrono_dyn_from_meta() -> None:
    p = SimpleNamespace(meta={"scene_design_variant": "chrono_dyn"})
    assert ag.uses_chrono_dyn(p) is True
    assert ag.uses_chrono_dyn(SimpleNamespace(meta={})) is False


def test_validate_chrono_dyn_action_rejects_flat_two_phases() -> None:
    scenes = [
        {
            "id_scene": f"scene_{i:02d}",
            "цепь_действия": [
                {
                    "phase_index": 1,
                    "action": "crowd goes",
                    "subject": "crowd",
                    "orientation": "side",
                    "info_change": "a",
                },
                {
                    "phase_index": 2,
                    "action": "door stays",
                    "subject": "prop",
                    "orientation": "object",
                    "info_change": "b",
                },
            ],
        }
        for i in range(1, 8)
    ]
    with pytest.raises(ag.SceneDesignAgentError, match="плотности"):
        ag.validate_chrono_dyn_action_scenes(scenes)


def test_validate_chrono_dyn_action_accepts_dense_cnn() -> None:
    beats = ("setup", "develop", "turn", "payoff")
    scenes = [
        {
            "id_scene": f"scene_{i:02d}",
            "связь_с_прошлой": "продолжение прошлого бита" if i > 1 else "",
            "крючок_в_следующую": "открытый жест в следующую сцену",
            "цепь_действия": [
                {
                    "phase_index": p,
                    "beat": beats[p - 1],
                    "action": f"beat {p}",
                    "subject": f"c0{(p % 3) + 1}",
                    "orientation": "face",
                    "info_change": f"info {p}",
                    "переход_к_следующей": "cut_on_action",
                }
                for p in range(1, 5)
            ],
        }
        for i in range(1, 6)
    ]
    ag.validate_chrono_dyn_action_scenes(scenes)


def test_validate_chrono_dyn_action_rejects_unlinked_scenes() -> None:
    scenes = [
        {
            "id_scene": f"scene_{i:02d}",
            "цепь_действия": [
                {
                    "phase_index": p,
                    "beat": "setup" if p == 1 else ("payoff" if p == 3 else "develop"),
                    "action": f"a{p}",
                    "subject": "c01",
                    "orientation": "face",
                    "info_change": "x",
                    "переход_к_следующей": "cut",
                }
                for p in range(1, 4)
            ],
        }
        for i in range(1, 5)
    ]
    with pytest.raises(ag.SceneDesignAgentError, match="связь_с_прошлой|крючок"):
        ag.validate_chrono_dyn_action_scenes(scenes)


def test_required_shots_chrono_dyn_uses_phases() -> None:
    n = required_shots_for_beat(
        {"крупность": "MS", "набор": "SET_01"},
        {"SET_01": (3, 5)},
        duration_sec=12.0,
        chrono_dyn=True,
        action_phases=4,
    )
    assert n == 4


def test_required_shots_legacy_forces_ladder() -> None:
    n = required_shots_for_beat(
        {"крупность": "MS", "набор": ""},
        {},
        duration_sec=12.0,
        chrono_dyn=False,
        action_phases=0,
    )
    assert n >= 3


@pytest.mark.asyncio
async def test_run_category_agents_camera_after_action() -> None:
    from app.services.scene_design import runner

    order: list[str] = []

    async def fake_one(project, name, context, *, timeout):
        order.append(name)
        if name == "action":
            assert "CHARACTERS SLICE" in context
            return {"scenes": [{"id_scene": "scene_01", "цепь_действия": ["a", "b"]}]}
        if name == "camera":
            assert "ACTION SCENES" in context
            return {"shot_plan": [{"цитата": "x", "крупность": "MS"}]}
        return {ag.LIST_KEY[name]: [{"id": "x"}]}

    project = SimpleNamespace(id=1, meta={"scene_design_variant": "chrono_dyn"})

    with (
        patch.object(runner, "load_checkpoint", return_value=None),
        patch.object(runner, "save_checkpoint", MagicMock()),
        patch.object(runner, "_run_one_agent", side_effect=fake_one),
    ):
        await runner.run_category_agents(project, "CTX", timeout=10)

    # chrono_dyn: верхние 3 → action → camera
    for top in ("characters", "world", "style"):
        assert order.index(top) < order.index("action")
    assert order.index("action") < order.index("camera")
    assert ag.agent_waves(project) == (
        ag.CHRONO_WAVE1_AGENTS,
        ag.CHRONO_WAVE2_AGENTS,
        ag.CHRONO_WAVE3_AGENTS,
    )
