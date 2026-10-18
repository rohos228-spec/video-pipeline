"""Точка входа сохранности вызывается из db_apply до записи."""

from __future__ import annotations

import copy

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.models import Base, Entity, Frame, FrameEdge, FrameText, Project
from app.settings import settings
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


@pytest.mark.asyncio
async def test_db_apply_preservation_view_includes_texts_edges_characters(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[dict] = []
    real = run_preservation_validators

    def _spy(before, after, ops=None, *, mode="reject"):
        captured.append(before)
        return real(before, after, ops, mode=mode)

    monkeypatch.setattr(db_apply, "run_preservation_validators", _spy)

    project = Project(slug="view", topic="тест", status=None)  # type: ignore[arg-type]
    session.add(project)
    await session.flush()
    left = Frame(
        project_id=project.id,
        number=1,
        uuid="dddddddddddddddddddddddd",
        voiceover_text="было",
        attrs={"кадры": [{"id": "k1"}]},
    )
    right = Frame(
        project_id=project.id,
        number=2,
        uuid="eeeeeeeeeeeeeeeeeeeeeeee",
        voiceover_text="ещё",
        attrs={},
    )
    session.add_all([left, right])
    await session.flush()
    session.add_all(
        [
            FrameText(project_id=project.id, frame_id=left.id, kind="voiceover", text="привет"),
            FrameEdge(
                project_id=project.id,
                from_frame_id=left.id,
                to_frame_id=right.id,
                type="next",
            ),
            Entity(project_id=project.id, type="character", code="c01", name="Анна", attrs={}),
        ]
    )
    await session.commit()

    await db_apply.apply_ops(
        session,
        project,
        [{"frame_uuid": left.uuid, "fields": {"закадр": "стало"}}],
    )
    assert captured
    view = captured[0]
    assert any(item.get("kind") == "voiceover" and item.get("text") == "привет" for item in view.get("frame_texts") or [])
    assert any(item.get("type") == "next" for item in view.get("frame_edges") or [])
    assert any(item.get("id") == "c01" or item.get("code") == "c01" for item in view.get("characters") or [])
    await session.refresh(left)
    assert left.voiceover_text == "стало"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "branch",
    ["frame", "replace_frames", "characters", "scenes", "meta"],
)
async def test_db_apply_preservation_covers_all_mutating_ops(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, branch: str
) -> None:
    seen: list[str] = []
    shot_action: list[str | None] = []
    real = run_preservation_validators

    def _spy(before, after, ops=None, *, mode="reject"):
        seen.append(mode)
        attrs = left.attrs if isinstance(left.attrs, dict) else {}
        shot_action.append(attrs.get("shot01_action"))
        return real(before, after, ops, mode=mode)

    monkeypatch.setattr(db_apply, "run_preservation_validators", _spy)

    project = Project(slug=f"allops-{branch}", topic="тест", status=None)  # type: ignore[arg-type]
    session.add(project)
    await session.flush()
    left = Frame(
        project_id=project.id,
        number=1,
        uuid="ffffffffffffffffffffffff",
        voiceover_text="было",
        attrs={"кадры": [{"id": "k1", "закадр": "а"}]},
    )
    right = Frame(
        project_id=project.id,
        number=2,
        uuid="111111111111111111111111",
        voiceover_text="ещё",
        attrs={},
    )
    session.add_all([left, right])
    await session.flush()
    text = FrameText(project_id=project.id, frame_id=left.id, kind="voiceover", text="привет")
    edge = FrameEdge(
        project_id=project.id,
        from_frame_id=left.id,
        to_frame_id=right.id,
        type="next",
    )
    person = Entity(project_id=project.id, type="character", code="c01", name="Анна", attrs={})
    session.add_all([text, edge, person])
    await session.commit()

    if branch == "frame":
        await db_apply.apply_ops(
            session,
            project,
            [{"frame_uuid": left.uuid, "fields": {"закадр": "стало"}}],
        )
    elif branch == "replace_frames":
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
    elif branch == "characters":
        await db_apply.apply_ops(
            session,
            project,
            None,
            characters=[{"id": "c01", "имя": "Анна Петровна"}],
        )
    elif branch == "scenes":
        await db_apply.apply_ops(
            session,
            project,
            None,
            scenes=[
                {
                    "id_scene": "sc1",
                    "start_words": "было",
                    "end_words": "было",
                    "shots": [{"id_shot": "sh1", "действие": "смотрит"}],
                }
            ],
        )
    elif branch == "meta":
        await db_apply.apply_ops(
            session,
            project,
            [{"target": "project", "fields": {"general_plan": "план на мета"}}],
        )
    assert seen == ["reject"]
    if branch == "scenes":
        assert shot_action == ["смотрит"]

    if branch != "characters":
        return

    await session.refresh(person)
    assert person.name == "Анна Петровна"
    await session.refresh(text)
    assert text.text == "привет"

    real_upsert = db_apply.upsert_characters

    async def _wipe(session, project, characters, *, replace=False):
        await real_upsert(session, project, characters, replace=replace)
        for row in (
            await session.execute(select(FrameText).where(FrameText.project_id == project.id))
        ).scalars():
            await session.delete(row)
        for row in (
            await session.execute(select(FrameEdge).where(FrameEdge.project_id == project.id))
        ).scalars():
            await session.delete(row)
        for row in (
            await session.execute(
                select(Entity).where(Entity.project_id == project.id, Entity.type == "character")
            )
        ).scalars():
            await session.delete(row)
        await session.flush()
        return 1

    monkeypatch.setattr(db_apply, "upsert_characters", _wipe)
    with pytest.raises(db_apply.ApplyOpsError):
        await db_apply.apply_ops(
            session,
            project,
            None,
            characters=[{"id": "c01", "имя": "Анна"}],
        )
    await session.rollback()
    await session.refresh(text)
    await session.refresh(edge)
    await session.refresh(person)
    assert text.text == "привет"
    assert edge.type == "next"
    assert person.code == "c01"


