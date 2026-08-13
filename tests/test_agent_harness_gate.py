"""T08: harness coverage-гейты на img_pr/anim_pr ready (13.08: нода done с дырами).

- _prompt_coverage_checks: N/N по image_prompt (на image_prompts_ready+)
  и animation_prompt (на animation_prompts_ready+) через живую сессию.
- _finish_success img_pr: падение гейта откатывает image_prompts_ready.
- anim_pr early-exit проходит гейт и откатывает статус при провале.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, Frame, FrameStatus, Project, ProjectStatus


@pytest_asyncio.fixture
async def session(tmp_path: Path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 't.db'}"
    engine = create_async_engine(db_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    async with SessionLocal() as s:
        yield s
    await engine.dispose()


async def _mkproject(session, slug: str = "gate8") -> Project:
    p = Project(
        slug=slug,
        topic="t",
        hero_mode="full_auto",
        status=ProjectStatus.generating_image_prompts,
    )
    session.add(p)
    await session.flush()
    return p


async def _mkframe(
    session,
    project: Project,
    n: int,
    *,
    vo: str = "vo",
    img: str = "",
    anim: str = "",
) -> Frame:
    fr = Frame(
        project_id=project.id,
        number=n,
        uuid=f"{n:024x}",
        voiceover_text=vo,
        image_prompt=img,
        animation_prompt=anim,
        status=FrameStatus.planned,
    )
    session.add(fr)
    await session.flush()
    return fr


def _names(checks):
    return {c.name: c for c in checks}


@pytest.mark.asyncio
async def test_coverage_checks_skip_early_statuses(session) -> None:
    from app.services.agent_harness import _prompt_coverage_checks

    p = await _mkproject(session)
    await _mkframe(session, p, 1)
    p.status = ProjectStatus.frames_ready
    checks = await _prompt_coverage_checks(session, p, "frames_ready")
    assert checks == []


@pytest.mark.asyncio
async def test_img_pr_coverage_flags_missing_prompt(session) -> None:
    from app.services.agent_harness import _prompt_coverage_checks

    p = await _mkproject(session)
    await _mkframe(session, p, 1, img="prompt one")
    await _mkframe(session, p, 2)  # VO есть, image_prompt пуст
    checks = await _prompt_coverage_checks(session, p, "image_prompts_ready")
    got = _names(checks)
    assert got["img_pr_db_coverage"].ok is False
    assert "2" in got["img_pr_db_coverage"].detail
    # anim-чек на этом статусе ещё не требуется
    assert "anim_pr_db_coverage" not in got


@pytest.mark.asyncio
async def test_img_pr_coverage_ok_when_full(session) -> None:
    from app.services.agent_harness import _prompt_coverage_checks

    p = await _mkproject(session)
    await _mkframe(session, p, 1, img="p1")
    await _mkframe(session, p, 2, img="p2")
    checks = await _prompt_coverage_checks(session, p, "image_prompts_ready")
    assert _names(checks)["img_pr_db_coverage"].ok is True


@pytest.mark.asyncio
async def test_anim_pr_coverage_flags_missing(session) -> None:
    from app.services.agent_harness import _prompt_coverage_checks

    p = await _mkproject(session)
    await _mkframe(session, p, 1, img="p1", anim="a" * 60)
    await _mkframe(session, p, 2, img="p2")  # нет animation_prompt
    checks = await _prompt_coverage_checks(session, p, "animation_prompts_ready")
    got = _names(checks)
    assert got["anim_pr_db_coverage"].ok is False
    assert "2" in got["anim_pr_db_coverage"].detail


@pytest.mark.asyncio
async def test_anim_pr_coverage_ignores_placeholder_image_prompt(session) -> None:
    from app.services.agent_harness import _prompt_coverage_checks

    p = await _mkproject(session)
    await _mkframe(session, p, 1, img="p1", anim="a" * 60)
    await _mkframe(session, p, 2, img="нет исходных данных")
    checks = await _prompt_coverage_checks(session, p, "animation_prompts_ready")
    got = _names(checks)
    assert got["anim_pr_db_coverage"].ok is True


@pytest.mark.asyncio
async def test_img_pr_finish_reverts_ready_on_gate_failure(
    session, monkeypatch
) -> None:
    from app.orchestrator.steps import generate_image_prompts as gip

    async def _boom(*a, **kw):
        raise RuntimeError("harness gate failed: ['x']")

    monkeypatch.setattr(
        "app.services.agent_harness.harness_gate_or_raise", _boom
    )
    p = await _mkproject(session)
    fr = await _mkframe(session, p, 1, img="p1")

    with pytest.raises(RuntimeError, match="harness gate failed"):
        await gip._finish_success(session, p, [fr])

    assert p.status is ProjectStatus.generating_image_prompts
    # и в БД тоже не ready
    row = await session.get(Project, p.id)
    assert row.status is ProjectStatus.generating_image_prompts


def test_anim_pr_early_exit_passes_gate() -> None:
    """Пин: early-exit anim_pr («nothing to do») тоже идёт через гейт."""
    import inspect

    from app.orchestrator.steps import make_animation_prompts as map_

    src = inspect.getsource(map_)
    early = src.index("nothing to do")
    gate = src.index("harness_gate_or_raise", 0)
    # гейт объявлен/вызван, и ранний выход стоит после первого вызова в файле
    assert gate != -1
    # в early-exit ветке (до «nothing to do» лог записи stats) есть commit+gate
    block_start = src.rindex("if not pending and not pending_shot2:", 0, early)
    block_end = src.index("return stats", early)
    block = src[block_start:block_end]
    assert "harness_gate_or_raise" in block
    assert "await session.commit()" in block


def test_anim_pr_main_exit_reverts_status_on_gate_failure() -> None:
    import inspect

    from app.orchestrator.steps import make_animation_prompts as map_

    src = inspect.getsource(map_)
    gate = src.index('step="anim_pr"')
    after = src[gate : gate + 900]
    assert "generating_animation_prompts" in after
