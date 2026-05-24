"""Сборка ProjectDetail с live-полями (generation_active)."""

from __future__ import annotations

from app.models import Project
from app.services.step_cancel import is_generation_active
from app.web.schemas import ProjectDetail


def project_to_detail(project: Project) -> ProjectDetail:
    detail = ProjectDetail.model_validate(project)
    detail.generation_active = is_generation_active(project.id)
    return detail
