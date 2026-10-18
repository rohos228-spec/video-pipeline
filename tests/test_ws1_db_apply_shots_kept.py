"""WS1: apply-ops и replace_all_frames не теряют шоты и кадры."""

from __future__ import annotations

import pytest
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Frame, Project
from app.services import db_v2
from app.services.db_apply import apply_ops
from app.services.frame_tombstones import deleted_numbers, frame_is_active, shot_is_active


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


@pytest.mark.asyncio
async def test_kadry_replace_keeps_missing_shots_inactive(session: AsyncSession) -> None:
    project = Project(slug="ws1-kadry", topic="t", status=None)  # type: ignore[arg-type]
    session.add(project)
    await session.flush()
    fr = Frame(
        project_id=project.id,
        number=7,
        uuid="kadry-keep-frame",
        voiceover_text="закадр ячейки",
        attrs={
            "кадры": [
                {"id": "7-K1", "порядок": 1, "закадр": "первый"},
                {"id": "7-K2", "порядок": 2, "закадр": "второй"},
                {"id": "7-K3", "порядок": 3, "закадр": "третий"},
            ]
        },
    )
    session.add(fr)
    await session.commit()

    await apply_ops(
        session,
        project,
        [
            {
                "frame_uuid": fr.uuid,
                "fields": {
                    "кадры": [
                        {"id": "7-K1", "порядок": 1, "закадр": "первый обновлён"},
                        {"id": "7-K4", "порядок": 4, "закадр": "новый шот"},
                    ]
                },
            }
        ],
        export_xlsx=False,
    )
    await session.refresh(fr)
    shots = list(fr.attrs["кадры"])
    by_id = {shot["id"]: shot for shot in shots}
    assert set(by_id) == {"7-K1", "7-K2", "7-K3", "7-K4"}
    assert len(shots) == 4
    assert by_id["7-K2"]["закадр"] == "второй"
    assert by_id["7-K2"]["активен"] is False
    assert shot_is_active(by_id["7-K2"]) is False
    assert by_id["7-K3"]["активен"] is False
    assert shot_is_active(by_id["7-K3"]) is False
    assert by_id["7-K1"]["закадр"] == "первый обновлён"
    assert shot_is_active(by_id["7-K1"]) is True
    assert shot_is_active(by_id["7-K4"]) is True
    assert fr.voiceover_text == "закадр ячейки"


@pytest.mark.asyncio
async def test_replace_all_frames_marks_without_renumber(session: AsyncSession) -> None:
    from app.services.montage_board_frames import restore_montage_frame

    project = Project(slug="ws1-replace", topic="t", status=None)  # type: ignore[arg-type]
    session.add(project)
    await session.flush()
    old = Frame(
        project_id=project.id,
        number=4,
        uuid="old-keep-uuid",
        voiceover_text="старый закадр",
        sort_key=4.0,
        attrs={"keep": 1},
    )
    session.add(old)
    await session.commit()
    old_id = int(old.id)

    created = await db_v2.replace_all_frames(
        session,
        project,
        [
            {"voiceover_text": "первая новая ячейка"},
            {"voiceover_text": "вторая новая ячейка"},
        ],
    )
    await session.commit()

    rows = list(
        (await session.execute(select(Frame).where(Frame.project_id == project.id))).scalars()
    )
    assert len(created) == 2
    assert len(rows) == 3
    kept = next(fr for fr in rows if fr.uuid == "old-keep-uuid")
    assert int(kept.id) == old_id
    assert int(kept.number) == 4
    assert float(kept.sort_key) == 4.0
    assert kept.voiceover_text == "старый закадр"
    assert (kept.attrs or {}).get("удалён") is True
    assert (kept.attrs or {}).get("активен") is False
    assert frame_is_active(kept) is False
    assert 4 in deleted_numbers(project)
    assert {int(fr.number) for fr in rows} == {4, int(created[0].number), int(created[1].number)}
    assert int(created[0].number) != 4

    restored = await restore_montage_frame(session, project, old_id)
    await session.refresh(kept)
    assert restored["restored"] == 1
    assert int(kept.number) == 4
    assert kept.voiceover_text == "старый закадр"
    assert frame_is_active(kept) is True
    assert (kept.attrs or {}).get("удалён") is not True
    still = list(
        (await session.execute(select(Frame).where(Frame.project_id == project.id))).scalars()
    )
    assert len(still) == 3
    assert any(fr.uuid == "old-keep-uuid" for fr in still)


