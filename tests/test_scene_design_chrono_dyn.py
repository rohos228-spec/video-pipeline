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
    # action → camera на канвасе
    edge_pairs = {(a, b) for a, b, _k in g.internal_edges}
    assert ("check_action", "camera") in edge_pairs


def test_uses_chrono_dyn_from_meta() -> None:
    p = SimpleNamespace(meta={"scene_design_variant": "chrono_dyn"})
    assert ag.uses_chrono_dyn(p) is True
    assert ag.uses_chrono_dyn(SimpleNamespace(meta={})) is False


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

    assert order.index("action") < order.index("camera")
    assert "characters" in order
