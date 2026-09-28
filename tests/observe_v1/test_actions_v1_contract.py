"""Safety contracts for the actions-v1 execution permission layer."""
import unittest
import tempfile
from pathlib import Path
from datetime import datetime
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.services.actions_v1 import (
    ActionSupervisor,
    ActionExecutionControl,
    ExecutionPermission,
    action_is_idempotent,
    clear_permission,
    evaluate_start,
    install_permission,
    get_permission,
    reload_permissions,
)
from app.models import Base, NodeRun, NodeRunStatus, Project, Workflow, WorkflowRun
from app.services.observation import ExecutionControl, digest
from app.services.node_status_machine import start_node_running


class ActionsContract(unittest.TestCase):
    def tearDown(self):
        clear_permission(7)

    def permission(self, **overrides):
        values = dict(enabled=False, held=False, project_id=7, execution_id="exec-1",
                      group_id="group-a", graph_version="graph-v1", node_keys=("n1", "n2"),
                      request_limit=12, requests_used=0)
        values.update(overrides)
        return ExecutionPermission(**values)

    def test_default_off_blocks_start(self):
        self.assertFalse(evaluate_start(self.permission(), project_id=7, node_key="n1",
                                        graph_version="graph-v1").allowed)

    def test_explicit_permission_is_limited_to_group_and_graph(self):
        p = self.permission(enabled=True)
        self.assertTrue(evaluate_start(p, project_id=7, node_key="n2", graph_version="graph-v1").allowed)
        self.assertFalse(evaluate_start(p, project_id=7, node_key="n3", graph_version="graph-v1").allowed)
        self.assertFalse(evaluate_start(p, project_id=7, node_key="n1", graph_version="graph-v2").allowed)
        self.assertFalse(evaluate_start(p, project_id=8, node_key="n1", graph_version="graph-v1").allowed)

    def test_hold_and_budget_block_without_mutating_permission(self):
        held = evaluate_start(self.permission(enabled=True, held=True), project_id=7,
                              node_key="n1", graph_version="graph-v1")
        spent = evaluate_start(self.permission(enabled=True, requests_used=12), project_id=7,
                               node_key="n1", graph_version="graph-v1")
        self.assertEqual(held.reason, "execution_held")
        self.assertEqual(spent.reason, "request_budget_exhausted")

    def test_action_idempotency_is_execution_scoped(self):
        self.assertTrue(action_is_idempotent("exec-1", "action-1", "exec-1", "action-1"))
        self.assertFalse(action_is_idempotent("exec-1", "action-1", "exec-1", "action-2"))
        self.assertFalse(action_is_idempotent("exec-1", "action-1", "exec-2", "action-1"))

    def test_fsm_blocks_nodes_outside_the_explicit_group(self):
        install_permission(self.permission(enabled=True))
        node = NodeRun(workflow_run_id=1, node_key="n3", node_type="script", status=NodeRunStatus.pending)
        self.assertFalse(start_node_running(node, project_id=7, initiator="worker"))
        self.assertEqual(node.status, NodeRunStatus.pending)

    def test_fsm_runs_allowed_nodes_and_leaves_unmanaged_projects_unchanged(self):
        install_permission(self.permission(enabled=True))
        node = NodeRun(workflow_run_id=1, node_key="n2", node_type="script", status=NodeRunStatus.pending)
        self.assertTrue(start_node_running(node, project_id=7, initiator="worker"))
        self.assertEqual(node.status, NodeRunStatus.running)
        other = NodeRun(workflow_run_id=2, node_key="n3", node_type="script", status=NodeRunStatus.pending)
        self.assertTrue(start_node_running(other, project_id=8, initiator="worker"))
        self.assertEqual(other.status, NodeRunStatus.running)


class DurableActions(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{Path(self.tmp.name) / 'master.db'}")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        self.nodes = [{"id":"n1","type":"plan","data":{"groupId":"g1"}},
                      {"id":"n2","type":"script","data":{"groupId":"g2"}}]
        self.graph = digest({"nodes":self.nodes,"edges":[]})
        async with self.sessions() as session:
            session.add(Project(id=7,slug="actions-fixture",topic="synthetic",meta={}))
            session.add(Workflow(id=9,name="actions fixture",nodes=self.nodes,edges=[]))
            session.add(WorkflowRun(id=11,workflow_id=9,project_id=7,nodes_snapshot=self.nodes,edges_snapshot=[]))
            session.add_all([
                NodeRun(workflow_run_id=11,node_key="n1",node_type="plan",status=NodeRunStatus.pending),
                NodeRun(workflow_run_id=11,node_key="n2",node_type="script",status=NodeRunStatus.pending),
                ExecutionControl(execution_id="exec-durable",project_id=7,workflow_run_id=11,
                    node_keys=["n1","n2"],graph_version=self.graph,mode="observe",
                    observer_state="active",created_at=datetime.utcnow(),cursors={},gaps=[]),
            ])
            await session.commit()
        self.supervisor = ActionSupervisor(self.sessions)

    async def asyncTearDown(self):
        clear_permission(7)
        await self.engine.dispose()
        self.tmp.cleanup()

    async def test_opt_in_budget_hold_restart_and_user_stop_are_safe(self):
        enabled = await self.supervisor.enable(project_id=7,execution_id="exec-durable",group_id="g1",
            node_keys=["n1"],graph_version=self.graph,initiator="test-operator",
            rationale="synthetic acceptance",request_limit=1)
        self.assertTrue(enabled["enabled"])
        async with self.sessions() as session:
            scope=await session.get(ExecutionControl,"exec-durable")
            self.assertEqual(scope.mode,"managed")
            self.assertTrue(await session.get(ActionExecutionControl,"exec-durable"))
        self.assertEqual(evaluate_start(get_permission(7),project_id=7,node_key="n2",
            graph_version=self.graph).reason,"outside_execution_scope")
        self.assertEqual(await self.supervisor.charge_request("exec-durable"),1)
        with self.assertRaisesRegex(RuntimeError,"budget exhausted"):
            await self.supervisor.charge_request("exec-durable")
        await self.supervisor.set_held(project_id=7,execution_id="exec-durable",held=True,
            initiator="test-operator",rationale="pause test",idempotency_key="pause-key-0001")
        await reload_permissions(self.sessions)
        self.assertTrue(get_permission(7).held)
        async with self.sessions() as session:
            project=await session.get(Project,7)
            project.meta={"user_stop":True}
            await session.commit()
        with self.assertRaisesRegex(ValueError,"user_stop"):
            await self.supervisor.set_held(project_id=7,execution_id="exec-durable",held=False,
                initiator="test-operator",rationale="resume test",idempotency_key="resume-key-0001")

    async def test_recovery_requires_actual_current_incident_before_any_dispatch(self):
        await self.supervisor.enable(project_id=7,execution_id="exec-durable",group_id="g1",
            node_keys=["n1"],graph_version=self.graph,initiator="test-operator",
            rationale="synthetic acceptance",request_limit=20)
        with self.assertRaisesRegex(ValueError,"incident"):
            await self.supervisor.recover_node(project_id=7,execution_id="exec-durable",
                incident_id="absent",node_key="n1",idempotency_key="recovery-test-0001",
                initiator="test-operator",rationale="synthetic recovery")
        async with self.sessions() as session:
            node=await session.scalar(__import__('sqlalchemy').select(NodeRun).where(NodeRun.node_key=='n1'))
            self.assertEqual(node.status,NodeRunStatus.pending)


if __name__ == "__main__":
    unittest.main()
