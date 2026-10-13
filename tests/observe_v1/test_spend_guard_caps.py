"""Local money caps: refusal at each level, read-only unaffected, charge_request wiring."""
from unittest.mock import patch

import test_actions_v1_contract as actions_tests
from app.services import spend_guard
from app.settings import settings


def caps(daily=0.0, project=0.0, run=0.0):
    return (patch.object(settings, "spend_daily_cap_usd", daily),
            patch.object(settings, "spend_project_cap_usd", project),
            patch.object(settings, "spend_run_cap_usd", run))


class Caps(actions_tests.DurableActions):
    async def reserve(self, usd, *, pid=7, run="r1"):
        async with self.sessions() as s:
            await spend_guard.reserve(s, project_id=pid, run_id=run, estimate_usd=usd, source="test", track=False)
            await s.commit()

    async def _refused(self, usd, **kw):
        with self.assertRaises(spend_guard.SpendCapExceeded) as cm:
            await self.reserve(usd, **kw)
        return cm.exception

    async def test_run_cap(self):
        a, b, c = caps(daily=100, project=100, run=1.0)
        with a, b, c:
            await self.reserve(0.6)
            exc = await self._refused(0.6)
            self.assertEqual(exc.code, "spend_cap_run")
            self.assertAlmostEqual(exc.spent, 0.6)
            self.assertEqual(exc.cap, 1.0)
            await self.reserve(0.6, run="r2")  # another run is independent

    async def test_project_cap(self):
        a, b, c = caps(daily=100, project=1.0, run=100)
        with a, b, c:
            await self.reserve(0.6, run="r1")
            exc = await self._refused(0.6, run="r2")
            self.assertEqual(exc.code, "spend_cap_project")
            await self.reserve(0.6, pid=8, run="r3")  # another project

    async def test_daily_cap_is_global(self):
        a, b, c = caps(daily=1.0, project=100, run=100)
        with a, b, c:
            await self.reserve(0.6, pid=7, run="r1")
            exc = await self._refused(0.6, pid=8, run="r2")
            self.assertEqual(exc.code, "spend_cap_daily")

    async def test_zero_means_unlimited(self):
        a, b, c = caps(0, 0, 0)
        with a, b, c:
            for _ in range(5):
                await self.reserve(1000.0)

    async def test_read_only_never_blocked_and_check_is_non_mutating(self):
        a, b, c = caps(daily=0.01, project=0.01, run=0.01)
        with a, b, c:
            await self.reserve(0.0)
            self.assertTrue((await spend_guard.check_allowed(7, 99.0, "read", read_only=True))["allowed"])
            async with self.sessions() as s:
                refusal = await spend_guard.check_allowed(7, 1.0, "paid", run_id="r1", session=s)
                self.assertFalse(refusal["allowed"])
                self.assertEqual(refusal["code"], "spend_cap_run")
                self.assertEqual(refusal["cap"], 0.01)
                self.assertEqual((await spend_guard.totals(s, 7, "r1"))["daily_spent"], 0.0)

    async def test_charge_request_refuses_before_reservation_on_run_cap(self):
        await self.supervisor.enable(project_id=7, execution_id="exec-durable", group_id="g1", node_keys=["n1"],
            graph_version=self.graph, initiator="test-operator", rationale="spend fixture", request_limit=8)
        a, b, c = caps(daily=100, project=100, run=0.12)  # two default estimates (0.05) fit, third does not
        with a, b, c:
            self.assertEqual(await self.supervisor.charge_request("exec-durable"), 1)
            self.assertEqual(await self.supervisor.charge_request("exec-durable"), 2)
            with self.assertRaises(spend_guard.SpendCapExceeded) as cm:
                await self.supervisor.charge_request("exec-durable")
            self.assertEqual(cm.exception.code, "spend_cap_run")
        from app.services.actions_v1 import ActionExecutionControl
        async with self.sessions() as s:
            self.assertEqual((await s.get(ActionExecutionControl, "exec-durable")).requests_used, 2)

    async def test_defaults_do_not_affect_current_workflow(self):
        # C-17 (W42): потолки по умолчанию подняты до высоких предохранителей ($200 / $100 / $50). Смысл теста прежний:
        # значения по умолчанию не ниже прежних $25 / $10 / $5 и не мешают обычному прогону.
        for value, previous in ((settings.spend_daily_cap_usd, 25.0), (settings.spend_project_cap_usd, 10.0),
                                (settings.spend_run_cap_usd, 5.0)):
            self.assertGreaterEqual(value, previous)
        await self.supervisor.enable(project_id=7, execution_id="exec-durable", group_id="g1", node_keys=["n1"],
            graph_version=self.graph, initiator="test-operator", rationale="spend fixture", request_limit=40)
        for expected in range(1, 41):  # a 40-request live run fits under the default run cap
            self.assertEqual(await self.supervisor.charge_request("exec-durable"), expected)


class ActionLayerBudget(actions_tests.DurableActions):
    async def test_budget_reserve_consults_usd_caps(self):
        from app.services.action_layer import budget, store
        from app.services.action_layer.spec import CostEstimate
        async with self.engine.begin() as conn:
            await store.ensure_action_layer_schema(conn)
        est = CostEstimate(low=100.0, high=200.0)  # 200 credits = $1.00
        a, b, c = caps(daily=100, project=100, run=1.5)
        with a, b, c:
            async with self.sessions() as s:
                await budget.reserve(s, key="plan:p1", plan_id="p1", step_index=0, estimate=est, cap=10000.0)
                await s.commit()
            async with self.sessions() as s:
                with self.assertRaises(budget.BudgetError) as cm:
                    await budget.reserve(s, key="plan:p1", plan_id="p1", step_index=1, estimate=est, cap=10000.0)
                self.assertEqual(cm.exception.code, "spend_cap_run")
                await s.rollback()
                self.assertEqual(await budget.spent(s, "plan:p1"), 200.0)  # credit row of the refused step rolled back


class SpendEndpointRuns(actions_tests.DurableActions):
    async def test_spend_lists_runs_and_selects_by_id(self):
        from app.web.routers import observations as router
        await self.supervisor.enable(project_id=7, execution_id="exec-durable", group_id="g1", node_keys=["n1"],
            graph_version=self.graph, initiator="test-operator", rationale="spend fixture", request_limit=8)
        async with self.sessions() as s:
            await spend_guard.reserve(s, project_id=7, run_id="exec-durable", estimate_usd=0.4, source="test", track=False)
            await spend_guard.reserve(s, project_id=7, run_id="other-run", estimate_usd=0.9, source="test", track=False)
            await s.commit()
        a, b, c = caps(daily=100, project=100, run=100)
        with a, b, c:
            default = await router.spend_state(7)
            self.assertEqual(default["run_id"], "exec-durable")
            self.assertAlmostEqual(default["run_spent"], 0.4)
            self.assertEqual([r["run_id"] for r in default["runs"]], ["exec-durable"])
            self.assertAlmostEqual(default["runs"][0]["run_spent"], 0.4)
            self.assertTrue(default["runs"][0]["enabled"])
            # an id that is not a run of this project is ignored, never trusted
            foreign = await router.spend_state(7, run_id="other-run")
            self.assertEqual(foreign["run_id"], "exec-durable")
            self.assertAlmostEqual(foreign["run_spent"], 0.4)
            empty = await router.spend_state(8)
            self.assertIsNone(empty["run_id"])
            self.assertEqual(empty["runs"], [])
            self.assertIsNone(empty["run_spent"])