@pytest.mark.asyncio
async def test_rejected_apply_ops_leaves_session_clean(session: AsyncSession) -> None:
    """ApplyOpsError, затем commit: кадры, тексты, связи и персонажи как были."""
    project = Project(slug="clean", topic="тест", status=None)  # type: ignore[arg-type]
    session.add(project)
    await session.flush()
    left = Frame(
        project_id=project.id,
        number=1,
        uuid="aaaaaaaaaaaaaaaaaaaaaaaa",
        voiceover_text="один два",
        attrs={"кадры": [{"id": "1-K1", "текст": "один"}, {"id": "1-K2", "текст": "два"}]},
    )
    right = Frame(
        project_id=project.id,
        number=2,
        uuid="bbbbbbbbbbbbbbbbbbbbbbbb",
        voiceover_text="ещё",
        attrs={},
    )
    session.add_all([left, right])
    await session.flush()
    session.add_all(
        [
            FrameText(project_id=project.id, frame_id=left.id, kind="voiceover", text="привет"),
            FrameEdge(
                project_id=project.id,
                from_frame_id=left.id,
                to_frame_id=right.id,
                type="next",
            ),
        ]
    )
    await db_apply.upsert_characters(
        session, project, [{"id": "c01", "имя": "Анна", "внешность": "рост"}]
    )
    await session.commit()
    project_id = project.id
    left_id = left.id

    real_upsert = db_apply.upsert_characters

    async def _also_drop_links(sess, proj, characters, *, replace=False):
        count = await real_upsert(sess, proj, characters, replace=replace)
        for row in (
            await sess.execute(select(FrameText).where(FrameText.project_id == proj.id))
        ).scalars():
            await sess.delete(row)
        for row in (
            await sess.execute(select(FrameEdge).where(FrameEdge.project_id == proj.id))
        ).scalars():
            await sess.delete(row)
        await sess.flush()
        return count

    db_apply.upsert_characters = _also_drop_links  # type: ignore[method-assign]
    try:
        with pytest.raises(db_apply.ApplyOpsError):
            await db_apply.apply_ops(
                session,
                project,
                [
                    {
                        "target": "frame",
                        "frame_uuid": left.uuid,
                        "fields": {"кадры": [{"id": "1-K1", "текст": "один"}]},
                    }
                ],
                characters=[{"id": "c01", "имя": "Борис", "внешность": "рост"}],
            )
        await session.commit()
    finally:
        db_apply.upsert_characters = real_upsert

    session.expire_all()
    kept = await session.get(Frame, left_id)
    person = (
        await session.execute(
            select(Entity).where(Entity.project_id == project_id, Entity.type == "character")
        )
    ).scalar_one()
    texts = list(
        (
            await session.execute(select(FrameText).where(FrameText.project_id == project_id))
        ).scalars()
    )
    edges = list(
        (
            await session.execute(select(FrameEdge).where(FrameEdge.project_id == project_id))
        ).scalars()
    )
    assert [shot["id"] for shot in (kept.attrs or {}).get("кадры") or []] == ["1-K1", "1-K2"]
    assert [item.text for item in texts] == ["привет"]
    assert [item.type for item in edges] == ["next"]
    assert person.name == "Анна"


