"""Общие фикстуры тестов контура изменений: временная БД, временный PROMPTS_ROOT, шлюз-заглушка."""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base
from app.services import prompt_active_global, prompt_library
from app.services.action_layer import store as al_store
from app.services.change_control import store as cs
from app.services.change_control.gateway import ProjectNotFound
from app.services.project_meta import merge_project_meta

NODES = [
    {"id": "n_plan", "type": "plan", "position": {"x": 0, "y": 0}, "data": {"label": "Plan"}},
    {"id": "n_script", "type": "script", "position": {"x": 1, "y": 0}, "data": {"label": "Script"}},
    {"id": "n_split", "type": "split", "position": {"x": 2, "y": 0}, "data": {"label": "Split"}},
    {"id": "n_gpt", "type": "excel_gpt", "position": {"x": 3, "y": 0},
     "data": {"label": "GPT", "slotIndex": 1}},
    {"id": "n_hero", "type": "hero", "position": {"x": 4, "y": 0}, "data": {"label": "Hero"}},
]
EDGES = [{"id": "e0", "source": "n_plan", "target": "n_script"},
         {"id": "e1", "source": "n_script", "target": "n_split"},
         {"id": "e2", "source": "n_split", "target": "n_gpt"}]

PLAN_PROMPT = ("Plan master prompt. Topic: {{VAR:PROJECT_TOPIC}}. Write a plan and answer with "
               "JSON only.\nKeep the structure of the answer.")
SCRIPT_PROMPT = "Script master prompt. Write the voice-over text for {{TOPIC}} in short lines."
SPLIT_PROMPT = "Split master prompt. Cut the voice-over into cells by meaning, never in half."
GPT_PROMPT = ('GPT agent prompt. Answer only with apply-ops JSON: {"ops":[{"frame_uuid":"<id>",'
              '"fields":{}}]}. Use {{VAR:STYLE}} for the style line.')

PROMPT_FILES = {
    "01_plan/default.md": PLAN_PROMPT,
    "01_plan/plan_alt.md": "Alternative plan prompt for tests. Topic: {{VAR:PROJECT_TOPIC}}. JSON only.",
    "02_script/default.md": SCRIPT_PROMPT,
    "03_razbivka/default.md": SPLIT_PROMPT,
    "05_excel_gpt/default.md": GPT_PROMPT,
    "05_excel_gpt/cc_test_alt.md": ('Alternative agent. Answer only with apply-ops JSON: '
                                    '{"ops":[{"frame_uuid":"<id>","fields":{}}]} {{VAR:STYLE}}'),
}


def canvas_meta(**extra: Any) -> dict:
    meta = {"canvas_graph": {"nodes": copy.deepcopy(NODES), "edges": copy.deepcopy(EDGES)},
            "node_step_params": {"plan": {"duration_seconds": 60},
                                 "split": {"cell_min_chars": 100, "cell_max_chars": 400}}}
    meta.update(extra)
    return meta


class FakeGateway:
    """Состояние проекта в памяти; PATCH ведёт себя как настоящий (merge защищённых бакетов)."""

    def __init__(self, *, meta: dict | None = None, status: str = "new", topic: str = "tema"):
        self.state = {"id": 1, "status": status, "topic": topic,
                      "meta": meta if meta is not None else canvas_meta(),
                      "prompt_overrides": {}, "gpt_text_overrides": {}}
        self.patches: list[dict] = []
        self.fail_patches = 0          # сколько следующих PATCH упадёт
        self.corrupt_after_patch = None  # callable(state) выполняется после следующего PATCH

    async def get(self, project_id: int) -> dict:
        if project_id != 1:
            raise ProjectNotFound("no such project")
        return copy.deepcopy(self.state)

    async def patch(self, project_id: int, body: dict) -> None:
        if self.fail_patches:
            self.fail_patches -= 1
            raise RuntimeError("endpoint down")
        self.patches.append(copy.deepcopy(body))
        for key, value in body.items():
            if key == "meta":
                self.state["meta"] = merge_project_meta(self.state["meta"], value, source="test")
            else:
                self.state[key] = copy.deepcopy(value)
        if self.corrupt_after_patch is not None:
            fn, self.corrupt_after_patch = self.corrupt_after_patch, None
            fn(self.state)


def write_prompts(root) -> None:
    for rel, text in PROMPT_FILES.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))


@dataclass
class CC:
    factory: Any
    prompts: Any
    gw: FakeGateway = field(default_factory=FakeGateway)

    async def history(self, event: str | None = None) -> list:
        async with self.factory() as s:
            q = select(al_store.ActionHistory).order_by(al_store.ActionHistory.id)
            if event:
                q = q.where(al_store.ActionHistory.event == event)
            return list((await s.scalars(q)).all())

    async def versions(self, target: str) -> list:
        async with self.factory() as s:
            return list((await s.scalars(select(cs.ConfigVersion).where(
                cs.ConfigVersion.target == target).order_by(cs.ConfigVersion.version))).all())

    async def proposal(self, pid: str):
        async with self.factory() as s:
            return await s.get(cs.ChangeProposal, pid)

    def master_bytes(self, rel: str) -> bytes:
        return (self.prompts / rel).read_bytes()


@pytest.fixture
def prompts_root(tmp_path, monkeypatch):
    root = tmp_path / "prompts"
    write_prompts(root)
    for target in (prompt_library, prompt_active_global):
        monkeypatch.setattr(target, "PROMPTS_ROOT", root)
    return root


@pytest_asyncio.fixture
async def cc(tmp_path, prompts_root):
    db_file = tmp_path / "cc.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await al_store.ensure_action_layer_schema(conn)
        await cs.ensure_change_control_schema(conn)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    al_store.configure_sessions(factory)
    yield CC(factory=factory, prompts=prompts_root)
    al_store.configure_sessions(None)
    await engine.dispose()


HUMAN = "web_operator:0123456789abcdef"


@pytest_asyncio.fixture
async def ccenv(env, prompts_root):
    """Настоящее приложение (ASGI) на временной БД: проект с холстом и таблицы контура."""
    from app.models import Project, ProjectStatus
    async with env.engine.begin() as conn:
        await cs.ensure_change_control_schema(conn)
    async with env.factory() as s:
        project = Project(topic="tema", slug=f"cc-{env.tmp_path.name[-6:]}",
                          status=ProjectStatus.new, meta=canvas_meta(), prompt_overrides={})
        s.add(project)
        await s.commit()
        env.pid = project.id
    env.prompts = prompts_root
    return env
