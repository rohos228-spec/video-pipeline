"""Поиск озвучки, BGM и видеоклипов для шага assemble."""

from __future__ import annotations

from pathlib import Path

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Artifact, ArtifactKind, Frame, Project
from app.services.assembly import ClipSpec, parse_frame_number_from_path
from app.services.xlsx_v8_plan import PlanColumn, read_plan_columns


def resolve_bgm_path(project: Project) -> Path | None:
    """Фоновая музыка: meta → audio/bgm* → audio/music*."""
    meta = project.meta or {}
    for key in ("assembly_bgm_path", "mass_bgm_path", "bgm_path"):
        raw = meta.get(key)
        if raw:
            p = Path(str(raw))
            if p.exists():
                return p

    audio_dir = project.data_dir / "audio"
    if not audio_dir.is_dir():
        return None
    patterns = ("bgm*.mp3", "bgm*.wav", "bgm*.m4a", "music*.mp3", "music*.wav")
    for pat in patterns:
        found = sorted(audio_dir.glob(pat))
        if found:
            return found[-1]
    return None


def bgm_enabled_for_project(project: Project) -> bool:
    meta = project.meta or {}
    if "mass_bgm_enabled" in meta:
        return bool(meta["mass_bgm_enabled"])
    return bool(meta.get("assembly_bgm_enabled", True))


async def load_plan_from_xlsx(project: Project) -> list[PlanColumn] | None:
    xlsx = project.data_dir / "project.xlsx"
    if not xlsx.exists():
        return None
    try:
        return read_plan_columns(xlsx)
    except Exception as e:  # noqa: BLE001
        logger.warning("[#{}] не прочитали лист «план»: {}", project.id, e)
        return None


async def resolve_scene_video_path(
    session: AsyncSession,
    project: Project,
    frame: Frame,
) -> Path | None:
    """Артефакт scene_video по frame_id, иначе clip_NNN_* в videos/."""
    video_art = (
        await session.execute(
            select(Artifact)
            .where(
                Artifact.project_id == project.id,
                Artifact.frame_id == frame.id,
                Artifact.kind == ArtifactKind.scene_video,
            )
            .order_by(Artifact.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if video_art is not None:
        p = Path(video_art.path)
        if p.exists():
            return p

    videos_dir = project.data_dir / "videos"
    if not videos_dir.is_dir():
        return None
    prefix = f"clip_{frame.number:03d}_"
    matches = sorted(videos_dir.glob(f"{prefix}*.mp4"))
    if matches:
        return matches[-1]
    # fallback: любой файл, где номер кадра в имени
    for p in sorted(videos_dir.glob("clip_*.mp4")):
        if parse_frame_number_from_path(p) == frame.number:
            return p
    return None


async def build_clip_specs(
    session: AsyncSession,
    project: Project,
    frames: list[Frame],
    plan_columns: list[PlanColumn] | None,
) -> list[ClipSpec]:
    """Клипы в порядке кадров; длительность из Whisper (Frame)."""
    voice_by_frame = {c.frame_number: c.voiceover_text for c in (plan_columns or [])}
    specs: list[ClipSpec] = []
    for fr in frames:
        src = await resolve_scene_video_path(session, project, fr)
        if src is None:
            raise RuntimeError(f"нет видеоклипа для кадра {fr.number}")
        duration = fr.duration_seconds or ((fr.end_ts or 0.0) - (fr.start_ts or 0.0))
        if duration <= 0:
            raise RuntimeError(f"длительность кадра {fr.number} ≤ 0 (нужен шаг audio/Whisper)")
        if plan_columns and fr.number in voice_by_frame:
            if not (fr.voiceover_text or "").strip():
                fr.voiceover_text = voice_by_frame[fr.number]
        specs.append(
            ClipSpec(src=src, duration=float(duration), frame_number=fr.number)
        )
    return specs
