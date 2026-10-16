import unittest

import test_actions_v1_contract as base_tests
from app.orchestrator.node_spec import capability_spec_for
from app.services import node_capabilities


class CoverageOnlyRecoveryDenials(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = base_tests.DurableActions.asyncSetUp
    asyncTearDown = base_tests.DurableActions.asyncTearDown
    exhausted_fixture = base_tests.DurableActions.exhausted_fixture
    retry = base_tests.DurableActions.retry
    assert_no_dispatch = base_tests.DurableActions.assert_no_dispatch
    mixed_recovery_fixture = base_tests.RecoveryCapabilityAdmission.mixed_recovery_fixture
    approved_profile_fixture = base_tests.RecoveryCapabilityAdmission.approved_profile_fixture
    recovery_side_effects = base_tests.RecoveryCapabilityAdmission.recovery_side_effects
    assert_retry_denied = base_tests.RecoveryCapabilityAdmission.assert_retry_denied

    NODE_TYPES = ("enrich_1",)
    MEDIA_RESUME_TYPES = ("videos", "items", "music", "hero", "audio", "sfx_gen", "images")
    LOCAL_RERUN_TYPES = ("assemble",)

    async def test_retry_same_is_refused_for_every_node_without_recovery(self):
        for node_type in self.NODE_TYPES:
            with self.subTest(node_type=node_type):
                spec = capability_spec_for(node_type)
                self.assertTrue(spec.forbidden_code, "the refusal must be explained in data")
                await self.asyncTearDown()
                await self.asyncSetUp()
                await self.assert_retry_denied(node_type)

    async def test_retry_profile_is_refused_for_every_node_without_recovery(self):
        for node_type in ("enrich_1",):
            with self.subTest(node_type=node_type):
                await self.asyncTearDown()
                await self.asyncSetUp()
                await self.assert_retry_denied(node_type, profile=True)

    async def test_the_legacy_scene_node_never_enters_a_managed_scope(self):
        with self.assertRaisesRegex(ValueError, "scene agent 'legacy' is not admitted"):
            await self.mixed_recovery_fixture("scene_design")

    async def test_a_text_style_retry_is_refused_for_the_media_resume_nodes(self):
        for node_type in self.MEDIA_RESUME_TYPES:
            for profile in (False, True):
                with self.subTest(node_type=node_type, profile=profile):
                    spec = capability_spec_for(node_type)
                    self.assertTrue((spec.recovery_only or node_type == "images") and spec.forbidden_code is None)
                    await self.asyncTearDown()
                    await self.asyncSetUp()
                    await self.assert_retry_denied(node_type, profile=profile,
                                                   reason="admitted for media incidents only")

    async def test_a_text_style_retry_is_refused_for_the_local_rerun_node(self):
        for node_type in self.LOCAL_RERUN_TYPES:
            for profile in (False, True):
                with self.subTest(node_type=node_type, profile=profile):
                    spec = capability_spec_for(node_type)
                    self.assertTrue(spec.local_recovery and spec.forbidden_code is None)
                    await self.asyncTearDown()
                    await self.asyncSetUp()
                    await self.assert_retry_denied(node_type, profile=profile,
                                                   reason="admitted for media incidents only")


if __name__ == "__main__":
    unittest.main()
