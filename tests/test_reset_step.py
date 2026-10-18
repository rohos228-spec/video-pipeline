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
    PromptVersion,
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
    """Было: session.delete(fr) и unlink картинки. Строка кадра и файл остаются.

    Статус всё равно script_ready: живых кадров нет, plan и script на месте.
    """
    p = await _mkproject(session)
    fr1 = await _mkframe(
        session, p, 1, image_prompt="p1", status=FrameStatus.image_approved,
    )
    img_p = tmp_path / "im1.png"
    img_p.write_bytes(b"x")
    art = await _mkart(
        session, p, ArtifactKind.scene_image,
        path=str(img_p), frame_id=fr1.id,
    )
    p.status = ProjectStatus.images_ready
    await session.flush()

    await reset_step(session, p, "split")

    frames = (await session.execute(
        select(Frame).where(Frame.project_id == p.id)
    )).scalars().all()
    assert len(frames) == 1
    assert (frames[0].attrs or {}).get("удалён") is True
    assert (frames[0].attrs or {}).get("активен") is False
    # Downstream-сброс img копирует scene_image в old/ и снимает исходный
    # файл. Кадр при этом не стирается. Копия картинки остаётся.
    backups = list((p.data_dir / "old").rglob("*im1.png"))
    assert backups
    # Строка кадра на месте, картинка шага img снята в old/.
    # Помеченный кадр не считается: статус снова script_ready.
    assert p.status is ProjectStatus.script_ready
    assert art.id is not None


@pytest.mark.asyncio
async def test_live_frame_still_counts_as_frames_ready(session):
    """Живой кадр со split_completed остаётся frames_ready. Фильтр только пометки."""
    from app.services.project_state import compute_actual_status

    p = await _mkproject(session)
    await _mkframe(session, p, 1, attrs={"удалён": True, "активен": False})
    await _mkframe(session, p, 2)
    p.meta = {"split_completed": True}
    p.status = ProjectStatus.frames_ready
    await session.flush()

    assert await compute_actual_status(session, p) is ProjectStatus.frames_ready


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
async def test_clear_step_outputs_split_soft_preserves_frames(session) -> None:
    """Soft ▶ split не должен обнулять кадры до GPT (#33 → 0 frames)."""
    p = await _mkproject(session)
    p.meta = {"split_completed": True, "split_params_applied": "27:54:27:52"}
    await _mkframe(session, p, 1)
    await _mkframe(session, p, 2)
    await session.flush()

    summary = await clear_step_outputs_for_rerun(
        session, p, "split", force_wipe=False
    )

    frames = (
        await session.execute(select(Frame).where(Frame.project_id == p.id))
    ).scalars().all()
    assert len(frames) == 2
    assert summary["split"]["frames_preserved"] is True
    assert (p.meta or {}).get("split_completed") is None


@pytest.mark.asyncio
async def test_clear_step_outputs_split_force_wipe_deletes_frames(session) -> None:
    """Было: ``await session.delete(fr)`` в ``_wipe_split``. Строки остаются."""
    p = await _mkproject(session)
    p.meta = {"split_completed": True}
    await _mkframe(session, p, 1)
    await _mkframe(session, p, 2)
    await session.flush()

    summary = await clear_step_outputs_for_rerun(
        session, p, "split", force_wipe=True
    )

    frames = (
        await session.execute(select(Frame).where(Frame.project_id == p.id))
    ).scalars().all()
    assert len(frames) == 2
    assert summary["split"]["frames_deleted"] == 0
    assert summary["split"]["frames_marked"] == 2
    assert all((fr.attrs or {}).get("удалён") is True for fr in frames)
    assert all((fr.attrs or {}).get("активен") is False for fr in frames)


