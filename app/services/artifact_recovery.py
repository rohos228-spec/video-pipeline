"""Восстановление записей Artifact из файлов на диске (после сбоя сессии / отката БД)."""

from __future__ import annotations

import re
import shutil
import uuid
from pathlib import Path

from loguru import logger
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Artifact,
    ArtifactKind,
    Frame,
    FrameStatus,
    HITLDecision,
    HITLKind,
    HITLRequest,
    Project,
)
from app.services.frame_audio import find_voice_full_on_disk

_CLIP_RE = re.compile(r"^clip_(\d{3})_", re.I)
_CLIP_S2_RE = re.compile(r"^clip_(\d{3})_s2_", re.I)
_FRAME_IMG_RE = re.compile(r"^frame_(\d{3})_", re.I)
_FRAME_MP3_RE = re.compile(r"^frame_(\d{3})\.mp3$", re.I)


def newest_disk_video(videos_dir: Path, frame_number: int, shot: int) -> Path | None:
    if shot == 2:
        candidates = [
            p
            for p in videos_dir.glob(f"clip_{frame_number:03d}_s2_*.mp4")
            if p.is_file()
        ]
    else:
        candidates = [
            p
            for p in videos_dir.glob(f"clip_{frame_number:03d}_*.mp4")
            if p.is_file() and "_s2_" not in p.name
        ]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


async def recover_scene_videos_from_disk(
    session: AsyncSession, project: Project
) -> list[int]:
    """Привязать clip_XXX_*.mp4 / clip_XXX_s2_*.mp4; newer-on-disk заменяет stale Artifact."""
    from app.services.plan_shot2 import effective_shot_from_artifact

    videos_dir = project.data_dir / "videos"
    if not videos_dir.is_dir():
        return []
    frames = (
        await session.execute(
            select(Frame).where(Frame.project_id == project.id).order_by(Frame.number)
        )
    ).scalars().all()
    recovered: list[int] = []

    for fr in frames:
        for shot in (1, 2):
            newest = newest_disk_video(videos_dir, fr.number, shot)
            if newest is None:
                continue
            arts = (
                await session.execute(
                    select(Artifact)
                    .where(
                        Artifact.project_id == project.id,
                        Artifact.frame_id == fr.id,
                        Artifact.kind == ArtifactKind.scene_video,
                    )
                    .order_by(Artifact.id.desc())
                )
            ).scalars().all()
            shot_arts = [
                a
                for a in arts
                if a.path
                and effective_shot_from_artifact(a.meta, a.path) == shot
            ]
            current = next(
                (a for a in shot_arts if Path(a.path).is_file()),
                None,
            )
            if current is not None:
                cur_path = Path(current.path)
                try:
                    same = cur_path.resolve() == newest.resolve()
                    newer_disk = newest.stat().st_mtime > cur_path.stat().st_mtime + 0.01
                except OSError:
                    same = False
                    newer_disk = True
                if same or not newer_disk:
                    if shot == 1 and fr.status not in (
                        FrameStatus.video_generated,
                        FrameStatus.video_approved,
                        FrameStatus.done,
                    ):
                        fr.status = FrameStatus.video_generated
                    continue
                for a in shot_arts:
                    await session.delete(a)
            elif shot_arts:
                for a in shot_arts:
                    await session.delete(a)

            session.add(
                Artifact(
                    project_id=project.id,
                    frame_id=fr.id,
                    kind=ArtifactKind.scene_video,
                    uuid=uuid.uuid4().hex,
                    path=str(newest.resolve()),
                    meta={"shot": shot},
                )
            )
            if shot == 1 and fr.status not in (
                FrameStatus.video_generated,
                FrameStatus.video_approved,
                FrameStatus.done,
            ):
                fr.status = FrameStatus.video_generated
            recovered.append(fr.number)
    if recovered:
        await session.flush()
        logger.info(
            "[#{}] artifact_recovery: scene_video с диска для кадров {}",
            project.id,
            recovered,
        )
    return recovered


