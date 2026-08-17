"""Единственные разрешённые Excel I/O точки: import / export.

Пайплайн и apply-ops в runtime не пишут и не читают project.xlsx как SoT.
См. docs/superpowers/specs/2026-08-17-excel-import-export-only-design.md
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Project
from app.services.db_apply import export_project_xlsx

__all__ = ["export_project_xlsx", "import_project_xlsx"]


async def import_project_xlsx(
    session: AsyncSession,
    project: Project,
    path: Path | None = None,
    *,
    keep_fields: bool = False,
) -> dict[str, Any]:
    """Явный Excel → DB. Единственный runtime-путь импорта."""
    from app.services.chatgpt_xlsx import sync_project_xlsx

    xlsx = path or (project.data_dir / "project.xlsx")
    return await sync_project_xlsx(
        session, project, xlsx, keep_fields=keep_fields
    )