@pytest.mark.asyncio
async def test_wipe_split_parks_numbers_so_resplit_does_not_revive(session):
    """Повторный split после сброса не падает на unique и не оживляет старые строки."""
    from app.services.db_v2 import replace_all_frames
    from app.services.frame_tombstones import frame_is_active
    from app.services.reset_step import _wipe_split

    p = await _mkproject(session, slug="re-split")
    p.meta = {"split_completed": True}
    old_a = await _mkframe(session, p, 1)
    old_b = await _mkframe(session, p, 2)
    id_a, id_b = old_a.id, old_b.id
    vo_a, vo_b = old_a.voiceover_text, old_b.voiceover_text

    await _wipe_split(session, p)
    await session.flush()

    parked = (
        await session.execute(select(Frame).where(Frame.project_id == p.id))
    ).scalars().all()
    assert {fr.number for fr in parked} == {900_000, 900_001}
    by_id = {fr.id: fr for fr in parked}
    assert by_id[id_a].attrs["исходный_номер"] == 1
    assert by_id[id_b].attrs["исходный_номер"] == 2
    tomb = set((p.meta or {}).get("deleted_frame_numbers") or [])
    assert {900_000, 900_001} <= tomb
    assert not ({1, 2} & tomb)
    assert (p.meta or {}).get("split_completed") is None

    session.add(
        Frame(
            project_id=p.id,
            number=1,
            voiceover_text="свежий",
            status=FrameStatus.planned,
        )
    )
    await session.flush()

    created = await replace_all_frames(
        session,
        p,
        [
            {"voiceover_text": "новая ячейка один"},
            {"voiceover_text": "новая ячейка два"},
        ],
    )
    await session.flush()

    again_a = await session.get(Frame, id_a)
    again_b = await session.get(Frame, id_b)
    assert again_a is not None and again_b is not None
    assert again_a.voiceover_text == vo_a
    assert again_b.voiceover_text == vo_b
    assert frame_is_active(again_a) is False
    assert frame_is_active(again_b) is False
    assert again_a.attrs["исходный_номер"] == 1
    assert {c.id for c in created}.isdisjoint({id_a, id_b})
    assert all(frame_is_active(c) for c in created)


@pytest.mark.asyncio
async def test_wipe_split_lets_xlsx_import_keep_original_numbers(
    session, tmp_path: Path
):
    """Сброс не кладёт 1..N в tombstone, и v8-импорт эти колонки не выкидывает."""
    from openpyxl import Workbook

    from app.services.frame_tombstones import deleted_numbers
    from app.services.reset_step import _wipe_split
    from app.services.xlsx_v8_import import ROW_VOICEOVER_V8, import_v8_xlsx

    p = await _mkproject(session, slug="import-after-reset")
    await _mkframe(session, p, 1)
    await _mkframe(session, p, 2)
    await _wipe_split(session, p)
    await session.flush()

    tomb = deleted_numbers(p)
    assert 1 not in tomb and 2 not in tomb
    assert {900_000, 900_001} <= tomb

    wb = Workbook()
    ws = wb.active
    ws.title = "план"
    ws.cell(row=ROW_VOICEOVER_V8, column=3, value="новый один")
    ws.cell(row=ROW_VOICEOVER_V8, column=4, value="новый два")
    xlsx = tmp_path / "project.xlsx"
    wb.save(xlsx)

    summary = await import_v8_xlsx(session, p, xlsx, keep_fields=True)
    kept = summary.get("frames_kept_marked") or []
    assert 1 not in kept and 2 not in kept


