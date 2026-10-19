from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("e2e_vibecode_text_nodes_seed", ROOT / "scripts" / "e2e_vibecode_text_nodes.py")
stand = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = stand
_spec.loader.exec_module(stand)


def _project(tmp_path):
    return SimpleNamespace(id=1, data_dir=tmp_path, meta={"scene_design_skeleton": True, "scene_design": {"only_agent": "world"}})


def test_stand_skeleton_is_loadable_checkpoint(tmp_path):
    from app.services.scene_design import agents as ag
    from app.services.scene_design import runner

    project = _project(tmp_path)
    runner.save_checkpoint(project, "skeleton", stand.WORLD_SKELETON)
    loaded = runner.load_checkpoint(project, "skeleton")
    assert loaded is not None, "empty/invalid skeleton scenes make load_checkpoint return None"
    assert ag.skeleton_location_seed_ids(project) == {"loc01"}
    assert {loc["id"] for loc in stand.GOOD_WORLD["locations"]} == {"loc01"}
    assert {sc["место_id"] for sc in loaded["scenes"]} == {"loc01"}


def test_empty_scenes_skeleton_is_not_a_checkpoint(tmp_path):
    from app.services.scene_design import runner

    project = _project(tmp_path)
    runner.save_checkpoint(project, "skeleton", {"locations_seed": [{"id": "loc01"}], "scenes": []})
    assert runner.load_checkpoint(project, "skeleton") is None


@pytest.mark.asyncio
async def test_world_request_carries_skeleton_block(tmp_path):
    from app.services import gpt_client
    from app.services.scene_design import runner

    project = _project(tmp_path)
    runner.save_checkpoint(project, "skeleton", stand.WORLD_SKELETON)
    sent: list[str] = []

    async def fake_ask(text, **_kw):
        sent.append(text)
        import json
        return json.dumps(stand.GOOD_WORLD, ensure_ascii=False)

    with patch.object(gpt_client, "gpt_ask_fresh", fake_ask):
        out = await runner.run_category_agents(project, "CTX", timeout=10, only_agent="world")
    assert out["world"]["locations"][0]["id"] == "loc01"
    assert len(sent) == 1
    assert "SKELETON (JSON)" in sent[0]
    assert '"locations_seed":[{"id":"loc01"' in sent[0]