@pytest.mark.asyncio
async def test_db_apply_restore_shot_via_kadry_edit_passes(session: AsyncSession) -> None:
    """Явный restore шота по shot_id или индексу в кадры[] проходит preservation."""
    project = Project(slug="revive", topic="тест", status=None)  # type: ignore[arg-type]
    session.add(project)
    await session.flush()
    frame = Frame(
        project_id=project.id,
        number=1,
        uuid="cccccccccccccccccccccccc",
        voiceover_text="один два",
        attrs={
            "кадры": [
                {"id": "1-K1", "текст": "один"},
                {"id": "1-K2", "текст": "два", "активен": False},
            ]
        },
    )
    session.add(frame)
    await session.commit()

    revived = [
        {"id": "1-K1", "текст": "один"},
        {"id": "1-K2", "текст": "два", "активен": True},
    ]
    await db_apply.apply_ops(
        session,
        project,
        [
            {
                "target": "frame",
                "frame_uuid": frame.uuid,
                "op": "restore",
                "shot_index": 1,
                "fields": {"кадры": revived},
            }
        ],
    )
    await session.refresh(frame)
    assert frame.attrs["кадры"][1]["активен"] is True

    frame.attrs = {
        "кадры": [
            {"id": "1-K1", "текст": "один"},
            {"id": "1-K2", "текст": "два", "активен": False},
        ]
    }
    await session.commit()
    await db_apply.apply_ops(
        session,
        project,
        [
            {
                "target": "frame",
                "frame_uuid": frame.uuid,
                "action": "activate",
                "shot_id": "1-K2",
                "fields": {"кадры": revived},
            }
        ],
    )
    await session.refresh(frame)
    assert frame.attrs["кадры"][1]["id"] == "1-K2"
    assert frame.attrs["кадры"][1]["активен"] is True