@pytest.mark.asyncio
async def test_clear_step_outputs_for_rerun_video_preserves_mp4(
    session, tmp_path: Path, monkeypatch,
):
    """Soft ▶ video: не удаляет clip_*.mp4, только soft-resume с диска."""
    import app.settings as app_settings

    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(app_settings.settings, "data_dir", tmp_path / "data")

    p = await _mkproject(session)
    p.data_dir.mkdir(parents=True, exist_ok=True)
    videos_dir = p.data_dir / "videos"
    videos_dir.mkdir(parents=True, exist_ok=True)
    clip = videos_dir / "clip_001_abcd.mp4"
    clip.write_bytes(b"fake-mp4")

    fr = await _mkframe(
        session,
        p,
        1,
        image_prompt="ip",
        animation_prompt="anim",
        status=FrameStatus.video_generated,
    )
    await _mkart(
        session,
        p,
        ArtifactKind.scene_video,
        path=str(clip),
        frame_id=fr.id,
    )
    p.status = ProjectStatus.generating_videos
    await session.flush()

    summary = await clear_step_outputs_for_rerun(session, p, "video", force_wipe=False)
    assert summary.get("video", {}).get("mode") == "soft_resume"
    assert clip.exists()
    videos_left = (
        await session.execute(
            select(Artifact).where(
                Artifact.project_id == p.id,
                Artifact.kind == ArtifactKind.scene_video,
            )
        )
    ).scalars().all()
    assert len(videos_left) == 1
    await session.refresh(fr)
    assert fr.status is FrameStatus.video_generated


@pytest.mark.asyncio
async def test_clear_step_outputs_for_rerun_img_pr_preserves(session):
    """Авто/очередь img_pr: не стираем image_prompt (догонка / skip)."""
    p = await _mkproject(session)
    fr = await _mkframe(
        session,
        p,
        1,
        image_prompt="clay prompt already done",
        status=FrameStatus.image_prompt_ready,
    )
    p.status = ProjectStatus.image_prompts_ready
    await session.flush()

    summary = await clear_step_outputs_for_rerun(session, p, "img_pr", force_wipe=False)
    assert "img_pr" in summary
    await session.refresh(fr)
    assert fr.image_prompt == "clay prompt already done"


@pytest.mark.asyncio
async def test_clear_step_outputs_for_rerun_img_pr_force_wipe_clears(session):
    """force_wipe=True (явный ▶) стирает image_prompt, чтобы GPT пересобрал."""
    p = await _mkproject(session)
    fr = await _mkframe(
        session,
        p,
        1,
        image_prompt="old watercolor prompt",
        status=FrameStatus.image_prompt_ready,
    )
    p.status = ProjectStatus.image_prompts_ready
    await session.flush()

    summary = await clear_step_outputs_for_rerun(session, p, "img_pr", force_wipe=True)
    assert "img_pr" in summary
    await session.refresh(fr)
    assert not (fr.image_prompt or "").strip()


@pytest.mark.asyncio
async def test_clear_step_outputs_hero_soft_keeps_png(session, tmp_path: Path, monkeypatch):
    """Soft ▶ hero не должен сносить уже готовые characters/*.png."""
    from app import settings as app_settings

    monkeypatch.setattr(app_settings.settings, "data_dir", tmp_path)
    p = await _mkproject(session)
    chars = p.data_dir / "characters"
    chars.mkdir(parents=True, exist_ok=True)
    png = chars / "c01.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 2000)
    await _mkart(
        session,
        p,
        ArtifactKind.hero_reference,
        path=str(png),
    )
    arts = (
        await session.execute(
            select(Artifact).where(
                Artifact.project_id == p.id,
                Artifact.kind == ArtifactKind.hero_reference,
            )
        )
    ).scalars().all()
    arts[0].meta = {"excel_id": "c01"}
    await session.flush()

    summary = await clear_step_outputs_for_rerun(session, p, "hero", force_wipe=False)
    assert summary.get("hero", {}).get("mode") == "soft_resume"
    assert png.exists()
    left = (
        await session.execute(
            select(Artifact).where(
                Artifact.project_id == p.id,
                Artifact.kind == ArtifactKind.hero_reference,
            )
        )
    ).scalars().all()
    assert len(left) == 1


