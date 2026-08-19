"""Модель с канвас-ноды, не дефолт каталога."""

from __future__ import annotations

from app.models import ProjectStatus
from app.services.llm_override import bind_project_llm, current_text_model_id
from app.services.vibecode_catalog import resolve_node_choice


class _P:
    def __init__(self, meta, *, pid: int = 61):
        self.id = pid
        self.meta = meta


_GRAPH = {
    "canvas_graph": {
        "workflow_id": 1,
        "nodes": [
            {
                "id": "n_excel_gpt_sd_cd_skeleton",
                "type": "excel_gpt",
                "data": {
                    "sd_agent": "skeleton",
                    "modelId": "gpt-5.5",
                    "modelChannel": "cheap",
                },
            },
            {
                "id": "n_excel_gpt_sd_cd_camera",
                "type": "excel_gpt",
                "data": {
                    "sd_agent": "camera",
                    "modelId": "gpt-5.6-sol",
                    "modelChannel": "cheap",
                },
            },
        ],
        "edges": [],
    }
}


def test_resolve_sd_agent_excel_gpt_node_by_type() -> None:
    """Ноды веера — type=excel_gpt, не sd_agent. Ищем по маркеру."""
    choice = resolve_node_choice(_GRAPH, node_type="sd_agent")
    assert choice is not None
    assert choice["id"] == "gpt-5.5"


def test_bind_only_agent_uses_that_nodes_model() -> None:
    meta = {
        **_GRAPH,
        "scene_design": {"only_agent": "camera"},
    }
    with bind_project_llm(_P(meta), ProjectStatus.scene_designing):
        assert current_text_model_id() == "gpt-5.6-sol"


def test_bind_skeleton_click_uses_gpt_55_not_default_56() -> None:
    meta = {
        **_GRAPH,
        "scene_design": {"only_agent": "skeleton"},
    }
    with bind_project_llm(_P(meta), ProjectStatus.scene_designing):
        assert current_text_model_id() == "gpt-5.5"
