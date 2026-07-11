"""Runtime + unit проверка gen_queue: reconcile, порядок, блокировки."""

from __future__ import annotations

import asyncio
import sys

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db import session_scope
from app.models import Base, Project, ProjectStatus
from app.services.gen_queue import (
    gen_queue_blocks_project,
    gen_queue_reconcile,
    gen_queue_tick,
)
from app.services.gen_queue_run import set_gen_queue_run
from app.services.sidebar_layout import set_gen_queue


async def _mem_session() -> tuple[AsyncSession, any]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    session = factory()
    return session, engine


async def test_reconcile_rolls_back_out_of_turn_runner() -> None:
    session, engine = await _mem_session()
    try:
        for pid, st in [(2, ProjectStatus.paused), (3, ProjectStatus.planning)]:
            p = Project(
                id=pid,
                slug=f"p{pid}",
                topic=f"t{pid}",
                status=st,
                auto_mode=True,
                meta={
                    "gen_queue_run": {
                        "mode": "until_node",
                        "target_node_type": "script",
                        "complete": False,
                    }
                },
            )
            session.add(p)
        await session.flush()

        import app.services.gen_queue as gq

        orig = gq.get_gen_queue
        gq.get_gen_queue = lambda: [2, 3, 4]
        try:
            rolled = await gen_queue_reconcile(session)
            await session.flush()
            p3 = await session.get(Project, 3)
            assert rolled == 1, f"expected 1 rollback, got {rolled}"
            assert p3 is not None and p3.status is ProjectStatus.new
        finally:
            gq.get_gen_queue = orig
    finally:
        await session.close()
        await engine.dispose()


async def test_later_blocked_while_head_paused() -> None:
    session, engine = await _mem_session()
    try:
        for pid, st in [
            (2, ProjectStatus.paused),
            (4, ProjectStatus.new),
        ]:
            session.add(
                Project(
                    id=pid,
                    slug=f"p{pid}",
                    topic=f"t{pid}",
                    status=st,
                    auto_mode=True,
                    meta={
                        "gen_queue_run": {
                            "mode": "until_node",
                            "target_node_type": "script",
                            "complete": False,
                        }
                    },
                )
            )
        await session.flush()
        import app.services.gen_queue as gq

        orig = gq.get_gen_queue
        gq.get_gen_queue = lambda: [2, 3, 4]
        try:
            assert await gen_queue_blocks_project(session, 4) == 2
            started = await gen_queue_tick(session)
            assert started == 0
        finally:
            gq.get_gen_queue = orig
    finally:
        await session.close()
        await engine.dispose()


async def test_live_db_reconcile() -> None:
    async with session_scope() as session:
        rolled = await gen_queue_reconcile(session)
        if rolled:
            await session.commit()
            logger.info("live reconcile: rolled back {} runner(s)", rolled)
        queue_rows = []
        from app.services.sidebar_layout import get_gen_queue

        queue = get_gen_queue()
        for pid in queue:
            p = await session.get(Project, pid)
            if p:
                queue_rows.append(f"#{p.id} {p.status.value}")
        logger.info("live queue {} states: {}", queue, queue_rows)


async def main() -> int:
    errors: list[str] = []
    for fn in (
        test_reconcile_rolls_back_out_of_turn_runner,
        test_later_blocked_while_head_paused,
    ):
        try:
            await fn()
            logger.info("OK: {}", fn.__name__)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{fn.__name__}: {e}")
            logger.exception("FAIL: {}", fn.__name__)

    try:
        await test_live_db_reconcile()
    except Exception as e:  # noqa: BLE001
        errors.append(f"live_db: {e}")

    if errors:
        for e in errors:
            logger.error("VERIFY FAIL: {}", e)
        return 1
    logger.info("VERIFY OK: gen_queue reconcile + strict order")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
