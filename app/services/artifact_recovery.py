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

_CLIP_RE = re.compile(r"^clip_(\d{3})_", re.I)
_FRAME_MP3_RE = re.compile(r"^frame_(\d{3})\.mp3$", re.I)
_VOICE_FULL_EXTS = (".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac")


def find_voice_full_on_disk(audio_dir: Path) -> Path | None:
    """voice_full на диске: mp3, wav, … (не frame_*)."""
    if not audio_dir.is_dir():
        return None
    candidates: list[Path] = []
    for ext in _VOICE_FULL_EXTS:
        candidates.extend(p for p in audio_dir.glob(f"voice_full_*{ext}") if p.is_file())
        legacy = audio_dir / f"voice_full{ext}"
        if legacy.is_file():
            candidates.append(legacy)
    if not candidates:
        for ext in _VOICE_FULL_EXTS:
            for p in audio_dir.glob(f"*{ext}"):
                if p.is_file() and not p.name.lower().startswith("frame_"):
                    if ".asr_mono." in p.name.lower():
                        continue
                    candidates.append(p)
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_size)


_S2_IN_NAME = re.compile(r"(^|_)s2(_|$)", re.I)


def _is_shot2_clip_name(name: str) -> bool:
    return bool(_S2_IN_NAME.search(name))


async def recover_scene_videos_from_disk(
    session: AsyncSession, project: Project
) -> list[int]:
    """Привязать clip_XXX_*.mp4 из data/.../videos/ к Frame как scene_video."""
    videos_dir = project.data_dir / "videos"
    if not videos_dir.is_dir():
        return []
    frames = (
        await session.execute(
            select(Frame).where(Frame.project_id == project.id).order_by(Frame.number)
        )
    ).scalars().all()
    by_number = {f.number: f for f in frames}
    recovered: list[int] = []
    for path in sorted(videos_dir.glob("clip_*.mp4")):
        m = _CLIP_RE.match(path.name)
        if not m:
            continue
        num = int(m.group(1))
        fr = by_number.get(num)
        if fr is None:
            continue
        if _is_shot2_clip_name(path.name):
            continue
        existing = (
            await session.execute(
                select(Artifact)
                .where(
                    Artifact.project_id == project.id,
                    Artifact.frame_id == fr.id,
                    Artifact.kind == ArtifactKind.scene_video,
                )
                .order_by(Artifact.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if existing is not None and Path(existing.path).is_file():
            if fr.status not in (
                FrameStatus.video_generated,
                FrameStatus.video_approved,
                FrameStatus.done,
            ):
                fr.status = FrameStatus.video_generated
            continue
        session.add(
            Artifact(
                project_id=project.id,
                frame_id=fr.id,
                kind=ArtifactKind.scene_video,
                uuid=uuid.uuid4().hex,
                path=str(path.resolve()),
            )
        )
        if fr.status not in (
            FrameStatus.video_generated,
            FrameStatus.video_approved,
            FrameStatus.done,
        ):
            fr.status = FrameStatus.video_generated
        recovered.append(num)
    if recovered:
        await session.flush()
        logger.info(
            "[#{}] artifact_recovery: scene_video с диска для кадров {}",
            project.id,
            recovered,
        )
    return recovered


async def recover_audio_from_disk(
    session: AsyncSession, project: Project
) -> bool:
    """Зарегистрировать voice_full (mp3/wav/…) как ArtifactKind.audio."""
    audio_dir = project.data_dir / "audio"
    full_path = find_voice_full_on_disk(audio_dir)

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
    if existing is not None:
        if Path(existing.path).is_file():
            return False
        if full_path is not None:
            existing.path = str(full_path.resolve())
            meta = dict(existing.meta or {})
            meta.update({"mode": "full_voice", "recovered_from_disk": True})
            existing.meta = meta
            await session.flush()
            logger.info(
                "[#{}] artifact_recovery: audio ← {} (stale row fixed)",
                project.id,
                full_path.name,
            )
            return True

    if not audio_dir.is_dir():
        return False

    if full_path is None:
        from app.services.frame_audio import FRAME_AUDIO_PREFIX, frame_audio_path

        frame_rows = (
            await session.execute(
                select(Frame.number).where(
                    Frame.project_id == project.id,
                    Frame.voiceover_text.isnot(None),
                    Frame.voiceover_text != "",
                )
            )
        ).scalars().all()
        frame_nums = sorted(int(n) for n in frame_rows)
        if frame_nums and all(frame_audio_path(audio_dir, n).is_file() for n in frame_nums):
            full_path = frame_audio_path(audio_dir, frame_nums[0])
            session.add(
                Artifact(
                    project_id=project.id,
                    kind=ArtifactKind.audio,
                    uuid=uuid.uuid4().hex,
                    path=str(full_path.resolve()),
                    meta={
                        "mode": "per_frame",
                        "recovered_from_disk": True,
                        "frame_count": len(frame_nums),
                    },
                )
            )
            await session.flush()
            logger.info(
                "[#{}] artifact_recovery: audio per_frame ({} clips)",
                project.id,
                len(frame_nums),
            )
            return True
        return False

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
    """Подхватить последний words_*.json в audio/, если артефакта нет."""
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
    if existing is not None:
        if Path(existing.path).is_file():
            return False
        logger.warning(
            "[#{}] artifact_recovery: stale whisper_words (нет файла {}) — удаляем запись",
            project.id,
            existing.path,
        )
        await session.delete(existing)
        await session.flush()

    audio_dir = project.data_dir / "audio"
    if not audio_dir.is_dir():
        return False
    candidates = sorted(
        audio_dir.glob("words_*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        return False
    path = candidates[0]
    session.add(
        Artifact(
            project_id=project.id,
            kind=ArtifactKind.whisper_words,
            uuid=uuid.uuid4().hex,
            path=str(path.resolve()),
        )
    )
    await session.flush()
    logger.info("[#{}] artifact_recovery: whisper_words ← {}", project.id, path.name)
    return True


async def recover_before_assemble(session: AsyncSession, project: Project) -> None:
    await recover_scene_videos_from_disk(session, project)
    if not await recover_audio_from_disk(session, project):
        await ensure_fleet_montage_voice(session, project)
    await recover_whisper_from_disk(session, project)


async def ensure_fleet_montage_voice(session: AsyncSession, project: Project) -> bool:
    """Fleet hub: voiceover.txt → local TTS, если нет voice_full на диске."""
    from app.fleet.montage_handoff import is_fleet_hub_montage

    if not is_fleet_hub_montage(project):
        return False
    if await recover_audio_from_disk(session, project):
        return True
    if find_voice_full_on_disk(project.data_dir / "audio") is not None:
        return await recover_audio_from_disk(session, project)
    vo = project.data_dir / "voiceover.txt"
    if not vo.is_file() and not (project.script_text or "").strip():
        return False
    try:
        from app.services.local_tts import synthesize_local_voice_for_montage

        path = await synthesize_local_voice_for_montage(
            project, project.data_dir / "audio"
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[#{}] ensure_fleet_montage_voice: {}", project.id, exc)
        return False
    session.add(
        Artifact(
            project_id=project.id,
            kind=ArtifactKind.audio,
            uuid=uuid.uuid4().hex,
            path=str(path.resolve()),
            meta={"mode": "full_voice", "source": "local_tts", "fleet_montage": True},
        )
    )
    await session.flush()
    logger.info("[#{}] ensure_fleet_montage_voice: {}", project.id, path.name)
    return True


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
