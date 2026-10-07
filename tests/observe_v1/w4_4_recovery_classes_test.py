"""W4-4 (C08): the supervised retry is offered for more incident classes, never twice and never for an unknown
outcome. Real ``recover_node`` / ``claim`` / ``check_body`` on the isolated actions fixture (SQLite, no provider,
no network, no paid call: reserving or refusing a ticket sends nothing).

Every test that opens a ticket also proves the double-paid-call barriers: a second ticket for the node is
refused, the same idempotency key replays the first ticket without a second row, and the request counter does
not move.

The file is named ``*_test.py`` so that pytest collects it with the ``w4_4_`` prefix; it lives in tests/observe_v1
to get that directory's synthetic-credentials fixture.
"""
import unittest
from datetime import datetime
from types import SimpleNamespace

from sqlalchemy import select

import test_actions_v1_contract as base_tests
from app.models import NodeRun, Project
from app.services import observation
from app.services.actions_recovery import (
    RECOVERY_FORMAT,
    RECOVERY_LOCAL_FIX,
    RECOVERY_TRANSIENT_API,
    RecoveryAction,
    check_body,
    recovery_class,
    server_rejected_requests,
)
from app.services.actions_v1 import ActionExecutionControl
from app.services.diagnosis import DiagnosisState, Incident
from app.services.observation import ObservationEvent, digest

EXEC = "exec-durable"


class RecoveryClassesUnit(unittest.TestCase):
    def test_recovery_class_is_a_prefilter_by_type_and_recorded_details(self):
        self.assertEqual(recovery_class("invalid_json", {}), RECOVERY_FORMAT)
        self.assertEqual(recovery_class("missing_payload", None), RECOVERY_FORMAT)
        self.assertEqual(recovery_class("api_error", {"safe_retry": {"eligible": True}}), RECOVERY_TRANSIENT_API)
        self.assertEqual(recovery_class("contract_rejected", {"local_fix_candidate": True}), RECOVERY_LOCAL_FIX)

    def test_no_class_for_the_rest(self):
        for incident_type in ("uncertain_request", "stalled", "user_stop", "journal_gap", "db_disagreement",
                              "runtime_wait", "media_policy", "split_degraded", "verification_hold",
                              "provider_degraded", "animation_degraded"):
            self.assertIsNone(recovery_class(incident_type, {"safe_retry": {"eligible": True},
                                                             "local_fix_candidate": True}), incident_type)
        self.assertIsNone(recovery_class("api_error", {"safe_retry": {"eligible": False}}))
        self.assertIsNone(recovery_class("api_error", {}))
        self.assertIsNone(recovery_class("contract_rejected", {}))
        self.assertIsNone(recovery_class("contract_rejected", {"local_fix_candidate": "yes"}))

    def test_only_a_5xx_answer_settles_a_request_as_refused(self):
        def fact(kind, request, **payload):
            return SimpleNamespace(kind=kind, request_id=request, payload=payload)

        facts = [fact("model_error", "ok5xx", uncertain=False, status_code=503),
                 fact("model_error", "gw", uncertain=False, status_code=500),
                 fact("model_error", "rate", uncertain=False, status_code=429),
                 fact("model_error", "auth", uncertain=False, status_code=401),
                 fact("model_error", "timeout", uncertain=True, status_code=None),
                 fact("model_error", "uncertain5xx", uncertain=True, status_code=503),
                 fact("model_error", "nostatus", uncertain=False, status_code=None),
                 fact("model_error", "boolean", uncertain=False, status_code=True),
                 fact("model_error", None, uncertain=False, status_code=502),
                 fact("model_end", "done", complete=True)]
        self.assertEqual(server_rejected_requests(facts), {"ok5xx", "gw"})


