"""Физические delete кадров/ячеек/связей заменены пометкой. Строки остаются."""

from __future__ import annotations

import uuid

import pytest
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from fastapi import HTTPException

from app.models import (
    Artifact,
    ArtifactKind,
    Base,
    Entity,
    Frame,
    FrameEdge,
    FrameText,
    Project,
    PromptVersion,
    SceneDesignCell,
)
from app.services import db_v2
from app.services.frame_tombstones import mark_frame_deleted
from app.services.montage_board_frames import upsert_frame_voiceover
from app.services.reset_step import _wipe_split
from app.services.scene_design import cells as sd_cells
from app.services.scene_design.camera_expand import (
    already_subdivided,
    collapse_shot_children,
)
from app.services.scene_design.truncate_vo import truncate_vo_to_ratio
from app.services.vo_shot_expand import rebuild_vo_cells_from_shots
from app.web.routers.db_browser import (
    delete_edge,
    delete_entity,
    delete_text,
    restore_edge,
    restore_entity,
    restore_text,
)


@pytest.fixture
def warn_log():
    lines: list[str] = []
    sink = logger.add(lambda message: lines.append(str(message)), level="WARNING")
    try:
        yield lines
    finally:
        logger.remove(sink)


@pytest.fixture
async def session() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await db_v2.migrate_db_v2_schema(conn)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


async def _project(session: AsyncSession, slug: str) -> Project:
    project = Project(slug=slug, topic="t", meta={})
    session.add(project)
    await session.flush()
    return project


@pytest.mark.asyncio
async def test_wipe_split_marks_frames_and_keeps_rows(session: AsyncSession, warn_log) -> None:
    """Было: ``await session.delete(fr)`` в ``_wipe_split`` (reset_step.py)."""
    project = await _project(session, "wipe-split")
    frame = Frame(
        project_id=project.id,
        number=1,
        uuid="frame-keep",
        voiceover_text="Текст кадра",
        sort_key=1.0,
        attrs={},
    )
    session.add(frame)
    await session.flush()
    session.add(
        FrameText(
            project_id=project.id,
            frame_id=frame.id,
            kind="voiceover",
            text="Текст кадра",
        )
    )
    session.add(
        PromptVersion(
            project_id=project.id,
            frame_id=frame.id,
            kind="img",
            version=1,
            text="промт кадра",
        )
    )
    session.add(
        Artifact(
            project_id=project.id,
            frame_id=frame.id,
            kind=ArtifactKind.scene_image,
            uuid=str(uuid.uuid4()),
            path="/tmp/frame-keep.png",
        )
    )
    await session.commit()
    frame_id = int(frame.id)

    report = await _wipe_split(session, project)
    await session.commit()

    kept = await session.get(Frame, frame_id)
    assert kept is not None
    assert kept.attrs.get("удалён") is True
    assert kept.attrs.get("активен") is False
    assert report["frames_deleted"] == 0
    assert report["frames_marked"] == 1
    texts = list(
        (await session.execute(select(FrameText).where(FrameText.frame_id == frame_id))).scalars()
    )
    prompts = list(
        (
            await session.execute(
                select(PromptVersion).where(PromptVersion.frame_id == frame_id)
            )
        ).scalars()
    )
    arts = list(
        (await session.execute(select(Artifact).where(Artifact.frame_id == frame_id))).scalars()
    )
    assert texts and texts[0].text == "Текст кадра"
    assert prompts and prompts[0].text == "промт кадра"
    assert arts and arts[0].path == "/tmp/frame-keep.png"
    assert any("помечен" in line and "строка сохранена" in line for line in warn_log)


def _frame(
    project: Project,
    number: int,
    uuid_value: str,
    text: str,
    *,
    sort_key: float,
    attrs: dict | None = None,
) -> Frame:
    return Frame(
        project_id=project.id,
        number=number,
        uuid=uuid_value,
        voiceover_text=text,
        sort_key=sort_key,
        attrs=attrs or {},
    )