@pytest.mark.asyncio
async def test_successful_apply_ops_rollback_discards_changes(tmp_path) -> None:
    """Успешный apply_ops не фиксирует транзакцию: rollback вызывающего стирает запись.

    Engine как у приложения (``app/db.py``): NullPool и PRAGMA на connect,
    без хука ``begin``. Иначе pysqlite не шлёт BEGIN, и RELEASE savepoint
    коммитит изменения до rollback.
    """

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'apply.db'}",
        poolclass=NullPool,
        connect_args={"timeout": 60},
    )

    def _configure(dbapi_connection, _record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=60000")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()

    event.listens_for(engine.sync_engine, "connect")(_configure)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await db_v2.migrate_db_v2_schema(conn)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            project = Project(slug="rollback-ok", topic="тест", status=None)  # type: ignore[arg-type]
            session.add(project)
            await session.flush()
            frame = Frame(
                project_id=project.id,
                number=1,
                uuid="ffffffffffffffffffffffff",
                voiceover_text="было",
                attrs={"кадры": [{"id": "k1", "закадр": "было"}]},
            )
            session.add(frame)
            await session.commit()

            await db_apply.apply_ops(
                session,
                project,
                [{"frame_uuid": frame.uuid, "fields": {"закадр": "стало"}}],
            )
            await session.rollback()
            await session.refresh(frame)
            assert frame.voiceover_text == "было"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_rejected_script_text_does_not_touch_voiceover_file(
    session: AsyncSession, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Отказ сохранности не оставляет на диске отвергнутый voiceover.txt."""

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    project = Project(slug="vo-file", topic="тест", status=None)  # type: ignore[arg-type]
    session.add(project)
    await session.flush()
    frame = Frame(
        project_id=project.id,
        number=1,
        uuid="111111111111111111111111",
        voiceover_text="а б",
        attrs={"кадры": [{"id": "k1", "закадр": "а"}, {"id": "k2", "закадр": "б"}]},
    )
    session.add(frame)
    await session.commit()
    vo_path = project.data_dir / "voiceover.txt"
    vo_path.parent.mkdir(parents=True, exist_ok=True)
    vo_path.write_text("старый закадр", encoding="utf-8")
    project.script_text = "старый закадр"

    with pytest.raises(db_apply.ApplyOpsError):
        await db_apply.apply_ops(
            session,
            project,
            [
                {"target": "project", "fields": {"script_text": "новый отвергнутый"}},
                {
                    "target": "frame",
                    "frame_uuid": frame.uuid,
                    "fields": {"кадры": [{"id": "k1", "закадр": "а"}]},
                },
            ],
        )

    assert vo_path.read_text(encoding="utf-8") == "старый закадр"


@pytest.mark.asyncio
async def test_accepted_script_text_writes_voiceover_file(
    session: AsyncSession, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """После успешного savepoint зеркало voiceover.txt совпадает с script_text."""

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    project = Project(slug="vo-ok", topic="тест", status=None)  # type: ignore[arg-type]
    session.add(project)
    await session.commit()

    await db_apply.apply_ops(
        session,
        project,
        [{"target": "project", "fields": {"script_text": "принятый закадр"}}],
    )
    assert (project.data_dir / "voiceover.txt").read_text(encoding="utf-8") == "принятый закадр"


def test_restore_shot_id_and_index_must_agree() -> None:
    """shot_id и индекс в кадры[] вместе разрешают restore только если указывают на один шот."""

    mismatch = {
        "op": "restore",
        "shot_id": "k2",
        "shot_index": 0,
        "fields": {
            "кадры": [
                {"id": "k1", "активен": True},
                {"id": "k2", "активен": True},
            ]
        },
    }
    prepared = db_apply._ops_with_shot_address([mismatch])
    assert prepared[0].get("shot_id") in (None, "")

    agreed = {
        "op": "restore",
        "shot_id": "k2",
        "shot_index": 1,
        "fields": {
            "кадры": [
                {"id": "k1"},
                {"id": "k2", "активен": True},
            ]
        },
    }
    assert db_apply._ops_with_shot_address([agreed])[0]["shot_id"] == "k2"


def test_already_counts_state_without_ops() -> None:
    """Нарушение из самого op не считается «уже было» на before."""

    project = {
        "сцены": [
            {
                "id": "s1",
                "кадры": [{"id": "dead", "активен": False, "закадр": ""}],
            }
        ]
    }
    ops = [{"op": "render", "shot_id": "dead"}]
    with pytest.raises(db_apply.ApplyOpsError):
        db_apply._raise_if_new_preservation(project, copy.deepcopy(project), ops)


def test_preservation_view_frame_without_uuid_keeps_id() -> None:
    """Кадр без uuid не схлопывается в пустой id снимка."""
    frame = Frame(
        number=4,
        voiceover_text="текст",
        attrs={"кадры": [{"id": "k"}]},
    )
    view = db_apply._preservation_view([frame])
    assert view["сцены"][0]["id"] == "n4"


def test_preservation_view_frame_without_uuid_prefers_pk() -> None:
    """Без uuid id снимка — pk кадра, а не номер, который может смениться."""

    frame = Frame(
        id=12,
        number=4,
        voiceover_text="текст",
        attrs={"кадры": [{"id": "k"}]},
    )
    frame.number = 9
    view = db_apply._preservation_view([frame])
    assert view["сцены"][0]["id"] == "pk:12"
