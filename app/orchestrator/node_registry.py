"""Канонический реестр рабочих нод пайплайна (не hitl_*)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.models import ProjectStatus
from app.services.excel_gpt_node import (
    EXCEL_GPT_NODE_TYPE,
    EXCEL_GPT_STEP_CODE,
    is_excel_gpt_node_type,
    ready_status_for_slot,
    running_status_for_slot,
    slot_index_from_node,
)

HITL_NODE_TYPES: frozenset[str] = frozenset(
    {"hitl_hero", "hitl_images", "hitl_videos", "hitl_final", "hitl_gate"}
)

CONFIG_NODE_TYPES: frozenset[str] = frozenset({"topic", "storage"})


@dataclass(frozen=True)
class WorkNodeSpec:
    node_type: str
    step_code: str
    running_status: ProjectStatus
    ready_status: ProjectStatus


WORK_NODES: dict[str, WorkNodeSpec] = {
    "plan": WorkNodeSpec("plan", "plan", ProjectStatus.planning, ProjectStatus.plan_ready),
    "script": WorkNodeSpec(
        "script", "script", ProjectStatus.scripting, ProjectStatus.script_ready
    ),
    "split": WorkNodeSpec(
        "split", "split", ProjectStatus.splitting, ProjectStatus.frames_ready
    ),
    # scene_design — legacy-тип старых канвасов (одна нода на всю ноду-фазу).
    # Канон: веер sd_agent ×5 (data.agent=characters/world/style/camera/action)
    # → sd_assemble. Оба типа делят step scene_d/scene_asm; в обратных map'ах
    # (RUNNING/READY_TO_NODE_TYPE) побеждают sd_agent/sd_assemble (см. ниже).
    "scene_design": WorkNodeSpec(
        "scene_design",
        "scene_d",
        ProjectStatus.scene_designing,
        ProjectStatus.scene_design_ready,
    ),
    "sd_agent": WorkNodeSpec(
        "sd_agent",
        "scene_d",
        ProjectStatus.scene_designing,
        ProjectStatus.scene_agents_ready,
    ),
    "sd_assemble": WorkNodeSpec(
        "sd_assemble",
        "scene_asm",
        ProjectStatus.scene_assembling,
        ProjectStatus.scene_design_ready,
    ),
    "hero": WorkNodeSpec(
        "hero", "hero", ProjectStatus.generating_hero, ProjectStatus.hero_ready
    ),
    "items": WorkNodeSpec(
        "items", "items", ProjectStatus.generating_items, ProjectStatus.items_ready
    ),
    "enrich_1": WorkNodeSpec(
        "enrich_1", "enrich_1", ProjectStatus.enriching_1, ProjectStatus.enrich_1_ready
    ),
    "enrich_2": WorkNodeSpec(
        "enrich_2", "enrich_2", ProjectStatus.enriching_2, ProjectStatus.enrich_2_ready
    ),
    "enrich_3": WorkNodeSpec(
        "enrich_3", "enrich_3", ProjectStatus.enriching_3, ProjectStatus.enrich_3_ready
    ),
    "enrich_4": WorkNodeSpec(
        "enrich_4", "enrich_4", ProjectStatus.enriching_4, ProjectStatus.enrich_4_ready
    ),
    "enrich_5": WorkNodeSpec(
        "enrich_5", "enrich_5", ProjectStatus.enriching_5, ProjectStatus.enrich_5_ready
    ),
    "image_prompts": WorkNodeSpec(
        "image_prompts",
        "img_pr",
        ProjectStatus.generating_image_prompts,
        ProjectStatus.image_prompts_ready,
    ),
    "images": WorkNodeSpec(
        "images", "img", ProjectStatus.generating_images, ProjectStatus.images_ready
    ),
    "animation_prompts": WorkNodeSpec(
        "animation_prompts",
        "anim_pr",
        ProjectStatus.generating_animation_prompts,
        ProjectStatus.animation_prompts_ready,
    ),
    "videos": WorkNodeSpec(
        "videos", "video", ProjectStatus.generating_videos, ProjectStatus.videos_ready
    ),
    "audio": WorkNodeSpec(
        "audio", "audio", ProjectStatus.generating_audio, ProjectStatus.audio_ready
    ),
    "music": WorkNodeSpec(
        "music", "music", ProjectStatus.generating_music, ProjectStatus.music_ready
    ),
    "assemble": WorkNodeSpec(
        "assemble", "assemble", ProjectStatus.assembling, ProjectStatus.assembled
    ),
    "publish": WorkNodeSpec(
        "publish", "publish", ProjectStatus.publishing, ProjectStatus.published
    ),
}

STEP_CODE_TO_NODE_TYPE: dict[str, str] = {s.step_code: s.node_type for s in WORK_NODES.values()}
STEP_CODE_TO_NODE_TYPE[EXCEL_GPT_STEP_CODE] = EXCEL_GPT_NODE_TYPE
NODE_TYPE_TO_STEP_CODE: dict[str, str] = {s.node_type: s.step_code for s in WORK_NODES.values()}
NODE_TYPE_TO_STEP_CODE[EXCEL_GPT_NODE_TYPE] = EXCEL_GPT_STEP_CODE

# Per-agent ручной перезапуск (canvas node ▶ / API). Все ведут на тип sd_agent;
# конкретный агент — из step code.
SD_AGENT_STEP_CODES: dict[str, str] = {
    "sd_skel": "skeleton",
    "sd_char": "characters",
    "sd_world": "world",
    "sd_style": "style",
    "sd_cam": "camera",
    "sd_act": "action",
}
for _code in SD_AGENT_STEP_CODES:
    STEP_CODE_TO_NODE_TYPE[_code] = "sd_agent"

RUNNING_TO_NODE_TYPE: dict[ProjectStatus, str] = {
    s.running_status: s.node_type for s in WORK_NODES.values()
}
READY_TO_NODE_TYPE: dict[ProjectStatus, str] = {
    s.ready_status: s.node_type for s in WORK_NODES.values()
}
# Коллизии legacy scene_design vs веер sd_*: канон — sd_agent/sd_assemble.
RUNNING_TO_NODE_TYPE[ProjectStatus.scene_designing] = "sd_agent"
RUNNING_TO_NODE_TYPE[ProjectStatus.scene_assembling] = "sd_assemble"
READY_TO_NODE_TYPE[ProjectStatus.scene_agents_ready] = "sd_agent"
READY_TO_NODE_TYPE[ProjectStatus.scene_design_ready] = "sd_assemble"
NODE_TYPE_TO_RUNNING: dict[str, ProjectStatus] = {
    s.node_type: s.running_status for s in WORK_NODES.values()
}
NODE_TYPE_TO_READY: dict[str, ProjectStatus] = {s.node_type: s.ready_status for s in WORK_NODES.values()}

LINEAR_NODE_TYPES: list[str] = [
    "plan",
    "script",
    "split",
    "sd_agent",
    "sd_assemble",
    "hero",
    "items",
    "enrich_1",
    "enrich_2",
    "enrich_3",
    "enrich_4",
    "enrich_5",
    "image_prompts",
    "images",
    "animation_prompts",
    "videos",
    "audio",
    "music",
    "assemble",
    "publish",
]

LINEAR_RUNNING_PIPELINE: list[tuple[ProjectStatus, str]] = [
    (NODE_TYPE_TO_RUNNING[t], t) for t in LINEAR_NODE_TYPES
]


def is_work_node_type(node_type: str) -> bool:
    return node_type in WORK_NODES or node_type == EXCEL_GPT_NODE_TYPE


def is_hitl_node_type(node_type: str) -> bool:
    return node_type.startswith("hitl_")


def is_config_node_type(node_type: str) -> bool:
    return node_type in CONFIG_NODE_TYPES


def spec_for_type(node_type: str) -> WorkNodeSpec | None:
    if node_type == EXCEL_GPT_NODE_TYPE:
        return WorkNodeSpec(
            EXCEL_GPT_NODE_TYPE,
            EXCEL_GPT_STEP_CODE,
            ProjectStatus.enriching_1,
            ProjectStatus.enrich_1_ready,
        )
    return WORK_NODES.get(node_type)


def excel_gpt_spec_for_node(node: dict[str, Any]) -> WorkNodeSpec:
    slot = slot_index_from_node(node)
    return WorkNodeSpec(
        EXCEL_GPT_NODE_TYPE,
        EXCEL_GPT_STEP_CODE,
        running_status_for_slot(slot),
        ready_status_for_slot(slot),
    )


def spec_for_node(node: dict[str, Any]) -> WorkNodeSpec | None:
    from app.services.excel_gpt_node import effective_node_type

    typ = effective_node_type(node)
    if typ == EXCEL_GPT_NODE_TYPE or is_excel_gpt_node_type(typ):
        return excel_gpt_spec_for_node(node)
    return spec_for_type(typ)


def spec_for_step_code(step_code: str) -> WorkNodeSpec | None:
    if step_code == EXCEL_GPT_STEP_CODE:
        return WorkNodeSpec(
            EXCEL_GPT_NODE_TYPE,
            EXCEL_GPT_STEP_CODE,
            ProjectStatus.enriching_1,
            ProjectStatus.enrich_1_ready,
        )
    typ = STEP_CODE_TO_NODE_TYPE.get(step_code)
    return WORK_NODES.get(typ) if typ else None
