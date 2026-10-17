"""img_pr must read/write isolated project.db, not stale state.db prompts."""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, Frame, FrameStatus, Project, ProjectStatus
from app.orchestrator.steps.generate_image_prompts import (
    _frames_needing_image_prompt,
    _frames_with_image_prompt,
)
from app.project_db import close_all_project_engines, init_project_db
from app.services.xlsx_step_runners import _load_img_pr_context


def _fr(**kw):
    attrs = kw.pop("attrs", {})
    defaults = dict(
        number=1,
        uuid="a" * 24,
        voiceover_text="закадр",
        image_prompt="",
        attrs=attrs,
    )
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def test_img_pr_need_parents_only_skips_shot_child() -> None:
    parent = _fr(attrs={"camera_subdivide": {"role": "vo_parent"}})
    child = _fr(
        number=2,
        uuid="b" * 24,
        attrs={"camera_subdivide": {"role": "shot", "parent_uuid": "a" * 24}},
    )
    filled = _fr(number=3, uuid="c" * 24, image_prompt="already")
    need = _frames_needing_image_prompt([parent, child, filled])
    assert need == [parent]
    assert _frames_with_image_prompt([parent, child, filled]) == [filled]


@pytest.mark.asyncio
async def test_load_img_pr_context_uses_project_db_not_stale_master(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.settings import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    p = Project(
        id=901,
        slug="imgpr-isol",
        title="isol",
        topic="t",
        status=ProjectStatus.generating_image_prompts,
    )
    p.data_dir.mkdir(parents=True, exist_ok=True)
    await init_project_db(p.data_dir, project=p)

    from app.project_db import get_project_sessionmaker

    sm = await get_project_sessionmaker(p.data_dir)
    async with sm() as session:
        session.add(
            Frame(
                project_id=901,
                number=1,
                uuid="parent-uuid-000000000001",
                voiceover_text="VO родителя",
                image_prompt=None,
                status=FrameStatus.planned,
                attrs={"camera_subdivide": {"role": "vo_parent"}},
            )
        )
        await session.commit()

    master_engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with master_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    master_factory = async_sessionmaker(master_engine, expire_on_commit=False)
    async with master_factory() as ms:
        ms.add(
            Project(
                id=901,
                slug="imgpr-isol",
                title="isol",
                topic="t",
                status=ProjectStatus.generating_image_prompts,
            )
        )
        ms.add(
            Frame(
                project_id=901,
                number=1,
                uuid="parent-uuid-000000000001",
                voiceover_text="VO родителя",
                image_prompt="STALE PROMPT FROM STATE.DB",
                status=FrameStatus.image_prompt_ready,
                attrs={"camera_subdivide": {"role": "vo_parent"}},
            )
        )
        await ms.commit()

    monkeypatch.setattr("app.db.SessionLocal", master_factory)

    selected, _cards, _plan, all_frames = await _load_img_pr_context(p)
    assert len(all_frames) == 1
    assert not (all_frames[0].image_prompt or "").strip()
    assert len(selected) == 1
    assert selected[0].uuid == "parent-uuid-000000000001"

    await master_engine.dispose()
    await close_all_project_engines()


@pytest.mark.asyncio
async def test_apply_img_pr_ops_writes_image_prompt_to_project_db(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.settings import settings
    from app.services.xlsx_step_runners import _apply_img_pr_ops_now

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    p = Project(
        id=902,
        slug="imgpr-flush",
        title="flush",
        topic="t",
        status=ProjectStatus.generating_image_prompts,
    )
    p.data_dir.mkdir(parents=True, exist_ok=True)
    await init_project_db(p.data_dir, project=p)
    uid = "aaaaaaaaaaaaaaaaaaaaaaa2"
    from app.project_db import get_project_sessionmaker

    sm = await get_project_sessionmaker(p.data_dir)
    async with sm() as session:
        session.add(
            Frame(
                project_id=902,
                number=1,
                uuid=uid,
                voiceover_text="VO",
                image_prompt=None,
                status=FrameStatus.planned,
                attrs={"camera_subdivide": {"role": "vo_parent"}},
            )
        )
        await session.commit()

    await _apply_img_pr_ops_now(
        p,
        [{
            "frame_uuid": uid,
            "fields": {
                "промт_картинки": (
                    "Full-bleed frame: archive room, noir still of folders. "
                    "STYLE LOCK: Archival Noir Watercolour. "
                    "Negative: text, watermark, extra limbs. "
                )
                + ("detail " * 40)
            },
        }],
        export_xlsx=False,
        label="test",
    )
    async with sm() as session:
        fr = (
            await session.execute(
                select(Frame).where(Frame.project_id == 902, Frame.uuid == uid)
            )
        ).scalar_one()
        assert (fr.image_prompt or "").startswith("Full-bleed")

    await close_all_project_engines()


_LONG_PROMPT = (
    "Full-bleed frame: archive room, noir still of folders. "
    "STYLE LOCK: Archival Noir Watercolour. "
    "Negative: text, watermark, extra limbs. "
) + ("detail " * 40)


@pytest.mark.asyncio
async def test_apply_img_pr_skips_unknown_uuid_and_writes_known(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services.xlsx_step_runners import _apply_img_pr_ops_now
    from app.settings import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    p = Project(
        id=903,
        slug="imgpr-skip-unknown",
        title="skip",
        topic="t",
        status=ProjectStatus.generating_image_prompts,
    )
    p.data_dir.mkdir(parents=True, exist_ok=True)
    await init_project_db(p.data_dir, project=p)
    uid = "bbbbbbbbbbbbbbbbbbbbbbb3"
    ghost = "deadbeefdeadbeefdeadbeef"
    from app.project_db import get_project_sessionmaker

    sm = await get_project_sessionmaker(p.data_dir)
    async with sm() as session:
        session.add(
            Frame(
                project_id=903,
                number=1,
                uuid=uid,
                voiceover_text="VO",
                image_prompt=None,
                status=FrameStatus.planned,
                attrs={"camera_subdivide": {"role": "vo_parent"}},
            )
        )
        await session.commit()

    applied = await _apply_img_pr_ops_now(
        p,
        [
            {"frame_uuid": ghost, "fields": {"промт_картинки": _LONG_PROMPT}},
            {"frame_uuid": uid, "fields": {"промт_картинки": _LONG_PROMPT}},
        ],
        export_xlsx=False,
        label="test-skip",
    )
    assert [op["frame_uuid"] for op in applied] == [uid]
    async with sm() as session:
        fr = (
            await session.execute(
                select(Frame).where(Frame.project_id == 903, Frame.uuid == uid)
            )
        ).scalar_one()
        assert (fr.image_prompt or "").startswith("Full-bleed")

    await close_all_project_engines()


def test_img_pr_live_streams_floor_eight(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.xlsx_step_runners import img_pr_live_streams

    monkeypatch.setattr(
        "app.services.check_streams.get_check_streams", lambda _p: 2
    )
    assert img_pr_live_streams(None) == 8
    monkeypatch.setattr(
        "app.services.check_streams.get_check_streams", lambda _p: 10
    )
    assert img_pr_live_streams(None) == 10