def _shots_with(shots: list, *, voice: str, description: str | None = None) -> list[dict]:
    found = []
    for shot in shots:
        if not isinstance(shot, dict) or shot.get("закадр") != voice:
            continue
        if description is not None and shot.get("описание") != description:
            continue
        found.append(shot)
    return found


_MIDDLE = [
    {"порядок": 1, "закадр": "альфа", "описание": "описание альфы"},
    {"порядок": 2, "закадр": "бета", "описание": "описание беты"},
    {"порядок": 3, "закадр": "гамма", "описание": "описание гаммы"},
]


async def _frame_with_kadry(
    session: AsyncSession, project: Project, uuid: str, kadry: list, *, number: int
) -> Frame:
    fr = Frame(
        project_id=project.id,
        number=number,
        uuid=uuid,
        voiceover_text="закадр ячейки",
        attrs={"кадры": [dict(shot) for shot in kadry]},
    )
    session.add(fr)
    await session.flush()
    return fr


@pytest.mark.asyncio
async def test_kadry_middle_insert_and_delete_without_id(session: AsyncSession) -> None:
    """Сдвиг ``порядок`` без id не подменяет и не выбрасывает чужой шот.

    Новый список после вставки не повторяет старое ``описание``: его можно
    сохранить только если старый шот не признан «тем же» по номеру.
    """
    project = Project(slug="ws1-middle-noid", topic="t", status=None)  # type: ignore[arg-type]
    session.add(project)
    await session.flush()
    inserted = await _frame_with_kadry(session, project, "middle-insert", _MIDDLE, number=1)
    deleted = await _frame_with_kadry(session, project, "middle-delete", _MIDDLE, number=2)
    await session.commit()

    await apply_ops(
        session,
        project,
        [
            {
                "frame_uuid": inserted.uuid,
                "fields": {
                    "кадры": [
                        {"порядок": 1, "закадр": "альфа"},
                        {"порядок": 2, "закадр": "вставка", "описание": "описание вставки"},
                        {"порядок": 3, "закадр": "бета"},
                        {"порядок": 4, "закадр": "гамма"},
                    ]
                },
            }
        ],
        export_xlsx=False,
    )
    await session.refresh(inserted)
    shots = list(inserted.attrs["кадры"])
    assert _shots_with(shots, voice="бета", description="описание беты"), shots
    assert _shots_with(shots, voice="гамма", description="описание гаммы"), shots
    assert _shots_with(shots, voice="альфа", description="описание альфы"), shots
    fresh = _shots_with(shots, voice="вставка")
    assert len(fresh) == 1
    assert fresh[0]["описание"] == "описание вставки"
    assert shot_is_active(fresh[0]) is True
    assert all(shot.get("описание") != "описание беты" for shot in fresh)

    await apply_ops(
        session,
        project,
        [
            {
                "frame_uuid": deleted.uuid,
                "fields": {
                    "кадры": [
                        {"порядок": 1, "закадр": "альфа", "описание": "описание альфы"},
                        {"порядок": 2, "закадр": "гамма", "описание": "описание гаммы"},
                    ]
                },
            }
        ],
        export_xlsx=False,
    )
    await session.refresh(deleted)
    shots = list(deleted.attrs["кадры"])
    beta = _shots_with(shots, voice="бета", description="описание беты")
    assert len(beta) == 1, shots
    assert beta[0]["активен"] is False
    assert shot_is_active(beta[0]) is False
    gamma = _shots_with(shots, voice="гамма", description="описание гаммы")
    assert gamma
    assert any(shot_is_active(shot) for shot in gamma)
    assert all(shot.get("закадр") != "бета" for shot in gamma)
    active_gamma = [shot for shot in gamma if shot_is_active(shot)]
    assert active_gamma[0]["описание"] == "описание гаммы"
    assert not _shots_with(
        [shot for shot in shots if shot_is_active(shot)],
        voice="бета",
    )