class RecoveryClassesDurable(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = base_tests.DurableActions.asyncSetUp
    asyncTearDown = base_tests.DurableActions.asyncTearDown
    exhausted_fixture = base_tests.DurableActions.exhausted_fixture
    retry = base_tests.DurableActions.retry
    assert_no_dispatch = base_tests.DurableActions.assert_no_dispatch

    # ---- fixture shaping -------------------------------------------------------------------------------
    async def shape(self, *, incident_type, details=None, events=None, drop=(), node_start=None, step_failure=None):
        """Turn the stock text-node fixture into an incident of another class.

        events: {event id: (kind, payload)}; the stock events are 1 node_start, 2 model_start (request-source),
        3 model_end complete, 4 json_retry, 5 parse_result, 6 node_error."""
        await self.exhausted_fixture()
        async with self.sessions() as s:
            incident = await s.get(Incident, "source-incident")
            incident.type = incident_type
            incident.details = {"currently_relevant": True, "event_ids": [], **(details or {})}
            for event_id, (kind, payload) in (events or {}).items():
                row = await s.get(ObservationEvent, event_id)
                row.kind, row.payload = kind, payload
            for event_id in drop:
                await s.delete(await s.get(ObservationEvent, event_id))
            if node_start is not None:
                start = await s.get(ObservationEvent, 1)
                start.payload = {**start.payload, **node_start}
            if step_failure is not None:
                project = await s.get(Project, 7)
                project.meta = {**project.meta, "step_failure": step_failure}
            await s.commit()

    async def api_error(self, *, status=503, uncertain=False, eligible=True, extra_events=None, **kw):
        await self.shape(
            incident_type="api_error",
            details={"safe_retry": {"eligible": eligible}, "http_status": status},
            events={3: ("model_error", {"uncertain": uncertain, "status_code": status,
                                        "error_type": "GptApiError", "complete": False}), **(extra_events or {})},
            drop=(4, 5), **kw)

    async def assert_one_ticket_and_no_second_paid_call(self, ticket):
        """The three barriers that keep a second paid request from ever being reserved."""
        self.assertEqual(ticket["status"], "queued")
        replay = await self.retry()
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(replay["action_id"], ticket["action_id"])
        with self.assertRaisesRegex(ValueError, "already consumed"):
            await self.retry(idempotency_key="second-key-0002")
        async with self.sessions() as s:
            self.assertEqual(len((await s.scalars(select(RecoveryAction))).all()), 1)
            control = await s.get(ActionExecutionControl, EXEC)
            self.assertEqual(control.requests_used, 0, "reserving a ticket must not spend a request")

    # ---- api_error: only a 5xx ANSWER ------------------------------------------------------------------
    async def test_a_5xx_api_error_gets_exactly_one_ticket_and_never_a_second(self):
        await self.api_error(status=503)
        ticket = await self.retry()
        self.assertEqual(ticket["preconditions"]["recovery_class"], RECOVERY_TRANSIENT_API)
        self.assertEqual(ticket["preconditions"]["first_body_hash"], digest({"test": 1}))
        await self.assert_one_ticket_and_no_second_paid_call(ticket)

    async def test_the_5xx_retry_must_carry_the_same_request_body(self):
        await self.api_error(status=500)
        ticket = await self.retry()
        ctx = SimpleNamespace(observer=observation._observer, attempt_id=ticket["new_attempt_id"], last_request_id=None,
                              scope=SimpleNamespace(execution_id=EXEC, observer_state="active", gaps=[]))
        await check_body(ctx, digest({"test": 1}))  # identical body: allowed
        with self.assertRaisesRegex(RuntimeError, "HTTP input changed"):
            await check_body(ctx, digest({"test": 2}))  # a different paid request is blocked before transport

    async def test_a_timeout_is_never_retried_even_if_the_incident_claims_it_is_safe(self):
        for error_type, status in (("ReadTimeout", None), ("ConnectTimeout", 503)):
            with self.subTest(error_type=error_type):
                await self.asyncTearDown()
                await self.asyncSetUp()
                await self.api_error(status=status, uncertain=True, eligible=True)
                with self.assertRaisesRegex(ValueError, "uncertain request"):
                    await self.retry()
                await self.assert_no_dispatch()

    async def test_non_5xx_answers_are_never_retried_even_if_the_incident_claims_it_is_safe(self):
        for status in (400, 401, 402, 429, None):
            with self.subTest(status=status):
                await self.asyncTearDown()
                await self.asyncSetUp()
                await self.api_error(status=status, eligible=True)
                with self.assertRaisesRegex(ValueError, "uncertain request"):
                    await self.retry()
                await self.assert_no_dispatch()

    async def test_an_api_error_not_marked_safe_by_diagnosis_is_refused_for_the_incident_reason(self):
        await self.api_error(status=503, eligible=False)
        with self.assertRaisesRegex(ValueError, "current mandatory JSON/payload incident required"):
            await self.retry()
        await self.assert_no_dispatch()

    async def test_a_paid_answer_earlier_in_the_attempt_forbids_the_5xx_retry(self):
        """One request got a complete (paid) answer, another got a 5xx: repeating would buy the first one twice."""
        await self.api_error(status=503)
        async with self.sessions() as s:
            for event_id, kind, payload in ((7, "model_start", {"input_revision": digest({"t": 2})}),
                                            (8, "model_end", {"complete": True})):
                s.add(ObservationEvent(id=event_id, event_id=f"paid-{event_id}", execution_id=EXEC, project_id=7,
                                       workflow_run_id=11, node_key="n1", attempt_id="source-attempt",
                                       request_id="request-paid", db_scope="master", kind=kind, payload=payload,
                                       created_at=datetime.utcnow()))
            state = await s.get(DiagnosisState, EXEC)
            state.cursor = 8
            await s.commit()
        with self.assertRaisesRegex(ValueError, "uncertain request"):
            await self.retry()
        await self.assert_no_dispatch()

    async def test_a_request_that_started_and_never_ended_forbids_the_retry(self):
        await self.api_error(status=503)
        async with self.sessions() as s:
            error = await s.get(ObservationEvent, 3)
            error.request_id = "other-request"  # the 5xx belongs to another request: request-source is open
            await s.commit()
        with self.assertRaisesRegex(ValueError, "uncertain request"):
            await self.retry()
        await self.assert_no_dispatch()

    async def test_a_profile_retry_is_refused_for_an_api_error(self):
        await self.api_error(status=503)
        with self.assertRaisesRegex(ValueError, "only for JSON/payload format incidents"):
            await self.retry(profile_overlay_id="some-overlay")
        await self.assert_no_dispatch()

    async def test_user_stop_and_audit_gaps_still_block_the_5xx_retry(self):
        await self.api_error(status=503)
        async with self.sessions() as s:
            project = await s.get(Project, 7)
            project.meta = {**project.meta, "user_stop": True}
            await s.commit()
        with self.assertRaisesRegex(ValueError, "user_stop"):
            await self.retry()
        await self.assert_no_dispatch()

    # ---- stalled / uncertain_request / the rest: diagnosis and notification only -----------------------------
    async def test_stalled_and_uncertain_incidents_never_get_a_ticket(self):
        for incident_type in ("stalled", "uncertain_request", "journal_gap", "db_disagreement", "user_stop"):
            with self.subTest(incident_type=incident_type):
                await self.asyncTearDown()
                await self.asyncSetUp()
                await self.shape(incident_type=incident_type,
                                 details={"safe_retry": {"eligible": True}, "local_fix_candidate": True})
                with self.assertRaisesRegex(ValueError, "current mandatory JSON/payload incident required"):
                    await self.retry()
                await self.assert_no_dispatch()

    async def test_a_relevant_stalled_incident_blocks_even_a_valid_5xx_ticket_of_another_incident(self):
        await self.api_error(status=503)
        async with self.sessions() as s:
            s.add(Incident(incident_id="stalled-1", execution_id=EXEC, project_id=7, node_key="n2", attempt_id="x",
                           type="stalled", severity="error", status="human_review",
                           details={"currently_relevant": True}))
            await s.commit()
        with self.assertRaisesRegex(ValueError, "uncertain request, user_stop or audit/state blocker"):
            await self.retry()
        await self.assert_no_dispatch()

    # ---- contract_rejected: only after a recorded local correction ---------------------------------------------
    async def contract_rejected(self, *, node_start, candidate=True):
        await self.shape(incident_type="contract_rejected",
                         details={"local_fix_candidate": True} if candidate else {},
                         drop=(4, 5), node_start=node_start)

    async def test_contract_rejected_with_an_unchanged_input_is_refused(self):
        await self.exhausted_fixture()
        async with self.sessions() as s:
            same = (await s.get(ObservationEvent, 1)).payload["recovery_input_revision"]
        await self.asyncTearDown()
        await self.asyncSetUp()
        await self.contract_rejected(node_start={"recovery_input_revision": same})
        with self.assertRaisesRegex(ValueError, "needs a recorded local correction"):
            await self.retry()
        await self.assert_no_dispatch()

    async def test_contract_rejected_without_a_recorded_revision_is_refused(self):
        await self.contract_rejected(node_start={"recovery_input_revision": None})
        with self.assertRaisesRegex(ValueError, "needs a recorded local correction"):
            await self.retry()
        await self.assert_no_dispatch()

    async def test_contract_rejected_after_a_recorded_local_correction_gets_exactly_one_ticket(self):
        await self.contract_rejected(node_start={"recovery_input_revision": "revision-before-the-fix"})
        ticket = await self.retry()
        pre = ticket["preconditions"]
        self.assertEqual(pre["recovery_class"], RECOVERY_LOCAL_FIX)
        self.assertIs(pre["same_body_required"], False)
        self.assertIsNone(pre["first_body_hash"])
        self.assertTrue(pre["local_correction_parts"], "the corrected input parts are recorded on the ticket")
        await self.assert_one_ticket_and_no_second_paid_call(ticket)

    async def test_the_corrected_request_may_differ_from_the_source_body_but_only_for_local_fix(self):
        await self.contract_rejected(node_start={"recovery_input_revision": "revision-before-the-fix"})
        ticket = await self.retry()
        ctx = SimpleNamespace(observer=observation._observer, attempt_id=ticket["new_attempt_id"], last_request_id=None,
                              scope=SimpleNamespace(execution_id=EXEC, observer_state="active", gaps=[]))
        await check_body(ctx, digest({"a": "corrected body"}))  # no equality demanded after a correction
        ctx.scope.observer_state = "degraded"
        with self.assertRaisesRegex(RuntimeError, "audit unavailable"):
            await check_body(ctx, digest({"a": "corrected body"}))  # the audit gate still applies

    async def test_a_generic_contract_rejected_is_refused_for_the_incident_reason(self):
        await self.contract_rejected(node_start={"recovery_input_revision": "revision-before-the-fix"}, candidate=False)
        with self.assertRaisesRegex(ValueError, "current mandatory JSON/payload incident required"):
            await self.retry()
        await self.assert_no_dispatch()

    async def test_a_profile_retry_is_refused_for_contract_rejected(self):
        await self.contract_rejected(node_start={"recovery_input_revision": "revision-before-the-fix"})
        with self.assertRaisesRegex(ValueError, "only for JSON/payload format incidents"):
            await self.retry(profile_overlay_id="some-overlay")
        await self.assert_no_dispatch()

    # ---- the format class is unchanged -----------------------------------------------------------------
    async def test_the_original_format_recovery_still_works_and_still_demands_the_same_input(self):
        await self.exhausted_fixture()
        ticket = await self.retry()
        self.assertEqual(ticket["preconditions"]["recovery_class"], RECOVERY_FORMAT)
        await self.assert_one_ticket_and_no_second_paid_call(ticket)

    async def test_a_changed_input_still_refuses_the_format_recovery(self):
        await self.shape(incident_type="missing_payload", node_start={"recovery_input_revision": "something-else"})
        with self.assertRaisesRegex(ValueError, "input/config revision changed or not recorded"):
            await self.retry()
        await self.assert_no_dispatch()

    # ---- wait profile meets recovery -------------------------------------------------------------------------
    async def test_a_fast_profile_closure_opens_the_same_single_recovery_after_four_failures(self):
        step_failure = {"total_fails": {"enriching_1": 4}, "last_running": "enriching_1",
                        "abandoned_at": "synthetic", "abandoned_threshold": 4}
        await self.shape(incident_type="missing_payload", step_failure=step_failure)
        ticket = await self.retry()
        self.assertEqual(ticket["preconditions"]["native_failures"], 4)
        await self.assert_one_ticket_and_no_second_paid_call(ticket)

    async def test_four_failures_of_the_safe_profile_do_not_open_a_recovery(self):
        step_failure = {"total_fails": {"enriching_1": 4}, "last_running": "enriching_1", "abandoned_at": "synthetic"}
        await self.shape(incident_type="missing_payload", step_failure=step_failure)
        with self.assertRaisesRegex(ValueError, "all native runtime retries must be exhausted"):
            await self.retry()
        await self.assert_no_dispatch()

    async def test_a_configuration_error_closure_never_opens_a_recovery(self):
        step_failure = {"total_fails": {"enriching_1": 9}, "last_running": "enriching_1",
                        "abandoned_at": "synthetic", "abandoned_reason": "fatal_config"}
        await self.shape(incident_type="missing_payload", step_failure=step_failure)
        with self.assertRaisesRegex(ValueError, "all native runtime retries must be exhausted"):
            await self.retry()
        await self.assert_no_dispatch()

    async def test_nothing_in_these_paths_changes_the_node_attempts(self):
        await self.api_error(status=503)
        await self.retry()
        async with self.sessions() as s:
            node = await s.scalar(select(NodeRun).where(NodeRun.node_key == "n1"))
            self.assertEqual(node.attempts, 9)


if __name__ == "__main__":
    unittest.main()
