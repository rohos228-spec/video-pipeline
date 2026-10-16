import asyncio
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import test_actions_v1_contract as actions_tests
from app.services import spend_guard
from app.settings import settings


def no_other_caps(run=0.0):
    return (patch.object(settings, "spend_run_cap_usd", run), patch.object(settings, "spend_daily_cap_usd", 0.0),
            patch.object(settings, "spend_project_cap_usd", 0.0))


class Ledger(actions_tests.DurableActions):
    async def test_append_only(self):
        async with self.sessions() as s:
            await spend_guard.reserve(s, project_id=7, run_id="r", estimate_usd=0.1, source="test", track=False)
            await s.commit()
        for stmt in ("UPDATE spend_ledger SET usd = 0", "DELETE FROM spend_ledger"):
            async with self.sessions() as s:
                with self.assertRaises(DBAPIError):
                    await s.execute(text(stmt))
                await s.rollback()
        async with self.sessions() as s:
            self.assertEqual((await spend_guard.totals(s, 7, "r"))["run_spent"], 0.1)

    async def test_two_reservations_race_only_one_fits(self):
        async def one():
            async with self.sessions() as s:
                try:
                    await spend_guard.reserve(s, project_id=7, run_id="race", estimate_usd=3.0,
                                              source="test", track=False)
                    await s.commit()
                    return True
                except spend_guard.SpendCapExceeded:
                    await s.rollback()
                    return False
        a, b, c = no_other_caps(5.0)
        with a, b, c:
            results = await asyncio.gather(*(one() for _ in range(4)))
        self.assertEqual(sorted(results), [False, False, False, True])
        async with self.sessions() as s:
            self.assertEqual((await spend_guard.totals(s, 7, "race"))["run_spent"], 3.0)

    async def test_survives_restart(self):
        async with self.sessions() as s:
            await spend_guard.reserve(s, project_id=7, run_id="persist", estimate_usd=2.0, source="test", track=False)
            await s.commit()
        spend_guard._ensured.clear()
        engine = create_async_engine(f"sqlite+aiosqlite:///{Path(self.tmp.name) / 'master.db'}")
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            a, b, c = no_other_caps(2.5)
            with a, b, c:
                async with sessions() as s:
                    self.assertEqual((await spend_guard.totals(s, 7, "persist"))["run_spent"], 2.0)
                    with self.assertRaises(spend_guard.SpendCapExceeded):
                        await spend_guard.reserve(s, project_id=7, run_id="persist", estimate_usd=1.0,
                                                  source="test", track=False)
        finally:
            await engine.dispose()

    async def test_settle_and_native_actual_are_counted(self):
        async with self.sessions() as s:
            await spend_guard.reserve(s, project_id=7, run_id="r", estimate_usd=0.5, source="test", track=False)
            await spend_guard.settle(s, project_id=7, run_id="r", reserved_usd=0.5, actual_usd=0.1, source="test")
            await spend_guard.settle(s, project_id=7, run_id="r", reserved_usd=0.5, actual_usd=None, source="test")
            await spend_guard.record_actual(s, project_id=7, usd=0.25, provider="kie")
            await s.commit()
            t = await spend_guard.totals(s, 7, "r")
        self.assertAlmostEqual(t["run_spent"], 0.1)
        self.assertAlmostEqual(t["project_spent"], 0.35)
        self.assertAlmostEqual(t["daily_spent"], 0.35)

    async def test_native_recording_ignores_caps(self):
        with patch.object(settings, "spend_daily_cap_usd", 0.01):
            async with self.sessions() as s:
                await spend_guard.record_actual(s, project_id=7, usd=5.0, provider="kie")
                await s.commit()