@pytest.mark.asyncio
async def test_kadry_middle_insert_and_delete_with_id(session: AsyncSession) -> None:
    """Id не сдвигается вместе с ``порядок``: середина не переписывает чужой шот."""
    project = Project(slug="ws1-middle-id", topic="t", status=None)  # type: ignore[arg-type]
    session.add(project)
    await session.flush()
    old = [
        {"id": "K1", "порядок": 1, "закадр": "альфа", "описание": "описание альфы"},
        {"id": "K2", "порядок": 2, "закадр": "бета", "описание": "описание беты"},
        {"id": "K3", "порядок": 3, "закадр": "гамма", "описание": "описание гаммы"},
    ]
    inserted = await _frame_with_kadry(session, project, "middle-id-insert", old, number=1)
    deleted = await _frame_with_kadry(session, project, "middle-id-delete", old, number=2)
    await session.commit()

    await apply_ops(
        session,
        project,
        [
            {
                "frame_uuid": inserted.uuid,
                "fields": {
                    "кадры": [
                        {"id": "K1", "порядок": 1, "закадр": "альфа", "описание": "описание альфы"},
                        {
                            "id": "Knew",
                            "порядок": 2,
                            "закадр": "вставка",
                            "описание": "описание вставки",
                        },
                        {
                            "id": "K2",
                            "порядок": 3,
                            "закадр": "бета",
                            "описание": "описание беты",
                        },
                        {
                            "id": "K3",
                            "порядок": 4,
                            "закадр": "гамма",
                            "описание": "описание гаммы",
                        },
                    ]
                },
            }
        ],
        export_xlsx=False,
    )
    await session.refresh(inserted)
    by_id = {shot["id"]: shot for shot in inserted.attrs["кадры"]}
    assert set(by_id) >= {"K1", "K2", "K3", "Knew"}
    assert by_id["K2"]["закадр"] == "бета"
    assert by_id["K2"]["описание"] == "описание беты"
    assert by_id["Knew"]["закадр"] == "вставка"
    assert by_id["Knew"]["описание"] == "описание вставки"
    assert shot_is_active(by_id["Knew"]) is True
    assert shot_is_active(by_id["K2"]) is True
    assert by_id["K3"]["закадр"] == "гамма"

    await apply_ops(
        session,
        project,
        [
            {
                "frame_uuid": deleted.uuid,
                "fields": {
                    "кадры": [
                        {"id": "K1", "порядок": 1, "закадр": "альфа", "описание": "описание альфы"},
                        {"id": "K3", "порядок": 2, "закадр": "гамма", "описание": "описание гаммы"},
                    ]
                },
            }
        ],
        export_xlsx=False,
    )
    await session.refresh(deleted)
    by_id = {shot["id"]: shot for shot in deleted.attrs["кадры"]}
    assert by_id["K2"]["закадр"] == "бета"
    assert by_id["K2"]["описание"] == "описание беты"
    assert by_id["K2"]["активен"] is False
    assert shot_is_active(by_id["K2"]) is False
    assert by_id["K3"]["закадр"] == "гамма"
    assert by_id["K3"]["описание"] == "описание гаммы"
    assert shot_is_active(by_id["K3"]) is True
    assert by_id["K1"]["закадр"] == "альфа"


