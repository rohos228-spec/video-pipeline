"""Nodes that are listed in the coverage panel but have no automatic recovery stay refused by
the real recover_node: no ticket, no dispatch, no profile reservation, no model request, no
change of any recovery-relevant row. The reason is the registry data (forbidden_code), not an
if-chain; the denial itself is the generic capability gate."""
import unittest

import test_actions_v1_contract as base_tests
from app.orchestrator.node_spec import capability_spec_for
from app.services import node_capabilities  # noqa: F401  (registers the capability specs)


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

    NODE_TYPES = ("scene_design", "sd_agent", "sd_assemble", "hero", "enrich_1",
                  "sfx_gen", "audio", "assemble")
    # managed for supervised RECOVERY only: a media resume, never a text-style restart
    MEDIA_RESUME_TYPES = ("videos", "items", "music")

    async def test_retry_same_is_refused_for_every_node_without_recovery(self):
        for node_type in self.NODE_TYPES:
            with self.subTest(node_type=node_type):
                spec = capability_spec_for(node_type)
                self.assertTrue(spec.forbidden_code, "the refusal must be explained in data")
                await self.asyncTearDown()
                await self.asyncSetUp()
                await self.assert_retry_denied(node_type)

    async def test_retry_profile_is_refused_for_every_node_without_recovery(self):
        for node_type in ("sd_agent", "hero", "audio"):
            with self.subTest(node_type=node_type):
                await self.asyncTearDown()
                await self.asyncSetUp()
                await self.assert_retry_denied(node_type, profile=True)

    async def test_a_text_style_retry_is_refused_for_the_media_resume_nodes(self):
        """videos/items/music recover only by resuming a provider task after a media incident: an
        invalid_json style retry (restart of the node) and a profile retry never reach them."""
        for node_type in self.MEDIA_RESUME_TYPES:
            for profile in (False, True):
                with self.subTest(node_type=node_type, profile=profile):
                    spec = capability_spec_for(node_type)
                    self.assertTrue(spec.recovery_only and spec.forbidden_code is None)
                    await self.asyncTearDown()
                    await self.asyncSetUp()
                    await self.assert_retry_denied(node_type, profile=profile,
                                                   reason="admitted for media incidents only")


if __name__ == "__main__":
    unittest.main()
