"""Post-check после шага: сверка Excel/файлов на диске и точечный regen.

См. docs/SPEC-RELIABILITY-QUEUE-GPT-MUSIC.md §2.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Artifact, ArtifactKind, Frame, FrameStatus, Project
from app.services.artifact_recovery import (
    recover_audio_from_disk,
    recover_scene_videos_from_disk,
)
from app.services.scan_frames import _disk_has_frame_image
from app.services.xlsx_v8_import import read_v8_active_frame_count


@dataclass
class ValidationResult:
    ok: bool
    expected_frames: int = 0
    missing_frame_numbers: list[int] = field(default_factory=list)
    duplicate_frame_numbers: list[int] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)


async def _expected_frame_count(session: AsyncSession, project: Project) -> int:
    xlsx = project.data_dir / "project.xlsx"
    n = read_v8_active_frame_count(xlsx) if xlsx.is_file() else 0
    if n > 0:
        return n
    frames = (
        await session.execute(
            select(Frame).where(
                Frame.project_id == project.id,
                Frame.voiceover_text.isnot(None),
                Frame.voiceover_text != "",
            )
        )
    ).scalars().all()
    if frames:
        return len(frames)
    all_frames = (
        await session.execute(select(Frame).where(Frame.project_id == project.id))
    ).scalars().all()
    return len(all_frames)


async def _frame_numbers(session: AsyncSession, project: Project) -> list[int]:
    frames = (
        await session.execute(
            select(Frame).where(Frame.project_id == project.id).order_by(Frame.number)
        )
    ).scalars().all()
    return [f.number for f in frames]


def _find_duplicate_numbers(numbers: list[int]) -> list[int]:
    seen: set[int] = set()
    dups: set[int] = set()
    for n in numbers:
        if n in seen:
            dups.add(n)
        seen.add(n)
    return sorted(dups)


async def _frames_with_artifact(
    session: AsyncSession,
    project: Project,
    kind: ArtifactKind,
) -> set[int]:
    rows = (
        await session.execute(
            select(Artifact.frame_id, Artifact.path)
            .where(
                Artifact.project_id == project.id,
                Artifact.kind == kind,
                Artifact.frame_id.isnot(None),
            )
        )
    ).all()
    out: set[int] = set()
    frames = (
        await session.execute(select(Frame).where(Frame.project_id == project.id))
    ).scalars().all()
    by_id = {f.id: f.number for f in frames}
    for frame_id, path in rows:
        if frame_id is None:
            continue
        if path and Path(path).is_file():
            num = by_id.get(frame_id)
            if num is not None:
                out.add(num)
    return out


async def validate_after_videos(
    session: AsyncSession, project: Project
) -> ValidationResult:
    await recover_scene_videos_from_disk(session, project)
    expected = await _expected_frame_count(session, project)
    numbers = await _frame_numbers(session, project)
    msgs: list[str] = []
    if expected <= 0:
        return ValidationResult(ok=False, messages=["нет кадров в Excel/БД"])
    if len(numbers) != expected:
        msgs.append(f"кадров в БД {len(numbers)}, в Excel {expected}")
    dups = _find_duplicate_numbers(numbers)
    if dups:
        msgs.append(f"дубликаты номеров кадров: {dups}")
    have = await _frames_with_artifact(
        session, project, ArtifactKind.scene_video
    )
    missing = sorted(set(range(1, expected + 1)) - have)
    if numbers and expected == len(numbers):
        missing = sorted(set(numbers) - have)
    ok = not missing and not dups and len(numbers) == expected
    if missing:
        msgs.append(f"нет клипов для кадров: {missing}")
    return ValidationResult(
        ok=ok,
        expected_frames=expected,
        missing_frame_numbers=missing,
        duplicate_frame_numbers=dups,
        messages=msgs,
    )


async def validate_after_images(
    session: AsyncSession, project: Project
) -> ValidationResult:
    expected = await _expected_frame_count(session, project)
    numbers = await _frame_numbers(session, project)
    msgs: list[str] = []
    if expected <= 0:
        return ValidationResult(ok=False, messages=["нет кадров в Excel/БД"])
    dups = _find_duplicate_numbers(numbers)
    if dups:
        msgs.append(f"дубликаты номеров кадров: {dups}")
    scenes = project.data_dir / "scenes"
    have: set[int] = set()
    for n in numbers:
        if _disk_has_frame_image(scenes, n):
            have.add(n)
    target = numbers if numbers else list(range(1, expected + 1))
    missing = sorted(set(target) - have)
    ok = not missing and not dups and len(numbers) == expected
    if missing:
        msgs.append(f"нет картинок для кадров: {missing}")
    return ValidationResult(
        ok=ok,
        expected_frames=expected,
        missing_frame_numbers=missing,
        duplicate_frame_numbers=dups,
        messages=msgs,
    )


async def validate_after_music(
    session: AsyncSession, project: Project
) -> ValidationResult:
    from app.services.bgm import find_bgm_file

    path = find_bgm_file(project)
    if path is not None and path.is_file():
        return ValidationResult(ok=True, messages=[])
    art = (
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
    if art and art.path and Path(art.path).is_file():
        return ValidationResult(ok=True, messages=[])
    return ValidationResult(
        ok=False,
        messages=["нет mp3 в music/"],
    )


async def validate_after_audio(
    session: AsyncSession, project: Project
) -> ValidationResult:
    await recover_audio_from_disk(session, project)
    expected = await _expected_frame_count(session, project)
    audio_dir = project.data_dir / "audio"
    missing: list[int] = []
    if expected > 0:
        for n in range(1, expected + 1):
            p = audio_dir / f"frame_{n:03d}.mp3"
            if not p.is_file():
                missing.append(n)
    voice_full = list(audio_dir.glob("voice_full_*.mp3")) if audio_dir.is_dir() else []
    msgs: list[str] = []
    if missing:
        msgs.append(f"нет frame_*.mp3: {missing}")
    if not voice_full:
        msgs.append("нет voice_full_*.mp3")
    ok = not missing and bool(voice_full)
    return ValidationResult(
        ok=ok,
        expected_frames=expected,
        missing_frame_numbers=missing,
        messages=msgs,
    )


async def mark_frames_for_video_regen(
    session: AsyncSession,
    project: Project,
    numbers: list[int],
) -> int:
    if not numbers:
        return 0
    frames = (
        await session.execute(
            select(Frame).where(
                Frame.project_id == project.id,
                Frame.number.in_(numbers),
            )
        )
    ).scalars().all()
    changed = 0
    for fr in frames:
        arts = (
            await session.execute(
                select(Artifact).where(
                    Artifact.project_id == project.id,
                    Artifact.frame_id == fr.id,
                    Artifact.kind == ArtifactKind.scene_video,
                )
            )
        ).scalars().all()
        for a in arts:
            if a.path:
                try:
                    Path(a.path).unlink(missing_ok=True)
                except OSError:
                    pass
            await session.delete(a)
        if fr.status not in (
            FrameStatus.animation_prompt_ready,
            FrameStatus.image_generated,
        ):
            fr.status = FrameStatus.animation_prompt_ready
            changed += 1
    if changed:
        await session.flush()
    logger.warning(
        "[#{}] post_validate: video regen queued for frames {}",
        project.id,
        numbers,
    )
    return changed


async def mark_frames_for_image_regen(
    session: AsyncSession,
    project: Project,
    numbers: list[int],
) -> int:
    if not numbers:
        return 0
    from app.services.scan_frames import reset_frames_to_image_prompt_ready

    return await reset_frames_to_image_prompt_ready(session, project, numbers)


async def mark_music_for_regen(session: AsyncSession, project: Project) -> None:
    arts = (
        await session.execute(
            select(Artifact).where(
                Artifact.project_id == project.id,
                Artifact.kind == ArtifactKind.music,
            )
        )
    ).scalars().all()
    for a in arts:
        if a.path:
            try:
                Path(a.path).unlink(missing_ok=True)
            except OSError:
                pass
        await session.delete(a)
    music_dir = project.data_dir / "music"
    if music_dir.is_dir():
        for p in music_dir.glob("*.mp3"):
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass
    await session.flush()
    logger.warning("[#{}] post_validate: music regen queued", project.id)


async def mark_audio_for_regen(session: AsyncSession, project: Project) -> None:
    audio_dir = project.data_dir / "audio"
    if audio_dir.is_dir():
        for p in audio_dir.glob("*.mp3"):
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass
    await session.execute(
        delete(Artifact).where(
            Artifact.project_id == project.id,
            Artifact.kind.in_([ArtifactKind.audio, ArtifactKind.whisper_words]),
        )
    )
    await session.flush()
    logger.warning("[#{}] post_validate: audio regen queued", project.id)


async def finalize_or_retry(
    session: AsyncSession,
    project: Project,
    *,
    step: str,
    ready_status,
    running_status,
) -> bool:
    """True если шаг можно перевести в ready, False если остались на running."""
    validators = {
        "video": validate_after_videos,
        "images": validate_after_images,
        "music": validate_after_music,
        "audio": validate_after_audio,
    }
    validate = validators.get(step)
    if validate is None:
        return True
    result = await validate(session, project)
    if result.ok:
        return True
    logger.warning(
        "[#{}] post_validate {} fail: {}",
        project.id,
        step,
        "; ".join(result.messages) or "unknown",
    )
    if step == "video":
        await mark_frames_for_video_regen(
            session, project, result.missing_frame_numbers
        )
    elif step == "images":
        await mark_frames_for_image_regen(
            session, project, result.missing_frame_numbers
        )
    elif step == "music":
        await mark_music_for_regen(session, project)
    elif step == "audio":
        await mark_audio_for_regen(session, project)
    project.status = running_status
    await session.flush()
    return False
