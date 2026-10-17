"""Пустой кусок закадра шота остаётся пустым: не действие и не весь закадр ячейки."""

from contextlib import asynccontextmanager

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base


@pytest.fixture
async def mem_db(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    from app import settings as app_settings

    monkeypatch.setattr(app_settings.settings, "data_dir", tmp_path / "data")
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    @asynccontextmanager
    async def _scope():
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    monkeypatch.setattr("app.db.session_scope", _scope)
    yield _scope
    await engine.dispose()


async def test_group_expand_keeps_empty_piece_empty(mem_db, monkeypatch) -> None:
    import uuid as _uuid

    from sqlalchemy import select

    from app.models import Frame, Project, ProjectStatus
    from app.services import vo_shot_expand as vse

    full = "Весь закадр этой ячейки одной фразой."
    actions = ["Следователь вошёл в кабинет.", "Следователь сел к столу.",
               "Следователь открыл папку."]
    kadry = [
        {"id": "1-S1-K1", "parent_id": None, "сцена": 1, "место": "кабинет",
         "действие": actions[0], "закадр": ""},
        {"id": "1-S1-K2", "parent_id": "1-S1-K1", "сцена": 1, "место": "кабинет",
         "действие": actions[1], "закадр": ""},
        {"id": "1-S1-K3", "parent_id": "1-S1-K1", "сцена": 1, "место": "кабинет",
         "действие": actions[2], "закадр": ""},
    ]
    # Нарезка дала текст только первому шоту — остальные куски пустые.
    monkeypatch.setattr(
        vse, "kadry_vo_pieces", lambda planned, text, action="": [full, "", ""]
    )
    canvas = {
        "canvas_graph": {
            "nodes": [{"id": "n_excel_gpt_fw_shots",
                       "data": {"groupId": "script_frames_qc"}}],
            "edges": [],
        }
    }
    async with mem_db() as session:
        project = Project(
            slug=f"vo-empty-{_uuid.uuid4().hex[:8]}",
            topic="t",
            status=ProjectStatus.new,
            meta=canvas,
        )
        session.add(project)
        await session.flush()
        session.add(
            Frame(
                project_id=project.id,
                number=1,
                sort_key=1.0,
                uuid="ab" * 12,
                voiceover_text=full,
                duration_seconds=4.0,
                attrs={"кадры": [dict(s) for s in kadry], "vo_cell_full": full},
            )
        )
        await session.flush()
        frames = list(
            (await session.execute(select(Frame).where(Frame.project_id == project.id)))
            .scalars()
            .all()
        )
        ordered, report = await vse.expand_vo_cells_into_shots(session, project, frames)
        assert report["inserted"] == 2
        children = [f for f in ordered if f.uuid != "ab" * 12 and vse._is_pipeline_frame(f)]
        assert len(children) == 2
        for child in children:
            vo = (child.voiceover_text or "").strip()
            assert vo not in actions, "действие шота попало в закадр"
            assert vo != full, "весь закадр ячейки скопирован в пустой шот"
            assert vo == ""
            assert (vse._cs(child).get("vo_shot") or "") not in actions
            assert (vse._cs(child).get("vo_shot") or "") != full
