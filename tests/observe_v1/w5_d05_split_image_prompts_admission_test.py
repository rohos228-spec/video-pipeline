import unittest
import uuid
from datetime import datetime

from app.services.diagnosis import DiagnosisState
from app.services.observation import ObservationEvent
import test_actions_v1_contract as base_tests
import test_text_nodes_recovery as text_tests

RETRYABLE_NEW = ("split", "image_prompts")


class SplitImagePromptsAdmission(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = base_tests.DurableActions.asyncSetUp
    asyncTearDown = base_tests.DurableActions.asyncTearDown
    exhausted_fixture = base_tests.DurableActions.exhausted_fixture
    retry = base_tests.DurableActions.retry
    assert_no_dispatch = base_tests.DurableActions.assert_no_dispatch
    mixed_recovery_fixture = base_tests.RecoveryCapabilityAdmission.mixed_recovery_fixture
    approved_profile_fixture = text_tests.TextNodeRecovery.approved_profile_fixture
    hermetic_runtime = text_tests.TextNodeRecovery.hermetic_runtime
    text_fixture = text_tests.TextNodeRecovery.text_fixture
    EXECUTION_UUID = text_tests.TextNodeRecovery.EXECUTION_UUID
    SOURCE_BODY = text_tests.TextNodeRecovery.SOURCE_BODY

    async def fresh(self, node_type, **kwargs):
        await self.asyncTearDown()
        await self.asyncSetUp()
        await self.text_fixture(node_type, **kwargs)

    async def test_total_failure_without_a_write_admits_one_retry_and_masks_attachment_stamps(self):
        for node_type in RETRYABLE_NEW:
            for incident_type in ("invalid_json", "missing_payload"):
                with self.subTest(node_type=node_type, incident=incident_type):
                    await self.fresh(node_type, incident_type=incident_type)
                    ticket = await self.retry()
                    pre = ticket["preconditions"]
                    self.assertEqual((ticket["status"], pre["effective_node_type"], pre["action_type"]),
                                     ("queued", node_type, "retry_same"))
                    self.assertTrue(pre["no_partial_apply"] and pre["prior_attempt_terminal"])
                    self.assertEqual(pre["first_body_normalization"], "attachment_timestamps")
                    self.assertEqual(ticket["budget_state"]["recovery_limit"], 1)

    async def test_profile_retry_binds_the_approved_transformation_of_the_first_body(self):
        for node_type in RETRYABLE_NEW:
            with self.subTest(node_type=node_type):
                await self.fresh(node_type)
                overlay_id = await self.approved_profile_fixture()
                ticket = await self.retry(profile_overlay_id=overlay_id)
                self.assertEqual(ticket["preconditions"]["action_type"], "retry_profile")
                self.assertEqual(ticket["preconditions"]["profile_overlay_id"], overlay_id)
                self.assertEqual(ticket["preconditions"]["first_body_normalization"], "attachment_timestamps")

    async def test_any_applied_package_blocks_the_retry_no_duplicates_no_partial_state(self):
        for node_type in RETRYABLE_NEW:
            for kind in ("data_applied", "apply_input", "apply_error"):
                with self.subTest(node_type=node_type, fact=kind):
                    await self.fresh(node_type)
                    async with self.sessions() as s:
                        s.add(ObservationEvent(id=7, event_id=str(uuid.uuid4()), execution_id="exec-durable",
                            project_id=7, workflow_run_id=11, node_key="n1", attempt_id="source-attempt",
                            db_scope="project", kind=kind, payload={}, created_at=datetime.utcnow()))
                        (await s.get(DiagnosisState, "exec-durable")).cursor = 7
                        await s.commit()
                    with self.assertRaisesRegex(ValueError, "partial apply"):
                        await self.retry()
                    await self.assert_no_dispatch()

    async def test_contract_rejection_and_degradation_never_open_a_retry(self):
        for node_type in RETRYABLE_NEW:
            with self.subTest(node_type=node_type):
                await self.fresh(node_type, incident_type="contract_rejected")
                with self.assertRaisesRegex(ValueError, "current mandatory JSON/payload incident required"):
                    await self.retry()
                await self.assert_no_dispatch()

    async def test_changed_input_between_failure_and_retry_is_refused(self):
        for node_type in RETRYABLE_NEW:
            with self.subTest(node_type=node_type):
                await self.fresh(node_type)
                async with self.sessions() as s:
                    from app.models import Project
                    project = await s.get(Project, 7)
                    project.general_plan = "the owner edited the plan after the failed attempt"
                    await s.commit()
                with self.assertRaisesRegex(ValueError, "input/config revision changed"):
                    await self.retry()
                await self.assert_no_dispatch()

    async def test_second_recovery_of_the_same_node_is_refused(self):
        await self.fresh("split")
        await self.retry()
        with self.assertRaisesRegex(ValueError, "already consumed"):
            await self.retry(idempotency_key="second-retry-0002")

    async def test_sd_assemble_and_sfx_plan_admit_the_profile_retry_too(self):
        for node_type in ("sd_assemble", "sfx_plan"):
            with self.subTest(node_type=node_type):
                await self.fresh(node_type)
                overlay_id = await self.approved_profile_fixture()
                ticket = await self.retry(profile_overlay_id=overlay_id)
                self.assertEqual(ticket["preconditions"]["action_type"], "retry_profile")
                self.assertIn("retry_profile", ticket["preconditions"]["capability_actions"])

    async def test_animation_prompts_still_cannot_take_a_profile(self):
        await self.fresh("animation_prompts")
        overlay_id = await self.approved_profile_fixture()
        with self.assertRaisesRegex(ValueError, "denied by node capability"):
            await self.retry(profile_overlay_id=overlay_id)
        await self.assert_no_dispatch()


if __name__ == "__main__":
    unittest.main()
