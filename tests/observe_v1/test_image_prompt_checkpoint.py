import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


class NativeCheckpointTransactions(unittest.IsolatedAsyncioTestCase):
    async def test_native_second_attempt_can_write_after_real_snapshot_flush(self):
        from openpyxl import Workbook
        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
        from app.models import Base, Frame, Project, ProjectStatus, WorkflowRun, NodeRun, NodeRunStatus
        from app.orchestrator.steps import generate_image_prompts
        from app.services import xlsx_step_runners

        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);data=root/'project';data.mkdir()
            workbook=Workbook();workbook.save(data/'project.xlsx');workbook.close()
            engine=create_async_engine('sqlite+aiosqlite:///'+str(data/'project.db'),
                connect_args={'timeout':0.05})
            sessions=async_sessionmaker(engine,expire_on_commit=False)
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            calls=[];prompt='Synthetic complete frame prompt. '*10
            uid1,uid2='af'*16,'bf'*16
            try:
                async with sessions() as session:
                    project=Project(id=2,slug='synthetic-checkpoint',title='Synthetic',topic='Synthetic',
                        status=ProjectStatus.generating_image_prompts,meta={})
                    session.add(project)
                    session.add(WorkflowRun(id=7,workflow_id=1,project_id=2,nodes_snapshot=[
                        {'id':'native-prompt-A','type':'image_prompts'}],edges_snapshot=[]))
                    session.add(NodeRun(workflow_run_id=7,node_key='native-prompt-A',
                        node_type='image_prompts',status=NodeRunStatus.running))
                    for number,uid in enumerate((uid1,uid2),1):
                        session.add(Frame(project_id=2,number=number,uuid=uid,sort_key=number,
                            voiceover_text='Synthetic voiceover '+str(number),attrs={}))
                    await session.commit()

                    async def partial_then_complete(*args,**kwargs):
                        calls.append(len(calls)+1)
                        uid=uid1 if len(calls)==1 else uid2
                        async with sessions() as writer:
                            frame=await writer.scalar(select(Frame).where(Frame.uuid==uid))
                            frame.image_prompt=prompt
                            await writer.commit()
                        return SimpleNamespace(apply_ops=[{'frame_uuid':uid,
                            'fields':{'image_prompt':prompt}}],ops_applied_inline=True)

                    with patch.object(Project,'data_dir',property(lambda _:data)), \
                         patch.object(xlsx_step_runners,'run_img_pr_xlsx',side_effect=partial_then_complete):
                        await generate_image_prompts.run(session,project,SimpleNamespace())
                        self.assertEqual(calls,[1,2])
                        self.assertEqual(project.status,ProjectStatus.image_prompts_ready)
                        frames=(await session.scalars(select(Frame).order_by(Frame.number))).all()
                        self.assertEqual([f.image_prompt for f in frames],[prompt,prompt])
                        self.assertEqual([f.voiceover_text for f in frames],
                            ['Synthetic voiceover 1','Synthetic voiceover 2'])
                        self.assertIn('native-prompt-A',project.meta['xlsx_snapshots_by_node'])
                        self.assertTrue((data/project.meta['xlsx_snapshots_by_node']['native-prompt-A']['rel']).is_file())
            finally:
                await engine.dispose()


if __name__=='__main__':
    unittest.main(verbosity=2)
