"""Pydantic DTO для web API.

Отдельный модуль, чтобы не тащить SQLAlchemy в схемы и избежать циклов.
Все DTO работают на сериализации в JSON для веб-фронтенда.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


# ── Общие ──


class _ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ── Project ──


class ProjectSummary(_ORM):
    id: int
    slug: str
    topic: str
    status: str
    hero_mode: str
    auto_mode: bool
    created_at: datetime
    updated_at: datetime
    mass_parent_id: int | None = None
    mass_factory: bool = False
    mass_lane_position: int | None = None
    sidebar_folder_id: str | None = None
    sidebar_order: int | None = None
    gen_queue_position: int | None = None


class ProjectDetail(ProjectSummary):
    @model_validator(mode="before")
    @classmethod
    def _coerce_null_collections(cls, data: Any) -> Any:
        """SQLite/ORM часто отдаёт NULL вместо []/{} — иначе model_validate падает."""
        from app.models import Project

        if isinstance(data, Project):
            data = {col.key: getattr(data, col.key) for col in data.__table__.columns}
        if isinstance(data, dict):
            for key in (
                "hero_descriptions",
                "hero_variations",
                "hero_variation_modifiers",
                "item_descriptions",
                "item_variations",
            ):
                if data.get(key) is None:
                    data[key] = []
            for key in ("prompt_overrides", "gpt_text_overrides", "meta"):
                if data.get(key) is None:
                    data[key] = {}
        return data

    general_plan: str | None = None
    script_text: str | None = None
    hero_description: str | None = None
    image_generator: str | None = None
    aspect_ratio: str | None = None
    image_resolution: str | None = None
    image_quality: str | None = None
    image_relax: bool | None = None
    video_generator: str | None = None
    video_resolution: str | None = None
    video_relax: bool | None = None
    hero_count: int | None = None
    hero_descriptions: list[str] = Field(default_factory=list)
    hero_variations: list[int] = Field(default_factory=list)
    hero_variation_modifiers: list[Any] = Field(default_factory=list)
    item_descriptions: list[str] = Field(default_factory=list)
    item_variations: list[int] = Field(default_factory=list)
    enrich_slots_count: int = 3
    # JSON: legacy step→variant strings + blocks/vars/style_profile/use_blocks_v2
    prompt_overrides: dict[str, Any] = Field(default_factory=dict)
    gpt_text_overrides: dict[str, str] = Field(default_factory=dict)
    meta: dict[str, Any] = Field(default_factory=dict)
    generation_active: bool = False


class CreateProjectRequest(BaseModel):
    topic: str
    hero_mode: str = "auto"  # hero | no_hero | auto
    workflow_id: int | None = None  # если None — берём дефолтный
    auto_mode: bool = False
    sidebar_folder_id: str | None = None


# ── Frame ──


class FrameDTO(_ORM):
    id: int
    project_id: int
    number: int
    voiceover_text: str
    meaning: str | None = None
    transition_from: str | None = None
    transition_to: str | None = None
    duration_seconds: float | None = None
    start_ts: float | None = None
    end_ts: float | None = None
    image_prompt: str | None = None
    animation_prompt: str | None = None
    status: str
    attrs: dict[str, Any] = Field(default_factory=dict)


class UpdateFrameRequest(BaseModel):
    voiceover_text: str | None = None
    meaning: str | None = None
    image_prompt: str | None = None
    animation_prompt: str | None = None
    duration_seconds: float | None = None
    status: str | None = None


# ── Workflow ──


class WorkflowNodeDTO(BaseModel):
    """Один узел графа. Совместим с шейпом @xyflow/react."""

    id: str
    type: str
    position: dict[str, float] = Field(default_factory=lambda: {"x": 0.0, "y": 0.0})
    data: dict[str, Any] = Field(default_factory=dict)


class WorkflowEdgeDTO(BaseModel):
    id: str
    source: str
    target: str
    sourceHandle: str | None = None  # noqa: N815 — соответствует @xyflow/react
    targetHandle: str | None = None  # noqa: N815


class WorkflowSummary(_ORM):
    id: int
    name: str
    description: str | None = None
    version: int
    is_default: bool
    created_at: datetime
    updated_at: datetime


class WorkflowDetail(WorkflowSummary):
    nodes: list[WorkflowNodeDTO] = Field(default_factory=list)
    edges: list[WorkflowEdgeDTO] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)


class WorkflowSaveRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    nodes: list[WorkflowNodeDTO]
    edges: list[WorkflowEdgeDTO]


# ── Workflow Run / Node Run ──


class NodeRunDTO(_ORM):
    id: int
    workflow_run_id: int
    node_key: str
    node_type: str
    status: str
    progress: int
    progress_text: str | None = None
    error: str | None = None
    hitl_request_id: int | None = None
    attempts: int
    started_at: datetime | None = None
    finished_at: datetime | None = None
    updated_at: datetime
    meta: dict[str, Any] = Field(default_factory=dict)


class WorkflowRunSummary(_ORM):
    id: int
    workflow_id: int
    project_id: int
    status: str
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class WorkflowRunDetail(WorkflowRunSummary):
    nodes_snapshot: list[WorkflowNodeDTO] = Field(default_factory=list)
    edges_snapshot: list[WorkflowEdgeDTO] = Field(default_factory=list)
    node_runs: list[NodeRunDTO] = Field(default_factory=list)


class StartRunRequest(BaseModel):
    project_id: int | None = None
    # Если project_id не задан, создаст новый проект на основе topic.
    topic: str | None = None
    hero_mode: str = "auto"


# ── Prompt ──


class PromptDTO(_ORM):
    id: int
    key: str
    version: int
    text: str
    active: bool
    created_at: datetime


# ── HITL ──


class HITLDTO(_ORM):
    id: int
    project_id: int
    frame_id: int | None = None
    kind: str
    decision: str
    payload: dict[str, Any] = Field(default_factory=dict)
    decided_at: datetime | None = None
    created_at: datetime


class HITLDecisionRequest(BaseModel):
    decision: str  # approve | regenerate | reject | edit_prompt
    edited_prompt: str | None = None


# ── Artifact ──


class ArtifactDTO(_ORM):
    id: int
    project_id: int
    frame_id: int | None = None
    kind: str
    uuid: str
    path: str
    meta: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
