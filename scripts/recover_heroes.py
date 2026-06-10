"""Восстановить PNG персонажей проекта из HITL / old/ / Outsee gallery.

Usage:
    .venv\\Scripts\\python.exe scripts\\recover_heroes.py 13
"""
from __future__ import annotations

import asyncio
import sys

from sqlalchemy import select

from app.db import session_scope
from app.models import Project
from app.services.artifact_recovery import recover_hero_references


async def main(project_id: int) -> None:
    async with session_scope() as session:
        project = (
            await session.execute(select(Project).where(Project.id == project_id))
        ).scalar_one_or_none()
        if project is None:
            print(f"project #{project_id} not found")
            return
        restored = await recover_hero_references(session, project)
        await session.commit()
        print(f"[#{project_id}] restored: {restored or '(nothing)'}")


if __name__ == "__main__":
    pid = int(sys.argv[1]) if len(sys.argv) > 1 else 13
    asyncio.run(main(pid))