@pytest.mark.asyncio
async def test_truncate_vo_marks_tail_and_keeps_related_rows(
    session: AsyncSession, warn_log
) -> None:
    """Было: delete(FrameText/PromptVersion/Artifact/FrameEdge/Frame) в truncate_vo."""
    project = await _project(session, "truncate-vo")
    frames = [
        _frame(project, i, f"u{i}", word, sort_key=float(i))
        for i, word in enumerate(("раз", "два", "три", "четыре"), start=1)
    ]
    session.add_all(frames)
    await session.flush()
    tail = frames[-1]
    session.add(
        FrameText(
            project_id=project.id,
            frame_id=tail.id,
            kind="voiceover",
            text="четыре",
        )
    )
    session.add(
        PromptVersion(
            project_id=project.id,
            frame_id=tail.id,
            kind="img",
            version=1,
            text="промт хвоста",
        )
    )
    session.add(
        Artifact(
            project_id=project.id,
            frame_id=tail.id,
            kind=ArtifactKind.scene_image,
            uuid=str(uuid.uuid4()),
            path="/tmp/tail.png",
        )
    )
    session.add(
        FrameEdge(
            project_id=project.id,
            from_frame_id=frames[2].id,
            to_frame_id=tail.id,
            type="next",
        )
    )
    await session.commit()
    tail_id = int(tail.id)

    report = await truncate_vo_to_ratio(session, project, keep_ratio=0.5)
    await session.commit()

    rows = list(
        (await session.execute(select(Frame).where(Frame.project_id == project.id))).scalars()
    )
    assert {fr.uuid for fr in rows} == {"u1", "u2", "u3", "u4"}
    marked = [fr for fr in rows if (fr.attrs or {}).get("удалён")]
    assert {fr.uuid for fr in marked} == {"u3", "u4"}
    assert all((fr.attrs or {}).get("активен") is False for fr in marked)
    assert report["deleted_frames"] == 0
    assert report["marked_frames"] == 2
    assert project.script_text == "раз\nдва"
    assert await session.get(FrameText, (
        await session.execute(select(FrameText.id).where(FrameText.frame_id == tail_id))
    ).scalar_one())
    assert (
        await session.execute(select(PromptVersion).where(PromptVersion.frame_id == tail_id))
    ).scalars().first() is not None
    assert (
        await session.execute(select(Artifact).where(Artifact.frame_id == tail_id))
    ).scalars().first() is not None
    edge = (
        await session.execute(select(FrameEdge).where(FrameEdge.project_id == project.id))
    ).scalars().first()
    assert edge is not None
    assert any("truncate_vo" in line for line in warn_log)


@pytest.mark.asyncio
async def test_collapse_shot_children_marks_child_and_keeps_rows(
    session: AsyncSession, warn_log
) -> None:
    """Было: delete(Frame/FrameText/FrameEdge) по child_ids в collapse_shot_children."""
    project = await _project(session, "collapse-shots")
    parent = _frame(
        project,
        1,
        "parent-1",
        "альфа",
        sort_key=1.0,
        attrs={"camera_subdivide": {"parent_uuid": "parent-1", "shot_index": 1}},
    )
    child = _frame(
        project,
        2,
        "child-1",
        "бета",
        sort_key=2.0,
        attrs={"camera_subdivide": {"parent_uuid": "parent-1", "shot_index": 2}},
    )
    session.add_all([parent, child])
    await session.flush()
    session.add(
        FrameText(
            project_id=project.id,
            frame_id=child.id,
            kind="voiceover",
            text="бета",
        )
    )
    session.add(
        PromptVersion(
            project_id=project.id,
            frame_id=child.id,
            kind="img",
            version=1,
            text="промт ребёнка",
        )
    )
    session.add(
        Artifact(
            project_id=project.id,
            frame_id=child.id,
            kind=ArtifactKind.scene_video,
            uuid=str(uuid.uuid4()),
            path="/tmp/child.mp4",
        )
    )
    session.add(
        FrameEdge(
            project_id=project.id,
            from_frame_id=parent.id,
            to_frame_id=child.id,
            type="next",
        )
    )
    await session.commit()
    child_id = int(child.id)

    report = await collapse_shot_children(session, project)
    await session.commit()

    live_parent = (
        await session.execute(select(Frame).where(Frame.uuid == "parent-1"))
    ).scalar_one()
    kept_child = await session.get(Frame, child_id)
    assert kept_child is not None
    assert kept_child.attrs.get("удалён") is True
    assert kept_child.attrs.get("активен") is False
    assert live_parent.voiceover_text == "альфа бета"
    assert report["deleted_shots"] == 0
    assert report["marked_shots"] == 1
    assert (
        await session.execute(select(FrameText).where(FrameText.frame_id == child_id))
    ).scalars().first() is not None
    assert (
        await session.execute(
            select(PromptVersion).where(PromptVersion.frame_id == child_id)
        )
    ).scalars().first() is not None
    assert (
        await session.execute(select(Artifact).where(Artifact.frame_id == child_id))
    ).scalars().first() is not None
    assert (
        await session.execute(select(FrameEdge).where(FrameEdge.to_frame_id == child_id))
    ).scalars().first() is not None
    ordered = list(
        (await session.execute(select(Frame).where(Frame.project_id == project.id))).scalars()
    )
    assert already_subdivided(ordered) is False
    again = await collapse_shot_children(session, project)
    assert again["marked_shots"] == 0
    parent_again = (
        await session.execute(select(Frame).where(Frame.uuid == "parent-1"))
    ).scalar_one()
    assert parent_again.voiceover_text == "альфа бета"
    assert any("collapse" in line for line in warn_log)


