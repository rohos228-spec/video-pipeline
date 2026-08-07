"""Тесты операций панели монтажа: meta, apply, assets."""

from __future__ import annotations

from pathlib import Path

import pytest
from openpyxl import Workbook
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Frame, Project
from app.orchestrator.steps.generate_images import _XLSX_ROWS_PERSONS
from app.services.montage_board_apply import apply_montage_board
from app.services.montage_board_assets import (
    _is_file_busy_error,
    archive_file,
    delete_scene_image,
    finalize_scene_image,
    move_scene_image,
    save_scene_image_upload,
    save_scene_video_upload,
    swap_media_slots,
    swap_shot_media,
)
from app.services.plan_shot2 import (
    SHOT2_PROMPT_ATTR,
    SHOT2_VIDEO_PROMPT_ATTR,
    find_shot1_image,
    find_shot2_image,
)
from app.services.montage_board_meta import montage_meta, trim_key
from app.services.xlsx_v8_import import SHEET_PLAN_V8, ROW_VOICEOVER_V8


@pytest.fixture
async def session(tmp_path: Path) -> AsyncSession:
    db_path = tmp_path / "montage_ops.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


@pytest.fixture
def montage_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Project:
    data_root = tmp_path / "data"
    data_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("app.settings.settings.data_dir", str(data_root))
    p = Project(id=101, slug="montage-ops", topic="Тест", hero_mode="auto")
    p.data_dir.mkdir(parents=True, exist_ok=True)
    return p


@pytest.mark.asyncio
async def test_apply_saves_video_trims_to_meta(
    montage_project: Project,
    session: AsyncSession,
) -> None:
    session.add(montage_project)
    await session.flush()

    trims = {"1:1": {"start": 0.0, "end": 2.5}}
    result = await apply_montage_board(
        session,
        montage_project,
        video_trims=trims,
        pending_ops=[],
    )
    assert result["ok"] is True
    meta = montage_meta(montage_project)
    assert meta["video_trims"]["1:1"]["end"] == 2.5
    assert meta.get("applied_at")


@pytest.mark.asyncio
async def test_delete_and_upload_scene_image(
    montage_project: Project,
    session: AsyncSession,
) -> None:
    xlsx = montage_project.data_dir / "project.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = SHEET_PLAN_V8
    ws.cell(row=ROW_VOICEOVER_V8, column=3, value="Текст")
    wb.save(xlsx)

    fr = Frame(project_id=montage_project.id, number=1, voiceover_text="t", status="planned")
    session.add(montage_project)
    session.add(fr)
    await session.flush()

    scenes = montage_project.data_dir / "scenes"
    scenes.mkdir(parents=True, exist_ok=True)
    img = scenes / "frame_001_abc.png"
    img.write_bytes(b"png")

    path = await save_scene_image_upload(
        session,
        montage_project,
        1,
        shot=1,
        content=b"x" * 128,
        suffix=".png",
    )
    assert path.is_file()

    deleted = await delete_scene_image(session, montage_project, 1, shot=1)
    assert deleted is True
    assert not list(scenes.glob("frame_001_*.png"))


def test_trim_key_format() -> None:
    assert trim_key(3, 1) == "3:1"


@pytest.mark.asyncio
async def test_finalize_scene_image_archives_old_only_after_new_ready(
    montage_project: Project,
    session: AsyncSession,
) -> None:
    fr = Frame(project_id=montage_project.id, number=2, voiceover_text="t", status="planned")
    session.add(montage_project)
    session.add(fr)
    await session.flush()

    scenes = montage_project.data_dir / "scenes"
    scenes.mkdir(parents=True, exist_ok=True)
    old = scenes / "frame_002_old.png"
    old.write_bytes(b"x" * 128)
    new = scenes / "frame_002_new.png"
    new.write_bytes(b"y" * 128)

    await finalize_scene_image(session, montage_project, 2, shot=1, new_path=new)

    assert new.is_file()
    assert not old.is_file()
    archived = list((montage_project.data_dir / "old" / "scenes").glob("*old.png"))
    assert len(archived) == 1