async def recover_scene_images_from_disk(
    session: AsyncSession, project: Project
) -> list[int]:
    """Привязать frame_NNN_*.png / frame_NNN_s2_*.png; newer-on-disk заменяет stale Artifact."""
    from app.services.plan_shot2 import find_shot1_image, find_shot2_image
    from app.services.scan_frames import is_valid_scene_image

    scenes_dir = project.data_dir / "scenes"
    if not scenes_dir.is_dir():
        return []
    frames = (
        await session.execute(
            select(Frame).where(Frame.project_id == project.id).order_by(Frame.number)
        )
    ).scalars().all()
    recovered: list[int] = []
    for fr in frames:
        for shot, finder in ((1, find_shot1_image), (2, find_shot2_image)):
            path = finder(scenes_dir, fr.number)
            if path is None:
                continue
            if shot == 1 and not is_valid_scene_image(path):
                continue
            if shot == 2:
                try:
                    if path.stat().st_size < 64:
                        continue
                except OSError:
                    continue
            arts = (
                await session.execute(
                    select(Artifact)
                    .where(
                        Artifact.project_id == project.id,
                        Artifact.frame_id == fr.id,
                        Artifact.kind == ArtifactKind.scene_image,
                    )
                    .order_by(Artifact.id.desc())
                )
            ).scalars().all()
            shot_arts = []
            for a in arts:
                meta_shot = (a.meta or {}).get("shot", 1)
                if (shot == 2 and meta_shot == 2) or (shot == 1 and meta_shot != 2):
                    shot_arts.append(a)
            current = next(
                (a for a in shot_arts if a.path and Path(a.path).is_file()),
                None,
            )
            if current is not None:
                cur_path = Path(current.path)
                try:
                    same = cur_path.resolve() == path.resolve()
                    newer_disk = path.stat().st_mtime > cur_path.stat().st_mtime + 0.01
                except OSError:
                    same = False
                    newer_disk = True
                if same or not newer_disk:
                    if shot == 1 and fr.status is FrameStatus.image_prompt_ready:
                        fr.status = FrameStatus.image_generated
                    continue
                for a in shot_arts:
                    await session.delete(a)
            elif shot_arts:
                for a in shot_arts:
                    await session.delete(a)

            session.add(
                Artifact(
                    project_id=project.id,
                    frame_id=fr.id,
                    kind=ArtifactKind.scene_image,
                    uuid=uuid.uuid4().hex,
                    path=str(path.resolve()),
                    meta={"shot": shot},
                )
            )
            if shot == 1 and fr.status in (
                FrameStatus.image_prompt_ready,
                FrameStatus.planned,
            ):
                fr.status = FrameStatus.image_generated
            recovered.append(fr.number)
    if recovered:
        await session.flush()
        logger.info(
            "[#{}] artifact_recovery: scene_image с диска для кадров {}",
            project.id,
            recovered[:40],
        )
    return recovered


def restore_scene_images_from_old(project: Project) -> dict[str, int]:
    """Скопировать frame_*.png из old/scenes/<timestamp>/ обратно в scenes/."""
    scenes_dir = project.data_dir / "scenes"
    scenes_dir.mkdir(parents=True, exist_ok=True)
    old_root = project.data_dir / "old" / "scenes"
    if not old_root.is_dir():
        return {"restored": 0, "skipped_existing": 0, "backup_dirs": 0}

    best_by_frame: dict[int, Path] = {}
    backup_dirs = 0
    for batch_dir in sorted(old_root.iterdir()):
        if not batch_dir.is_dir():
            continue
        backup_dirs += 1
        for src in batch_dir.glob("frame_*.png"):
            m = _FRAME_IMG_RE.match(src.name)
            if not m:
                continue
            num = int(m.group(1))
            prev = best_by_frame.get(num)
            if prev is None or src.stat().st_mtime > prev.stat().st_mtime:
                best_by_frame[num] = src

    restored = 0
    skipped = 0
    for num, src in sorted(best_by_frame.items()):
        existing = list(scenes_dir.glob(f"frame_{num:03d}_*.png"))
        if existing:
            newest = max(existing, key=lambda p: p.stat().st_mtime)
            if newest.stat().st_size >= src.stat().st_size:
                skipped += 1
                continue
        dest = scenes_dir / src.name
        if dest.exists():
            skipped += 1
            continue
        shutil.copy2(src, dest)
        restored += 1

    if restored:
        logger.info(
            "[#{}] artifact_recovery: restored {} scene png from old/scenes ({} batches)",
            project.id,
            restored,
            backup_dirs,
        )
    return {
        "restored": restored,
        "skipped_existing": skipped,
        "backup_dirs": backup_dirs,
    }


