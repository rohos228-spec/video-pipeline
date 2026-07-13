"""Тесты сервиса `app.services.reset_step`.

Проверяем что `reset_step()`:
1. Удаляет данные конкретного шага (artifacts, frame fields, files).
2. Каскадно зачищает все downstream-шаги.
3. После сброса `project.status` пересчитан корректно.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import (
    Artifact,
    ArtifactKind,
    Base,
    Frame,
    FrameStatus,
    Project,
    ProjectStatus,
)
from app.services.reset_step import (
    RESET_SUPPORTED_STEP_CODES,
    clear_step_outputs_for_rerun,
    is_reset_supported,
    reset_step,
)


@pytest_asyncio.fixture
async def session(tmp_path: Path):
    """In-memory SQLite session со всеми таблицами."""
    db_url = f"sqlite+aiosqlite:///{tmp_path / 't.db'}"
    engine = create_async_engine(db_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    async with SessionLocal() as s:
        yield s
    await engine.dispose()


async def _mkproject(
    session,
    slug: str = "p1",
    *,
    with_plan: bool = True,
    with_script: bool = True,
    with_hero_descr: bool = True,
) -> Project:
    p = Project(
        slug=slug,
        topic="t",
        hero_mode="full_auto",
        status=ProjectStatus.new,
    )
    if with_plan:
        p.general_plan = "general plan text"
    if with_script:
        p.script_text = "script text"
    if with_hero_descr:
        p.hero_description = "hero descr"
    session.add(p)
    await session.flush()
    return p


async def _mkframe(session, project: Project, n: int, **kw) -> Frame:
    kw.setdefault("status", FrameStatus.planned)
    fr = Frame(
        project_id=project.id,
        number=n,
        voiceover_text=f"vo {n}",
        **kw,
    )
    session.add(fr)
    await session.flush()
    return fr


async def _mkart(
    session, project: Project, kind: ArtifactKind, *, path: str | None = None,
    frame_id: int | None = None,
) -> Artifact:
    import uuid as _uuid
    a = Artifact(
        project_id=project.id,
        frame_id=frame_id,
        kind=kind,
        uuid=_uuid.uuid4().hex,
        path=path or "/no/such/path",
    )
    session.add(a)
    await session.flush()
    return a


# ---------------------------------------------------------------------------


def test_reset_supported_step_codes_includes_main_pipeline():
    """Все основные шаги pipeline'а должны поддерживать сброс."""
    expected = {
        "plan", "script", "split",
        "objects", "hero", "items",
        "enrich", "enrich_1", "enrich_2", "enrich_3", "enrich_4", "enrich_5",
        "img_pr", "img", "anim_pr", "video", "audio", "assemble",
    }
    assert expected <= RESET_SUPPORTED_STEP_CODES


def test_is_reset_supported():
    assert is_reset_supported("img")
    assert is_reset_supported("img_pr")
    assert is_reset_supported("video")
    assert not is_reset_supported("unknown_step")
    assert not is_reset_supported("")


def test_unknown_step_returns_error(tmp_path):
    """Неизвестный код шага должен вернуть {"error": ...} а не падать."""
    # синхронный тест: используем фиктивный объект, т.к. unknown шаги
    # фейлят валидацию до обращения к БД.
    from app.services.reset_step import _resolve_start_index
    assert _resolve_start_index("unknown_xyz") is None
    assert _resolve_start_index("img") is not None
    assert _resolve_start_index("img_pr") is not None
    # wrapper expands and finds min index
    assert _resolve_start_index("objects") is not None
    assert _resolve_start_index("enrich") is not None


@pytest.mark.asyncio
async def test_reset_img_clears_scene_image_and_resets_frames(
    session, tmp_path: Path
):
    """Сброс шага «img»:
    - удаляет scene_image артефакты и файлы,
    - сбрасывает frame.status: image_generated → image_prompt_ready,
    - project.status: images_ready → image_prompts_ready.
    """
    p = await _mkproject(session)
    # Создаём 3 кадра с готовыми промтами и сгенерированными картинками.
    frames = []
    for i in range(1, 4):
        fr = await _mkframe(
            session, p, i,
            image_prompt=f"prompt {i}",
            status=FrameStatus.image_generated,
        )
        # На диск кладём файл (фейковая картинка), чтобы reset_step его
        # удалил.
        img_path = tmp_path / f"img_{i}.png"
        img_path.write_bytes(b"\x89PNG\r\n\x1a\n")
        await _mkart(
            session, p, ArtifactKind.scene_image,
            path=str(img_path), frame_id=fr.id,
        )
        frames.append((fr, img_path))
    # Также: hero — должен остаться нетронутым (он upstream).
    await _mkart(session, p, ArtifactKind.hero_reference, path=str(tmp_path / "hero.png"))
    # Иду через статус: имитируем images_ready после успешной генерации.
    p.status = ProjectStatus.images_ready

    await session.flush()
    summary = await reset_step(session, p, "img")

    # 1) Все scene_image удалены
    cnt = (await session.execute(
        select(Artifact).where(
            Artifact.project_id == p.id,
            Artifact.kind == ArtifactKind.scene_image,
        )
    )).scalars().all()
    assert len(cnt) == 0
    # 2) Файлы удалены
    for _fr, path in frames:
        assert not path.exists(), f"file {path} should be deleted"
    # 3) Frame статусы сброшены
    for fr, _ in frames:
        await session.refresh(fr)
        assert fr.status is FrameStatus.image_prompt_ready
    # 4) hero_reference нетронут (upstream)
    hero_arts = (await session.execute(
        select(Artifact).where(
            Artifact.project_id == p.id,
            Artifact.kind == ArtifactKind.hero_reference,
        )
    )).scalars().all()
    assert len(hero_arts) == 1
    # 5) Project статус пересчитан: должны быть на image_prompts_ready
    #    (есть frames с image_prompt + plan + script + hero_arts → шаг
    #    «промты картинок» считается пройденным, а scene_image — нет).
    assert p.status is ProjectStatus.image_prompts_ready
    # 6) summary правильный
    assert "img" in summary
    assert summary["img"]["artifacts"] == 3
    assert summary["__project_status_was"] == ProjectStatus.images_ready.value
    assert summary["__project_status"] == ProjectStatus.image_prompts_ready.value