@pytest.mark.asyncio
async def test_regen_failure_keeps_old_image(
    montage_project: Project,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fr = Frame(
        project_id=montage_project.id,
        number=1,
        voiceover_text="t",
        status="planned",
        image_prompt="test prompt for image",
    )
    session.add(montage_project)
    session.add(fr)
    await session.flush()

    scenes = montage_project.data_dir / "scenes"
    scenes.mkdir(parents=True, exist_ok=True)
    old = scenes / "frame_001_keep.png"
    old.write_bytes(b"x" * 128)

    async def _fail(*_a, **_k):
        raise RuntimeError("outsee unavailable")

    monkeypatch.setattr(
        "app.services.montage_board_regen.generate_image_with_retries",
        _fail,
    )

    from app.services.montage_board_regen import regen_scene_image

    with pytest.raises(RuntimeError, match="outsee"):
        await regen_scene_image(session, montage_project, 1, shot=1)

    assert old.is_file()


@pytest.mark.asyncio
async def test_apply_keeps_failed_pending_ops(
    montage_project: Project,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from contextlib import asynccontextmanager

    fr = Frame(
        project_id=montage_project.id,
        number=1,
        voiceover_text="t",
        status="planned",
        image_prompt="test prompt for image",
    )
    session.add(montage_project)
    session.add(fr)
    await session.flush()

    @asynccontextmanager
    async def _scope():
        yield session

    monkeypatch.setattr("app.services.montage_board_apply.session_scope", _scope)

    async def _fail(*_a, **_k):
        raise RuntimeError("download failed")

    monkeypatch.setattr(
        "app.services.montage_board_apply.execute_image_regen",
        _fail,
    )

    op = {"type": "image_regen", "frame_number": 1, "shot": 1}
    result = await apply_montage_board(
        session,
        montage_project,
        pending_ops=[op],
    )
    assert result["ok"] is False
    meta = montage_meta(montage_project)
    assert meta["pending_ops"] == [op]


@pytest.mark.asyncio
async def test_apply_finalizes_when_file_ready_despite_execute_error(
    montage_project: Project,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from contextlib import asynccontextmanager

    fr = Frame(
        project_id=montage_project.id,
        number=1,
        voiceover_text="t",
        status="planned",
        image_prompt="test prompt for image",
    )
    session.add(montage_project)
    session.add(fr)
    await session.flush()

    scenes = montage_project.data_dir / "scenes"
    scenes.mkdir(parents=True, exist_ok=True)
    old = scenes / "frame_001_old.png"
    old.write_bytes(b"x" * 128)

    @asynccontextmanager
    async def _scope():
        yield session

    monkeypatch.setattr("app.services.montage_board_apply.session_scope", _scope)

    from app.services.montage_board_regen import ImageRegenPrep

    prep_box: dict[str, ImageRegenPrep] = {}

    async def _fake_prepare(session, project, frame_number, **kwargs):
        scenes_dir = project.data_dir / "scenes"
        scenes_dir.mkdir(parents=True, exist_ok=True)
        new_path = scenes_dir / "frame_001_new.png"
        prep = ImageRegenPrep(
            project_id=project.id,
            frame_number=frame_number,
            shot=int(kwargs.get("shot") or 1),
            prompt_text="p",
            file_path=new_path,
        )
        prep_box["prep"] = prep
        return prep

    async def _fail_after_write(prep: ImageRegenPrep):
        # Порог finalize-on-error = 200 KB (как outsee-валидация).
        prep.file_path.write_bytes(b"y" * 220_000)
        raise RuntimeError("post-download glitch")

    monkeypatch.setattr(
        "app.services.montage_board_apply.prepare_image_regen",
        _fake_prepare,
    )
    monkeypatch.setattr(
        "app.services.montage_board_apply.execute_image_regen",
        _fail_after_write,
    )

    result = await apply_montage_board(
        session,
        montage_project,
        pending_ops=[{"type": "image_regen", "frame_number": 1, "shot": 1}],
    )
    assert result["ok"] is True
    assert prep_box["prep"].file_path.is_file()
    assert not old.is_file()
    assert montage_meta(montage_project).get("pending_ops") == []


@pytest.mark.asyncio
async def test_swap_shot_images_and_prompts(
    montage_project: Project,
    session: AsyncSession,
) -> None:
    fr = Frame(
        project_id=montage_project.id,
        number=1,
        voiceover_text="t",
        status="planned",
        image_prompt="PROMPT_A",
        animation_prompt="VID_A",
        attrs={
            SHOT2_PROMPT_ATTR: "PROMPT_B",
            SHOT2_VIDEO_PROMPT_ATTR: "VID_B",
        },
    )
    session.add(montage_project)
    session.add(fr)
    await session.flush()

    await save_scene_image_upload(
        session, montage_project, 1, shot=1, content=b"A" * 128, suffix=".png"
    )
    await save_scene_image_upload(
        session, montage_project, 1, shot=2, content=b"B" * 128, suffix=".png"
    )
    await save_scene_video_upload(
        session, montage_project, 1, shot=1, content=b"VA" * 600, suffix=".mp4"
    )
    await save_scene_video_upload(
        session, montage_project, 1, shot=2, content=b"VB" * 600, suffix=".mp4"
    )

    scenes = montage_project.data_dir / "scenes"
    videos = montage_project.data_dir / "videos"
    img1_before = find_shot1_image(scenes, 1)
    img2_before = find_shot2_image(scenes, 1)
    assert img1_before is not None and img2_before is not None
    a_bytes, b_bytes = img1_before.read_bytes(), img2_before.read_bytes()

    result = await swap_shot_media(session, montage_project, 1, kind="both")
    await session.commit()
    assert result["ok"] is True
    assert result["images_swapped"] is True
    assert result["videos_swapped"] is True

    img1_after = find_shot1_image(scenes, 1)
    img2_after = find_shot2_image(scenes, 1)
    assert img1_after is not None and img2_after is not None
    assert img1_after.read_bytes() == b_bytes
    assert img2_after.read_bytes() == a_bytes

    await session.refresh(fr)
    assert fr.image_prompt == "PROMPT_B"
    assert (fr.attrs or {}).get(SHOT2_PROMPT_ATTR) == "PROMPT_A"
    assert fr.animation_prompt == "VID_B"
    assert (fr.attrs or {}).get(SHOT2_VIDEO_PROMPT_ATTR) == "VID_A"

    shot1_vids = [
        p for p in videos.glob("clip_001_*.mp4") if "_s2_" not in p.name
    ]
    shot2_vids = list(videos.glob("clip_001_s2_*.mp4"))
    assert shot1_vids and shot2_vids
    assert shot1_vids[0].read_bytes() == b"VB" * 600
    assert shot2_vids[0].read_bytes() == b"VA" * 600


@pytest.mark.asyncio
async def test_move_image_into_empty_shot2(
    montage_project: Project,
    session: AsyncSession,
) -> None:
    fr = Frame(
        project_id=montage_project.id,
        number=1,
        voiceover_text="t",
        status="planned",
        image_prompt="ONLY_SHOT1",
    )
    session.add(montage_project)
    session.add(fr)
    await session.flush()
    await save_scene_image_upload(
        session, montage_project, 1, shot=1, content=b"ONLY" * 40, suffix=".png"
    )
    scenes = montage_project.data_dir / "scenes"
    assert find_shot1_image(scenes, 1) is not None
    assert find_shot2_image(scenes, 1) is None

    result = await move_scene_image(
        session,
        montage_project,
        from_frame=1,
        from_shot=1,
        to_frame=1,
        to_shot=2,
    )
    await session.commit()
    assert result["ok"] is True
    assert result["mode"] == "move"
    assert find_shot1_image(scenes, 1) is None
    img2 = find_shot2_image(scenes, 1)
    assert img2 is not None
    assert img2.read_bytes() == b"ONLY" * 40
    await session.refresh(fr)
    assert not (fr.image_prompt or "").strip()
    assert (fr.attrs or {}).get(SHOT2_PROMPT_ATTR) == "ONLY_SHOT1"


@pytest.mark.asyncio
async def test_move_image_swap_when_target_occupied(
    montage_project: Project,
    session: AsyncSession,
) -> None:
    fr1 = Frame(
        project_id=montage_project.id,
        number=1,
        voiceover_text="a",
        status="planned",
        image_prompt="P1",
    )
    fr2 = Frame(
        project_id=montage_project.id,
        number=2,
        voiceover_text="b",
        status="planned",
        image_prompt="P2",
    )
    session.add(montage_project)
    session.add(fr1)
    session.add(fr2)
    await session.flush()
    await save_scene_image_upload(
        session, montage_project, 1, shot=1, content=b"A1" * 64, suffix=".png"
    )
    await save_scene_image_upload(
        session, montage_project, 2, shot=1, content=b"B1" * 64, suffix=".png"
    )
    scenes = montage_project.data_dir / "scenes"
    a = find_shot1_image(scenes, 1).read_bytes()  # type: ignore[union-attr]
    b = find_shot1_image(scenes, 2).read_bytes()  # type: ignore[union-attr]

    result = await move_scene_image(
        session,
        montage_project,
        from_frame=1,
        from_shot=1,
        to_frame=2,
        to_shot=1,
    )
    await session.commit()
    assert result["mode"] == "swap"
    assert find_shot1_image(scenes, 1).read_bytes() == b  # type: ignore[union-attr]
    assert find_shot1_image(scenes, 2).read_bytes() == a  # type: ignore[union-attr]
    await session.refresh(fr1)
    await session.refresh(fr2)
    assert fr1.image_prompt == "P2"
    assert fr2.image_prompt == "P1"


@pytest.mark.asyncio
async def test_swap_media_slots_images_across_frames(
    montage_project: Project,
    session: AsyncSession,
) -> None:
    fr1 = Frame(
        project_id=montage_project.id,
        number=1,
        voiceover_text="a",
        status="planned",
        image_prompt="IMG1",
    )
    fr2 = Frame(
        project_id=montage_project.id,
        number=3,
        voiceover_text="c",
        status="planned",
        attrs={SHOT2_PROMPT_ATTR: "IMG3S2"},
    )
    session.add(montage_project)
    session.add(fr1)
    session.add(fr2)
    await session.flush()
    await save_scene_image_upload(
        session, montage_project, 1, shot=1, content=b"AA" * 64, suffix=".png"
    )
    await save_scene_image_upload(
        session, montage_project, 3, shot=2, content=b"BB" * 64, suffix=".png"
    )
    scenes = montage_project.data_dir / "scenes"
    a = find_shot1_image(scenes, 1).read_bytes()  # type: ignore[union-attr]
    b = find_shot2_image(scenes, 3).read_bytes()  # type: ignore[union-attr]

    result = await swap_media_slots(
        session,
        montage_project,
        kind="image",
        a_frame=1,
        a_shot=1,
        b_frame=3,
        b_shot=2,
    )
    await session.commit()
    assert result["ok"] is True
    assert result["mode"] == "swap"
    assert find_shot1_image(scenes, 1).read_bytes() == b  # type: ignore[union-attr]
    assert find_shot2_image(scenes, 3).read_bytes() == a  # type: ignore[union-attr]
    await session.refresh(fr1)
    await session.refresh(fr2)
    assert fr1.image_prompt == "IMG3S2"
    assert (fr2.attrs or {}).get(SHOT2_PROMPT_ATTR) == "IMG1"


@pytest.mark.asyncio
async def test_swap_media_slots_videos_across_frames(
    montage_project: Project,
    session: AsyncSession,
) -> None:
    fr1 = Frame(
        project_id=montage_project.id,
        number=1,
        voiceover_text="a",
        status="planned",
        animation_prompt="VID1",
    )
    fr2 = Frame(
        project_id=montage_project.id,
        number=2,
        voiceover_text="b",
        status="planned",
        animation_prompt="VID2",
    )
    session.add(montage_project)
    session.add(fr1)
    session.add(fr2)
    await session.flush()
    await save_scene_video_upload(
        session, montage_project, 1, shot=1, content=b"VA" * 600, suffix=".mp4"
    )
    await save_scene_video_upload(
        session, montage_project, 2, shot=1, content=b"VB" * 600, suffix=".mp4"
    )
    videos = montage_project.data_dir / "videos"

    result = await swap_media_slots(
        session,
        montage_project,
        kind="video",
        a_frame=1,
        a_shot=1,
        b_frame=2,
        b_shot=1,
    )
    await session.commit()
    assert result["ok"] is True
    assert result["mode"] == "swap"
    v1 = [p for p in videos.glob("clip_001_*.mp4") if "_s2_" not in p.name]
    v2 = [p for p in videos.glob("clip_002_*.mp4") if "_s2_" not in p.name]
    assert v1 and v2
    assert v1[0].read_bytes() == b"VB" * 600
    assert v2[0].read_bytes() == b"VA" * 600
    await session.refresh(fr1)
    await session.refresh(fr2)
    assert fr1.animation_prompt == "VID2"
    assert fr2.animation_prompt == "VID1"


def test_is_file_busy_error_win32() -> None:
    assert _is_file_busy_error(
        OSError(
            "[WinError 32] Процесс не может получить доступ к файлу, "
            "так как этот файл занят другим процессом"
        )
    )
    busy = OSError(32, "busy")
    busy.winerror = 32  # type: ignore[attr-defined]
    assert _is_file_busy_error(busy)
    assert not _is_file_busy_error(OSError(2, "no such file"))


def test_archive_file_copy_unlink_fallback(
    montage_project: Project,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Если shutil.move ловит WinError 32 — архивируем через copy+unlink."""
    src = montage_project.data_dir / "videos"
    src.mkdir(parents=True, exist_ok=True)
    path = src / "clip_001_locked.mp4"
    path.write_bytes(b"VIDEO" * 200)

    calls = {"n": 0}

    def _busy_move(a: str, b: str) -> None:
        calls["n"] += 1
        err = OSError(32, "busy")
        err.winerror = 32  # type: ignore[attr-defined]
        raise err

    monkeypatch.setattr(
        "app.services.montage_board_assets.shutil.move",
        _busy_move,
    )
    monkeypatch.setattr(
        "app.services.montage_board_assets.time.sleep",
        lambda _s: None,
    )

    dest = archive_file(path, montage_project, "videos")
    assert dest is not None
    assert dest.is_file()
    assert dest.read_bytes() == b"VIDEO" * 200
    assert not path.exists()
    assert calls["n"] == 8


def test_archive_file_moves_json_sidecar(montage_project: Project) -> None:
    src = montage_project.data_dir / "videos"
    src.mkdir(parents=True, exist_ok=True)
    mp4 = src / "clip_007_aabbccdd.mp4"
    meta = src / "clip_007_aabbccdd.json"
    mp4.write_bytes(b"VIDEO" * 200)
    meta.write_text('{"ok":1}', encoding="utf-8")

    dest = archive_file(mp4, montage_project, "videos")
    assert dest is not None
    assert not mp4.exists()
    assert not meta.exists()
    old = montage_project.data_dir / "old" / "videos"
    assert any(p.name.endswith("clip_007_aabbccdd.mp4") for p in old.iterdir())
    assert any(p.name.endswith("clip_007_aabbccdd.json") for p in old.iterdir())


def test_purge_replaced_media_keeps_only_new(montage_project: Project) -> None:
    from app.services.montage_board_assets import purge_replaced_media

    videos = montage_project.data_dir / "videos"
    videos.mkdir(parents=True, exist_ok=True)
    keep = videos / "clip_005_newhash01.mp4"
    keep.write_bytes(b"NEW" * 400)
    (videos / "clip_005_newhash01.json").write_text("{}", encoding="utf-8")
    old = videos / "clip_005_oldhash99.mp4"
    old.write_bytes(b"OLD" * 400)
    (videos / "clip_005_oldhash99.json").write_text("{}", encoding="utf-8")
    # shot2 must stay
    s2 = videos / "clip_005_s2_zzzzzzzz.mp4"
    s2.write_bytes(b"S2" * 400)

    n = purge_replaced_media(
        videos,
        patterns=["clip_005_*.mp4"],
        keep=keep,
        project=montage_project,
        sub="videos",
        shot=1,
    )
    assert n >= 1  # mp4; sidecar json уходит внутри archive_file
    assert keep.is_file()
    assert (videos / "clip_005_newhash01.json").is_file()
    assert not old.exists()
    assert not (videos / "clip_005_oldhash99.json").exists()
    assert s2.is_file()
