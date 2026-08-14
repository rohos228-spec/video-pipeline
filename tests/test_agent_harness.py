"""Harness N/N coverage at img_pr / anim_pr / excel_gpt — before *_ready."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from openpyxl import Workbook
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, Frame, FrameStatus, Project, ProjectStatus
from app.services.agent_harness import harness_gate_or_raise, verify_project_disk
from app.services.node_write_contract import skip_frame_coverage
from app.settings import settings


def _write_plan_xlsx(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "план"
    wb.save(path)
    wb.close()


@pytest.fixture
async def harness_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_file = tmp_path / "harness.db"
    monkeypatch.setattr(settings, "sqlite_path", db_file)
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session, tmp_path
    await engine.dispose()


def test_skip_coverage_character_registry_not_regressed() -> None:
    """Task 2: character_registry never requires per-frame N/N coverage."""
    assert skip_frame_coverage(
        scene_grammar=False,
        character_registry=True,
        ops_list=[],
        chars_list=[{"id": "c01"}],
        scenes_list=[],
    )
    assert skip_frame_coverage(
        scene_grammar=False,
        character_registry=True,
        ops_list=[{"frame_uuid": "a", "fields": {"персонажи": "c01"}}],
        chars_list=[],
        scenes_list=[],
    )


@pytest.mark.asyncio
async def test_img_pr_gate_raises_if_vo_frame_lacks_image_prompt(harness_db) -> None:
    session, tmp_path = harness_db
    _write_plan_xlsx(tmp_path / "project.xlsx")
    session.add(
        Project(id=501, slug="img-pr-gap", topic="t", status=ProjectStatus.generating_image_prompts)
    )
    session.add(
        Frame(
            project_id=501,
            number=1,
            voiceover_text="кадр один закадр",
            image_prompt="wide shot kitchen",
        )
    )
    session.add(
        Frame(
            project_id=501,
            number=2,
            voiceover_text="кадр два без промта",
            image_prompt=None,
        )
    )
    await session.commit()

    p = SimpleNamespace(
        id=501,
        data_dir=tmp_path,
        status="generating_image_prompts",
        meta={},
    )
    with pytest.raises(RuntimeError, match=r"img_pr.*harness gate failed"):
        await harness_gate_or_raise(session, p, step="img_pr")


@pytest.mark.asyncio
async def test_img_pr_gate_does_not_require_png(harness_db) -> None:
    session, tmp_path = harness_db
    _write_plan_xlsx(tmp_path / "project.xlsx")
    session.add(
        Project(id=502, slug="img-pr-ok", topic="t", status=ProjectStatus.generating_image_prompts)
    )
    session.add(
        Frame(
            project_id=502,
            number=1,
            voiceover_text="vo one",
            image_prompt="a usable image prompt here",
        )
    )
    session.add(
        Frame(
            project_id=502,
            number=2,
            voiceover_text="",  # SET-child: no VO → no image_prompt required
            image_prompt=None,
        )
    )
    await session.commit()
    assert not (tmp_path / "scenes").exists()

    p = SimpleNamespace(
        id=502,
        data_dir=tmp_path,
        status="generating_image_prompts",
        meta={},
    )
    rep = await harness_gate_or_raise(session, p, step="img_pr")
    by_name = {c.name: c for c in rep.checks}
    assert by_name["scenes_png"].ok is True
    assert "img_pr" not in (rep.repair_steps or [])


@pytest.mark.asyncio
async def test_anim_pr_gate_does_not_require_png(harness_db) -> None:
    """anim_pr пишет текст из image_prompt; PNG появляются на шаге img."""
    session, tmp_path = harness_db
    _write_plan_xlsx(tmp_path / "project.xlsx")
    session.add(
        Project(
            id=506,
            slug="anim-pr-no-png",
            topic="t",
            status=ProjectStatus.generating_animation_prompts,
        )
    )
    session.add(
        Frame(
            project_id=506,
            number=1,
            voiceover_text="vo one",
            image_prompt="a usable image prompt here",
            animation_prompt="slow push in, hold 2s",
        )
    )
    await session.commit()
    assert not (tmp_path / "scenes").exists()

    p = SimpleNamespace(
        id=506,
        data_dir=tmp_path,
        status="generating_animation_prompts",
        meta={},
    )
    rep = await harness_gate_or_raise(session, p, step="anim_pr")
    by_name = {c.name: c for c in rep.checks}
    assert by_name["scenes_png"].ok is True
    assert "img" not in (rep.repair_steps or [])
    assert "anim_pr" not in (rep.repair_steps or [])


def test_verify_generating_animation_prompts_does_not_require_png(tmp_path: Path) -> None:
    _write_plan_xlsx(tmp_path / "project.xlsx")
    report = verify_project_disk(507, tmp_path, "generating_animation_prompts")
    by_name = {c.name: c for c in report.checks}
    assert by_name["scenes_png"].ok is True
    assert "img" not in report.repair_steps


def test_verify_image_prompts_ready_does_not_require_png(tmp_path: Path) -> None:
    _write_plan_xlsx(tmp_path / "project.xlsx")
    report = verify_project_disk(503, tmp_path, "image_prompts_ready")
    by_name = {c.name: c for c in report.checks}
    assert by_name["scenes_png"].ok is True
    assert "img" not in report.repair_steps


@pytest.mark.asyncio
async def test_anim_pr_gate_raises_if_usable_image_prompt_lacks_anim(harness_db) -> None:
    session, tmp_path = harness_db
    _write_plan_xlsx(tmp_path / "project.xlsx")
    session.add(
        Project(
            id=504,
            slug="anim-pr-gap",
            topic="t",
            status=ProjectStatus.generating_animation_prompts,
        )
    )
    session.add(
        Frame(
            project_id=504,
            number=1,
            voiceover_text="vo",
            image_prompt="usable camera on the table",
            animation_prompt=None,
        )
    )
    await session.commit()
    scenes = tmp_path / "scenes"
    scenes.mkdir()
    (scenes / "frame_001.png").write_bytes(b"x")

    p = SimpleNamespace(
        id=504,
        data_dir=tmp_path,
        status="generating_animation_prompts",
        meta={},
    )
    with pytest.raises(RuntimeError, match=r"anim_pr.*harness gate failed"):
        await harness_gate_or_raise(session, p, step="anim_pr")


@pytest.mark.asyncio
async def test_excel_gpt_gate_does_not_require_image_prompt(harness_db) -> None:
    session, tmp_path = harness_db
    _write_plan_xlsx(tmp_path / "project.xlsx")
    session.add(
        Project(id=505, slug="excel-gpt-ok", topic="t", status=ProjectStatus.enriching_1)
    )
    session.add(
        Frame(
            project_id=505,
            number=1,
            voiceover_text="vo still waiting for img_pr",
            image_prompt=None,
        )
    )
    await session.commit()

    p = SimpleNamespace(id=505, data_dir=tmp_path, status="enriching_1", meta={})
    rep = await harness_gate_or_raise(session, p, step="excel_gpt")
    assert any(c.name == "excel_gpt_gate" for c in rep.checks)
    assert all(
        c.ok
        for c in rep.checks
        if c.name not in {"project_log_clean", "node_runs_failed"}
    )


@pytest.mark.asyncio
async def test_img_pr_finish_does_not_set_ready_when_gate_fails(harness_db) -> None:
    """Gate must run before image_prompts_ready — otherwise fail leaves status ready."""
    session, tmp_path = harness_db
    p = Project(
        slug="img-pr-before-ready",
        topic="t",
        status=ProjectStatus.generating_image_prompts,
    )
    session.add(p)
    await session.flush()
    p.data_dir.mkdir(parents=True, exist_ok=True)
    fr = Frame(
        project_id=p.id,
        number=1,
        voiceover_text="vo filled",
        image_prompt="a complete image prompt",
        status=FrameStatus.planned,
    )
    session.add(fr)
    await session.commit()
    await session.refresh(p)
    await session.refresh(fr)

    from app.orchestrator.steps.generate_image_prompts import _finish_success

    with pytest.raises(RuntimeError, match="harness gate failed"):
        await _finish_success(session, p, [fr])

    assert p.status is ProjectStatus.generating_image_prompts
    assert p.status is not ProjectStatus.image_prompts_ready