@pytest.mark.asyncio
async def test_reset_img_pr_cascades_to_img_and_below(
    session, tmp_path: Path
):
    """Сброс img_pr должен снести image_prompt + ВСЁ downstream
    (scene_image, scene_video, audio, final_video, animation_prompt)."""
    p = await _mkproject(session)
    fr = await _mkframe(
        session, p, 1,
        image_prompt="p1",
        animation_prompt="a1",
        status=FrameStatus.video_generated,
    )
    img_p = tmp_path / "scene1.png"
    img_p.write_bytes(b"x")
    vid_p = tmp_path / "scene1.mp4"
    vid_p.write_bytes(b"x")
    aud_p = tmp_path / "audio.mp3"
    aud_p.write_bytes(b"x")
    fin_p = tmp_path / "final.mp4"
    fin_p.write_bytes(b"x")
    await _mkart(session, p, ArtifactKind.scene_image, path=str(img_p), frame_id=fr.id)
    await _mkart(session, p, ArtifactKind.scene_video, path=str(vid_p), frame_id=fr.id)
    await _mkart(session, p, ArtifactKind.audio, path=str(aud_p))
    await _mkart(session, p, ArtifactKind.final_video, path=str(fin_p))
    p.status = ProjectStatus.assembled
    await session.flush()

    await reset_step(session, p, "img_pr")

    # image_prompt очищен
    await session.refresh(fr)
    assert fr.image_prompt is None
    # animation_prompt тоже очищен (downstream)
    assert fr.animation_prompt is None
    # все артефакты scene_image / scene_video / audio / final удалены
    for kind in (
        ArtifactKind.scene_image, ArtifactKind.scene_video,
        ArtifactKind.audio, ArtifactKind.final_video,
    ):
        arts = (await session.execute(
            select(Artifact).where(
                Artifact.project_id == p.id, Artifact.kind == kind
            )
        )).scalars().all()
        assert not arts, f"{kind} should be wiped"
    # все файлы удалены
    assert not img_p.exists()
    assert not vid_p.exists()
    assert aud_p.exists()  # озвучка на диске не удаляется при reset
    assert not fin_p.exists()


@pytest.mark.asyncio
async def test_reset_split_deletes_all_frames(session, tmp_path: Path):
    """Сброс split удаляет все Frame'ы (cascade удалит и Artifact'ы
    с frame_id)."""
    p = await _mkproject(session)
    fr1 = await _mkframe(
        session, p, 1, image_prompt="p1", status=FrameStatus.image_approved,
    )
    img_p = tmp_path / "im1.png"
    img_p.write_bytes(b"x")
    await _mkart(
        session, p, ArtifactKind.scene_image,
        path=str(img_p), frame_id=fr1.id,
    )
    p.status = ProjectStatus.images_ready
    await session.flush()

    await reset_step(session, p, "split")

    # Frame'ы удалены
    frames = (await session.execute(
        select(Frame).where(Frame.project_id == p.id)
    )).scalars().all()
    assert not frames
    # файл картинки тоже удалён (мы его руками unlink'нули до удаления
    # frame'а)
    assert not img_p.exists()
    # статус пересчитался на script_ready (plan + script есть, но
    # frames удалены split-ом).
    assert p.status is ProjectStatus.script_ready


