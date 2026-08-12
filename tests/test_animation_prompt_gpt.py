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
    fr = SimpleNamespace(
        number=3,
        voiceover_text="Hello",
        uuid="P9-F3-deadbeef",
        image_prompt="Noir archive still: man at desk, Action: turns page.",
    )
    item = FrameImageBatchItem(
        frame=fr,
        image_path=Path("/x.png"),
        image_id="[ID: P9-F3-deadbeef]",
        voiceover="Hello",
    )
    msg = build_batch_message([item])
    assert "ID изображения: [ID: P9-F3-deadbeef]" in msg
    assert "frame_uuid: P9-F3-deadbeef" in msg
    assert "Закадровый текст: Hello" in msg
    assert "Промт картинки:" in msg
    assert "turns page" in msg
    assert "apply-ops" in msg
    assert "промт_видео" in msg
    assert "лента" in msg
    assert "Позиция 1" in msg


def test_build_batch_message_text_from_image_prompt_without_png() -> None:
    fr = SimpleNamespace(
        number=1,
        voiceover_text="VO",
        uuid="u1",
        image_prompt="Background: rain street. Action: coat flutters.",
    )
    item = FrameImageBatchItem(
        frame=fr,
        image_path=None,
        image_id="[ID: u1]",
        voiceover="VO",
    )
    msg = build_batch_message([item])
    assert "Промт картинки:" in msg
    assert "coat flutters" in msg
    assert "Картинок/ленты нет" in msg
    assert "Прикреплено изображение-лента" not in msg


def test_parse_animation_reply_no_positional_zip_mixup() -> None:
    """Без ID/JSON нельзя раскладывать чанки по порядку — это путало кадры."""
    from app.services.animation_prompt_gpt import parse_animation_reply

    frames = [
        SimpleNamespace(number=1, uuid="u1", voiceover_text="a", image_prompt=""),
        SimpleNamespace(number=2, uuid="u2", voiceover_text="b", image_prompt=""),
    ]
    batch = [
        FrameImageBatchItem(
            frame=frames[0],
            image_path=Path("/1.png"),
            image_id="[ID: u1]",
            voiceover="a",
        ),
        FrameImageBatchItem(
            frame=frames[1],
            image_path=Path("/2.png"),
            image_id="[ID: u2]",
            voiceover="b",
        ),
    ]
    # Два абзаца без ID — раньше zip клал их в F1/F2 вперемешку с риском ошибки.
    reply = (
        "Slow push on wrong scene, NO VOICE.\n\n"
        "Gentle pan that belonged to another frame, silent."
    )
    pairs = parse_animation_reply(reply, frames, batch_items=batch)
    assert pairs == []


def test_parse_animation_reply_json_apply_ops_all_frames() -> None:
    from app.services.animation_prompt_gpt import parse_animation_reply

    frames = [
        SimpleNamespace(number=1, uuid="P1-F1-aaa", voiceover_text="a", image_prompt=""),
        SimpleNamespace(number=2, uuid="P1-F2-bbb", voiceover_text="b", image_prompt=""),
        SimpleNamespace(number=3, uuid="P1-F3-ccc", voiceover_text="c", image_prompt=""),
    ]
    batch = [
        FrameImageBatchItem(
            frame=frames[i],
            image_path=Path(f"/{i}.png"),
            image_id=f"[ID: {frames[i].uuid}]",
            voiceover=frames[i].voiceover_text,
        )
        for i in range(3)
    ]
    reply = (
        '{"ops":['
        '{"frame_uuid":"P1-F1-aaa","fields":{"промт_видео":"Slow push on hero, NO VOICE."}},'
        '{"frame_uuid":"P1-F2-bbb","fields":{"промт_видео":"Gentle pan across room, silent."}},'
        '{"frame_uuid":"P1-F3-ccc","fields":{"промт_видео":"Dolly in on detail, no speech."}}'
        "]}"
    )
    pairs = parse_animation_reply(reply, frames, batch_items=batch)
    assert [p.frame_number for p in pairs] == [1, 2, 3]
    assert "Slow push" in pairs[0].animation_text
    assert "Gentle pan" in pairs[1].animation_text
    assert not pairs[0].animation_text.startswith("{")


