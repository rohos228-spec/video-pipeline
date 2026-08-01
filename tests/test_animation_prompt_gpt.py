"""Парсер и batch-сообщения anim_pr."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, Frame, FrameStatus, Project, ProjectStatus
from app.services.animation_prompt_gpt import (
    FrameImageBatchItem,
    _clean_animation_text,
    build_batch_message,
)
from app.services.project_steps import start_step


def test_clean_animation_text_strips_label() -> None:
    raw = "текст анимации: Camera dolly in slowly."
    assert _clean_animation_text(raw) == "Camera dolly in slowly."


def test_build_batch_message_has_id_and_voiceover() -> None:
    fr = SimpleNamespace(number=3, voiceover_text="Hello")
    item = FrameImageBatchItem(
        frame=fr,
        image_path=Path("/x.png"),
        image_id="[ID: P9-F3-deadbeef]",
        voiceover="Hello",
    )
    msg = build_batch_message([item])
    assert "ID изображения: [ID: P9-F3-deadbeef]" in msg
    assert "Закадровый текст: Hello" in msg
    assert "лента" in msg
    assert "Позиция 1" in msg


@pytest_asyncio.fixture
async def anim_pr_session(tmp_path: Path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'anim_pr.db'}"
    engine = create_async_engine(db_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        p = Project(
            topic="t",
            slug="anim-skip",
            status=ProjectStatus.image_prompts_ready,
            hero_mode="no_hero",
        )
        session.add(p)
        await session.flush()
        session.add(
            Frame(
                project_id=p.id,
                number=1,
                voiceover_text="v",
                image_prompt="ip",
                animation_prompt="already filled " * 2,
                status=FrameStatus.image_prompt_ready,
            )
        )
        await session.commit()
        yield session, p
    await engine.dispose()


@pytest.mark.asyncio
async def test_sync_clears_stale_db_when_xlsx_r48_empty(
    anim_pr_session, tmp_path: Path, monkeypatch
) -> None:
    """Пустой plan R48 → убираем мусорные animation_prompt из БД."""
    from openpyxl import Workbook

    from app.services.animation_prompt_gpt import (
        has_animation_prompt_for_frame,
        scan_missing_animation_prompts,
        sync_animation_prompts_from_xlsx,
    )
    from app.settings import settings

    session, project = anim_pr_session
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    data_dir = tmp_path / "videos" / project.slug
    data_dir.mkdir(parents=True)
    scenes = data_dir / "scenes"
    scenes.mkdir()
    (scenes / "frame_001_test.png").write_bytes(b"x" * 250_000)

    xlsx = data_dir / "project.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "план"
    wb.save(xlsx)

    fr = (await session.execute(
        __import__("sqlalchemy").select(Frame).where(Frame.project_id == project.id)
    )).scalar_one()

    changed = await sync_animation_prompts_from_xlsx(session, project)
    assert changed >= 1
    assert not (fr.animation_prompt or "").strip()
    assert not has_animation_prompt_for_frame(project, fr)
    missing = scan_missing_animation_prompts(project, [fr])
    assert missing == [1]


@pytest.mark.asyncio
async def test_start_step_anim_pr_skips_when_no_missing_on_disk(
    anim_pr_session, tmp_path: Path, monkeypatch
) -> None:
    """Промты в БД есть, картинок на диске нет — не уходим в generating_animation_prompts."""
    from app.settings import settings

    session, project = anim_pr_session
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    data_dir = tmp_path / "videos" / project.slug
    data_dir.mkdir(parents=True)
    (data_dir / "scenes").mkdir()

    status = await start_step(session, project, "anim_pr")
    assert status is not ProjectStatus.generating_animation_prompts
    assert project.status is not ProjectStatus.generating_animation_prompts


@pytest.mark.asyncio
async def test_start_step_anim_pr_runs_when_only_shot2_missing(
    anim_pr_session, tmp_path: Path, monkeypatch
) -> None:
    """shot_01 готов, shot_02 нужен — ▶ anim_pr не должен пропускать."""
    import app.services.animation_prompt_gpt as apg
    from app.settings import settings

    session, project = anim_pr_session
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    data_dir = tmp_path / "videos" / project.slug
    scenes = data_dir / "scenes"
    scenes.mkdir(parents=True)
    (scenes / "frame_001_abcd1234.png").write_bytes(b"x" * 250_000)
    (scenes / "frame_001_s2_efgh5678.png").write_bytes(b"y" * 250_000)

    monkeypatch.setattr(
        apg,
        "frame_needs_shot2_video_prompt",
        lambda _project, fr: fr.number == 1,
    )

    status = await start_step(session, project, "anim_pr")
    assert status is ProjectStatus.generating_animation_prompts
    assert project.status is ProjectStatus.generating_animation_prompts


def test_build_apply_ops_from_pairs_maps_uuid_and_field() -> None:
    from app.services.animation_prompt_gpt import (
        ParsedAnimationPair,
        build_apply_ops_from_pairs,
    )

    frames = [
        SimpleNamespace(number=1, uuid="u1"),
        SimpleNamespace(number=2, uuid="u2"),
    ]
    pairs = [
        ParsedAnimationPair(
            image_id="a", animation_text="dolly in slowly", frame_number=1
        ),
        ParsedAnimationPair(
            image_id="b", animation_text="pan right across", frame_number=2
        ),
        ParsedAnimationPair(image_id="c", animation_text="x", frame_number=1),
        ParsedAnimationPair(image_id="d", animation_text="no frame", frame_number=None),
    ]
    ops = build_apply_ops_from_pairs(pairs, frames, shot=1)
    assert ops == [
        {"frame_uuid": "u1", "fields": {"промт_видео": "dolly in slowly"}},
        {"frame_uuid": "u2", "fields": {"промт_видео": "pan right across"}},
    ]
    ops2 = build_apply_ops_from_pairs(pairs, frames, shot=2)
    assert ops2[0]["fields"] == {"промт_видео_2": "dolly in slowly"}
