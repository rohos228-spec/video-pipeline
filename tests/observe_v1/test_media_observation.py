"""Generic worker observation/diagnosis contracts on synthetic database state."""
import unittest

from test_media_jobs import MediaJobs
from app.models import ProjectStatus
from app.services import observation


class MediaObservation(MediaJobs):
    async def test_managed_start_maps_existing_node_type_to_native_step_code(self):
        from app.web.routers.observations import managed_step_code
        self.assertEqual(managed_step_code({"id": "image", "type": "images"}), "img")
        self.assertEqual(managed_step_code({"id": "video", "type": "videos"}), "video")
        self.assertEqual(managed_step_code({"id": "text", "type": "excel_gpt"}), "excel_gpt")
        with self.assertRaises(ValueError):
            managed_step_code({"id": "bad", "type": "unregistered"})

    async def test_native_media_attempt_has_node_and_attempt_correlation(self):
        self.project.status = ProjectStatus.generating_images
        token = observation._attempt.set(None)
        try:
            async with observation.observe_attempt(self.session, self.project):
                ctx = observation._attempt.get()
                self.assertIsNotNone(ctx, "original worker media phase must be observable")
                self.assertEqual(ctx.node_key, "image")
                self.assertEqual(ctx.scope.execution_id, self.scope.execution_id)
                self.assertTrue(ctx.attempt_id)
        finally:
            observation._attempt.reset(token)

    async def test_failed_media_is_distinct_from_json_and_running_is_not_hung(self):
        from app.services.diagnosis import derive_incidents
        base = {"execution_id": self.scope.execution_id, "project_id": 7, "node_key": "image",
                "attempt_id": "attempt-original", "db_scope": "master", "request_id": None}
        running = {**base, "id": 1, "event_id": "media-running", "kind": "media_phase",
                   "payload": {"job_id": "local-job", "provider_job_id": "external-job",
                               "phase": "running", "kind": "image"}}
        snapshot = {"nodes": {"image": {"status": "running"}}}
        issues = derive_incidents(self.scope, [running], snapshot)
        self.assertFalse(any(i.get("blocking") for i in issues))
        failed = {**base, "id": 2, "event_id": "media-failed", "kind": "media_failure",
                  "payload": {"job_id": "local-job", "provider_job_id": "external-job",
                              "phase": "failed", "error_type": "KieHttpError", "uncertain": False}}
        issues = derive_incidents(self.scope, [running, failed], snapshot)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["type"], "media_failure")
        self.assertEqual(issues[0]["recommendation"]["action"], "human_review")
        self.assertIn("media-failed", issues[0]["event_ids"])

    async def test_unknown_submit_is_human_review_not_safe_retry(self):
        from app.services.diagnosis import derive_incidents
        event = {"execution_id": self.scope.execution_id, "project_id": 7, "node_key": "image",
                 "attempt_id": "attempt-original", "db_scope": "master", "request_id": "request",
                 "id": 1, "event_id": "uncertain-media", "kind": "media_failure",
                 "payload": {"job_id": "local-job", "phase": "unknown", "provider_job_id": None,
                             "uncertain": True, "error_type": "RemoteProtocolError"}}
        issues = derive_incidents(self.scope, [event], {"nodes": {"image": {"status": "running"}}})
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["type"], "uncertain_request")
        self.assertEqual(issues[0]["status"], "human_review")


if __name__ == "__main__":
    names = [n for n in unittest.defaultTestLoader.getTestCaseNames(MediaObservation)
             if n not in unittest.defaultTestLoader.getTestCaseNames(MediaJobs)]
    result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(MediaObservation(n) for n in names))
    raise SystemExit(0 if result.wasSuccessful() else 1)
