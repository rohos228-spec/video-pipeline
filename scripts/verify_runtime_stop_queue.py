"""Runtime-проверка STOP + gen_queue без моков (реальная SQLite + сервисы)."""

from __future__ import annotations

import asyncio
import sys

from loguru import logger
from sqlalchemy import select

from app.db import session_scope
from app.models import Project, ProjectStatus
from app.orchestrator.auto_advance import maybe_auto_advance
from app.services.gen_queue import gen_queue_blocks_project, gen_queue_tick
from app.services.gen_queue_run import is_user_stopped, set_gen_queue_run
from app.services.project_control import stop_project_running
from app.services.sidebar_layout import set_gen_queue


async def _reset_queue(ids: list[int]) -> None:
    set_gen_queue(ids)


async def _ensure_verify_projects(session) -> list[Project]:
    """Переиспользовать runtime-verify-* из БД или создать заново."""
    rows = (
        await session.execute(
            select(Project).where(Project.slug.like("runtime-verify-%"))
        )
    ).scalars().all()
    by_slug = {p.slug: p for p in rows}
    projects: list[Project] = []
    for i in range(1, 5):
        slug = f"runtime-verify-{i}"
        p = by_slug.get(slug)
        if p is None:
            p = Project(
                slug=slug,
                topic=f"verify {i}",
                status=ProjectStatus.script_ready,
                auto_mode=True,
                script_text="x" * 600,
                general_plan="y" * 600,
            )
            session.add(p)
        else:
            p.status = ProjectStatus.script_ready
            p.auto_mode = True
            meta = dict(p.meta or {})
            meta.pop("user_stop", None)
            meta.pop("mass_lane_user_stop", None)
            p.meta = meta
        projects.append(p)
    await session.flush()
    return projects


async def main() -> int:
    errors: list[str] = []

    async with session_scope() as s:
        projects = await _ensure_verify_projects(s)
        ids = [p.id for p in projects]
        await _reset_queue(sorted(ids))

        for p in projects:
            await set_gen_queue_run(
                s, p, mode="until_node", target_node_type="script"
            )
        await s.commit()

        p2 = projects[1]
        async with session_scope() as s2:
            p2 = await s2.get(Project, p2.id)
            assert p2 is not None
            info = await stop_project_running(s2, p2)
            await s2.commit()
            if not info["ok"]:
                errors.append(f"STOP ready failed: {info}")
            if not is_user_stopped(p2):
                errors.append("user_stop not set after STOP on script_ready")

        async with session_scope() as s3:
            p2 = await s3.get(Project, p2.id)
            p4 = await s3.get(Project, projects[3].id)
            assert p2 and p4
            adv = await maybe_auto_advance(s3, p2, bot=None)
            if adv:
                errors.append("#2 auto_advance after STOP must be False")
            blocker = await gen_queue_blocks_project(s3, p4.id)
            if blocker != p2.id:
                errors.append(
                    f"#4 should be blocked by #2 user_stop, got blocker={blocker}"
                )

        async with session_scope() as s4:
            rows = (
                await s4.execute(select(Project).where(Project.slug.like("runtime-verify-%")))
            ).scalars().all()
            for p in rows:
                p.status = ProjectStatus.new
                meta = dict(p.meta or {})
                meta.pop("user_stop", None)
                p.meta = meta
            await s4.commit()

        await _reset_queue(sorted(ids))
        async with session_scope() as s5:
            rows = (
                await s5.execute(select(Project).where(Project.slug.like("runtime-verify-%")))
            ).scalars().all()
            by_id = {p.id: p for p in rows}
            for pid in sorted(ids):
                p = by_id[pid]
                await set_gen_queue_run(
                    s5, p, mode="until_node", target_node_type="script"
                )
                p.auto_mode = True
            await s5.commit()
            started = await gen_queue_tick(s5)
            await s5.commit()
            first = by_id[sorted(ids)[0]]
            await s5.refresh(first)
            if started != 1 or first.status != ProjectStatus.planning:
                errors.append(
                    f"gen_queue_tick should start #1 plan, got started={started} "
                    f"status={first.status.value}"
                )

    if errors:
        for e in errors:
            logger.error("VERIFY FAIL: {}", e)
        return 1
    logger.info("VERIFY OK: STOP iron gate + gen_queue serial order")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
