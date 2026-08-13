"""Дедуп видео (инцидент 13.08: «нода Видео генерирует два видео по несколько
раз, в папке несколько одинаковых файлов»).

A. claim пропускает кадр, если clip_NNN_*.mp4 есть на диске без Artifact.
B. GC: старые sibling'и кадра → old/videos/<ts>/ (после записи, в recover,
   в mark_frames_for_video_regen).
C. HTTP-путь: скачанный дубликат → OutseeDuplicateVideoError, новый файл
   удалён, существующий сохранён; шаг привязывает имеющийся клип.
D. short_uuid/prompt_id_prefix стабильны на (frame, shot) внутри прогона.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.bots.outsee import GenerationResult, OutseeDuplicateVideoError
from app.models import Artifact, ArtifactKind, Base, Frame, FrameStatus, Project
from app.orchestrator.steps import generate_videos as gv
from app.services import outsee_retry as retry_mod
from app.services.artifact_recovery import (
    move_frame_videos_to_old,
    recover_scene_videos_from_disk,
)
from app.services.post_step_validate import mark_frames_for_video_regen


@pytest.fixture
async def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """SQLite in tmp + data_dir проекта в tmp (как test_parallel_split_lock)."""
    data_root = tmp_path / "data"
    data_root.mkdir()
    monkeypatch.setattr("app.settings.settings.data_dir", str(data_root))
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 't.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr("app.db.SessionLocal", factory)
    try:
        yield engine, factory
    finally:
        await engine.dispose()


async def _mk_project(session: AsyncSession, pid: int = 1) -> Project:
    p = Project(id=pid, slug=f"p{pid}", topic="t", hero_mode="auto")
    session.add(p)
    await session.flush()
    return p


def _clip(videos: Path, name: str, body: bytes, *, age_s: float = 0) -> Path:
    videos.mkdir(parents=True, exist_ok=True)
    p = videos / name
    p.write_bytes(body)
    if age_s:
        ts = time.time() - age_s
        import os

        os.utime(p, (ts, ts))
    return p


def _valid_png(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 250_000)
    return path


# ── A. claim: skip по диску, а не только по Artifact ────────────────────────


@pytest.mark.asyncio
async def test_claim_skips_frame_with_disk_clip_without_artifact(env) -> None:
    """Файл на диске без Artifact → кадр НЕ claim'ится на новую генерацию."""
    _, factory = env
    async with factory() as session:
        p = await _mk_project(session, 1)
        fr1 = Frame(
            project_id=p.id,
            number=1,
            animation_prompt="pan left",
            voiceover_text="v",
            status=FrameStatus.animation_prompt_ready,
            attrs={},
        )
        fr2 = Frame(
            project_id=p.id,
            number=2,
            animation_prompt="dolly in",
            voiceover_text="v",
            status=FrameStatus.animation_prompt_ready,
            attrs={},
        )
        session.add_all([fr1, fr2])
        await session.flush()
        videos = p.data_dir / "videos"
        _clip(videos, "clip_001_deadbee1.mp4", b"x" * 5000)

        claimed = await gv._claim_shot1_video_batch(
            session, p.id, limit=4, out_dir=videos
        )

        assert [f.number for f in claimed] == [2]
        await session.refresh(fr1)
        assert fr1.status == FrameStatus.video_generated
        # Кадр 2 реально взят в работу (inflight проставлен).
        await session.refresh(fr2)
        assert (fr2.attrs or {}).get("video_gen_inflight")


@pytest.mark.asyncio
async def test_claim_without_out_dir_keeps_artifact_only_semantics(env) -> None:
    """Обратная совместимость: без out_dir — только Artifact (старые caller'ы)."""
    _, factory = env
    async with factory() as session:
        p = await _mk_project(session, 1)
        fr = Frame(
            project_id=p.id,
            number=1,
            animation_prompt="pan left",
            voiceover_text="v",
            status=FrameStatus.animation_prompt_ready,
            attrs={},
        )
        session.add(fr)
        await session.flush()
        videos = p.data_dir / "videos"
        _clip(videos, "clip_001_deadbee1.mp4", b"x" * 5000)

        claimed = await gv._claim_shot1_video_batch(session, p.id, limit=4)
        assert [f.id for f in claimed] == [fr.id]


