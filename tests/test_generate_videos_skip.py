"""Повторный запуск шага video не должен пропускать кадры без файла клипа."""

from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, Frame, FrameStatus, Project, ProjectStatus
from app.orchestrator.steps.generate_videos import (
    _claim_shot1_video_batch,
    _skip_frame_video_generation,
    resolve_scene_image_path,
)


def _frame(status: FrameStatus) -> Frame:
    return Frame(id=1, project_id=1, number=1, status=status)


def test_video_generated_without_file_not_skipped() -> None:
    fr = _frame(FrameStatus.video_generated)
    assert _skip_frame_video_generation(fr, has_video_file=False) is False


def test_video_generated_with_file_skipped() -> None:
    fr = _frame(FrameStatus.video_generated)
    assert _skip_frame_video_generation(fr, has_video_file=True) is True


def test_video_approved_always_skipped() -> None:
    fr = _frame(FrameStatus.video_approved)
    assert _skip_frame_video_generation(fr, has_video_file=False) is True


def test_resolve_scene_image_falls_back_to_disk(tmp_path) -> None:
    scenes = tmp_path / "scenes"
    scenes.mkdir()
    png = scenes / "frame_007_ea970ac6.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 250_000)
    stale = str(scenes / "frame_007_s2_760fbcbb.png")
    got = resolve_scene_image_path(
        artifact_path=stale,
        scenes_dir=scenes,
        frame_number=7,
    )
    assert got == png


def test_resolve_scene_image_rejects_s2_even_when_file_exists(tmp_path) -> None:
    """Регрессия: shot_01 video не должен брать frame_*_s2_*.png из артефакта БД."""
    scenes = tmp_path / "scenes"
    scenes.mkdir()
    shot1 = scenes / "frame_007_ea970ac6.png"
    shot1.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 250_000)
    shot2 = scenes / "frame_007_s2_760fbcbb.png"
    shot2.write_bytes(b"\x89PNG\r\n\x1a\n" + b"y" * 250_000)
    got = resolve_scene_image_path(
        artifact_path=str(shot2),
        scenes_dir=scenes,
        frame_number=7,
    )
    assert got == shot1
    assert "_s2_" not in got.name


def test_effective_shot_from_artifact_path_wins_over_meta() -> None:
    from app.services.plan_shot2 import effective_shot_from_artifact

    path = Path("clip_001_s2_abc.mp4")
    assert effective_shot_from_artifact({"shot": 1}, path) == 2


@pytest.mark.asyncio
async def test_claim_shot1_skips_disk_clip_without_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Клип на диске без Artifact — не генерировать заново."""
    monkeypatch.setattr("app.settings.settings.data_dir", tmp_path)
    db_path = tmp_path / "claim.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            project = Project(
                slug="clipskip",
                topic="t",
                status=ProjectStatus.generating_videos,
                meta={},
            )
            session.add(project)
            await session.flush()
            fr = Frame(
                project_id=project.id,
                number=3,
                voiceover_text="vo",
                animation_prompt="slow pan across the room",
                status=FrameStatus.animation_prompt_ready,
                attrs={},
            )
            session.add(fr)
            await session.flush()
            videos = project.data_dir / "videos"
            videos.mkdir(parents=True)
            (videos / "clip_003_orphanhash.mp4").write_bytes(b"x" * 5000)
            claimed = await _claim_shot1_video_batch(session, project.id, limit=4)
            assert claimed == []
            await session.refresh(fr)
            assert fr.status is FrameStatus.video_generated
    finally:
        await engine.dispose()


def test_archive_older_frame_clips_keeps_newest_and_other_shot(tmp_path: Path) -> None:
    from app.services.artifact_recovery import archive_older_frame_clips

    videos = tmp_path / "videos"
    videos.mkdir()
    old = videos / "clip_001_oldhash99.mp4"
    keep = videos / "clip_001_newhash01.mp4"
    s2 = videos / "clip_001_s2_zzzzzzzz.mp4"
    old.write_bytes(b"OLD" * 400)
    keep.write_bytes(b"NEW" * 400)
    s2.write_bytes(b"S2" * 400)

    n = archive_older_frame_clips(videos, 1, shot=1, keep=keep)
    assert n >= 1
    assert keep.is_file()
    assert not old.exists()
    assert s2.is_file()
    archived = list((tmp_path / "old" / "videos").iterdir())
    assert any("clip_001_oldhash99.mp4" in p.name for p in archived)