@pytest.mark.asyncio
async def test_rebuild_keeps_live_frame_edges(session: AsyncSession, warn_log) -> None:
    """Было: delete(FrameEdge).where(type=='next') в rebuild_vo_cells_from_shots.

    Связь двух живых кадров остаётся той же строкой. Связь с leftover
    помечается archived:next, а не стирается. type=continues не трогается.
    """
    project = await _project(session, "live-edges")
    keep = _frame(project, 1, "keep-edge", "Живой закадр первого.", sort_key=1.0)
    nxt = _frame(project, 2, "next-edge", "Живой закадр второго.", sort_key=2.0)
    orphan = Frame(
        project_id=project.id,
        number=3,
        uuid="orphan-edge",
        voiceover_text="",
        sort_key=3.0,
        attrs={},
    )
    session.add_all([keep, nxt, orphan])
    await session.flush()
    live = FrameEdge(
        project_id=project.id,
        from_frame_id=keep.id,
        to_frame_id=nxt.id,
        type="next",
    )
    side = FrameEdge(
        project_id=project.id,
        from_frame_id=keep.id,
        to_frame_id=nxt.id,
        type="continues",
    )
    stale = FrameEdge(
        project_id=project.id,
        from_frame_id=nxt.id,
        to_frame_id=orphan.id,
        type="next",
    )
    session.add_all([live, side, stale])
    await session.commit()
    live_id, side_id, stale_id = int(live.id), int(side.id), int(stale.id)

    await rebuild_vo_cells_from_shots(session, project, [keep, nxt, orphan])
    await session.commit()

    kept_live = await session.get(FrameEdge, live_id)
    kept_side = await session.get(FrameEdge, side_id)
    kept_stale = await session.get(FrameEdge, stale_id)
    assert kept_live is not None and kept_live.type == "next"
    assert kept_side is not None and kept_side.type == "continues"
    assert kept_stale is not None and kept_stale.type == "archived:next"
    assert any("archived:next" in line for line in warn_log)


@pytest.mark.asyncio
async def test_store_and_wipe_cells_archive_instead_of_delete(
    session: AsyncSession, warn_log
) -> None:
    """Было: delete(SceneDesignCell) в store_cells и wipe_cells (все ячейки проекта)."""
    project = await _project(session, "cells-archive")
    payload = {
        "characters": [
            {"id": "c01", "имя": "Анна", "внешность": "пальто"},
        ]
    }
    cells = sd_cells.slice_to_cells(project, "characters", payload, "Анна в пальто.")
    await sd_cells.store_cells(session, project, "characters", cells, chunk_size=2)
    first_ids = list(
        (
            await session.execute(
                select(SceneDesignCell.id).where(SceneDesignCell.project_id == project.id)
            )
        ).scalars()
    )
    assert first_ids
    fresh = sd_cells.slice_to_cells(project, "characters", payload, "Анна в пальто.")
    await sd_cells.store_cells(session, project, "characters", fresh, chunk_size=2)
    all_rows = list(
        (
            await session.execute(
                select(SceneDesignCell).where(SceneDesignCell.project_id == project.id)
            )
        ).scalars()
    )
    assert len(all_rows) > len(first_ids)
    assert any(row.status == "archived" for row in all_rows)
    loaded = await sd_cells.load_cells(session, project)
    assert loaded
    assert all(row.status == "ok" for row in loaded)
    wiped = await sd_cells.wipe_cells(session, project)
    assert wiped == len(loaded)
    assert await sd_cells.load_cells(session, project) == []
    still = list(
        (
            await session.execute(
                select(SceneDesignCell).where(SceneDesignCell.project_id == project.id)
            )
        ).scalars()
    )
    assert still
    assert all(row.status == "archived" for row in still)
    assert any("archived" in line for line in warn_log)


