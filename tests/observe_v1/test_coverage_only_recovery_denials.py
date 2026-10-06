"""Nodes that are listed in the coverage panel but have no automatic recovery stay refused by
the real recover_node: no ticket, no dispatch, no profile reservation, no model request, no
change of any recovery-relevant row. The reason is the registry data (forbidden_code), not an
if-chain; the denial itself is the generic capability gate."""
import unittest

import test_actions_v1_contract as base_tests
from app.orchestrator.node_spec import capability_spec_for


class CoverageOnlyRecoveryDenials(unittest.IsolatedAsyncioTestCase):
    # Reuse the isolated persistence fixtures without inheriting unrelated test cases.
    asyncSetUp = base_tests.DurableActions.asyncSetUp
    asyncTearDown = base_tests.DurableActions.asyncTearDown
    exhausted_fixture = base_tests.DurableActions.exhausted_fixture
    retry = base_tests.DurableActions.retry
    assert_no_dispatch = base_tests.DurableActions.assert_no_dispatch
    mixed_recovery_fixture = base_tests.RecoveryCapabilityAdmission.mixed_recovery_fixture
    approved_profile_fixture = base_tests.RecoveryCapabilityAdmission.approved_profile_fixture
    recovery_side_effects = base_tests.RecoveryCapabilityAdmission.recovery_side_effects
    assert_retry_denied = base_tests.RecoveryCapabilityAdmission.assert_retry_denied

    NODE_TYPES = ("scene_design", "sd_agent", "sd_assemble", "hero", "items", "music", "enrich_1",
                  "sfx_gen", "videos")

    async def test_retry_same_is_refused_for_every_node_without_recovery(self):
        for node_type in self.NODE_TYPES:
            with self.subTest(node_type=node_type):
                spec = capability_spec_for(node_type)
                self.assertTrue(spec.forbidden_code, "the refusal must be explained in data")
                await self.asyncTearDown()
                await self.asyncSetUp()
                await self.assert_retry_denied(node_type)

    async def test_retry_profile_is_refused_for_every_node_without_recovery(self):
        for node_type in ("sd_agent", "hero", "music"):
            with self.subTest(node_type=node_type):
                await self.asyncTearDown()
                await self.asyncSetUp()
                await self.assert_retry_denied(node_type, profile=True)


if __name__ == "__main__":
    unittest.main()