@pytest.mark.asyncio
async def test_incoming_inactive_shot_stays_inactive(session: AsyncSession) -> None:
    """``активен: False`` во входящем списке не снимается, пропущенный тоже не оживает."""
    project = Project(slug="ws1-stay-off", topic="t", status=None)  # type: ignore[arg-type]
    session.add(project)
    await session.flush()
    fr = Frame(
        project_id=project.id,
        number=1,
        uuid="stay-off",
        voiceover_text="ячейка",
        attrs={
            "кадры": [
                {"id": "a", "закадр": "альфа"},
                {"id": "b", "закадр": "бета", "активен": False},
                {"id": "d", "закадр": "дельта", "активен": False},
                {"id": "e", "закадр": "эпсилон"},
            ]
        },
    )
    session.add(fr)
    await session.commit()

    await apply_ops(
        session,
        project,
        [
            {
                "frame_uuid": fr.uuid,
                "fields": {
                    "кадры": [
                        {"id": "a", "закадр": "альфа помечена", "активен": False},
                        {"id": "d", "закадр": "дельта новая"},
                        {"id": "e", "закадр": "эпсилон обновлён"},
                    ]
                },
            }
        ],
        export_xlsx=False,
    )
    await session.refresh(fr)
    by_id = {shot["id"]: shot for shot in fr.attrs["кадры"]}
    assert set(by_id) == {"a", "b", "d", "e"}
    assert by_id["a"]["закадр"] == "альфа помечена"
    assert by_id["a"]["активен"] is False
    assert shot_is_active(by_id["a"]) is False
    assert by_id["b"]["закадр"] == "бета"
    assert by_id["b"]["активен"] is False
    assert shot_is_active(by_id["b"]) is False
    assert by_id["d"]["закадр"] == "дельта новая"
    assert by_id["d"]["активен"] is False
    assert shot_is_active(by_id["d"]) is False
    assert by_id["e"]["закадр"] == "эпсилон обновлён"
    assert shot_is_active(by_id["e"]) is True


@pytest.mark.asyncio
async def test_garbage_kadry_returns_old_unchanged(
    session: AsyncSession, warn_log: list[str]
) -> None:
    """``{}`` и не-JSON не список: поле не меняется, живой шот не гаснет.

    Явный ``null`` по-прежнему становится пустым списком и помечает шоты
    неактивными — это отдельная семантика, не мусор.
    """
    project = Project(slug="ws1-nonspec", topic="t", status=None)  # type: ignore[arg-type]
    session.add(project)
    await session.flush()
    stored = [
        {"id": "a", "закадр": "альфа", "порядок": 1},
        {"id": "b", "закадр": "бета", "порядок": 2, "активен": False},
    ]

    async def _frame(uuid: str, number: int) -> Frame:
        fr = Frame(
            project_id=project.id,
            number=number,
            uuid=uuid,
            voiceover_text="ячейка",
            attrs={"кадры": [dict(shot) for shot in stored]},
        )
        session.add(fr)
        return fr

    as_null = await _frame("kadry-null", 1)
    as_obj = await _frame("kadry-obj", 2)
    as_text = await _frame("kadry-text", 3)
    await session.commit()

    await apply_ops(
        session,
        project,
        [
            {"frame_uuid": as_null.uuid, "fields": {"кадры": None}},
            {"frame_uuid": as_obj.uuid, "fields": {"кадры": {}}},
            {"frame_uuid": as_text.uuid, "fields": {"кадры": "не json"}},
        ],
        export_xlsx=False,
    )

    await session.refresh(as_null)
    null_shots = as_null.attrs["кадры"]
    assert isinstance(null_shots, list)
    null_by_id = {shot["id"]: shot for shot in null_shots}
    assert set(null_by_id) == {"a", "b"}
    assert shot_is_active(null_by_id["a"]) is False
    assert shot_is_active(null_by_id["b"]) is False

    for fr in (as_obj, as_text):
        await session.refresh(fr)
        shots = fr.attrs["кадры"]
        assert shots == [dict(shot) for shot in stored]
        by_id = {shot["id"]: shot for shot in shots}
        assert by_id["a"].get("активен") is not False
        assert shot_is_active(by_id["a"]) is True
        assert by_id["b"]["активен"] is False
        assert shot_is_active(by_id["b"]) is False
        assert by_id["a"]["закадр"] == "альфа"
        assert by_id["b"]["закадр"] == "бета"
    assert any("WARN" in line and "кадры" in line for line in warn_log)


