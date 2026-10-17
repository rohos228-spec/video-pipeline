"""Порядок и параллельность apply монтажа."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Frame, Project
from app.services.montage_board_apply import (
    apply_montage_board,
    group_ops_by_frame,
    order_montage_pending_ops,
)


def test_order_images_before_videos_and_by_frame_shot() -> None:
    ops = [
        {"type": "video_regen", "frame_number": 1, "shot": 1},
        {"type": "image_regen", "frame_number": 2, "shot": 2},
        {"type": "image_regen", "frame_number": 1, "shot": 2},
        {"type": "image_regen", "frame_number": 1, "shot": 1},
        {"type": "video_regen", "frame_number": 2, "shot": 1},
    ]
    ordered = order_montage_pending_ops(ops)
    types = [o["type"] for o in ordered]
    assert types[:3] == ["image_regen", "image_regen", "image_regen"]
    assert types[3:] == ["video_regen", "video_regen"]
    assert [(o["frame_number"], o["shot"]) for o in ordered[:3]] == [
        (1, 1),
        (1, 2),
        (2, 2),
    ]


def test_montage_parallel_falls_back_to_create_outsee() -> None:
    from app.services.montage_board_apply import _montage_apply_parallel

    p = Project(id=1, slug="x", topic="t", hero_mode="auto")
    p.meta = {}
    assert _montage_apply_parallel(p) == 4  # CREATE_MAX_PARALLEL_OUTSEE default

    p.meta = {"outsee_streams": 2}
    assert _montage_apply_parallel(p) == 2


def test_group_ops_by_frame_shot_order() -> None:
    ops = [
        {"type": "image_regen", "frame_number": 2, "shot": 1},
        {"type": "image_regen", "frame_number": 1, "shot": 2},
        {"type": "image_regen", "frame_number": 1, "shot": 1},
    ]
    groups = group_ops_by_frame(ops)
    assert [fr for fr, _ in groups] == [1, 2]
    assert [o["shot"] for o in groups[0][1]] == [1, 2]


@pytest.fixture
async def session(tmp_path: Path) -> AsyncSession:
    db_path = tmp_path / "montage_parallel.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


@pytest.mark.asyncio
async def test_apply_images_phase_before_videos(
    tmp_path: Path,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "data"
    data_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("app.settings.settings.data_dir", str(data_root))
    project = Project(id=201, slug="montage-par", topic="t", hero_mode="auto")
    project.data_dir.mkdir(parents=True, exist_ok=True)
    project.meta = {"outsee_streams": 2}
    session.add(project)
    for n in (1, 2):
        session.add(
            Frame(
                project_id=201,
                number=n,
                voiceover_text="t",
                status="planned",
                image_prompt="p",
            )
        )
    await session.flush()

    @asynccontextmanager
    async def _scope(*_a, **_k):
        yield session

    monkeypatch.setattr("app.services.montage_board_apply.session_scope", _scope)

    events: list[str] = []

    async def _run(project_id, op, board):
        kind = "img" if str(op["type"]).startswith("image") else "vid"
        key = f"{kind}:F{op['frame_number']}"
        events.append(f"start:{key}")
        if kind == "vid":
            img_starts = [e for e in events if e.startswith("start:img")]
            img_dones = [e for e in events if e.startswith("done:img")]
            assert len(img_dones) == len(img_starts) == 2
        await asyncio.sleep(0.05)
        events.append(f"done:{key}")
        return {"ok": True, "highlight": key}

    monkeypatch.setattr(
        "app.services.montage_board_apply._run_op_with_short_sessions",
        _run,
    )

    ops = [
        {"type": "video_regen", "frame_number": 1, "shot": 1},
        {"type": "image_regen", "frame_number": 2, "shot": 1},
        {"type": "image_regen", "frame_number": 1, "shot": 1},
    ]
    result = await apply_montage_board(session, project, pending_ops=ops)
    assert result["ok"] is True

    first_vid = next(i for i, e in enumerate(events) if e.startswith("start:vid"))
    assert all(
        e.startswith("start:img") or e.startswith("done:img") for e in events[:first_vid]
    )


@pytest.mark.asyncio
async def test_apply_shot1_before_shot2_same_frame(
    tmp_path: Path,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "data"
    data_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("app.settings.settings.data_dir", str(data_root))
    project = Project(id=202, slug="montage-shots", topic="t", hero_mode="auto")
    project.data_dir.mkdir(parents=True, exist_ok=True)
    project.meta = {"outsee_streams": 4}
    session.add(project)
    session.add(
        Frame(
            project_id=202,
            number=1,
            voiceover_text="t",
            status="planned",
            image_prompt="p",
        )
    )
    await session.flush()

    @asynccontextmanager
    async def _scope(*_a, **_k):
        yield session

    monkeypatch.setattr("app.services.montage_board_apply.session_scope", _scope)

    events: list[str] = []
    shot1_done = asyncio.Event()

    async def _run(project_id, op, board):
        shot = int(op.get("shot") or 1)
        events.append(f"start:{shot}")
        if shot == 2:
            assert shot1_done.is_set(), "shot2 стартовал до конца shot1"
        await asyncio.sleep(0.08)
        events.append(f"done:{shot}")
        if shot == 1:
            shot1_done.set()
        return {"ok": True}

    monkeypatch.setattr(
        "app.services.montage_board_apply._run_op_with_short_sessions",
        _run,
    )

    result = await apply_montage_board(
        session,
        project,
        pending_ops=[
            {"type": "image_regen", "frame_number": 1, "shot": 2},
            {"type": "image_regen", "frame_number": 1, "shot": 1},
        ],
    )
    assert result["ok"] is True
    assert events == ["start:1", "done:1", "start:2", "done:2"]


@pytest.mark.asyncio
async def test_apply_skips_child_when_parent_fails(
    tmp_path: Path,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "data"
    data_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("app.settings.settings.data_dir", str(data_root))
    project = Project(id=203, slug="montage-parent-fail", topic="t", hero_mode="auto")
    project.data_dir.mkdir(parents=True, exist_ok=True)
    project.meta = {"outsee_streams": 2}
    session.add(project)
    for n in (33, 34, 36):
        session.add(
            Frame(
                project_id=203,
                number=n,
                voiceover_text="t",
                status="planned",
                image_prompt="p",
            )
        )
    await session.flush()

    @asynccontextmanager
    async def _scope(*_a, **_k):
        yield session

    monkeypatch.setattr("app.services.montage_board_apply.session_scope", _scope)

    async def _parents(_project_id: int):
        return {33: None, 34: 33, 36: None}

    monkeypatch.setattr(
        "app.services.montage_board_apply.coverage_parent_map",
        _parents,
    )

    ran: list[int] = []

    async def _run(_project_id, op, _board):
        fr = int(op["frame_number"])
        ran.append(fr)
        if fr == 33:
            raise RuntimeError("GPT провайдер error: upstream error")
        return {"ok": True, "highlight": f"F{fr}"}

    monkeypatch.setattr(
        "app.services.montage_board_apply._run_op_with_short_sessions",
        _run,
    )

    result = await apply_montage_board(
        session,
        project,
        pending_ops=[
            {"type": "image_ai_change", "frame_number": 33, "shot": 1},
            {"type": "image_ai_change", "frame_number": 36, "shot": 1},
            {"type": "image_ai_change", "frame_number": 34, "shot": 1},
            {"type": "image_ai_change", "frame_number": 34, "shot": 2},
        ],
    )
    assert 34 not in ran
    assert ran.count(33) == 1
    assert 36 in ran
    assert result["ok"] is False
    child_errs = [
        e
        for e in result["errors"]
        if "34" in e or "родител" in e.lower()
    ]
    assert child_errs, result["errors"]
    assert any("33" in e for e in result["errors"])


def test_waves_parent_then_child_only_if_parent_in_batch() -> None:
    from app.services.montage_board_apply import waves_parent_then_child

    parent_of = {1: None, 2: 1, 3: 1, 8: 7}
    # Родитель уже есть и не в очереди — дети сразу.
    assert waves_parent_then_child([2, 3, 8], parent_of) == [[2, 3, 8]]
    # Родитель тоже в пачке — сначала он, потом дети.
    assert waves_parent_then_child([1, 2, 3], parent_of) == [[1], [2, 3]]
    # Дети 150/151 ждут still #148, а не leftover #2.
    assert waves_parent_then_child([148, 1, 150, 151], {1: 148, 150: 148, 151: 148, 148: None}) == [
        [148],
        [1, 150, 151],
    ]
