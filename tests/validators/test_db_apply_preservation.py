"""Точка входа сохранности вызывается из db_apply до записи."""

from __future__ import annotations

import copy

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from sqlalchemy import select

from app.models import Base, Frame, Project
from app.orchestrator.validators.preservation import (
    PreservationRejected,
    run_preservation_validators,
)
from app.services import db_apply, db_v2


@pytest.fixture
async def session() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await db_v2.migrate_db_v2_schema(conn)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as item:
        yield item
    await engine.dispose()


@pytest.mark.asyncio
async def test_db_apply_runs_no_deletions(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []
    real = run_preservation_validators

    def _spy(before, after, ops=None, *, mode="reject"):
        seen.append(mode)
        return real(before, after, ops, mode=mode)

    monkeypatch.setattr(db_apply, "run_preservation_validators", _spy)

    project = Project(slug="keep", topic="тест", status=None)  # type: ignore[arg-type]
    session.add(project)
    await session.flush()
    frame = Frame(
        project_id=project.id,
        number=1,
        uuid="bbbbbbbbbbbbbbbbbbbbbbbb",
        voiceover_text="было",
        attrs={"кадры": [{"id": "k1", "закадр": "а"}, {"id": "k2", "закадр": "б"}]},
    )
    session.add(frame)
    await session.commit()

    with pytest.raises(db_apply.ApplyOpsError):
        await db_apply.apply_ops(
            session,
            project,
            [
                {
                    "frame_uuid": frame.uuid,
                    "fields": {"кадры": [{"id": "k1", "закадр": "а"}]},
                }
            ],
        )
    await session.rollback()
    await session.refresh(project)
    await session.refresh(frame)
    assert [item["id"] for item in frame.attrs["кадры"]] == ["k1", "k2"]
    assert seen == ["reject"]

    await db_apply.apply_ops(
        session,
        project,
        [{"frame_uuid": frame.uuid, "fields": {"закадр": "стало"}}],
    )
    await session.refresh(frame)
    assert frame.voiceover_text == "стало"
    assert [item["id"] for item in frame.attrs["кадры"]] == ["k1", "k2"]
    assert seen == ["reject", "reject"]

    before = {
        "сцены": [
            {
                "id": "s1",
                "номер": 1,
                "кадры": [{"id": "k1", "закадр": "а"}, {"id": "k2", "закадр": "б"}],
            }
        ]
    }
    after = {"сцены": [{"id": "s1", "номер": 1, "кадры": [{"id": "k1", "закадр": "а"}]}]}
    frozen = copy.deepcopy(after)
    with pytest.raises(PreservationRejected) as raised:
        run_preservation_validators(before, after)
    assert any(item.code == "frame_deleted" and item.id == "k2" for item in raised.value.violations)
    assert after == frozen

    softened = run_preservation_validators(before, after, mode="soft")
    assert any(item.code == "kept_inactive" and item.severity == "warn" and item.id == "k2" for item in softened)
    assert all(item.severity != "error" for item in softened)
    kept = {item["id"]: item for item in after["сцены"][0]["кадры"]}
    assert kept["k2"]["активен"] is False
    assert kept["k1"]["закадр"] == "а"


def _shot_ids(frames: list[Frame]) -> dict[str, dict]:
    found: dict[str, dict] = {}
    for frame in frames:
        attrs = frame.attrs if isinstance(frame.attrs, dict) else {}
        shots = attrs.get("кадры")
        if not isinstance(shots, list):
            continue
        for shot in shots:
            if isinstance(shot, dict) and shot.get("id") not in (None, ""):
                found[str(shot["id"])] = {"shot": shot, "frame": frame, "attrs": attrs}
    return found


@pytest.mark.asyncio
async def test_replace_frames_runs_preservation(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []
    real = run_preservation_validators

    def _spy(before, after, ops=None, *, mode="reject"):
        seen.append(mode)
        return real(before, after, ops, mode=mode)

    monkeypatch.setattr(db_apply, "run_preservation_validators", _spy)

    project = Project(slug="repl", topic="тест", status=None)  # type: ignore[arg-type]
    session.add(project)
    await session.flush()
    old_uuid = "cccccccccccccccccccccccc"
    frame = Frame(
        project_id=project.id,
        number=3,
        uuid=old_uuid,
        voiceover_text="было",
        attrs={"кадры": [{"id": "k1", "закадр": "а"}, {"id": "k2", "закадр": "б"}]},
    )
    session.add(frame)
    await session.commit()

    await db_apply.apply_ops(
        session,
        project,
        [
            {
                "target": "replace_frames",
                "frames": [{"закадр": "новый один"}, {"закадр": "новый два"}],
            }
        ],
    )
    assert seen == ["reject"]
    rows = list(
        (await session.execute(select(Frame).where(Frame.project_id == project.id))).scalars()
    )
    kept = _shot_ids(rows)
    assert set(kept) >= {"k1", "k2"}
    for fid in ("k1", "k2"):
        owner = kept[fid]
        archived = bool(owner["attrs"].get("archived")) or owner["frame"].uuid != old_uuid
        inactive = owner["shot"].get("активен") is False
        assert archived or inactive

    real_replace = db_v2.replace_all_frames

    async def _wipe(session, project, specs):
        created = await real_replace(session, project, specs)
        for row in (
            await session.execute(select(Frame).where(Frame.project_id == project.id))
        ).scalars():
            attrs = dict(row.attrs or {})
            if not isinstance(attrs.get("кадры"), list):
                continue
            if any(isinstance(shot, dict) and shot.get("id") in {"k1", "k2"} for shot in attrs["кадры"]):
                attrs["кадры"] = []
                attrs.pop("archived", None)
                row.attrs = attrs
        await session.flush()
        return created

    monkeypatch.setattr(db_apply.db_v2, "replace_all_frames", _wipe)
    with pytest.raises(db_apply.ApplyOpsError):
        await db_apply.apply_ops(
            session,
            project,
            [
                {
                    "target": "replace_frames",
                    "frames": [{"закадр": "ещё один"}, {"закадр": "ещё два"}],
                }
            ],
        )
    await session.rollback()
    await session.refresh(frame)
    assert [item["id"] for item in frame.attrs["кадры"]] == ["k1", "k2"]
