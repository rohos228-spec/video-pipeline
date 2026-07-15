"""Сборка ProjectDetail / ProjectSummary с live-полями."""

from __future__ import annotations

from app.models import Project
from app.services.mass_factory import is_mass_factory_parent, mass_parent_id
from app.services.step_cancel import is_generation_active
from app.web.schemas import ProjectDetail, ProjectSummary


def project_to_summary(
    project: Project,
    *,
    sidebar_folder_id: str | None = None,
    sidebar_order: int | None = None,
    gen_queue_position: int | None = None,
) -> ProjectSummary:
    meta = project.meta if isinstance(project.meta, dict) else {}
    lane_raw = meta.get("mass_lane_position")
    lane_pos: int | None
    try:
        lane_pos = int(lane_raw) if lane_raw is not None else None
    except (TypeError, ValueError):
        lane_pos = None
    return ProjectSummary(
        id=project.id,
        slug=project.slug,
        topic=project.topic,
        status=project.status.value,
        hero_mode=project.hero_mode,
        auto_mode=bool(project.auto_mode),
        created_at=project.created_at,
        updated_at=project.updated_at,
        mass_parent_id=mass_parent_id(project),
        mass_factory=is_mass_factory_parent(project),
        mass_lane_position=lane_pos,
        batch_id=project.batch_id,
        batch_position=project.batch_position,
        sidebar_folder_id=sidebar_folder_id,
        sidebar_order=sidebar_order,
        gen_queue_position=gen_queue_position,
    )


def project_to_detail(project: Project) -> ProjectDetail:
    detail = ProjectDetail.model_validate(project)
    meta = project.meta if isinstance(project.meta, dict) else {}
    detail.mass_parent_id = mass_parent_id(project)
    detail.mass_factory = is_mass_factory_parent(project)
    lane_raw = meta.get("mass_lane_position")
    try:
        detail.mass_lane_position = int(lane_raw) if lane_raw is not None else None
    except (TypeError, ValueError):
        detail.mass_lane_position = None
    detail.generation_active = is_generation_active(project.id)
    return detail