# ── B. GC старых sibling'ов ─────────────────────────────────────────────────


def test_move_frame_videos_to_old_shot1_keeps_other_shot(tmp_path: Path) -> None:
    data_dir = tmp_path / "proj"
    videos = data_dir / "videos"
    old = _clip(videos, "clip_003_aaa.mp4", b"a" * 1000, age_s=10)
    keep = _clip(videos, "clip_003_bbb.mp4", b"b" * 1000)
    s2 = _clip(videos, "clip_003_s2_ccc.mp4", b"c" * 1000)

    moved = move_frame_videos_to_old(data_dir, 3, keep=keep, shot=1)

    assert [p.name for p in moved] == ["clip_003_aaa.mp4"]
    assert not old.exists()
    assert moved[0].is_file()
    assert moved[0].parent.parent == data_dir / "old" / "videos"
    assert keep.is_file()
    assert s2.is_file(), "файл другого шота трогать нельзя"


def test_move_frame_videos_to_old_all_shots(tmp_path: Path) -> None:
    data_dir = tmp_path / "proj"
    videos = data_dir / "videos"
    a = _clip(videos, "clip_003_aaa.mp4", b"a" * 1000)
    b = _clip(videos, "clip_003_s2_bbb.mp4", b"b" * 1000)

    moved = move_frame_videos_to_old(data_dir, 3, shot=None)

    assert sorted(p.name for p in moved) == [
        "clip_003_aaa.mp4",
        "clip_003_s2_bbb.mp4",
    ]
    assert not a.exists() and not b.exists()
    assert all(p.is_file() for p in moved)


@pytest.mark.asyncio
async def test_recover_links_newest_and_gcs_older_siblings(env) -> None:
    """recover: Artifact → newest; старый sibling того же шота → old/videos."""
    _, factory = env
    async with factory() as session:
        p = await _mk_project(session, 7)
        fr = Frame(project_id=7, number=1, voiceover_text="x", status="planned")
        session.add(fr)
        await session.flush()
        videos = p.data_dir / "videos"
        old = _clip(videos, "clip_001_old.mp4", b"0" * 90_000, age_s=10)
        new = _clip(videos, "clip_001_new.mp4", b"1" * 90_000)
        session.add(
            Artifact(
                project_id=7,
                frame_id=fr.id,
                kind=ArtifactKind.scene_video,
                uuid="oldart",
                path=str(old.resolve()),
                meta={"shot": 1},
            )
        )
        await session.flush()

        recovered = await recover_scene_videos_from_disk(session, p)

        assert 1 in recovered
        arts = (
            await session.execute(
                select(Artifact).where(
                    Artifact.project_id == 7,
                    Artifact.kind == ArtifactKind.scene_video,
                )
            )
        ).scalars().all()
        assert len(arts) == 1
        assert Path(arts[0].path).name == "clip_001_new.mp4"
        assert new.is_file()
        assert not old.exists(), "старый sibling должен уехать в old/videos"
        old_root = p.data_dir / "old" / "videos"
        assert old_root.is_dir()
        assert list(old_root.rglob("clip_001_old.mp4"))


@pytest.mark.asyncio
async def test_recover_gc_even_when_artifact_already_current(env) -> None:
    """Artifact уже указывает на newest — накопленные сироты всё равно в old/."""
    _, factory = env
    async with factory() as session:
        p = await _mk_project(session, 8)
        fr = Frame(project_id=8, number=2, voiceover_text="x", status="planned")
        session.add(fr)
        await session.flush()
        videos = p.data_dir / "videos"
        orphan = _clip(videos, "clip_002_orph.mp4", b"0" * 90_000, age_s=10)
        new = _clip(videos, "clip_002_new.mp4", b"1" * 90_000)
        session.add(
            Artifact(
                project_id=8,
                frame_id=fr.id,
                kind=ArtifactKind.scene_video,
                uuid="cur",
                path=str(new.resolve()),
                meta={"shot": 1},
            )
        )
        await session.flush()

        recovered = await recover_scene_videos_from_disk(session, p)

        assert recovered == [], "пере-привязка не нужна — artifact уже текущий"
        assert new.is_file()
        assert not orphan.exists()
        assert list((p.data_dir / "old" / "videos").rglob("clip_002_orph.mp4"))


