"""Generic worker observation/diagnosis contracts on synthetic database state."""
import unittest

from test_media_jobs import MediaJobs
from app.services import revisions
from app.models import ProjectStatus
from app.services import observation


class MediaObservation(MediaJobs):
    async def continuation_fixture(self):
        from app.models import Project, ProjectStatus, Workflow, WorkflowRun, NodeRun, NodeRunStatus, Frame
        from app.services.actions_v1 import ActionExecutionControl, reload_permissions
        from app.services.decision_config import runtime_config
        from sqlalchemy import select
        self.nodes.insert(0, {"id":"text", "type":"excel_gpt", "data":{"groupId":"media", "slotIndex":0}})
        self.edges=[{"id":"text-image", "source":"text", "target":"image", "data":{"kind":"after"}}]
        version=revisions.graph_version(self.nodes, self.edges)
        async with self.sessions() as s:
            p=await s.get(Project,7);p.status=ProjectStatus.enrich_1_ready;p.auto_mode=False
            p.meta={"canvas_graph":{"nodes":self.nodes,"edges":self.edges},
                    "excel_gpt_completed_keys":["text"],"active_excel_gpt_node_key":"text"}
            w=await s.get(Workflow,1);w.nodes=self.nodes;w.edges=self.edges;w.is_default=True
            r=await s.get(WorkflowRun,1);r.nodes_snapshot=self.nodes;r.edges_snapshot=self.edges
            s.add(NodeRun(workflow_run_id=1,node_key="text",node_type="excel_gpt",status=NodeRunStatus.done))
            s.add(Frame(project_id=7,number=1,voiceover_text="Synthetic scene",image_prompt="Synthetic image"))
            scope=await s.get(observation.ExecutionControl,self.scope.execution_id)
            scope.node_keys=["text","image"];scope.graph_version=version
            c=await s.get(ActionExecutionControl,self.scope.execution_id);c.node_keys=scope.node_keys;c.graph_version=version
            await s.commit()
        await self.observer.reload()
        self.scope=self.observer.scopes[1]
        await self.session.refresh(self.project)
        ctx=observation._attempt.get();ctx.scope=self.scope;ctx.node_key="text"
        ctx.runtime_config_hash=observation.digest(runtime_config())
        self.image_node=await self.session.scalar(select(NodeRun).where(NodeRun.node_key=="image"))

    async def continue_native(self):
        from app.orchestrator.steps.enrich_xlsx import _maybe_auto_chain_excel_gpt
        from app.models import ProjectStatus
        await _maybe_auto_chain_excel_gpt(self.session,self.project,1,ProjectStatus.enrich_1_ready,finished_key="text")

    async def finished_recovery_fixture(self, *, error=False, change_config_before_finish=False):
        """Reconciliation integration fixture, NOT a worker/exhaustion acceptance.

        Seed committed completion evidence explicitly; run the real ticket finish,
        restart reload, DB sync, verification, planner and FSM without substitutes.
        Only the master connection factory points to this test's isolated SQLite.
        """
        import json
        import uuid
        from datetime import datetime
        from unittest.mock import patch
        from sqlalchemy import select
        from app import db, project_db
        from app.models import Project
        from app.services.actions_v1 import ActionRecord
        from app.services.actions_recovery import RecoveryAction, worker_finished

        await self.continuation_fixture()
        ctx = observation._attempt.get()
        self.captured_config_hash = ctx.runtime_config_hash
        self.original_config = self.cfg.read_text()
        resource = patch.object(db, "SessionLocal", self.sessions)
        resource.start()
        self.addCleanup(resource.stop)
        project_db.register_project_data_dir(7, self.project.data_dir)
        self.addAsyncCleanup(project_db.close_all_project_engines)
        async with self.sessions() as master:
            project = await master.get(Project, 7)
            await project_db.migrate_single_project_data(master, project, create_backup=False)
            self.recovery_id = str(uuid.uuid4())
            master.add(RecoveryAction(action_id=self.recovery_id,
                execution_id=self.scope.execution_id, project_id=7, incident_id="fixture-incident",
                node_key="text", source_attempt_id="fixture-source", new_attempt_id=ctx.attempt_id,
                idempotency_key="fixture-recovery", preconditions={"graph_version": self.scope.graph_version},
                budget_state={"recovery_limit": 1}, status="claimed", result={}, verification_result={}))
            master.add(ActionRecord(action_id=self.recovery_id, execution_id=self.scope.execution_id,
                project_id=7, node_key="text", attempt_id=ctx.attempt_id, action_kind="node_recovery",
                initiator="fixture", rationale="committed recovery fixture", idempotency_key="fixture-recovery",
                status="running", preconditions={}, evidence_ids=[], verification={}))
            # These are test input facts, not claims that a model/worker ran here.
            for kind, payload in (
                ("node_start", {}), ("model_start", {}), ("model_end", {"complete": True}),
                ("parse_result", {"payload_present": True}), ("data_applied", {"transactional": True}),
                ("node_end", {}), ("node_change", {"transactional": True,
                    "changes": {"status": {"before": ["running"], "after": ["done"]}}}),
            ):
                master.add(observation.ObservationEvent(event_id=str(uuid.uuid4()),
                    execution_id=self.scope.execution_id, project_id=7, workflow_run_id=1,
                    node_key="text", attempt_id=ctx.attempt_id, request_id="fixture-request",
                    db_scope="project", kind=kind, payload=payload, created_at=datetime.utcnow()))
            await master.commit()
        if change_config_before_finish:
            changed = json.loads(self.original_config)
            changed["media"]["max_bytes"] -= 1
            self.cfg.write_text(json.dumps(changed))
        await worker_finished(ctx, error=error)
        await self.session.rollback()

    async def reconcile_after_context_loss(self):
        from app.services.actions_recovery import reconcile
        # Drop live AttemptContext and replace Observer/permission cache. Only
        # persisted ticket/config evidence can authorize the original successor.
        token = observation._attempt.set(None)
        try:
            restarted = observation.Observer(self.sessions, body_root=self.root / "bodies")
            observation._observer = restarted
            await restarted.reload()
            await reconcile(restarted)
        finally:
            observation._attempt.reset(token)

    async def recovery_state(self):
        from sqlalchemy import select
        from app import project_db
        from app.models import NodeRun, Project
        from app.services.actions_recovery import RecoveryAction
        async with self.sessions() as master:
            ticket = await master.get(RecoveryAction, self.recovery_id)
            master_status = (await master.get(Project, 7)).status.value
        async with project_db.project_db_session_scope(7) as session:
            node = await session.scalar(select(NodeRun).where(NodeRun.node_key == "image"))
            events = list(await session.scalars(select(observation.ObservationOutbox).where(
                observation.ObservationOutbox.kind.in_(["managed_successor", "managed_successor_denied"]))))
            return ticket, node, events, master_status

    async def test_recovery_keeps_attempt_config_and_continues_image_once_after_reload(self):
        from app.services.actions_recovery import RecoveryAction
        from app.models import NodeRunStatus
        await self.finished_recovery_fixture(change_config_before_finish=True)
        async with self.sessions() as s:
            row = await s.get(RecoveryAction, self.recovery_id)
            self.assertEqual(row.result.get("runtime_config_hash"), self.captured_config_hash,
                             "persist the attempt's captured hash, never the config at finish/restart")
        self.cfg.write_text(self.original_config)
        await self.reconcile_after_context_loss()
        ticket, node, events, status = await self.recovery_state()
        self.assertTrue(ticket.verification_result["verified"])
        self.assertEqual(node.status, NodeRunStatus.running)
        self.assertEqual(status, "generating_images")
        self.assertEqual([e.kind for e in events], ["managed_successor"])
        self.assertEqual(events[0].payload["runtime_config_hash"], self.captured_config_hash)
        await self.reconcile_after_context_loss()
        replay, same_node, same_events, _ = await self.recovery_state()
        self.assertEqual(replay.action_id, ticket.action_id)
        self.assertEqual((same_node.attempts, len(same_events)), (node.attempts, 1))

    async def test_recovery_cannot_rebase_config_at_reconciliation(self):
        from app.models import NodeRunStatus
        await self.finished_recovery_fixture(change_config_before_finish=True)
        await self.reconcile_after_context_loss()
        ticket, node, events, status = await self.recovery_state()
        self.assertTrue(ticket.verification_result["verified"], "text result verified independently of next-node gate")
        self.assertEqual(node.status, NodeRunStatus.pending)
        self.assertEqual(status, "enrich_1_ready")
        self.assertEqual([e.kind for e in events], ["managed_successor_denied"])
        self.assertEqual(events[0].payload["reason"], "media_configuration_changed_or_disabled")

    async def test_reconciliation_reads_persisted_hash_without_live_attempt(self):
        from app.services.actions_recovery import RecoveryAction
        from app.models import NodeRunStatus
        await self.finished_recovery_fixture()
        # Independent read-side contract: explicitly seed the future stored field
        # so a write-side failure cannot mask failure to rehydrate it.
        async with self.sessions() as s:
            row = await s.get(RecoveryAction, self.recovery_id)
            row.result = {**row.result, "runtime_config_hash": self.captured_config_hash}
            await s.commit()
        await self.reconcile_after_context_loss()
        _, node, events, _ = await self.recovery_state()
        self.assertEqual(node.status, NodeRunStatus.running)
        self.assertEqual([e.kind for e in events], ["managed_successor"])

    async def test_legacy_recovery_without_config_proof_stays_blocked(self):
        from app.services.actions_recovery import RecoveryAction
        from app.models import NodeRunStatus
        await self.finished_recovery_fixture()
        async with self.sessions() as s:
            row = await s.get(RecoveryAction, self.recovery_id)
            row.result = {k: v for k, v in row.result.items() if k != "runtime_config_hash"}
            await s.commit()
        await self.reconcile_after_context_loss()
        _, node, events, _ = await self.recovery_state()
        self.assertEqual(node.status, NodeRunStatus.pending)
        self.assertEqual([e.kind for e in events], ["managed_successor_denied"])

    async def test_failed_recovery_does_not_use_persisted_config_to_continue(self):
        from app.services.actions_v1 import get_permission
        from app.models import NodeRunStatus
        await self.finished_recovery_fixture(error=True)
        await self.reconcile_after_context_loss()
        ticket, node, events, _ = await self.recovery_state()
        self.assertEqual(ticket.status, "human_review")
        self.assertTrue(get_permission(7).held)
        self.assertEqual(node.status, NodeRunStatus.pending)
        self.assertEqual(events, [])

    async def test_managed_text_to_image_continues_once_via_original_fsm(self):
        from app.models import ProjectStatus,NodeRunStatus
        from sqlalchemy import select
        await self.continuation_fixture()
        await self.continue_native()
        self.assertEqual(self.project.status,ProjectStatus.generating_images)
        self.assertEqual(self.image_node.status,NodeRunStatus.running)
        await self.continue_native()  # must not reset/restart the scheduled target
        self.assertEqual(self.image_node.attempts,0)
        await self.session.commit()
        facts=(await self.session.scalars(select(observation.ObservationOutbox).where(
            observation.ObservationOutbox.kind=="managed_successor"))).all()
        self.assertEqual(len(facts),1)
        self.assertEqual(facts[0].payload["to"],"image")

    async def test_managed_media_continuation_preserves_stop_scope_and_uncertainty(self):
        from app.models import NodeRunStatus
        from app.services.actions_v1 import ActionExecutionControl,update_permission
        await self.continuation_fixture()
        ctx=observation._attempt.get()
        baseline=dict(self.project.meta)
        for flag in ("user_stop","mass_lane_user_stop","auto_await_manual_start"):
            with self.subTest(flag=flag):
                self.project.meta={**baseline,flag:True}
                await self.continue_native()
                self.assertTrue(self.project.meta[flag]);self.assertEqual(self.image_node.status,NodeRunStatus.pending)
        self.project.meta=baseline
        for reason in ("scope","audit","uncertain","config","held","budget"):
            with self.subTest(reason=reason):
                old_keys=list(ctx.scope.node_keys);old_hash=ctx.runtime_config_hash
                if reason=="scope":ctx.scope.node_keys=["text"]
                if reason=="audit":self.observer.failures[self.scope.execution_id]="disk"
                if reason=="uncertain":self.observer.commit_uncertainty[self.scope.execution_id]="unknown"
                if reason=="config":ctx.runtime_config_hash="stale"
                if reason=="held":update_permission(7,held=True)
                if reason=="budget":update_permission(7,requests_used=8)
                await self.continue_native()
                self.assertEqual(self.image_node.status,NodeRunStatus.pending)
                ctx.scope.node_keys=old_keys;ctx.runtime_config_hash=old_hash
                self.observer.failures.clear();self.observer.commit_uncertainty.clear()
                from app.services.actions_v1 import reload_permissions
                await reload_permissions(self.sessions)

    async def test_managed_media_continuation_requires_input_pending_and_revision(self):
        from app.models import NodeRunStatus,Frame
        from sqlalchemy import select
        await self.continuation_fixture()
        original=self.project.meta
        self.project.meta={**original,"canvas_graph":{"nodes":self.nodes,"edges":[]}}
        await self.continue_native();self.assertEqual(self.image_node.status,NodeRunStatus.pending)
        self.project.meta=original
        frame=await self.session.scalar(select(Frame).where(Frame.project_id==7))
        frame.image_prompt=""
        await self.continue_native();self.assertEqual(self.image_node.status,NodeRunStatus.pending)
        frame.image_prompt="Synthetic image"
        for status in (NodeRunStatus.failed,NodeRunStatus.waiting_hitl,NodeRunStatus.done):
            self.image_node.status=status
            await self.continue_native();self.assertEqual(self.image_node.status,status)
        self.image_node.status=NodeRunStatus.pending
        observation._attempt.get().scope.mode="observe"
        await self.continue_native();self.assertEqual(self.image_node.status,NodeRunStatus.pending)

    async def test_managed_media_continuation_rolls_back_with_project(self):
        from app.models import NodeRun,NodeRunStatus
        from sqlalchemy import select
        await self.continuation_fixture()
        await self.continue_native()
        self.assertEqual(self.image_node.status,NodeRunStatus.running)
        await self.session.rollback()
        async with self.sessions() as s:
            node=await s.scalar(select(NodeRun).where(NodeRun.node_key=="image"))
            self.assertEqual(node.status,NodeRunStatus.pending)
            facts=(await s.scalars(select(observation.ObservationOutbox).where(
                observation.ObservationOutbox.kind=="managed_successor"))).all()
            self.assertEqual(facts,[])

    async def test_unintegrated_family_cannot_be_enabled_as_managed_executor(self):
        from app.services.node_capabilities import require_managed_capability, capability_for
        require_managed_capability("images")
        require_managed_capability("excel_gpt")
        require_managed_capability("plan")
        require_managed_capability("script")
        for kind in ("publish", "audio", "unregistered"):
            self.assertFalse(capability_for(kind).managed, "unaccepted adapters stay disabled even if tools are later installed")
            with self.assertRaisesRegex(ValueError, "adapter"):
                require_managed_capability(kind)
        # videos/items/music are managed for supervised RECOVERY only: never startable by the supervisor
        # assemble likewise: managed only for ONE local re-run under a claimed ticket, never a fresh managed start
        for kind in ("videos", "items", "music", "assemble"):
            self.assertTrue(capability_for(kind).managed)
            with self.assertRaisesRegex(ValueError, "recovery only"):
                require_managed_capability(kind)

    async def test_continuation_requires_original_planner_prerequisites(self):
        from app.models import Workflow, WorkflowRun, NodeRun, NodeRunStatus
        from app.services.actions_v1 import ActionExecutionControl, reload_permissions
        await self.continuation_fixture()
        self.nodes.append({"id":"unfinished", "type":"excel_gpt", "data":{"slotIndex":0}})
        self.edges.append({"id":"other-input", "source":"unfinished", "target":"image", "data":{"kind":"after"}})
        version=revisions.graph_version(self.nodes, self.edges)
        self.project.meta={**self.project.meta,"canvas_graph":{"nodes":self.nodes,"edges":self.edges}}
        for cls in (Workflow,WorkflowRun):
            row=await self.session.get(cls,1)
            if cls is Workflow:row.nodes=self.nodes;row.edges=self.edges
            else:row.nodes_snapshot=self.nodes;row.edges_snapshot=self.edges
        self.session.add(NodeRun(workflow_run_id=1,node_key="unfinished",node_type="excel_gpt",status=NodeRunStatus.pending))
        for cls in (ActionExecutionControl,observation.ExecutionControl):
            row=await self.session.get(cls,self.scope.execution_id);row.graph_version=version
        await self.session.commit()
        observation._attempt.get().scope.graph_version=version
        await reload_permissions(self.sessions)
        await self.continue_native()
        self.assertEqual(self.image_node.status,NodeRunStatus.pending)

    async def test_continuation_audit_failure_aborts_transaction(self):
        from unittest.mock import patch
        from app.models import NodeRun,NodeRunStatus
        from sqlalchemy import select
        await self.continuation_fixture()
        original=self.observer.stage
        def fail_success(*args,**kwargs):
            if args[2]=="managed_successor":return False
            return original(*args,**kwargs)
        with patch.object(self.observer,"stage",side_effect=fail_success):
            with self.assertRaisesRegex(RuntimeError,"audit"):
                await self.continue_native()
        await self.session.rollback()
        async with self.sessions() as s:
            node=await s.scalar(select(NodeRun).where(NodeRun.node_key=="image"))
            self.assertEqual(node.status,NodeRunStatus.pending)

    async def test_nonblocking_snapshot_warning_is_not_an_operator_hold(self):
        from app.models import NodeRunStatus
        from app.services.diagnosis import Incident
        await self.continuation_fixture()
        async with self.sessions() as s:
            s.add(Incident(incident_id="lag-warning",execution_id=self.scope.execution_id,
                project_id=7,node_key=None,attempt_id=None,type="db_disagreement",severity="warning",
                status="human_review",details={"blocking":False,"currently_relevant":True}))
            await s.commit()
        await self.continue_native()
        self.assertEqual(self.image_node.status,NodeRunStatus.running)

    async def test_blocking_incident_prevents_media_continuation(self):
        from app.models import NodeRunStatus
        from app.services.diagnosis import Incident
        await self.continuation_fixture()
        async with self.sessions() as s:
            s.add(Incident(incident_id="current-blocker",execution_id=self.scope.execution_id,
                project_id=7,node_key="text",attempt_id="attempt-original",type="missing_payload",severity="error",
                status="human_review",details={"blocking":True,"currently_relevant":True}))
            await s.commit()
        await self.continue_native()
        self.assertEqual(self.image_node.status,NodeRunStatus.pending)

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
