"""Generic worker observation/diagnosis contracts on synthetic database state."""
import unittest

from test_media_jobs import MediaJobs
from app.models import ProjectStatus
from app.services import observation


class MediaObservation(MediaJobs):
    async def test_unintegrated_family_cannot_be_enabled_as_managed_executor(self):
        from app.services.node_capabilities import require_managed_capability, capability_for
        require_managed_capability("images")
        require_managed_capability("excel_gpt")
        for kind in ("plan", "videos", "publish", "audio", "assemble", "unregistered"):
            self.assertFalse(capability_for(kind).managed, "unaccepted adapters stay disabled even if tools are later installed")
            with self.assertRaisesRegex(ValueError, "adapter"):
                require_managed_capability(kind)

    async def test_panel_media_context_has_no_fake_progress_or_paths(self):
        from app.services.media_view import context_for_nodes
        async with self.sessions() as s:
            result = await context_for_nodes(s, self.scope)
        self.assertEqual(set(result), {"image"})
        self.assertEqual(result["image"]["result_kind"], "image")
        self.assertEqual(result["image"]["jobs"], [])
        self.assertFalse(result["image"]["production_profile_configured"])
        self.assertTrue(result["image"]["synthetic_only"])
        self.assertNotIn("retry_same", result["image"]["allowed_actions"])

    async def test_publication_preview_is_read_only_and_unknown_is_not_ready(self):
        from app.orchestrator.steps.publish import preview_publication
        from app.models import Artifact, ArtifactKind
        from sqlalchemy import select
        before_status, before_meta = self.project.status, dict(self.project.meta or {})
        missing = await preview_publication(self.session, self.project)
        self.assertEqual(missing["validation"]["reason"], "final_artifact_missing")
        path = self.project.data_dir / "opaque-final.mp4"
        path.write_bytes(b"synthetic file; not a verified movie")
        artifact = Artifact(project_id=7, kind=ArtifactKind.final_video,
            uuid="a"*32, path=str(path), meta={})
        self.session.add(artifact)
        await self.session.commit()
        first = await preview_publication(self.session, self.project)
        self.assertTrue(first["dry_run"])
        self.assertFalse(first["send_allowed"])
        self.assertIn(first["validation"]["status"], {"unknown", "failed"})
        self.assertEqual(len(first["platforms"]), 5)
        self.assertEqual(first, await preview_publication(self.session, self.project))
        self.assertEqual(len((await self.session.scalars(select(Artifact))).all()), 1)
        await self.session.refresh(self.project)
        self.assertEqual((self.project.status, self.project.meta or {}), (before_status, before_meta))
        outside = self.root / "foreign.mp4"
        outside.write_bytes(b"synthetic")
        artifact.path = str(outside)
        await self.session.commit()
        escaped = await preview_publication(self.session, self.project)
        self.assertEqual(escaped["validation"]["reason"], "artifact_outside_project")
        self.assertNotIn(str(outside), str(escaped))

    async def test_legacy_publish_step_cannot_mark_managed_project_published(self):
        from app.orchestrator.steps.publish import run
        from app.settings import settings
        from unittest.mock import patch
        self.project.status = ProjectStatus.publishing
        await self.session.commit()
        with patch.object(settings, "social_publish_enabled", False):
            with self.assertRaisesRegex(RuntimeError, "managed"):
                await run(self.session, self.project, None)
        self.assertEqual(self.project.status, ProjectStatus.publishing)

    async def test_panel_publication_exposes_preview_without_enabling_send(self):
        from app.models import WorkflowRun
        from app.services.media_view import context_for_nodes
        async with self.sessions() as s:
            run = await s.get(WorkflowRun, 1)
            run.nodes_snapshot = [{**node, "type":"publish"} if node["id"]=="image" else node
                                  for node in run.nodes_snapshot]
            await s.commit()
            result = await context_for_nodes(s, self.scope)
        self.assertEqual(result["image"]["result_kind"], "publication")
        self.assertTrue(result["image"]["publication"]["dry_run"])
        self.assertFalse(result["image"]["publication"]["send_allowed"])
        self.assertFalse(result["image"]["limited_management"])

    async def test_managed_image_cannot_start_unscoped_animation_sidecar(self):
        from app.services.anim_pr_sidecar import ensure_anim_pr_sidecar, _TASKS
        try:
            self.assertFalse(ensure_anim_pr_sidecar(7))
        finally:
            task = _TASKS.get(7)
            if task:
                import asyncio
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                _TASKS.pop(7, None)

    async def test_decision_reads_media_contract_and_never_offers_text_recovery(self):
        import json
        from app.services.decision_state import DecisionRecord
        from app.services.decision_v1 import DecisionSupervisor
        from app.services.diagnosis import Incident
        cfg = json.loads(self.cfg.read_text())
        cfg["decision"] = {"catalog_id": "gpt-kie", "model_id": "gpt-5-6-sol",
            "reasoning_effort": "low", "max_output_tokens": 1024, "timeout_s": 30}
        cfg["endpoint"] = "http://127.0.0.1:18766/codex/v1/responses"
        cfg["allowed_actions"] = ["read_events", "read_contract", "retry_same", "wait", "human_review", "finish"]
        self.cfg.write_text(json.dumps(cfg))
        async with self.sessions() as s:
            s.add(Incident(incident_id="media-incident", execution_id=self.scope.execution_id,
                project_id=7, node_key="image", attempt_id="attempt-original", type="media_failure",
                severity="error", status="human_review", details={"currently_relevant": True}))
            s.add(DecisionRecord(decision_id="media-decision", execution_id=self.scope.execution_id,
                project_id=7, incident_id="media-incident", node_key="image",
                source_attempt_id="attempt-original", context_version="pending"))
            await s.commit()
        supervisor = DecisionSupervisor(self.observer)
        from unittest.mock import patch
        # Unit-level DB dependency binding only; no worker/HTTP replacement.
        with patch("app.project_db.project_db_session_scope", lambda _id: self.sessions()):
            context = await supervisor.context("media-decision")
            self.assertNotIn("retry_same", context["allowed_actions"])
            self.assertNotIn("finish", context["allowed_actions"])
            contract = await supervisor.read_contract("media-decision")
        self.assertEqual(contract["result_kind"], "image")
        self.assertNotIn("empty_ops_allowed", contract)
        self.assertFalse(contract["semantic_quality_verified"])

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