@pytest.mark.asyncio
async def test_mark_frames_for_video_regen_moves_all_clips_to_old(env) -> None:
    """Regen: все clip_001_* (вкл. сирот и _s2_) → old/videos, не только a.path."""
    _, factory = env
    async with factory() as session:
        p = await _mk_project(session, 9)
        fr = Frame(
            project_id=9,
            number=1,
            animation_prompt="pan",
            voiceover_text="v",
            status=FrameStatus.video_generated,
            attrs={"video_gen_skip": "x"},
        )
        session.add(fr)
        await session.flush()
        videos = p.data_dir / "videos"
        linked = _clip(videos, "clip_001_aaa.mp4", b"a" * 2000)
        orphan = _clip(videos, "clip_001_bbb.mp4", b"b" * 2000)
        s2 = _clip(videos, "clip_001_s2_ccc.mp4", b"c" * 2000)
        session.add(
            Artifact(
                project_id=9,
                frame_id=fr.id,
                kind=ArtifactKind.scene_video,
                uuid="a1",
                path=str(linked.resolve()),
                meta={"shot": 1},
            )
        )
        await session.flush()

        changed = await mark_frames_for_video_regen(session, p, [1])

        assert changed >= 1
        for f in (linked, orphan, s2):
            assert not f.exists(), f"{f.name} должен уехать из videos/"
        moved = list((p.data_dir / "old" / "videos").rglob("clip_001_*.mp4"))
        assert sorted(f.name for f in moved) == [
            "clip_001_aaa.mp4",
            "clip_001_bbb.mp4",
            "clip_001_s2_ccc.mp4",
        ]
        arts = (
            await session.execute(
                select(Artifact).where(
                    Artifact.project_id == 9,
                    Artifact.kind == ArtifactKind.scene_video,
                )
            )
        ).scalars().all()
        assert arts == []
        await session.refresh(fr)
        assert not (fr.attrs or {}).get("video_gen_skip")


# ── C. HTTP duplicate check ─────────────────────────────────────────────────


