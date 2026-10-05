"""HTTP 402 / credit exhaustion engages the supervisory kill switch only when the setting is on."""
from unittest.mock import patch

from sqlalchemy import select

import test_actions_v1_contract as actions_tests
from app.services import kill_switch, spend_guard
from app.services.actions_v1 import ActionRecord
from app.settings import settings


class Credit402(Exception):
    def __init__(self, status=402):
        super().__init__(f"http {status}")
        self.context = {"status_code": status, "error_kind": "http"}


class Auto402(actions_tests.DurableActions):
    async def asyncSetUp(self):
        kill_switch.reset_cache()
        await super().asyncSetUp()

    async def asyncTearDown(self):
        kill_switch.reset_cache()
        await super().asyncTearDown()

    async def test_402_engages_with_audit_when_enabled(self):
        await self.supervisor.enable(project_id=7, execution_id="exec-durable", group_id="g1", node_keys=["n1"],
            graph_version=self.graph, initiator="test-operator", rationale="402 fixture", request_limit=4)
        with patch.object(settings, "spend_auto_kill_on_402", True):
            self.assertTrue(await spend_guard.engage_on_credit_error(Credit402(), master=self.sessions, provider="kie"))
        st = await kill_switch.status(self.sessions)
        self.assertTrue(st["active"])
        self.assertIn("credits_exhausted", st["rationale"])
        self.assertEqual(st["history"][0]["source"], "spend_guard")
        async with self.sessions() as s:
            kinds = [r.action_kind for r in (await s.scalars(select(ActionRecord))).all()]
        self.assertIn("kill_switch_on", kinds)
        with self.assertRaises(kill_switch.KillSwitchActive):  # supervisory work now refused
            await self.supervisor.charge_request("exec-durable")

    async def test_setting_off_does_nothing(self):
        with patch.object(settings, "spend_auto_kill_on_402", False):
            self.assertFalse(await spend_guard.engage_on_credit_error(Credit402(), master=self.sessions))
        self.assertFalse((await kill_switch.status(self.sessions))["active"])

    async def test_other_errors_do_not_engage(self):
        with patch.object(settings, "spend_auto_kill_on_402", True):
            self.assertFalse(await spend_guard.engage_on_credit_error(Credit402(500), master=self.sessions))
            self.assertFalse(await spend_guard.engage_on_credit_error(RuntimeError("boom"), master=self.sessions))
        self.assertFalse((await kill_switch.status(self.sessions))["active"])
