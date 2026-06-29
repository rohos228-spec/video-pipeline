"""Pause a project by id (stops worker loop). Usage: python scripts/pause_project.py 13"""
import asyncio
import sys

from app.db import session_scope
from app.models import Project, ProjectStatus


async def main() -> None:
    pid = int(sys.argv[1]) if len(sys.argv) > 1 else 13
    async with session_scope() as session:
        project = await session.get(Project, pid)
        if project is None:
            print(f"project #{pid} not found")
            return
        project.status = ProjectStatus.paused
        meta = dict(project.meta or {})
        meta["assemble_blocked"] = meta.get("assemble_blocked") or "paused manually"
        meta.pop("montage_queue_enqueued", None)
        project.meta = meta
        print(f"paused #{project.id} {project.slug}")


if __name__ == "__main__":
    asyncio.run(main())