@pytest.mark.asyncio
async def test_empty_voiceover_keeps_frame_text(session: AsyncSession, warn_log) -> None:
    """Было: ``await session.delete(existing)`` в upsert_frame_voiceover."""
    project = await _project(session, "vo-text")
    frame = _frame(project, 1, "vo-1", "было", sort_key=1.0)
    session.add(frame)
    await session.flush()
    session.add(
        FrameText(
            project_id=project.id,
            frame_id=frame.id,
            kind="voiceover",
            text="было",
        )
    )
    await session.commit()
    text_id = (
        await session.execute(select(FrameText.id).where(FrameText.frame_id == frame.id))
    ).scalar_one()

    await upsert_frame_voiceover(session, frame, "")
    await session.commit()

    row = await session.get(FrameText, text_id)
    assert row is not None
    assert row.kind == "vo_archived"
    assert row.text == "было"
    assert frame.voiceover_text == ""
    assert any("vo_archived" in line for line in warn_log)


@pytest.mark.asyncio
async def test_db_browser_entity_and_edge_need_confirm_and_restore(
    session: AsyncSession, warn_log
) -> None:
    """Было: session.delete(en) и session.delete(e) в db_browser."""
    project = await _project(session, "db-browser-mark")
    entity = Entity(
        project_id=project.id,
        type="character",
        code="c01",
        name="Анна",
        attrs={"роль": "герой"},
    )
    left = _frame(project, 1, "e-left", "слева", sort_key=1.0)
    right = _frame(project, 2, "e-right", "справа", sort_key=2.0)
    session.add_all([entity, left, right])
    await session.flush()
    edge = FrameEdge(
        project_id=project.id,
        from_frame_id=left.id,
        to_frame_id=right.id,
        type="continues",
    )
    session.add(edge)
    await session.commit()

    with pytest.raises(HTTPException) as denied:
        await delete_entity(entity.id, confirm=False, session=session)
    assert denied.value.status_code == 400
    untouched = await session.get(Entity, entity.id)
    assert untouched is not None
    assert not (untouched.attrs or {}).get("удалён")

    marked = await delete_entity(entity.id, confirm=True, session=session)
    assert marked["archived"] is True
    stored = await session.get(Entity, entity.id)
    assert stored is not None
    assert stored.attrs.get("удалён") is True
    assert stored.attrs.get("активен") is False
    assert stored.attrs.get("archived") is True
    assert stored.name == "Анна"

    restored = await restore_entity(entity.id, session=session)
    assert restored["archived"] is False
    back = await session.get(Entity, entity.id)
    assert back is not None
    assert "удалён" not in (back.attrs or {})
    assert back.attrs.get("роль") == "герой"

    with pytest.raises(HTTPException) as edge_denied:
        await delete_edge(edge.id, confirm=False, session=session)
    assert edge_denied.value.status_code == 400
    same_edge = await session.get(FrameEdge, edge.id)
    assert same_edge is not None and same_edge.type == "continues"

    archived = await delete_edge(edge.id, confirm=True, session=session)
    assert archived["archived"] is True
    kept_edge = await session.get(FrameEdge, edge.id)
    assert kept_edge is not None
    assert kept_edge.type == "archived:continues"
    revived = await restore_edge(edge.id, session=session)
    assert revived["type"] == "continues"
    assert await session.get(FrameEdge, edge.id) is not None
    assert any("Entity id=" in line for line in warn_log)
    assert any("FrameEdge id=" in line for line in warn_log)


