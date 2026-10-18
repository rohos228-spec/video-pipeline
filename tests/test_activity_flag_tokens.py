"""Одинаковые значения «активен» в фильтре LLM и в claim картинки.

Числовая строка равна числу: «ложь», «0.0», 0.0 и False — пропуск.
«-» для activity_flags неизвестен, флага нет, шот живой.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Frame, FrameStatus, Project, ProjectStatus
from app.orchestrator.validators.activity_flags import frame_is_active, shot_is_active
from app.services.scene_design.assemble_chunks import filter_assembly_input_for_frames

_FLAGS = ["ложь", "0.0", 0.0, "-", False]


@pytest.fixture
async def session(tmp_path: Path) -> AsyncSession:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'flags.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("flag", _FLAGS, ids=["ложь", "str-0.0", "float-0.0", "dash", "false"])
async def test_activity_tokens_skip_llm_and_claim_like_activity_flags(
    session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, flag: object
) -> None:
    from app.orchestrator.steps.generate_images import _claim_shot1_batch

    live = shot_is_active({"id": "s-flag", "активен": flag})
    assert frame_is_active({"активен": flag}) is live

    vo = "Альфа один."
    frame = SimpleNamespace(
        number=1,
        uuid="a",
        voiceover_text=vo,
        duration_seconds=5.0,
        attrs={},
    )
    shot = {
        "uuid": "a",
        "id": "s-flag",
        "время_сек": 5,
        "закадр": "Альфа",
        "активен": flag,
    }
    assembly = {
        "characters": [],
        "locations": [],
        "style_arc": [],
        "scenes_chrono": [
            {
                "id_scene": "scene_live",
                "start_words": "Альфа один",
                "end_words": "Альфа один",
                "кадры": [shot],
            }
        ],
        "shot_plan_chrono": [
            {"цитата": "Альфа один", "набор": "SET_FLAG", "активен": flag}
        ],
    }
    chunk = filter_assembly_input_for_frames(
        assembly, [frame], [frame], vo, include_characters=False
    )
    kid_ids = [
        kid.get("id")
        for scene in chunk["scenes_chrono"]
        for kid in scene.get("кадры") or []
    ]
    plan_ids = [item.get("набор") for item in chunk["shot_plan_chrono"]]
    assert ("s-flag" in kid_ids) is live
    assert ("SET_FLAG" in plan_ids) is live
    assert assembly["scenes_chrono"][0]["кадры"][0]["id"] == "s-flag"
    assert assembly["scenes_chrono"][0]["кадры"][0]["активен"] == flag

    data_root = tmp_path / "data"
    data_root.mkdir()
    monkeypatch.setattr("app.settings.settings.data_dir", str(data_root))
    project = Project(
        slug=f"flags-{abs(hash(str(flag))) % 10_000}",
        topic="t",
        hero_mode="auto",
        status=ProjectStatus.frames_ready,
        meta={},
    )
    session.add(project)
    await session.flush()
    row = Frame(
        project_id=project.id,
        number=1,
        uuid="aa" * 12,
        voiceover_text=vo,
        image_prompt="промт",
        status=FrameStatus.planned,
        attrs={"активен": flag},
    )
    session.add(row)
    await session.flush()
    scenes = tmp_path / "scenes"
    scenes.mkdir()
    claimed = await _claim_shot1_batch(
        session, project.id, scenes, project=project, limit=4
    )
    assert ([fr.number for fr in claimed] == [1]) is live
    left = (
        await session.execute(select(Frame).where(Frame.project_id == project.id))
    ).scalars().all()
    assert [fr.number for fr in left] == [1]
    assert left[0].image_prompt == "промт"
