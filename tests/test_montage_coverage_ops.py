"""Правки покрытия (план / действие / родитель-дочерний) с панели монтажа."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Frame, Project
from app.services.montage_board_apply import order_montage_pending_ops
from app.services.montage_board_meta import slot_key_from_op
from app.services.montage_coverage_ops import apply_coverage_op
from app.services.vo_shot_expand import find_coverage_parent_frame, is_shot_child


@pytest.fixture
async def session(tmp_path: Path) -> AsyncSession:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'cov.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Project:
    data_root = tmp_path / "data"
    data_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("app.settings.settings.data_dir", str(data_root))
    return Project(id=40, slug="cov-edit", topic="t", hero_mode="auto")


def _parent_child(project_id: int) -> tuple[Frame, Frame]:
    parent_uid = "aa" * 12
    child_uid = "bb" * 12
    parent = Frame(
        project_id=project_id,
        number=1,
        uuid=parent_uid,
        voiceover_text="vo",
        status="planned",
        attrs={
            "shot01_action": "сидит",
            "кадры": [
                {"id": "1-K1", "план": "ОБЩИЙ", "действие": "сидит", "parent_id": None},
                {"id": "1-K2", "план": "ДЕТАЛЬ", "действие": "рука", "parent_id": "1-K1"},
            ],
            "camera_subdivide": {
                "role": "vo_parent",
                "parent_uuid": parent_uid,
                "план": "ОБЩИЙ",
            },
        },
    )
    child = Frame(
        project_id=project_id,
        number=2,
        uuid=child_uid,
        voiceover_text="кусок",
        status="planned",
        attrs={
            "shot01_action": "рука",
            "кадры": [
                {"id": "1-K2", "план": "ДЕТАЛЬ", "действие": "рука", "parent_id": "1-K1"},
            ],
            "camera_subdivide": {
                "role": "shot",
                "parent_uuid": parent_uid,
                "coverage_parent_id": "1-K1",
                "план": "ДЕТАЛЬ",
            },
        },
    )
    return parent, child


def test_coverage_slot_keys_and_order() -> None:
    assert slot_key_from_op({"type": "coverage_plan", "frame_number": 7}) == "7:plan"
    assert slot_key_from_op({"type": "coverage_action", "frame_number": 7}) == "7:action"
    assert slot_key_from_op({"type": "coverage_kind", "frame_number": 7}) == "7:kind"
    assert slot_key_from_op({"type": "coverage_delete", "frame_number": 7}) == "7:kind"
    ordered = order_montage_pending_ops(
        [
            {"type": "image_regen", "frame_number": 2, "shot": 1},
            {"type": "coverage_plan", "frame_number": 2, "plan": "КРУПНЫЙ"},
            {"type": "video_regen", "frame_number": 2, "shot": 1},
        ]
    )
    assert [o["type"] for o in ordered] == [
        "coverage_plan",
        "image_regen",
        "video_regen",
    ]


@pytest.mark.asyncio
async def test_apply_plan_and_action(session: AsyncSession, project: Project) -> None:
    parent, child = _parent_child(project.id)
    session.add_all([project, parent, child])
    await session.flush()

    result = await apply_coverage_op(
        session,
        project,
        {"type": "coverage_plan", "frame_number": 2, "plan": "КРУПНЫЙ"},
    )
    assert result["ok"] is True
    assert result["highlight"] == "2:plan"
    await session.refresh(child)
    assert (child.attrs or {}).get("крупность") == "КРУПНЫЙ"
    assert (child.attrs or {}).get("camera_subdivide", {}).get("план") == "КРУПНЫЙ"

    result = await apply_coverage_op(
        session,
        project,
        {"type": "coverage_action", "frame_number": 2, "action": "берёт ключ"},
    )
    assert result["highlight"] == "2:action"
    await session.refresh(child)
    assert (child.attrs or {}).get("shot01_action") == "берёт ключ"


@pytest.mark.asyncio
async def test_relink_and_delete_child(session: AsyncSession, project: Project) -> None:
    parent, child = _parent_child(project.id)
    other_uid = "cc" * 12
    other = Frame(
        project_id=project.id,
        number=3,
        uuid=other_uid,
        voiceover_text="другой",
        status="planned",
        attrs={"camera_subdivide": {"role": "vo_parent", "parent_uuid": other_uid}},
    )
    session.add_all([project, parent, child, other])
    await session.flush()

    await apply_coverage_op(
        session,
        project,
        {
            "type": "coverage_kind",
            "frame_number": 2,
            "kind": "child",
            "parent_number": 3,
        },
    )
    rows = list(
        (
            await session.execute(
                select(Frame).where(Frame.project_id == project.id).order_by(Frame.number)
            )
        )
        .scalars()
        .all()
    )
    child2 = next(fr for fr in rows if int(fr.number) == 2)
    found = find_coverage_parent_frame(rows, child2)
    assert found is not None
    assert int(found.number) == 3
    assert is_shot_child(child2) is True

    await apply_coverage_op(
        session,
        project,
        {"type": "coverage_delete", "frame_number": 2},
    )
    gone = await session.get(Frame, child.id)
    assert gone is None
