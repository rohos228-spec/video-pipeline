"""Tests for prompt block CRUD and activity logging."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base
from app.services import prompt_blocks as pb


@pytest.fixture
async def session() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


@pytest.mark.asyncio
async def test_create_and_update_block_logs_events(session: AsyncSession, tmp_path, monkeypatch):
    monkeypatch.setattr(pb, "PROMPTS_ROOT", tmp_path)
    blocks_root = tmp_path / "blocks" / "test_cat"
    blocks_root.mkdir(parents=True)

    created = await pb.create_block(
        session,
        "test_cat",
        "sample_block",
        "hello block",
        message="test create",
    )
    await session.commit()
    assert created["created"] is True
    assert (blocks_root / "sample_block.md").is_file()

    activity = await pb.list_block_activity(session, limit=20)
    types = [a["event_type"] for a in activity]
    assert "block_created" in types

    updated = await pb.save_block(
        session,
        "test_cat",
        "sample_block",
        "hello block v2",
        message="test update",
    )
    await session.commit()
    assert updated["changed"] is True

    activity2 = await pb.list_block_activity(session, limit=20)
    assert "block_updated" in [a["event_type"] for a in activity2]


@pytest.mark.asyncio
async def test_rename_and_delete_block(session: AsyncSession, tmp_path, monkeypatch):
    monkeypatch.setattr(pb, "PROMPTS_ROOT", tmp_path)
    cat_dir = tmp_path / "blocks" / "visual_style"
    cat_dir.mkdir(parents=True)

    await pb.create_block(session, "visual_style", "orig", "content v1", message="seed")
    await session.commit()

    renamed = await pb.rename_block(session, "visual_style", "orig", "renamed_style")
    await session.commit()
    assert renamed["id"] == "renamed_style"
    assert (cat_dir / "renamed_style.md").is_file()
    assert not (cat_dir / "orig.md").is_file()

    deleted = await pb.delete_block(session, "visual_style", "renamed_style")
    await session.commit()
    assert deleted["deleted"] is True
    assert not (cat_dir / "renamed_style.md").is_file()


@pytest.mark.asyncio
async def test_sync_discovers_new_block(session: AsyncSession, tmp_path, monkeypatch):
    import app.services.prompt_composer as pc

    monkeypatch.setattr(pb, "PROMPTS_ROOT", tmp_path)
    monkeypatch.setattr(pc, "PROMPTS_ROOT", tmp_path)
    cat_dir = tmp_path / "blocks" / "visual_style"
    cat_dir.mkdir(parents=True)
    (cat_dir / "new_style.md").write_text("new visual style block", encoding="utf-8")

    result = await pb.sync_blocks_catalog(session)
    await session.commit()
    assert result["discovered_count"] >= 1
    assert any(d["block_id"] == "new_style" for d in result["discovered"])