def test_unpack_animation_prompt_blobs_splits_batch_json() -> None:
    from app.services.animation_prompt_gpt import (
        has_animation_prompt_for_frame,
        unpack_animation_prompt_blobs,
    )

    blob = (
        '{"ops":['
        '{"frame_uuid":"u1","fields":{"промт_видео":"Camera A movement, NO VOICE silent."}},'
        '{"frame_uuid":"u2","fields":{"промт_видео":"Camera B pan right, no speech."}},'
        '{"frame_uuid":"u3","fields":{"промт_видео":"Camera C tilt up, mouth idle."}}'
        "]}"
    )
    frames = [
        SimpleNamespace(
            number=1,
            uuid="u1",
            animation_prompt=blob,
            status=FrameStatus.image_prompt_ready,
        ),
        SimpleNamespace(
            number=2,
            uuid="u2",
            animation_prompt=None,
            status=FrameStatus.image_prompt_ready,
        ),
        SimpleNamespace(
            number=3,
            uuid="u3",
            animation_prompt=None,
            status=FrameStatus.image_prompt_ready,
        ),
    ]
    project = SimpleNamespace(id=1)
    assert not has_animation_prompt_for_frame(project, frames[0])  # type: ignore[arg-type]
    n = unpack_animation_prompt_blobs(frames)  # type: ignore[arg-type]
    assert n == 3
    assert has_animation_prompt_for_frame(project, frames[0])  # type: ignore[arg-type]
    assert has_animation_prompt_for_frame(project, frames[1])  # type: ignore[arg-type]
    assert "Camera A" in frames[0].animation_prompt
    assert "Camera B" in frames[1].animation_prompt
    assert not frames[0].animation_prompt.startswith("{")


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
    """Авто-старт без missing — ready, НЕ картинки и НЕ video."""
    from app.settings import settings

    session, project = anim_pr_session
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    data_dir = tmp_path / "videos" / project.slug
    data_dir.mkdir(parents=True)
    (data_dir / "scenes").mkdir()

    status = await start_step(session, project, "anim_pr", explicit_ui_start=False)
    assert status is ProjectStatus.animation_prompts_ready
    assert project.status is ProjectStatus.animation_prompts_ready
    assert project.status is not ProjectStatus.image_prompts_ready
    assert project.status is not ProjectStatus.generating_images
    assert project.status is not ProjectStatus.generating_videos


@pytest.mark.asyncio
async def test_start_step_anim_pr_explicit_reruns_even_if_ready(
    anim_pr_session, tmp_path: Path, monkeypatch
) -> None:
    """Ручной ▶ при готовых промтах: status → generating_*; soft clear без wipe R48/DB.

    Полный wipe animation_prompt — только reset_step / force_wipe=True.
    """
    from app.settings import settings
    from sqlalchemy import select

    from app.models import Frame

    session, project = anim_pr_session
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    data_dir = tmp_path / "videos" / project.slug
    scenes = data_dir / "scenes"
    scenes.mkdir(parents=True)
    (scenes / "frame_001_abcd1234.png").write_bytes(b"x" * 250_000)

    before = (
        await session.execute(select(Frame).where(Frame.project_id == project.id))
    ).scalar_one()
    kept = (before.animation_prompt or "").strip()
    assert kept  # fixture даёт готовый промт

    status = await start_step(session, project, "anim_pr", explicit_ui_start=True)
    assert status is ProjectStatus.generating_animation_prompts
    fr = (
        await session.execute(select(Frame).where(Frame.project_id == project.id))
    ).scalar_one()
    # Soft ▶ сохраняет уже сгенерированные промты (не сжигает пачки).
    assert (fr.animation_prompt or "").strip() == kept


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