@pytest.mark.asyncio
async def test_repeated_same_text_without_id_does_not_grow(session: AsyncSession) -> None:
    """Одинаковый закадр без id не копит неактивные копии на каждом apply.

    Уникальный текст, которого нет в новом списке, остаётся одной неактивной
    копией и на втором прогоне не двоится.
    """
    project = Project(slug="ws1-dedup", topic="t", status=None)  # type: ignore[arg-type]
    session.add(project)
    await session.flush()
    same = {"закадр": "один и тот же"}
    repeated = await _frame_with_kadry(
        session,
        project,
        "dedup-same",
        [{"порядок": 1, **same}, {"порядок": 2, **same}],
        number=1,
    )
    mixed = await _frame_with_kadry(
        session,
        project,
        "dedup-mixed",
        [
            {"порядок": 1, **same},
            {"порядок": 2, **same},
            {"порядок": 3, "закадр": "другой", "описание": "только у него"},
        ],
        number=2,
    )
    await session.commit()
    same_ops = [
        {"порядок": 1, "закадр": "один и тот же"},
        {"порядок": 2, "закадр": "один и тот же"},
    ]

    async def _apply(frame: Frame, kadry: list[dict]) -> list[dict]:
        await apply_ops(
            session,
            project,
            [{"frame_uuid": frame.uuid, "fields": {"кадры": kadry}}],
            export_xlsx=False,
        )
        await session.refresh(frame)
        return list(frame.attrs["кадры"])

    first = await _apply(repeated, same_ops)
    second = await _apply(repeated, same_ops)
    assert len(first) == 2, first
    assert len(second) == len(first)
    assert all(shot_is_active(shot) for shot in second)
    assert [shot["закадр"] for shot in second] == ["один и тот же", "один и тот же"]

    dropped = await _apply(mixed, same_ops)
    again = await _apply(mixed, same_ops)
    assert len(dropped) == 3, dropped
    assert len(again) == len(dropped)
    active = [shot for shot in again if shot_is_active(shot)]
    inactive = [shot for shot in again if not shot_is_active(shot)]
    assert [shot["закадр"] for shot in active] == ["один и тот же", "один и тот же"]
    assert len(inactive) == 1
    assert inactive[0]["закадр"] == "другой"
    assert inactive[0]["описание"] == "только у него"
    assert inactive[0]["активен"] is False


@pytest.mark.asyncio
async def test_replace_all_frames_skips_tombstone_numbers(session: AsyncSession) -> None:
    """Номер из ``deleted_frame_numbers`` без строки кадра не достаётся новому."""
    project = Project(slug="ws1-tomb-num", topic="t", status=None)  # type: ignore[arg-type]
    project.meta = {"deleted_frame_numbers": [3]}
    session.add(project)
    await session.flush()
    old = Frame(
        project_id=project.id,
        number=1,
        uuid="tomb-old",
        voiceover_text="старая ячейка",
        sort_key=1.0,
    )
    session.add(old)
    await session.commit()

    created = await db_v2.replace_all_frames(
        session,
        project,
        [
            {"voiceover_text": "первая новая ячейка"},
            {"voiceover_text": "вторая новая ячейка"},
        ],
    )
    await session.commit()
    numbers = {int(fr.number) for fr in created}
    assert numbers.isdisjoint(deleted_numbers(project)), (numbers, deleted_numbers(project))
    assert 3 not in numbers
    rows = list(
        (await session.execute(select(Frame).where(Frame.project_id == project.id))).scalars()
    )
    assert len(rows) == 3
    assert any(fr.uuid == "tomb-old" and int(fr.number) == 1 for fr in rows)