def _force_outsee_http_video(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.bots.grsai.grsai_video_enabled", lambda: False)
    monkeypatch.setattr(
        "app.bots.outsee_http.outsee_api_enabled_for_video", lambda: True
    )
    monkeypatch.setattr(
        "app.bots.outsee_http.outsee_api_configured", lambda: True
    )


async def _identity_prepare(
    gpt, body, prefix, *, project_id=None, max_body=None, max_full=None
):
    return body


@pytest.mark.asyncio
async def test_http_video_duplicate_raises_and_unlinks_new(
    env, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """HTTP скачал байты == имеющемуся клипу → dup-сигнал, новый файл удалён."""
    _force_outsee_http_video(monkeypatch)
    monkeypatch.setattr(retry_mod, "_prepare_prompt_for_outsee", _identity_prepare)
    videos = tmp_path / "videos"
    ref = _clip(videos, "clip_001_aaaaaaaa.mp4", b"same-bytes" * 500)
    out_path = videos / "clip_001_bbbbbbbb.mp4"
    api_calls = 0

    async def fake_api_video(prompt, out_path, **kwargs):
        nonlocal api_calls
        api_calls += 1
        Path(out_path).write_bytes(b"same-bytes" * 500)  # дубликат ref
        return GenerationResult(
            file_path=Path(out_path), raw_url="https://x/v.mp4", gen_id="g1"
        )

    monkeypatch.setattr("app.bots.outsee_http.generate_video", fake_api_video)

    with pytest.raises(OutseeDuplicateVideoError) as ei:
        await retry_mod.generate_video_with_retries(
            None,
            None,
            prompt="silent calm scene",
            out_path=out_path,
            gpt_rewrite=False,
            project_id=1,
            duplicate_check_paths=[ref],
        )

    assert api_calls == 1, "лестница не должна жечь попытки на дубликате"
    assert not out_path.exists(), "новый дубликат-файл удалён"
    assert ref.is_file(), "существующий клип сохранён"
    assert Path(ei.value.context["duplicate_of"]).name == ref.name


@pytest.mark.asyncio
async def test_http_video_unique_download_passes(
    env, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Happy path без изменений: уникальный файл — результат возвращается."""
    _force_outsee_http_video(monkeypatch)
    monkeypatch.setattr(retry_mod, "_prepare_prompt_for_outsee", _identity_prepare)
    videos = tmp_path / "videos"
    ref = _clip(videos, "clip_001_aaaaaaaa.mp4", b"old-bytes" * 500)
    out_path = videos / "clip_001_bbbbbbbb.mp4"

    async def fake_api_video(prompt, out_path, **kwargs):
        Path(out_path).write_bytes(b"fresh-bytes" * 500)
        return GenerationResult(
            file_path=Path(out_path), raw_url="https://x/v.mp4", gen_id="g2"
        )

    monkeypatch.setattr("app.bots.outsee_http.generate_video", fake_api_video)

    result = await retry_mod.generate_video_with_retries(
        None,
        None,
        prompt="silent calm scene",
        out_path=out_path,
        gpt_rewrite=False,
        project_id=1,
        duplicate_check_paths=[ref],
    )
    assert Path(result.file_path) == out_path
    assert out_path.is_file()
    assert ref.is_file()


@pytest.mark.asyncio
async def test_http_video_dup_check_error_is_best_effort(
    env, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Если сам dup-check падает — генерация не блокируется."""
    _force_outsee_http_video(monkeypatch)
    monkeypatch.setattr(retry_mod, "_prepare_prompt_for_outsee", _identity_prepare)
    videos = tmp_path / "videos"
    ref = _clip(videos, "clip_001_aaaaaaaa.mp4", b"same" * 500)
    out_path = videos / "clip_001_bbbbbbbb.mp4"

    async def fake_api_video(prompt, out_path, **kwargs):
        Path(out_path).write_bytes(b"same" * 500)
        return GenerationResult(file_path=Path(out_path), gen_id="g3")

    def _boom(candidate, references):
        raise RuntimeError("ffmpeg exploded")

    monkeypatch.setattr("app.bots.outsee_http.generate_video", fake_api_video)
    monkeypatch.setattr(
        "app.services.video_duplicate.find_duplicate_reference", _boom
    )

    result = await retry_mod.generate_video_with_retries(
        None,
        None,
        prompt="silent calm scene",
        out_path=out_path,
        gpt_rewrite=False,
        project_id=1,
        duplicate_check_paths=[ref],
    )
    assert Path(result.file_path) == out_path
    assert out_path.is_file(), "ошибка чека не должна удалять/валить результат"


# ── C→step: link existing, done ──────────────────────────────────────────────


@asynccontextmanager
async def _dummy_slot():
    yield


async def _prepare_job_env(
    env, monkeypatch: pytest.MonkeyPatch, *, pid: int = 1, frame_number: int = 1
):
    """Project+Frame+scenes png; возвращает (factory, project, frame, dirs)."""
    _, factory = env
    monkeypatch.setattr(gv, "acquire_outsee_slot", _dummy_slot)
    async with factory() as session:
        p = await _mk_project(session, pid)
        fr = Frame(
            project_id=pid,
            number=frame_number,
            animation_prompt="pan left",
            voiceover_text="v",
            status=FrameStatus.animation_prompt_ready,
            attrs={"video_gen_inflight": True},
        )
        session.add(fr)
        await session.commit()
        _valid_png(p.data_dir / "scenes" / f"frame_{frame_number:03d}_abc12345.png")
        (p.data_dir / "videos").mkdir(parents=True, exist_ok=True)
        fr_id = fr.id
    return factory, p, fr_id


@pytest.mark.asyncio
async def test_shot1_job_duplicate_links_existing_clip(
    env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dup-сигнал из retry-слоя → артефакт на существующий файл, второго нет."""
    factory, p, fr_id = await _prepare_job_env(env, monkeypatch)
    videos = p.data_dir / "videos"
    existing = _clip(videos, "clip_001_aaaaaaaa.mp4", b"same" * 500)
    gen_calls = 0

    async def fake_gen(outsee, gpt, *, prompt, out_path, **kwargs):
        nonlocal gen_calls
        gen_calls += 1
        raise OutseeDuplicateVideoError(
            "outsee video: скачан дубликат имеющегося ролика",
            context={"duplicate_of": str(existing), "gen_id": "g9"},
        )

    monkeypatch.setattr(gv, "generate_video_with_retries", fake_gen)

    ok = await gv._shot1_job(
        project_id=p.id,
        frame_id=fr_id,
        out_dir=videos,
        scenes_dir=p.data_dir / "scenes",
        outsee=None,
        gpt=None,
        session_clip_paths=[],
        clips_lock=asyncio.Lock(),
    )

    assert ok is True
    assert gen_calls == 1
    async with factory() as session:
        arts = (
            await session.execute(
                select(Artifact).where(
                    Artifact.project_id == p.id,
                    Artifact.kind == ArtifactKind.scene_video,
                )
            )
        ).scalars().all()
        assert len(arts) == 1, "никакого второго артефакта"
        assert Path(arts[0].path) == existing
        fr = await session.get(Frame, fr_id)
        assert fr.status == FrameStatus.video_generated
        assert not (fr.attrs or {}).get("video_gen_inflight")
    # Новых клипов не появилось — только исходный.
    assert [f.name for f in videos.glob("clip_001_*.mp4")] == [existing.name]


@pytest.mark.asyncio
async def test_shot1_job_gc_older_siblings_and_stable_prefix(
    env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """После успешной записи старые sibling'и → old/videos; retry = тот же путь."""
    factory, p, fr_id = await _prepare_job_env(env, monkeypatch)
    videos = p.data_dir / "videos"
    old = _clip(videos, "clip_001_old0ld0.mp4", b"old" * 500, age_s=10)
    s2 = _clip(videos, "clip_001_s2_keepme.mp4", b"s2" * 500)
    calls: list[dict] = []

    async def fake_gen(outsee, gpt, *, prompt, out_path, **kwargs):
        calls.append({"out_path": Path(out_path), **kwargs})
        Path(out_path).write_bytes(b"fresh" * 500)
        return GenerationResult(file_path=Path(out_path), gen_id="g1")

    monkeypatch.setattr(gv, "generate_video_with_retries", fake_gen)
    gv._reset_run_clip_ids()
    try:
        ok1 = await gv._shot1_job(
            project_id=p.id,
            frame_id=fr_id,
            out_dir=videos,
            scenes_dir=p.data_dir / "scenes",
            outsee=None,
            gpt=None,
            session_clip_paths=[],
            clips_lock=asyncio.Lock(),
        )
        assert ok1 is True
        assert not old.exists(), "старый sibling уехал в old/videos"
        assert list((p.data_dir / "old" / "videos").rglob("clip_001_old0ld0.mp4"))
        assert s2.is_file(), "другой шот не трогаем"
        new_clip = calls[0]["out_path"]
        assert new_clip.is_file()
        assert len(list(videos.glob("clip_001_*.mp4"))) == 2  # new + s2

        async with factory() as session:
            arts = (
                await session.execute(
                    select(Artifact).where(
                        Artifact.project_id == p.id,
                        Artifact.kind == ArtifactKind.scene_video,
                    )
                )
            ).scalars().all()
            assert len(arts) == 1
            assert Path(arts[0].path) == new_clip

        # Повторный заход того же кадра в ЭТОМ ЖЕ прогоне — тот же путь и ID.
        ok2 = await gv._shot1_job(
            project_id=p.id,
            frame_id=fr_id,
            out_dir=videos,
            scenes_dir=p.data_dir / "scenes",
            outsee=None,
            gpt=None,
            session_clip_paths=[],
            clips_lock=asyncio.Lock(),
        )
        assert ok2 is True
        assert calls[1]["out_path"] == calls[0]["out_path"]
        assert calls[1]["prompt_id_prefix"] == calls[0]["prompt_id_prefix"]
    finally:
        gv._reset_run_clip_ids()


# ── D. стабильный idempotency-префикс ────────────────────────────────────────


def test_stable_clip_uuid_per_frame_shot_within_run() -> None:
    gv._reset_run_clip_ids()
    try:
        a = gv._stable_clip_uuid(5, 1)
        assert gv._stable_clip_uuid(5, 1) == a, "retry кадра — тот же uuid"
        assert gv._stable_clip_uuid(5, 2) != a, "shot_02 — свой uuid"
        assert gv._stable_clip_uuid(6, 1) != a, "другой кадр — свой uuid"
        gv._reset_run_clip_ids()
        assert gv._stable_clip_uuid(5, 1) != a, "новый прогон — новый uuid"
    finally:
        gv._reset_run_clip_ids()