@pytest.mark.asyncio
async def test_delete_text_marks_and_restores(session: AsyncSession, warn_log) -> None:
    """Было: ``await session.delete(t)`` в ``delete_text``."""
    project = await _project(session, "text-mark")
    frame = _frame(project, 1, "text-frame", "кадр", sort_key=1.0)
    session.add(frame)
    await session.flush()
    row = FrameText(
        project_id=project.id,
        frame_id=frame.id,
        kind="voiceover",
        text="живой закадр",
    )
    session.add(row)
    await session.commit()

    with pytest.raises(HTTPException) as denied:
        await delete_text(row.id, confirm=False, session=session)
    assert denied.value.status_code == 400
    kept = await session.get(FrameText, row.id)
    assert kept is not None
    assert kept.kind == "voiceover"
    assert kept.text == "живой закадр"

    marked = await delete_text(row.id, confirm=True, session=session)
    assert marked["archived"] is True
    stored = await session.get(FrameText, row.id)
    assert stored is not None
    assert stored.kind == "archived:voiceover"
    assert stored.text == "живой закадр"

    restored = await restore_text(row.id, session=session)
    assert restored["kind"] == "voiceover"
    assert restored["archived"] is False
    back = await session.get(FrameText, row.id)
    assert back is not None
    assert back.kind == "voiceover"
    assert back.text == "живой закадр"
    assert any("FrameText id=" in line for line in warn_log)


@pytest.mark.asyncio
async def test_deleted_mark_does_not_trigger_orphan_cascade(session: AsyncSession) -> None:
    """Пометка ``удалён`` не снимает кадр с коллекции и не включает delete-orphan.

    Каскады Project.frames / artifacts / hitl_requests и Frame.artifacts
    (models.py) не меняются: orphan срабатывает при удалении из коллекции,
    не при смене attrs.
    """
    source = (await _models_text()).splitlines()
    for line_no in (299, 301, 304, 354):
        assert "delete-orphan" in source[line_no - 1]

    project = await _project(session, "cascade-mark")
    frame = _frame(project, 1, "cascade-frame", "текст", sort_key=1.0)
    session.add(frame)
    await session.flush()
    art = Artifact(
        project_id=project.id,
        frame_id=frame.id,
        kind=ArtifactKind.scene_image,
        uuid=str(uuid.uuid4()),
        path="/tmp/cascade.png",
    )
    text = FrameText(
        project_id=project.id,
        frame_id=frame.id,
        kind="voiceover",
        text="текст",
    )
    prompt = PromptVersion(
        project_id=project.id,
        frame_id=frame.id,
        kind="img",
        version=1,
        text="промт",
    )
    session.add_all([art, text, prompt])
    await session.commit()
    frame_id, art_id, text_id, prompt_id = (
        int(frame.id),
        int(art.id),
        int(text.id),
        int(prompt.id),
    )
    await session.refresh(project, attribute_names=["frames", "artifacts"])
    await session.refresh(frame, attribute_names=["artifacts"])
    assert frame in project.frames
    assert art in frame.artifacts

    mark_frame_deleted(frame, project, "cascade probe")
    await session.commit()
    session.expire_all()

    assert await session.get(Frame, frame_id) is not None
    assert await session.get(Artifact, art_id) is not None
    assert await session.get(FrameText, text_id) is not None
    assert await session.get(PromptVersion, prompt_id) is not None
    reloaded = await session.get(Frame, frame_id)
    assert reloaded is not None
    assert reloaded.attrs.get("удалён") is True
    assert reloaded.attrs.get("активен") is False


async def _models_text() -> str:
    from pathlib import Path

    return Path("app/models.py").read_text(encoding="utf-8")


def test_pipeline_frame_skips_marked() -> None:
    from types import SimpleNamespace

    from app.services.vo_shot_expand import _is_pipeline_frame

    live = SimpleNamespace(uuid="abc", attrs={})
    marked = SimpleNamespace(uuid="abc", attrs={"удалён": True, "активен": False})
    inactive = SimpleNamespace(uuid="abc", attrs={"активен": False})
    assert _is_pipeline_frame(live) is True
    assert _is_pipeline_frame(marked) is False
    assert _is_pipeline_frame(inactive) is False


