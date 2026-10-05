"""Общие фикстуры тестов слоя действий: временная БД, приложение, заглушки."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, Frame, Project, ProjectStatus
from app.services import db_v2, kill_switch
from app.services.action_layer import executor, store
from app.services.action_layer.spec import ActionRegistry, ActionSpec, CostEstimate
from app.settings import settings
from app.web.api import create_app
from app.web.deps import get_session


@dataclass
class Env:
    app: Any
    engine: Any
    factory: Any
    client: AsyncClient
    tmp_path: Any
    calls: list = field(default_factory=list)

    async def add_project(self, frames: int = 2) -> int:
        async with self.factory() as session:
            p = Project(topic="al", slug=f"al-{self.tmp_path.name[-6:]}", status=ProjectStatus.new)
            session.add(p)
            await session.flush()
            for i in range(1, frames + 1):
                session.add(Frame(project_id=p.id, number=i, voiceover_text=f"реплика {i}",
                                  image_prompt=f"img {i}", animation_prompt=f"vid {i}", attrs={}))
            await session.commit()
            return p.id

    async def frame_ids(self, project_id: int) -> dict[int, int]:
        from sqlalchemy import select
        async with self.factory() as session:
            rows = (await session.execute(select(Frame.number, Frame.id).where(
                Frame.project_id == project_id))).all()
            return {n: i for n, i in rows}


@pytest_asyncio.fixture
async def env(tmp_path, monkeypatch):
    db_file = tmp_path / "state.db"
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "sqlite_path", db_file)
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await db_v2.migrate_db_v2_schema(conn)
        await store.ensure_action_layer_schema(conn)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    await kill_switch.ensure_table(factory)
    kill_switch.reset_cache()
    store.configure_sessions(factory)

    app = create_app()

    async def _gen():
        async with factory() as s:
            yield s

    app.dependency_overrides[get_session] = _gen
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield Env(app=app, engine=engine, factory=factory, client=client, tmp_path=tmp_path)
    for plan_id in executor.live_plan_ids():
        executor._TASKS[plan_id].cancel()
    store.configure_sessions(None)
    kill_switch.reset_cache()
    await engine.dispose()


def make_spec(action_id: str, executor_fn, *, risk: str = "read", spends: bool = False,
              destructive: bool = False, cost: tuple[float, float] | None = None, **kw) -> ActionSpec:
    estimator = None
    if spends:
        estimator = (lambda p: CostEstimate(cost[0], cost[1], "stub")) if cost else (lambda p: None)
    return ActionSpec(
        id=action_id, title_ru="Тест " + action_id, description_ru="Заглушка " + action_id,
        params_schema=kw.pop("schema", {"type": "object", "properties": {
            "n": {"type": "integer", "minimum": 0}}, "additionalProperties": False}),
        executor=executor_fn, risk=risk, spends_money=spends, destructive=destructive,
        estimator=estimator, **kw)


def fresh_registry(*specs: ActionSpec) -> ActionRegistry:
    reg = ActionRegistry()
    for spec in specs:
        reg.register(spec)
    return reg
