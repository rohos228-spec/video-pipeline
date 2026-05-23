"""Учёт отключённых нод workflow в meta.disabled_nodes (ключи n_*)."""

from __future__ import annotations

from app.models import Project, ProjectStatus

# step_code (Telegram / API) → node_type (workflow / UI).
STEP_CODE_TO_NODE_TYPE: dict[str, str] = {
    "plan": "plan",
    "script": "script",
    "split": "split",
    "hero": "hero",
    "items": "items",
    "enrich_1": "enrich_1",
    "enrich_2": "enrich_2",
    "enrich_3": "enrich_3",
    "enrich_4": "enrich_4",
    "enrich_5": "enrich_5",
    "img_pr": "image_prompts",
    "img": "images",
    "anim_pr": "animation_prompts",
    "video": "videos",
    "audio": "audio",
    "assemble": "assemble",
    "publish": "publish",
}

KNOWN_NODE_TYPES: tuple[str, ...] = tuple(
    sorted(set(STEP_CODE_TO_NODE_TYPE.values()), key=len, reverse=True)
)

# Линейный порядок running-статусов (как в воркере).
RUNNING_PIPELINE: list[tuple[ProjectStatus, str]] = [
    (ProjectStatus.planning, "plan"),
    (ProjectStatus.scripting, "script"),
    (ProjectStatus.splitting, "split"),
    (ProjectStatus.generating_hero, "hero"),
    (ProjectStatus.generating_items, "items"),
    (ProjectStatus.enriching_1, "enrich_1"),
    (ProjectStatus.enriching_2, "enrich_2"),
    (ProjectStatus.enriching_3, "enrich_3"),
    (ProjectStatus.enriching_4, "enrich_4"),
    (ProjectStatus.enriching_5, "enrich_5"),
    (ProjectStatus.generating_image_prompts, "image_prompts"),
    (ProjectStatus.generating_images, "images"),
    (ProjectStatus.generating_animation_prompts, "animation_prompts"),
    (ProjectStatus.generating_videos, "videos"),
    (ProjectStatus.generating_audio, "audio"),
    (ProjectStatus.assembling, "assemble"),
    (ProjectStatus.publishing, "publish"),
]

_RUNNING_BY_TYPE = {typ: st for st, typ in RUNNING_PIPELINE}


def node_type_from_key(node_key: str) -> str | None:
    """n_plan → plan, n_plan_1700000000 → plan, n_enrich_1 → enrich_1."""
    key = (node_key or "").strip()
    if not key:
        return None
    if not key.startswith("n_"):
        return key if key in KNOWN_NODE_TYPES else None
    rest = key[2:]
    if rest in KNOWN_NODE_TYPES:
        return rest
    for typ in KNOWN_NODE_TYPES:
        if rest == typ or rest.startswith(f"{typ}_"):
            return typ
    return None


def disabled_node_types(project: Project) -> set[str]:
    meta = project.meta if isinstance(project.meta, dict) else {}
    keys = meta.get("disabled_nodes") or []
    out: set[str] = set()
    for k in keys:
        t = node_type_from_key(str(k))
        if t:
            out.add(t)
    return out


def step_code_to_node_type(step_code: str) -> str | None:
    return STEP_CODE_TO_NODE_TYPE.get(step_code)


def is_step_disabled(project: Project, step_code: str) -> bool:
    typ = step_code_to_node_type(step_code)
    if typ is None:
        return False
    return typ in disabled_node_types(project)


def is_node_type_disabled(project: Project, node_type: str) -> bool:
    return node_type in disabled_node_types(project)


def skip_disabled_running(
    project: Project,
    target: ProjectStatus | None,
) -> ProjectStatus | None:
    """Если running-шаг отключён в UI — перейти к следующему включённому."""
    if target is None:
        return None
    disabled = disabled_node_types(project)
    if not disabled:
        return target
    start_idx = 0
    for i, (st, _) in enumerate(RUNNING_PIPELINE):
        if st == target:
            start_idx = i
            break
    for st, typ in RUNNING_PIPELINE[start_idx:]:
        if typ not in disabled:
            return st
    return None
