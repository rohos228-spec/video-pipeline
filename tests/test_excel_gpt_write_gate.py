"""Регрессии инцидентов 13.08 (проект #14): запись excel_gpt в БД.

T01: coverage-гейт ДО commit — частичный apply не фиксируется в БД,
     xlsx-зеркало экспортируется только после успешного commit.
T03: _APPLY_OPS_HINT не предлагает модели чужие prompt-поля.
T04: checkFix пишет с правами проверяемой (upstream) ноды.
T06: entity-ноды (scene_grammar/character_registry) клипают VO/meaning.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, Frame, FrameStatus, Project, ProjectStatus
from app.services import db_apply
from app.services.node_write_contract import precheck_coverage


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


async def _mkproject(session, slug: str = "gate") -> Project:
    p = Project(
        slug=slug,
        topic="t",
        hero_mode="full_auto",
        status=ProjectStatus.new,
    )
    session.add(p)
    await session.flush()
    return p


async def _mkframe(session, project: Project, n: int) -> Frame:
    fr = Frame(
        project_id=project.id,
        number=n,
        voiceover_text=f"vo {n}",
        status=FrameStatus.planned,
        uuid=f"{n:024x}",
    )
    session.add(fr)
    await session.flush()
    return fr


# ── T01: coverage gate до записи ─────────────────────────────────────


def test_precheck_coverage_partial_not_ok() -> None:
    ops = [{"frame_uuid": "a", "fields": {"смысл": "x"}}]
    cov = precheck_coverage(ops, ["a", "b"])
    assert cov.ok is False
    assert cov.missing == ["b"]


def test_precheck_coverage_repairs_near_miss() -> None:
    known = ["abc4bdef0123456789abcdef", "fff4bdef0123456789abcdef"]
    typo = "abc4ddef0123456789abcdef"  # 4b → 4d
    ops = [
        {"frame_uuid": typo, "fields": {"смысл": "x"}},
        {"frame_uuid": known[1], "fields": {"смысл": "y"}},
    ]
    cov = precheck_coverage(ops, known)
    assert cov.ok is True
    assert ops[0]["frame_uuid"] == known[0]


def test_enrich_checks_coverage_before_apply_and_exports_after_commit() -> None:
    """Пин порядка: gate → apply → commit → export (13.08: было наоборот)."""
    from app.orchestrator.steps import enrich_xlsx

    src = inspect.getsource(enrich_xlsx)
    i_gate = src.index("precheck_coverage(")
    i_apply = src.index("await db_apply.apply_ops(", i_gate)
    i_commit = src.index("await session.commit()", i_apply)
    i_export = src.index("export_project_xlsx_snapshot(", i_commit)
    assert i_gate < i_apply < i_commit < i_export
    # apply вызывается без экспорта — файл пишется только после commit
    call_span = src[i_apply : src.index(")", src.index("node_kind=", i_apply))]
    assert "export_xlsx=False" in call_span


@pytest.mark.asyncio
async def test_apply_ops_without_export_does_not_touch_xlsx(session) -> None:
    p = await _mkproject(session)
    fr = await _mkframe(session, p, 1)
    # project.xlsx нет вообще — apply без экспорта обязан пройти
    applied = await db_apply.apply_ops(
        session,
        p,
        [{"frame_uuid": fr.uuid, "fields": {"смысл": "новый"}}],
        export_xlsx=False,
    )
    await session.commit()
    assert applied["updated"] == 1
    assert applied["exported"] is None
    assert not (p.data_dir / "project.xlsx").exists()


@pytest.mark.asyncio
async def test_export_snapshot_requires_xlsx(session) -> None:
    p = await _mkproject(session)
    await _mkframe(session, p, 1)
    with pytest.raises(db_apply.ApplyOpsError):
        await db_apply.export_project_xlsx_snapshot(session, p)


# ── T03: hint не учит писать чужие промты ────────────────────────────


def test_apply_ops_hint_bans_prompt_fields() -> None:
    from app.orchestrator.steps.enrich_xlsx import _APPLY_OPS_HINT

    allowed_line = next(
        line for line in _APPLY_OPS_HINT.splitlines() if "Поля по-человечески" in line
    )
    assert "промт_картинки" not in allowed_line
    assert "промт_видео" not in allowed_line
    assert "ЗАПРЕЩЕНО" in _APPLY_OPS_HINT
    assert "отдельные ноды" in _APPLY_OPS_HINT


# ── T04: checkFix пишет с правами upstream-ноды ──────────────────────


def test_check_fix_node_kind_follows_upstream(monkeypatch) -> None:
    from app.orchestrator.steps import enrich_xlsx

    monkeypatch.setattr(
        "app.services.gpt_operator.upstream_node_type_for_check",
        lambda project, key: "image_prompts",
    )
    assert enrich_xlsx._node_kind_for_check_fix(object(), "n1") == "img_pr"

    monkeypatch.setattr(
        "app.services.gpt_operator.upstream_node_type_for_check",
        lambda project, key: "animation_prompts",
    )
    assert enrich_xlsx._node_kind_for_check_fix(object(), "n1") == "anim_pr"

    monkeypatch.setattr(
        "app.services.gpt_operator.upstream_node_type_for_check",
        lambda project, key: "excel_gpt",
    )
    assert enrich_xlsx._node_kind_for_check_fix(object(), "n1") == (
        "excel_gpt_no_prompts"
    )


def test_check_fix_node_kind_fail_closed_on_error(monkeypatch) -> None:
    from app.orchestrator.steps import enrich_xlsx

    def _boom(project, key):
        raise RuntimeError("no graph")

    monkeypatch.setattr(
        "app.services.gpt_operator.upstream_node_type_for_check", _boom
    )
    assert enrich_xlsx._node_kind_for_check_fix(object(), "n1") == (
        "excel_gpt_no_prompts"
    )


# ── T06: entity-ноды клипают длинный текст ───────────────────────────


def test_clip_text_limits() -> None:
    from app.services.db_frames_context import (
        ATTR_TEXT_MAX,
        ENTITY_VO_MAX,
        clip_text,
    )

    assert clip_text("короткий", 100) == "короткий"
    long = "x" * (ENTITY_VO_MAX + 50)
    assert len(clip_text(long, ENTITY_VO_MAX)) == ENTITY_VO_MAX
    assert clip_text("y" * (ATTR_TEXT_MAX + 5), ATTR_TEXT_MAX).endswith("…")


def test_entity_branch_uses_clip() -> None:
    from app.orchestrator.steps import enrich_xlsx

    src = inspect.getsource(enrich_xlsx)
    assert "ENTITY_VO_MAX" in src and "clip_text(" in src