@pytest.mark.asyncio
async def test_split_frames_ignores_marked_rows(session: AsyncSession, monkeypatch) -> None:
    """Помеченный кадр не входит в пропуск GPT, экспорт и финальный счётчик."""
    from types import SimpleNamespace

    from app.models import ProjectStatus
    from app.orchestrator.steps.split_frames import active_split_frames, run

    project = await _project(session, "split-marked")
    project.status = ProjectStatus.splitting
    project.meta = {"split_completed": True}
    marked = Frame(
        project_id=project.id,
        number=2,
        uuid="dead-2",
        voiceover_text="старый",
        attrs={"удалён": True, "активен": False},
    )
    session.add(marked)
    await session.flush()

    async def _gpt_ran(_project):
        raise RuntimeError("gpt-ran")

    monkeypatch.setattr(
        "app.orchestrator.steps.split_frames.xsr.run_split_xlsx", _gpt_ran
    )
    with pytest.raises(RuntimeError, match="gpt-ran"):
        await run(session, project)

    live = Frame(
        project_id=project.id,
        number=1,
        uuid="live-1",
        voiceover_text="живой",
        attrs={},
    )
    session.add(live)
    project.meta = {}
    project.status = ProjectStatus.splitting
    await session.flush()
    assert [fr.uuid for fr in active_split_frames([live, marked])] == ["live-1"]

    exported: dict[str, list] = {}

    async def _fake_split(_project):
        return SimpleNamespace(apply_ops=[{"target": "noop"}], frames_spec=None)

    async def _fake_apply(*_a, **_k):
        return {"updated": 0}

    def _fake_export(_project, frames):
        exported["ids"] = [fr.id for fr in frames]
        return {"frames": len(frames), "cells": 0}

    async def _noop(*_a, **_k):
        return None

    monkeypatch.setattr(
        "app.orchestrator.steps.split_frames.xsr.run_split_xlsx", _fake_split
    )
    monkeypatch.setattr("app.services.db_apply.apply_ops", _fake_apply)
    monkeypatch.setattr("app.services.db_apply.export_project_xlsx", _fake_export)
    monkeypatch.setattr("app.services.agent_harness.harness_gate_or_raise", _noop)

    with pytest.raises(RuntimeError, match="кадров 1"):
        await run(session, project)
    assert "ids" not in exported

    extra = Frame(
        project_id=project.id,
        number=3,
        uuid="live-3",
        voiceover_text="ещё живой",
        attrs={},
    )
    session.add(extra)
    project.status = ProjectStatus.splitting
    await session.flush()

    await run(session, project)
    assert exported["ids"] == [live.id, extra.id]
    assert project.status is ProjectStatus.frames_ready


def test_mark_frame_deleted_keeps_existing_flags(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Повторная пометка не затирает причину, карантин и активен."""
    from types import SimpleNamespace

    frame = SimpleNamespace(
        id=9,
        number=4,
        attrs={
            "удалён": True,
            "активен": False,
            "карантин": True,
            "пометка": "первая причина",
            "причина": "ручная",
        },
    )
    project = SimpleNamespace(meta={})
    before = dict(frame.attrs)
    sink = logger.add(caplog.handler, level="WARNING", format="{message}")
    try:
        mark_frame_deleted(frame, project, "вторая причина")
    finally:
        logger.remove(sink)
    assert frame.attrs == before
    assert "уже помечен" in caplog.text
    assert "не перезаписаны" in caplog.text
    assert 4 in (project.meta or {}).get("deleted_frame_numbers", [])


def test_park_does_not_reuse_a_held_number(caplog: pytest.LogCaptureFixture) -> None:
    """Уже занятый 900000 не отдаётся следующему кадру, исходный номер цел."""
    from types import SimpleNamespace

    from app.services.frame_tombstones import park_marked_frame_numbers

    earlier = SimpleNamespace(
        id=7,
        number=900_000,
        attrs={
            "удалён": True,
            "активен": False,
            "исходный_номер": 7,
            "пометка": "первая",
        },
    )
    fresh = SimpleNamespace(
        id=3,
        number=3,
        attrs={"удалён": True, "активен": False, "пометка": "сброс"},
    )
    live = SimpleNamespace(id=1, number=4, attrs={})
    sink = logger.add(caplog.handler, level="WARNING", format="{message}")
    try:
        assert park_marked_frame_numbers([fresh, earlier, live]) == 2
        assert fresh.number == 900_001
        assert earlier.number == 900_000
        assert live.number == 4
        assert fresh.attrs["исходный_номер"] == 3
        assert earlier.attrs["исходный_номер"] == 7
        assert earlier.attrs["пометка"] == "первая"
        assert "900000" in caplog.text
        assert "занят" in caplog.text
        park_marked_frame_numbers([fresh, earlier, live])
        assert fresh.number == 900_001
        assert earlier.number == 900_000
        assert fresh.attrs["исходный_номер"] == 3
        assert earlier.attrs["исходный_номер"] == 7
        assert live.number == 4
    finally:
        logger.remove(sink)