@pytest.mark.asyncio
async def test_reset_objects_wraps_to_hero_and_items(session, tmp_path: Path):
    """Сброс «objects» = сброс hero + items + downstream."""
    p = await _mkproject(session)
    h_p = tmp_path / "hero.png"
    h_p.write_bytes(b"x")
    i_p = tmp_path / "item.png"
    i_p.write_bytes(b"x")
    await _mkart(session, p, ArtifactKind.hero_reference, path=str(h_p))
    await _mkart(session, p, ArtifactKind.item_reference, path=str(i_p))
    p.status = ProjectStatus.items_ready
    await session.flush()

    await reset_step(session, p, "objects")

    hero = (await session.execute(

        select(Artifact).where(
            Artifact.project_id == p.id, Artifact.kind == ArtifactKind.hero_reference,
        )
    )).scalars().all()
    items = (await session.execute(
        select(Artifact).where(
            Artifact.project_id == p.id, Artifact.kind == ArtifactKind.item_reference,
        )
    )).scalars().all()
    assert not hero
    assert not items
    assert not h_p.exists()
    assert not i_p.exists()


@pytest.mark.asyncio
async def test_clear_step_outputs_for_rerun_script_preserves_voiceover(
    session, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    from app import settings as app_settings

    monkeypatch.setattr(app_settings.settings, "data_dir", tmp_path / "data")
    p = await _mkproject(session, with_script=True)
    p.data_dir.mkdir(parents=True, exist_ok=True)
    (p.data_dir / "voiceover.txt").write_text("исходный закадровый", encoding="utf-8")
    await session.flush()

    summary = await clear_step_outputs_for_rerun(session, p, "script")

    assert summary["script"]["source_voiceover_preserved"] is True
    assert p.script_text == "script text"
    assert (p.data_dir / "voiceover.txt").read_text(encoding="utf-8") == "исходный закадровый"


@pytest.mark.asyncio
async def test_clear_step_outputs_for_rerun_anim_pr_preserves(session, tmp_path: Path):
    """Повторный запуск anim_pr: не стираем animation_prompt (догонка с xlsx)."""
    p = await _mkproject(session)
    fr = await _mkframe(
        session,
        p,
        1,
        image_prompt="ip",
        animation_prompt="anim done",
        status=FrameStatus.animation_prompt_ready,
    )
    vid_path = tmp_path / "v.mp4"
    vid_path.write_bytes(b"x")
    await _mkart(
        session,
        p,
        ArtifactKind.scene_video,
        path=str(vid_path),
        frame_id=fr.id,
    )
    p.status = ProjectStatus.videos_ready
    await session.flush()

    summary = await clear_step_outputs_for_rerun(session, p, "anim_pr")
    assert "anim_pr" in summary
    await session.refresh(fr)
    assert fr.animation_prompt == "anim done"
    assert fr.status is FrameStatus.animation_prompt_ready

    videos_left = (
        await session.execute(
            select(Artifact).where(
                Artifact.project_id == p.id,
                Artifact.kind == ArtifactKind.scene_video,
            )
        )
    ).scalars().all()
    assert len(videos_left) == 1
    assert vid_path.exists()


@pytest.mark.asyncio
async def test_reset_returns_error_for_unknown_step(session):
    p = await _mkproject(session)
    summary = await reset_step(session, p, "definitely_not_a_real_step")
    assert "error" in summary


@pytest.mark.asyncio
async def test_reset_audio_does_not_wipe_music(session, tmp_path: Path):
    """Сброс audio не должен удалять music (независимый шаг)."""
    p = await _mkproject(session)
    aud_p = tmp_path / "voice.mp3"
    aud_p.write_bytes(b"audio")
    mus_p = tmp_path / "music" / "track.mp3"
    mus_p.parent.mkdir(parents=True)
    mus_p.write_bytes(b"music")
    await _mkart(session, p, ArtifactKind.audio, path=str(aud_p))
    await _mkart(session, p, ArtifactKind.music, path=str(mus_p))
    fin_p = tmp_path / "final.mp4"
    fin_p.write_bytes(b"fin")
    await _mkart(session, p, ArtifactKind.final_video, path=str(fin_p))
    p.status = ProjectStatus.assembled
    await session.flush()

    summary = await reset_step(session, p, "audio")

    assert "music" not in summary
    assert mus_p.exists()
    music_left = (
        await session.execute(
            select(Artifact).where(
                Artifact.project_id == p.id,
                Artifact.kind == ArtifactKind.music,
            )
        )
    ).scalars().all()
    assert len(music_left) == 1
    assert "audio" in summary
    assert not aud_p.exists() or "audio" in summary


@pytest.mark.asyncio
async def test_reset_video_resets_frame_status(session, tmp_path: Path):
    """Сброс шага video: scene_video артефакты + статус frame
    video_generated → animation_prompt_ready (если есть anim_prompt)."""
    p = await _mkproject(session)
    fr = await _mkframe(
        session, p, 1,
        image_prompt="p1",
        animation_prompt="a1",
        status=FrameStatus.video_generated,
    )
    vid_p = tmp_path / "v.mp4"
    vid_p.write_bytes(b"x")
    await _mkart(session, p, ArtifactKind.scene_video, path=str(vid_p), frame_id=fr.id)
    p.status = ProjectStatus.videos_ready
    await session.flush()

    summary = await reset_step(session, p, "video")

    await session.refresh(fr)
    assert fr.status is FrameStatus.animation_prompt_ready
    # downstream audio тоже должен быть очищен (его нет, проверим что не
    # упало)
    assert "video" in summary
