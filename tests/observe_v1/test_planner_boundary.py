"""Same native auto-advance graph with management OFF/ON, synthetic data only."""
import json,unittest
from pathlib import Path
from unittest.mock import AsyncMock,patch
from sqlalchemy import select
import test_actions_v1_contract as fixtures
from app.models import Project,ProjectStatus,Workflow,WorkflowRun,NodeRun,NodeRunStatus,HITLRequest,HITLKind,HITLDecision
from app.services.observation import ExecutionControl,digest

class Boundary(fixtures.DurableActions):
    async def check_boundary(self,managed):
        from app.orchestrator.auto_advance import maybe_auto_advance
        edges=[{'id':'after-plan','source':'n1','target':'n2'}]
        graph=digest({'nodes':self.nodes,'edges':edges})
        async with self.sessions() as s:
            p=await s.get(Project,7);p.auto_mode=True;p.status=ProjectStatus.plan_ready
            p.general_plan='Synthetic offline plan: character enters room, sees object, walks to table. '*5
            p.meta={'canvas_graph':{'nodes':self.nodes,'edges':edges}}
            wf=await s.get(Workflow,9);wf.is_default=True;wf.edges=edges
            run=await s.get(WorkflowRun,11);run.edges_snapshot=edges
            n=await s.scalar(select(NodeRun).where(NodeRun.node_key=='n1'));n.status=NodeRunStatus.done
            scope=await s.get(ExecutionControl,'exec-durable');scope.graph_version=graph
            s.add(HITLRequest(project_id=7,kind=HITLKind.approve_plan,decision=HITLDecision.approved,payload={'synthetic_fixture':True}))
            await s.commit()
        if managed:
            await self.supervisor.enable(project_id=7,execution_id='exec-durable',group_id='g1',
                node_keys=['n1'],graph_version=graph,initiator='synthetic-operator',rationale='planner boundary',request_limit=2)
        model=AsyncMock(side_effect=AssertionError('No model request allowed during planning'))
        async with self.sessions() as s:
            p=await s.get(Project,7)
            with patch('app.services.gpt_api.chat',model):
                reported=await maybe_auto_advance(s,p,None)
            await s.commit()
            node=await s.scalar(select(NodeRun).where(NodeRun.node_key=='n2'))
            result={'managed':managed,'planner_return':reported,'project_status':p.status.value,
                'next_node_status':node.status.value,'next_node_attempts':node.attempts,'model_posts':model.call_count,
                'fixture':'native plan_ready input with pre-approved HITL, original planner/prepare/FSM'}
            self.assertEqual(node.status,NodeRunStatus.pending if managed else NodeRunStatus.running)
            self.assertEqual(p.status,ProjectStatus.plan_ready if managed else ProjectStatus.scripting)
            self.assertEqual(node.attempts,0 if managed else 1)
            model.assert_not_awaited()
        Path('/results/planner-'+('on' if managed else 'off')+'.json').write_text(json.dumps(result,indent=2))
    async def test_native_auto_advance_off_really_starts_next_node(self):await self.check_boundary(False)
    async def test_native_auto_advance_managed_blocks_before_dispatch(self):await self.check_boundary(True)

for name in dir(Boundary):
    if name.startswith('test_') and name not in Boundary.__dict__:setattr(Boundary,name,None)
if __name__=='__main__':unittest.main()