@pytest.mark.asyncio
async def test_clear_step_outputs_hero_force_wipe_removes_png(session, tmp_path: Path, monkeypatch):
    from app import settings as app_settings

    monkeypatch.setattr(app_settings.settings, "data_dir", tmp_path)
    p = await _mkproject(session)
    chars = p.data_dir / "characters"
    chars.mkdir(parents=True, exist_ok=True)
    png = chars / "c01.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 2000)
    await _mkart(session, p, ArtifactKind.hero_reference, path=str(png))
    await session.flush()

    summary = await clear_step_outputs_for_rerun(session, p, "hero", force_wipe=True)
    assert "hero" in summary
    assert not png.exists()


@pytest.mark.asyncio
async def test_clear_step_outputs_hero_force_wipe_removes_orphan_png(
    session, tmp_path: Path, monkeypatch
):
    """force_wipe hero удаляет characters/*.png даже без Artifact в project.db."""
    from app import settings as app_settings

    monkeypatch.setattr(app_settings.settings, "data_dir", tmp_path)
    p = await _mkproject(session)
    chars = p.data_dir / "characters"
    chars.mkdir(parents=True, exist_ok=True)
    png = chars / "c01.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 2000)
    await session.flush()

    summary = await clear_step_outputs_for_rerun(session, p, "hero", force_wipe=True)
    assert "hero" in summary
    assert summary["hero"].get("orphan_character_files") == 1
    assert not png.exists()


@pytest.mark.asyncio
async def test_clear_step_outputs_img_force_wipe_removes_png(session):
    """Явный ▶ img: PNG не скип — бэкап и удаление, кадры снова в очередь."""
    p = await _mkproject(session)
    fr = await _mkframe(
        session,
        p,
        1,
        image_prompt="still prompt",
        status=FrameStatus.image_generated,
    )
    scenes = p.data_dir / "scenes"
    scenes.mkdir(parents=True, exist_ok=True)
    png = scenes / "frame_001_abcd1234.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 1000)

    summary = await clear_step_outputs_for_rerun(session, p, "img", force_wipe=True)
    assert "img" in summary
    assert not png.exists()
    await session.refresh(fr)
    assert fr.status is FrameStatus.image_prompt_ready


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


async def _mk_active_pv(
    session, project: Project, frame: Frame, *, kind: str, text: str
) -> PromptVersion:
    pv = PromptVersion(
        project_id=project.id,
        frame_id=frame.id,
        kind=kind,
        version=1,
        text=text,
        is_active=True,
    )
    session.add(pv)
    await session.flush()
    return pv


@pytest.mark.asyncio
async def test_wipe_img_pr_deactivates_prompt_versions(session):
    """reset_step(img_pr) clears Frame.image_prompt and deactivates img PVs.

    Rows stay (history). Montage overlay only copies is_active=True, so
    inactive PVs cannot resurrect stale text into the UI.
    """
    from app.services.montage_board import _overlay_active_prompt_versions

    p = await _mkproject(session)
    fr = await _mkframe(
        session, p, 1,
        image_prompt="stale img prompt",
        status=FrameStatus.image_prompt_ready,
    )
    pv = await _mk_active_pv(session, p, fr, kind="img", text="stale img prompt")
    p.status = ProjectStatus.image_prompts_ready
    await session.flush()

    await reset_step(session, p, "img_pr")

    await session.refresh(fr)
    await session.refresh(pv)
    assert fr.image_prompt is None
    assert pv.is_active is False
    still = (
        await session.execute(
            select(PromptVersion).where(
                PromptVersion.project_id == p.id,
                PromptVersion.kind == "img",
            )
        )
    ).scalars().all()
    assert len(still) == 1
    assert still[0].id == pv.id
    assert not any(x.is_active for x in still)

    await _overlay_active_prompt_versions(session, [fr])
    assert fr.image_prompt is None


