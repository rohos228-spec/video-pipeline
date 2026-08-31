"""Повторный ▶ ноды: frame ops не затираются старым scene_registry."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Frame, Project
from app.services import db_apply, db_v2


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
async def test_excel_gpt_place_survives_old_scene_registry(session: AsyncSession) -> None:
    p = Project(slug="stomp", topic="тест", status=None)  # type: ignore[arg-type]
    p.meta = {
        "scene_registry": [
            {
                "id_scene": "scene_01",
                "start_words": "двое крестьян",
                "end_words": "у ворот",
                "место": "подъезд к петербургской канцелярии XVIII века, у тяжёлых ворот",
                "смысл_сцены": "двое крестьян передают прошение у ворот",
            }
        ]
    }
    session.add(p)
    await session.flush()
    fr = Frame(
        project_id=p.id,
        number=1,
        uuid="4d1abd519e744686b938372a",
        voiceover_text="двое крестьян у ворот",
        attrs={
            "place": "подъезд к петербургской канцелярии XVIII века, у тяжёлых ворот",
            "scene_sense": "двое крестьян передают прошение у ворот",
        },
    )
    session.add(fr)
    await session.commit()

    await db_apply.apply_ops(
        session,
        p,
        [
            {
                "frame_uuid": "4d1abd519e744686b938372a",
                "fields": {
                    "место": "дворянская усадьба Российской империи XVIII века с каменным крыльцом",
                    "смысл_сцены": "Строгая помещица стоит у входа в усадьбу как властная хозяйка владений.",
                },
            }
        ],
        node_kind="excel_gpt_no_prompts",
    )
    await session.refresh(fr)
    assert fr.attrs["place"] == (
        "дворянская усадьба Российской империи XVIII века с каменным крыльцом"
    )
    assert fr.attrs["scene_sense"] == (
        "Строгая помещица стоит у входа в усадьбу как властная хозяйка владений."
    )


@pytest.mark.asyncio
async def test_scenes_payload_still_expands_onto_frames(session: AsyncSession) -> None:
    p = Project(slug="expand", topic="тест", status=None)  # type: ignore[arg-type]
    session.add(p)
    await session.flush()
    fr = Frame(
        project_id=p.id,
        number=1,
        uuid="aaaaaaaaaaaaaaaaaaaaaaaa",
        voiceover_text="Утром метро заполнено людьми",
        attrs={},
    )
    session.add(fr)
    await session.commit()

    await db_apply.apply_ops(
        session,
        p,
        scenes=[
            {
                "id_scene": "scene_01",
                "start_words": "Утром метро",
                "end_words": "людьми",
                "место": "метро Токио",
                "смысл_сцены": "вагон полон",
            }
        ],
    )
    await session.refresh(fr)
    assert fr.attrs.get("place") == "метро Токио"
    assert fr.attrs.get("scene_sense") == "вагон полон"
