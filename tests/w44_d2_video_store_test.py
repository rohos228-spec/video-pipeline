from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.db as app_db
from app.models import Artifact, ArtifactKind, Base, Frame, FrameStatus, Project, ProjectStatus
from app.orchestrator.steps import generate_videos as gv
from app.project_db import get_project_engine, get_project_sessionmaker
from app.services import project_db_guard as guard
from app.settings import settings

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def stand(outside_tmp, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", outside_tmp / "data")
    eng = create_async_engine(f"sqlite+aiosqlite:///{(outside_tmp / 'master.db').as_posix()}")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    master = async_sessionmaker(eng, expire_on_commit=False)
    monkeypatch.setattr(app_db, "engine", eng)
    monkeypatch.setattr(app_db, "SessionLocal", master)
    guard.reset_state()
    async with master() as s:
        s.add(Project(id=1, slug="p1", topic="t", status=ProjectStatus.generating_videos, meta={}))
        await s.commit()
        project = await s.get(Project, 1)
    yield SimpleNamespace(master=master, project=project, root=outside_tmp)
    await eng.dispose()


async def _make_project_db(project: Project, *, frame_id: int = 1):
    peng = await get_project_engine(project.data_dir)
    async with peng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    pm = await get_project_sessionmaker(project.data_dir)
    async with pm() as s:
        s.add(Project(id=project.id, slug=project.slug, topic="t", status=ProjectStatus.generating_videos, meta={}))
        s.add(Frame(id=frame_id, project_id=project.id, number=1, voiceover_text="vo",
                    animation_prompt="slow pan", status=FrameStatus.animation_prompt_ready, attrs={}))
        await s.commit()
    return pm


def _job_kwargs(project: Project, out_dir: Path):
    scenes = project.data_dir / "scenes"
    scenes.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    return dict(project_id=project.id, frame_id=1, out_dir=out_dir, scenes_dir=scenes, outsee=None, gpt=None,
                session_clip_paths=[], clips_lock=asyncio.Lock())


@pytest.fixture
def fake_provider(monkeypatch):
    calls: list[dict] = []

    async def fake_gen(outsee, gpt, **kw):
        calls.append(kw)
        out = Path(kw["out_path"])
        out.write_bytes(b"x" * 100)
        return SimpleNamespace(file_path=out)

    async def fake_start(session, project, fr, scenes_dir):
        return scenes_dir / "start.png"

    monkeypatch.setattr(gv, "generate_video_with_retries", fake_gen)
    monkeypatch.setattr(gv, "_shot1_start_frame", fake_start)
    return calls


async def test_frame_store_is_project_db_when_it_has_the_project(stand):
    pm = await _make_project_db(stand.project)
    factory = await gv._frame_store(stand.project)
    assert factory is not stand.master
    assert factory is pm


async def test_frame_store_falls_back_to_master_without_project_db(stand):
    assert await gv._frame_store(stand.project) is stand.master


async def test_frame_store_refuses_paid_step_on_id_collision(stand):
    await _make_project_db(stand.project, frame_id=1)
    async with stand.master() as s:
        s.add(Project(id=2, slug="p2", topic="t", status=ProjectStatus.new, meta={}))
        await s.flush()
        s.add(Frame(id=1, project_id=2, number=1, voiceover_text="other", attrs={}))
        await s.commit()
    guard.reset_state()
    with pytest.raises(guard.IdCollisionError):
        await gv._frame_store(stand.project)


async def test_shot1_job_writes_clip_into_project_db_not_master(stand, fake_provider):
    pm = await _make_project_db(stand.project)
    kw = _job_kwargs(stand.project, stand.project.data_dir / "videos")
    ok = await gv._shot1_job(**kw, session_factory=pm)
    assert ok is True and len(fake_provider) == 1
    assert fake_provider[0]["prompt"] == "slow pan"
    async with pm() as s:
        assert (await s.scalar(select(func.count()).select_from(Artifact).where(Artifact.kind == ArtifactKind.scene_video))) == 1
        assert (await s.get(Frame, 1)).status is FrameStatus.video_generated
    async with stand.master() as s:
        assert (await s.scalar(select(func.count()).select_from(Artifact))) == 0


async def test_shot1_job_in_master_cannot_see_project_db_frame(stand, fake_provider):
    await _make_project_db(stand.project)
    kw = _job_kwargs(stand.project, stand.project.data_dir / "videos")
    assert await gv._shot1_job(**kw) is False
    assert fake_provider == []


async def test_failure_counter_goes_to_the_same_store(stand, fake_provider, monkeypatch):
    pm = await _make_project_db(stand.project)

    async def boom(outsee, gpt, **kw):
        raise RuntimeError("provider down")

    monkeypatch.setattr(gv, "generate_video_with_retries", boom)
    kw = _job_kwargs(stand.project, stand.project.data_dir / "videos")
    assert await gv._shot1_job(**kw, session_factory=pm) is False
    async with pm() as s:
        fr = await s.get(Frame, 1)
        assert (fr.attrs or {}).get(gv.VIDEO_FAIL_ATTR)
        assert gv.VIDEO_INFLIGHT_ATTR not in (fr.attrs or {})