@pytest.mark.asyncio
async def test_wipe_anim_pr_deactivates_video_prompt_versions(session):
    """reset_step(anim_pr) clears Frame.animation_prompt, shot2 video attr,
    and deactivates video PVs. Img PVs stay active (upstream).
    """
    from app.services.plan_shot2 import SHOT2_VIDEO_PROMPT_ATTR
    from app.services.montage_board import _overlay_active_prompt_versions

    p = await _mkproject(session)
    fr = await _mkframe(
        session, p, 1,
        image_prompt="keep img",
        animation_prompt="stale video prompt",
        status=FrameStatus.animation_prompt_ready,
        attrs={SHOT2_VIDEO_PROMPT_ATTR: "stale shot2 video"},
    )
    img_pv = await _mk_active_pv(session, p, fr, kind="img", text="keep img")
    vid_pv = await _mk_active_pv(
        session, p, fr, kind="video", text="stale video prompt"
    )
    p.status = ProjectStatus.animation_prompts_ready
    await session.flush()

    await reset_step(session, p, "anim_pr")

    await session.refresh(fr)
    await session.refresh(img_pv)
    await session.refresh(vid_pv)
    assert fr.animation_prompt is None
    assert not (fr.attrs or {}).get(SHOT2_VIDEO_PROMPT_ATTR)
    assert vid_pv.is_active is False
    assert img_pv.is_active is True
    video_pvs = (
        await session.execute(
            select(PromptVersion).where(
                PromptVersion.project_id == p.id,
                PromptVersion.kind == "video",
            )
        )
    ).scalars().all()
    assert len(video_pvs) == 1
    assert not any(x.is_active for x in video_pvs)

    await _overlay_active_prompt_versions(session, [fr])
    assert fr.animation_prompt is None
    assert fr.image_prompt == "keep img"


@pytest.mark.asyncio
async def test_soft_resume_img_pr_does_not_deactivate_prompt_versions(session):
    """Soft ▶ img_pr (force_wipe=False) must not deactivate PromptVersion."""
    p = await _mkproject(session)
    fr = await _mkframe(
        session, p, 1,
        image_prompt="keep me",
        status=FrameStatus.image_prompt_ready,
    )
    pv = await _mk_active_pv(session, p, fr, kind="img", text="keep me")
    p.status = ProjectStatus.image_prompts_ready
    await session.flush()

    await clear_step_outputs_for_rerun(session, p, "img_pr", force_wipe=False)

    await session.refresh(fr)
    await session.refresh(pv)
    assert fr.image_prompt == "keep me"
    assert pv.is_active is True


@pytest.mark.asyncio
async def test_soft_resume_anim_pr_does_not_deactivate_prompt_versions(session):
    """Soft ▶ anim_pr must not deactivate video PromptVersion."""
    p = await _mkproject(session)
    fr = await _mkframe(
        session, p, 1,
        image_prompt="ip",
        animation_prompt="keep anim",
        status=FrameStatus.animation_prompt_ready,
    )
    pv = await _mk_active_pv(session, p, fr, kind="video", text="keep anim")
    p.status = ProjectStatus.animation_prompts_ready
    await session.flush()

    await clear_step_outputs_for_rerun(session, p, "anim_pr", force_wipe=False)

    await session.refresh(fr)
    await session.refresh(pv)
    assert fr.animation_prompt == "keep anim"
    assert pv.is_active is True


def _bind_data_dir(monkeypatch, tmp_path: Path):
    import app.settings as app_settings

    monkeypatch.setattr(app_settings.settings, "data_dir", tmp_path / "data")


def _warn_lines():
    from loguru import logger

    lines: list[str] = []
    sink = logger.add(lambda message: lines.append(str(message)), level="WARNING")
    return lines, sink, logger


