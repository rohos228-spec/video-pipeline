"""End-to-End verification test suite for incident #14 bugfixes."""

from __future__ import annotations

import uuid
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Artifact, ArtifactKind, Base, Frame, FrameStatus, Project, ProjectStatus
from app.services.project_state import compute_actual_status
from app.orchestrator.steps.generate_images import (
    INFLIGHT_ATTR,
    _claim_shot1_batch,
    _clear_stale_inflight,
    _pending_shot1_numbers,
)


@pytest_asyncio.fixture
async def db_session(tmp_path):
    db_file = tmp_path / "test_e2e.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_full_ladder_status_progression(db_session) -> None:
    """Test all stages from frames to assembled to ensure monotonic, rollback-free progression."""
    project = Project(
        topic="Генерация фильма",
        slug="movie-e2e",
        script_text="Сценарий",
        general_plan="План" * 100,
        status=ProjectStatus.frames_ready,
        meta={},
    )
    db_session.add(project)
    await db_session.commit()
    await db_session.refresh(project)

    # 5 frames
    for i in range(1, 6):
        f = Frame(
            project_id=project.id,
            number=i,
            voiceover_text=f"Текст {i}",
            image_prompt=f"Промпт {i}",
            animation_prompt=f"Анимация {i}",
        )
        db_session.add(f)
    await db_session.commit()

    # 1. All image prompts ready -> image_prompts_ready
    st1 = await compute_actual_status(db_session, project)
    assert st1 == ProjectStatus.image_prompts_ready

    # 2. Add 5 scene images -> images_ready (if animation prompt not completed) or animation_prompts_ready
    for i in range(1, 6):
        db_session.add(
            Artifact(
                project_id=project.id,
                kind=ArtifactKind.scene_image,
                uuid=str(uuid.uuid4()),
                path=f"/fake/img_{i}.png",
            )
        )
    await db_session.commit()

    st2 = await compute_actual_status(db_session, project)
    assert st2 == ProjectStatus.animation_prompts_ready

    # 3. Add 5 scene videos -> videos_ready
    for i in range(1, 6):
        db_session.add(
            Artifact(
                project_id=project.id,
                kind=ArtifactKind.scene_video,
                uuid=str(uuid.uuid4()),
                path=f"/fake/vid_{i}.mp4",
            )
        )
    await db_session.commit()

    st3 = await compute_actual_status(db_session, project)
    assert st3 == ProjectStatus.videos_ready

    # 4. Add audio -> audio_ready
    db_session.add(
        Artifact(
            project_id=project.id,
            kind=ArtifactKind.audio,
            uuid=str(uuid.uuid4()),
            path="/fake/voice.mp3",
        )
    )
    await db_session.commit()

    st4 = await compute_actual_status(db_session, project)
    assert st4 == ProjectStatus.audio_ready

    # 5. Add final video -> assembled
    db_session.add(
        Artifact(
            project_id=project.id,
            kind=ArtifactKind.final_video,
            uuid=str(uuid.uuid4()),
            path="/fake/final.mp4",
        )
    )
    await db_session.commit()

    st5 = await compute_actual_status(db_session, project)
    assert st5 == ProjectStatus.assembled


@pytest.mark.asyncio
async def test_recovery_from_stale_inflight_under_failure(db_session, tmp_path) -> None:
    """Simulate worker crash leaving all frames with INFLIGHT_ATTR and verify recovery."""
    project = Project(
        topic="Краш тест",
        slug="crash-test",
        status=ProjectStatus.generating_images,
        meta={},
    )
    db_session.add(project)
    await db_session.commit()
    await db_session.refresh(project)

    scenes_dir = tmp_path / "scenes"
    scenes_dir.mkdir(parents=True, exist_ok=True)

    # 10 frames with stuck inflight from previous failed run
    for i in range(1, 11):
        f = Frame(
            project_id=project.id,
            number=i,
            voiceover_text=f"Текст {i}",
            image_prompt=f"Промпт {i}",
            status=FrameStatus.image_prompt_ready,
            attrs={INFLIGHT_ATTR: True},
        )
        db_session.add(f)
    await db_session.commit()

    # Step 1: Batch cannot be claimed because all are inflight
    batch = await _claim_shot1_batch(db_session, project.id, scenes_dir, limit=5)
    assert len(batch) == 0

    # Step 2: Pending finds all 10
    pending = await _pending_shot1_numbers(db_session, project.id, scenes_dir)
    assert len(pending) == 10

    # Step 3: Clear stale inflight
    cleared = await _clear_stale_inflight(db_session, project.id)
    await db_session.commit()
    assert cleared == 10

    # Step 4: Now claim works immediately!
    batch_after = await _claim_shot1_batch(db_session, project.id, scenes_dir, limit=5)
    assert len(batch_after) == 5
    assert [f.number for f in batch_after] == [1, 2, 3, 4, 5]