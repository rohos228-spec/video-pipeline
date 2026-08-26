"""Montage regen: API-путь без CDP + промты из БД."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Frame, Project, PromptVersion
from app.services import db_v2
from app.services.montage_board_regen import (
    ImageRegenPrep,
    VideoRegenPrep,
    execute_image_regen,
    execute_video_regen,
    majority_scene_image_model,
    prepare_image_regen,
    resolve_image_prompt,
    resolve_video_prompt,
)


@pytest.fixture
async def session(tmp_path: Path) -> AsyncSession:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'regen.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await db_v2.migrate_db_v2_schema(conn)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Project:
    data_root = tmp_path / "data"
    data_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("app.settings.settings.data_dir", str(data_root))
    return Project(id=55, slug="regen-api", topic="t", hero_mode="auto")


@pytest.mark.asyncio
async def test_resolve_image_prompt_prefers_active_version(
    session: AsyncSession, project: Project
) -> None:
    session.add(project)
    fr = Frame(
        project_id=project.id,
        number=1,
        voiceover_text="v",
        image_prompt="из Frame",
    )
    session.add(fr)
    await session.flush()
    session.add(
        PromptVersion(
            project_id=project.id,
            frame_id=fr.id,
            kind="img",
            version=2,
            text="из prompt_versions",
            is_active=True,
        )
    )
    await session.commit()

    text = await resolve_image_prompt(session, project, fr, 1)
    assert text == "из prompt_versions"


def test_majority_scene_image_model_prefers_originals(tmp_path: Path) -> None:
    scenes = tmp_path / "scenes"
    scenes.mkdir()
    for i, model in enumerate(["gpt-image-2"] * 4 + ["nano-banana-2"], start=1):
        (scenes / f"frame_{i:03d}_aaaa.json").write_text(
            f'{{"model": "{model}"}}', encoding="utf-8"
        )
    assert majority_scene_image_model(scenes) == "gpt-image-2"


@pytest.mark.asyncio
async def test_edit_prompt_writes_db_without_excel(
    session: AsyncSession, project: Project, tmp_path: Path
) -> None:
    session.add(project)
    fr = Frame(project_id=project.id, number=1, voiceover_text="v", image_prompt="old")
    session.add(fr)
    await session.flush()
    (project.data_dir / "scenes").mkdir(parents=True, exist_ok=True)

    prep = await prepare_image_regen(
        session,
        project,
        1,
        shot=1,
        mode="edit_prompt",
        new_prompt="новый промт API",
    )
    await session.commit()
    assert prep.prompt_text == "новый промт API"

    fr2 = await session.get(Frame, fr.id)
    assert fr2 is not None and fr2.image_prompt == "новый промт API"
    pvs = (
        await session.execute(
            select(PromptVersion).where(
                PromptVersion.frame_id == fr.id, PromptVersion.kind == "img"
            )
        )
    ).scalars().all()
    assert any(p.is_active and p.text == "новый промт API" for p in pvs)


@pytest.mark.asyncio
async def test_prepare_uses_original_scene_model_not_project(
    session: AsyncSession, project: Project
) -> None:
    project.image_generator = "nano_banana_2"
    session.add(project)
    fr = Frame(project_id=project.id, number=1, voiceover_text="v", image_prompt="p")
    session.add(fr)
    await session.flush()
    scenes = project.data_dir / "scenes"
    scenes.mkdir(parents=True, exist_ok=True)
    for i in range(1, 5):
        (scenes / f"frame_{i:03d}_orig.json").write_text(
            '{"model": "gpt-image-2"}', encoding="utf-8"
        )
    prep = await prepare_image_regen(
        session, project, 1, shot=1, mode="same_prompt"
    )
    assert prep.model_slug == "gpt-image-2"


@pytest.mark.asyncio
async def test_execute_image_regen_api_skips_cdp(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    called: dict[str, object] = {}

    async def _fake_gen(outsee, gpt, **kwargs):
        called["outsee_type"] = type(outsee).__name__
        called["prompt"] = kwargs.get("prompt")
        out = Path(kwargs["out_path"])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"x" * 220_000)
        res = MagicMock()
        res.file_path = out
        return res

    monkeypatch.setattr(
        "app.services.montage_board_regen._image_api_enabled", lambda: True
    )
    monkeypatch.setattr(
        "app.services.montage_board_regen.generate_image_with_retries", _fake_gen
    )
    # Если кто-то снова импортирует browser_session — тест взорвётся.
    import sys
    import types

    fake_browser = types.ModuleType("app.bots.browser")

    async def _boom_session(*_a, **_k):
        raise AssertionError("browser_session не должен вызываться")

    fake_browser.browser_session = _boom_session  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "app.bots.browser", fake_browser)

    prep = ImageRegenPrep(
        project_id=1,
        frame_number=1,
        shot=1,
        prompt_text="api prompt",
        file_path=tmp_path / "frame_001_abc.png",
        prompt_id_prefix="[ID: P1-F1-abcd]",
        gen_id="abcd",
    )
    out = await execute_image_regen(prep)
    assert out.is_file()
    assert called["outsee_type"] == "_ApiOnlyOutseeStub"
    assert called["prompt"] == "api prompt"


@pytest.mark.asyncio
async def test_execute_video_regen_api_skips_cdp(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def _fake_gen(outsee, gpt, **kwargs):
        out = Path(kwargs["out_path"])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"v" * 1000)
        res = MagicMock()
        res.file_path = out
        return res

    monkeypatch.setattr(
        "app.services.montage_board_regen._video_api_enabled", lambda: True
    )
    monkeypatch.setattr(
        "app.services.montage_board_regen.generate_video_with_retries", _fake_gen
    )

    start = tmp_path / "start.png"
    start.write_bytes(b"png")
    prep = VideoRegenPrep(
        project_id=1,
        frame_number=1,
        shot=1,
        prompt_text="vid",
        file_path=tmp_path / "clip_001_x.mp4",
        start_frame=start,
    )
    out = await execute_video_regen(prep)
    assert out.is_file()


@pytest.mark.asyncio
async def test_resolve_video_prompt_frame_over_empty_version(
    session: AsyncSession, project: Project
) -> None:
    session.add(project)
    fr = Frame(
        project_id=project.id,
        number=1,
        voiceover_text="v",
        animation_prompt="anim from frame",
    )
    session.add(fr)
    await session.commit()
    text = await resolve_video_prompt(session, project, fr, 1)
    assert text == "anim from frame"