@pytest.mark.asyncio
async def test_wipe_keeps_file_when_old_copy_fails(session, tmp_path: Path, monkeypatch):
    """Сбой копии в old/ — файл и строка Artifact остаются, в логе WARN."""
    from app.services.reset_step import _wipe_artifacts_by_kind

    _bind_data_dir(monkeypatch, tmp_path)
    p = await _mkproject(session, slug="copy-fail")
    p.data_dir.mkdir(parents=True, exist_ok=True)
    src = p.data_dir / "scene.png"
    payload = b"scene-bytes"
    src.write_bytes(payload)
    art = await _mkart(
        session, p, ArtifactKind.scene_image, path=str(src),
    )

    def _boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr("app.services.reset_step.shutil.copy2", _boom)
    lines, sink, logger = _warn_lines()
    try:
        await _wipe_artifacts_by_kind(session, p, ArtifactKind.scene_image)
    finally:
        logger.remove(sink)

    assert src.read_bytes() == payload
    row = await session.get(Artifact, art.id)
    assert row is not None
    assert row.path == str(src)
    assert any("без копии" in line or "файл не удаляем" in line for line in lines)


@pytest.mark.asyncio
async def test_wipe_assemble_archives_final_and_subtitles(
    session, tmp_path: Path, monkeypatch
):
    """final_video и субтитры уходят в old/ с тем же размером, исходники снимаются."""
    from app.services.reset_step import _wipe_assemble

    _bind_data_dir(monkeypatch, tmp_path)
    p = await _mkproject(session, slug="assemble-arc")
    p.data_dir.mkdir(parents=True, exist_ok=True)
    final = p.data_dir / "movie.mp4"
    subs = p.data_dir / "movie.srt"
    final.write_bytes(b"final-video-bytes")
    subs.write_bytes(b"1\n00:00:00,000 --> 00:00:01,000\nhi\n")
    await _mkart(session, p, ArtifactKind.final_video, path=str(final))
    await _mkart(session, p, ArtifactKind.subtitle, path=str(subs))

    await _wipe_assemble(session, p)

    assert not final.exists()
    assert not subs.exists()
    final_copies = list((p.data_dir / "old" / "final").rglob("*.mp4"))
    sub_copies = list((p.data_dir / "old" / "subtitles").rglob("*.srt"))
    assert len(final_copies) == 1
    assert len(sub_copies) == 1
    assert final_copies[0].stat().st_size == len(b"final-video-bytes")
    assert sub_copies[0].read_bytes() == b"1\n00:00:00,000 --> 00:00:01,000\nhi\n"
    left = (
        await session.execute(
            select(Artifact).where(Artifact.project_id == p.id)
        )
    ).scalars().all()
    assert left == []


@pytest.mark.asyncio
async def test_wipe_sfx_moves_dir_and_keeps_it_when_copy_fails(
    session, tmp_path: Path, monkeypatch
):
    """sfx/ переносится в old/sfx. Если копия не подтверждена — каталог на месте."""
    from app.services.reset_step import _wipe_sfx_gen

    _bind_data_dir(monkeypatch, tmp_path)
    p = await _mkproject(session, slug="sfx-arc")
    p.data_dir.mkdir(parents=True, exist_ok=True)
    sfx = p.data_dir / "sfx"
    (sfx / "nested").mkdir(parents=True)
    payload = b"RIFFxxxxWAVEfmt "
    (sfx / "nested" / "hit.wav").write_bytes(payload)

    summary = await _wipe_sfx_gen(session, p)

    assert not sfx.exists()
    archived = Path(summary["archived_to"])
    copied = archived / "nested" / "hit.wav"
    assert copied.is_file()
    assert copied.stat().st_size == len(payload)

    sfx.mkdir()
    (sfx / "keep.wav").write_bytes(b"keep-me-please")

    def _bad_tree(src, dest):
        dest.mkdir(parents=True)
        (dest / "keep.wav").write_bytes(b"x")

    monkeypatch.setattr("app.services.reset_step.shutil.copytree", _bad_tree)
    lines, sink, logger = _warn_lines()
    try:
        failed = await _wipe_sfx_gen(session, p)
    finally:
        logger.remove(sink)

    assert (sfx / "keep.wav").read_bytes() == b"keep-me-please"
    assert failed["archived_to"] is None
    assert any("каталог сохранён" in line for line in lines)