async def recover_scene_images_full(
    session: AsyncSession, project: Project
) -> dict[str, int | list[int]]:
    """old/scenes → scenes/ → артефакты БД → статусы кадров."""
    from app.services.scan_frames import sync_frames_with_disk_images

    old_stats = restore_scene_images_from_old(project)
    recovered = await recover_scene_images_from_disk(session, project)
    synced = await sync_frames_with_disk_images(session, project)
    return {
        **old_stats,
        "artifacts_registered": len(recovered),
        "frames_synced": synced,
        "frame_numbers": recovered,
    }


async def recover_audio_from_disk(
    session: AsyncSession, project: Project
) -> bool:
    """Зарегистрировать готовую озвучку на диске как ArtifactKind.audio."""
    full_path = find_voice_full_on_disk(project.data_dir)
    if full_path is None:
        return False

    existing = (
        await session.execute(
            select(Artifact)
            .where(
                Artifact.project_id == project.id,
                Artifact.kind == ArtifactKind.audio,
            )
            .order_by(Artifact.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing is not None and existing.path:
        ep = Path(existing.path)
        if ep.is_file() and ep.resolve() == full_path.resolve():
            return False

    audio_dir = project.data_dir / "audio"
    clip_meta: list[dict] = []
    for mp3 in sorted(audio_dir.glob("frame_*.mp3")):
        m = _FRAME_MP3_RE.match(mp3.name)
        if m:
            clip_meta.append({"frame_number": int(m.group(1)), "path": str(mp3)})

    session.add(
        Artifact(
            project_id=project.id,
            kind=ArtifactKind.audio,
            uuid=uuid.uuid4().hex,
            path=str(full_path.resolve()),
            meta={
                "mode": "full_voice",
                "recovered_from_disk": True,
            },
        )
    )
    await session.flush()
    logger.info(
        "[#{}] artifact_recovery: audio ← {} (full_voice)",
        project.id,
        full_path.name,
    )
    return True


async def recover_whisper_from_disk(
    session: AsyncSession, project: Project
) -> bool:
    """Подхватить words_*.json; обновить запись, если путь в БД битый."""
    existing = (
        await session.execute(
            select(Artifact)
            .where(
                Artifact.project_id == project.id,
                Artifact.kind == ArtifactKind.whisper_words,
            )
            .order_by(Artifact.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing is not None and existing.path and Path(existing.path).is_file():
        return False

    audio_dir = project.data_dir / "audio"
    if not audio_dir.is_dir():
        return False
    candidates = sorted(
        audio_dir.glob("words_*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    path = next((p for p in candidates if p.is_file()), None)
    if path is None:
        return False

    resolved = str(path.resolve())
    if existing is not None:
        existing.path = resolved
        await session.flush()
        logger.info(
            "[#{}] artifact_recovery: whisper_words обновлён ← {} (старый путь отсутствовал)",
            project.id,
            path.name,
        )
        return True

    session.add(
        Artifact(
            project_id=project.id,
            kind=ArtifactKind.whisper_words,
            uuid=uuid.uuid4().hex,
            path=resolved,
        )
    )
    await session.flush()
    logger.info("[#{}] artifact_recovery: whisper_words ← {}", project.id, path.name)
    return True


async def ensure_whisper_words(
    session: AsyncSession,
    project: Project,
    audio_path: Path,
    *,
    whisper_model: str,
) -> list:
    """Загрузить words.json; при отсутствии — Whisper по voice и сохранить артефакт."""
    import asyncio

    from app.services.media_probe import probe_duration
    from app.services.whisper import (
        dump_words_json,
        load_words_json,
        transcribe_words,
        whisper_available,
    )

    await recover_whisper_from_disk(session, project)

    whisper_art = (
        await session.execute(
            select(Artifact)
            .where(
                Artifact.project_id == project.id,
                Artifact.kind == ArtifactKind.whisper_words,
            )
            .order_by(Artifact.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if whisper_art is not None and whisper_art.path:
        wp = Path(whisper_art.path)
        if wp.is_file():
            return load_words_json(wp)

    if not audio_path.is_file():
        return []
    if not whisper_available():
        return []

    logger.info(
        "[#{}] ensure_whisper_words: words.json нет — whisper по {}",
        project.id,
        audio_path.name,
    )
    duration = await probe_duration(audio_path)
    words = await asyncio.to_thread(
        transcribe_words,
        audio_path,
        model_name=whisper_model,
        language="ru",
        beam_size=1 if duration > 300 else 5,
    )
    audio_dir = project.data_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    words_path = audio_dir / f"words_{uuid.uuid4().hex[:8]}.json"
    dump_words_json(words, words_path)
    session.add(
        Artifact(
            project_id=project.id,
            kind=ArtifactKind.whisper_words,
            uuid=uuid.uuid4().hex,
            path=str(words_path.resolve()),
        )
    )
    await session.flush()
    logger.info(
        "[#{}] ensure_whisper_words: сохранён {} ({} слов)",
        project.id,
        words_path.name,
        len(words),
    )
    return words


async def recover_music_from_disk(
    session: AsyncSession, project: Project
) -> bool:
    """Зарегистрировать music/*.mp3 как ArtifactKind.music, если записи нет."""
    existing = (
        await session.execute(
            select(Artifact)
            .where(
                Artifact.project_id == project.id,
                Artifact.kind == ArtifactKind.music,
            )
            .order_by(Artifact.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing is not None and existing.path and Path(existing.path).is_file():
        return False

    music_dir = project.data_dir / "music"
    if not music_dir.is_dir():
        return False
    candidates = sorted(
        music_dir.glob("*.mp3"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    path = next((p for p in candidates if p.is_file()), None)
    if path is None:
        return False

    session.add(
        Artifact(
            project_id=project.id,
            kind=ArtifactKind.music,
            uuid=uuid.uuid4().hex,
            path=str(path.resolve()),
            meta={"recovered_from_disk": True},
        )
    )
    await session.flush()
    logger.info("[#{}] artifact_recovery: music ← {}", project.id, path.name)
    return True


async def recover_before_assemble(session: AsyncSession, project: Project) -> None:
    await recover_scene_videos_from_disk(session, project)
    await recover_audio_from_disk(session, project)
    await recover_whisper_from_disk(session, project)
    await recover_music_from_disk(session, project)


_CHAR_ID_RE = re.compile(r"^(c\d+)\.png$", re.I)
_CHAR_ID_IN_NAME_RE = re.compile(r"(c\d+)\.png$", re.I)


def _latest_approved_hero_hitl(
    rows: list[HITLRequest],
) -> dict[str, HITLRequest]:
    """Последний approved/regenerate HITL по excel_id персонажа."""
    out: dict[str, HITLRequest] = {}
    for row in sorted(rows, key=lambda r: r.id):
        payload = row.payload or {}
        excel_id = payload.get("excel_id")
        if not isinstance(excel_id, str) or not excel_id:
            continue
        if row.decision not in (HITLDecision.approved, HITLDecision.regenerate):
            continue
        out[excel_id.lower()] = row
    return out


def _restore_hero_png_from_path(
    session: AsyncSession,
    project: Project,
    excel_id: str,
    src: Path,
    *,
    hitl: HITLRequest | None = None,
) -> Path | None:
    chars_dir = project.data_dir / "characters"
    chars_dir.mkdir(parents=True, exist_ok=True)
    dest = chars_dir / f"{excel_id.lower()}.png"
    shutil.copy2(src, dest)
    payload = (hitl.payload or {}) if hitl else {}
    session.add(
        Artifact(
            project_id=project.id,
            kind=ArtifactKind.hero_reference,
            uuid=uuid.uuid4().hex,
            path=str(dest.resolve()),
            meta={
                "excel_id": excel_id.lower(),
                "recovered": True,
                "from_hitl_id": hitl.id if hitl else None,
                "excel_ref_ids": payload.get("excel_ref_ids") or [],
            },
        )
    )
    return dest


async def recover_hero_references_from_old_dir(
    session: AsyncSession,
    project: Project,
) -> list[str]:
    """Восстановить cNN.png из data/.../old/characters/ (после wipe)."""
    old_dir = project.data_dir / "old" / "characters"
    if not old_dir.is_dir():
        return []
    by_id: dict[str, Path] = {}
    for path in old_dir.glob("*.png"):
        m = _CHAR_ID_IN_NAME_RE.search(path.name)
        if not m:
            continue
        cid = m.group(1).lower()
        prev = by_id.get(cid)
        if prev is None or path.stat().st_mtime > prev.stat().st_mtime:
            by_id[cid] = path
    restored: list[str] = []
    for cid, src in sorted(by_id.items()):
        existing = (
            await session.execute(
                select(Artifact)
                .where(
                    Artifact.project_id == project.id,
                    Artifact.kind == ArtifactKind.hero_reference,
                )
                .order_by(desc(Artifact.id))
            )
        ).scalars().all()
        if any((a.meta or {}).get("excel_id") == cid for a in existing):
            dest = project.data_dir / "characters" / f"{cid}.png"
            if dest.is_file():
                continue
        _restore_hero_png_from_path(session, project, cid, src)
        restored.append(cid)
    if restored:
        await session.flush()
        logger.info(
            "[#{}] artifact_recovery: heroes из old/characters: {}",
            project.id,
            restored,
        )
    return restored


async def recover_hero_references_from_hitl(
    session: AsyncSession,
    project: Project,
) -> list[str]:
    """Восстановить персонажей: HITL photo_path → old/ → Outsee gallery."""
    rows = (
        await session.execute(
            select(HITLRequest)
            .where(
                HITLRequest.project_id == project.id,
                HITLRequest.kind == HITLKind.approve_hero,
            )
            .order_by(HITLRequest.id)
        )
    ).scalars().all()
    by_id = _latest_approved_hero_hitl(rows)
    if not by_id:
        return []

    restored: list[str] = []
    need_outsee: list[tuple[str, HITLRequest]] = []

    for excel_id, hitl in sorted(by_id.items()):
        dest = project.data_dir / "characters" / f"{excel_id}.png"
        if dest.is_file() and dest.stat().st_size > 50_000:
            restored.append(excel_id)
            continue
        payload = hitl.payload or {}
        photo = payload.get("photo_path")
        if isinstance(photo, str) and Path(photo).is_file():
            _restore_hero_png_from_path(
                session, project, excel_id, Path(photo), hitl=hitl
            )
            restored.append(excel_id)
            continue
        prefix = payload.get("prompt_id_prefix")
        if isinstance(prefix, str) and prefix.strip():
            need_outsee.append((excel_id, hitl))

    if need_outsee:
        from app.bots.browser import browser_session
        from app.bots.outsee import (
            _download_via_card_click,
            _image_page_url,
            find_img_src_by_prompt_id_in_gallery,
        )

        async with browser_session() as bs:
            page = await bs.open_page(_image_page_url(None), reuse=True)
            for excel_id, hitl in need_outsee:
                payload = hitl.payload or {}
                prefix = str(payload.get("prompt_id_prefix") or "").strip()
                dest = project.data_dir / "characters" / f"{excel_id}.png"
                try:
                    img_url = await find_img_src_by_prompt_id_in_gallery(
                        page, prefix, limit=25
                    )
                    if not img_url:
                        logger.warning(
                            "[#{}] recover hero {}: [ID] не найден в Outsee",
                            project.id,
                            excel_id,
                        )
                        continue
                    await _download_via_card_click(
                        page,
                        prompt_id_prefix=prefix,
                        out_path=dest,
                        project_id=project.id,
                        img_url=img_url,
                    )
                    _restore_hero_png_from_path(
                        session, project, excel_id, dest, hitl=hitl
                    )
                    restored.append(excel_id)
                    logger.info(
                        "[#{}] recover hero {} из Outsee → {}",
                        project.id,
                        excel_id,
                        dest,
                    )
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        "[#{}] recover hero {} failed: {}",
                        project.id,
                        excel_id,
                        e,
                    )

    if restored:
        await session.flush()
    return sorted(set(restored))


async def recover_hero_references(
    session: AsyncSession,
    project: Project,
) -> list[str]:
    """Полное восстановление рефов персонажей (old/ → HITL → Outsee)."""
    from_old = await recover_hero_references_from_old_dir(session, project)
    from_hitl = await recover_hero_references_from_hitl(session, project)
    return sorted(set(from_old) | set(from_hitl))
