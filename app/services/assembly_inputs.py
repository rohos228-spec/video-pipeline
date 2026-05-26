"""Поиск озвучки, BGM и видеоклипов для шага assemble."""

from __future__ import annotations

from pathlib import Path

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Artifact, ArtifactKind, Frame, Project
from app.services.assembly import ClipSpec, parse_frame_number_from_path
from app.services.xlsx_v8_plan import PlanColumn, read_plan_columns

# Озвучка и BGM: mp3 или wav (ffmpeg / faster-whisper принимают оба).
AUDIO_FILE_SUFFIXES = frozenset({".mp3", ".wav"})


def is_supported_audio(path: Path) -> bool:
    return path.suffix.lower() in AUDIO_FILE_SUFFIXES and path.is_file()


def _meta_audio_path(project: Project, *keys: str) -> Path | None:
    meta = project.meta or {}
    for key in keys:
        raw = meta.get(key)
        if not raw:
            continue
        p = Path(str(raw))
        if is_supported_audio(p):
            return p
    return None


def _latest_audio_glob(audio_dir: Path, patterns: tuple[str, ...]) -> Path | None:
    candidates: list[Path] = []
    for pat in patterns:
        candidates.extend(audio_dir.glob(pat))
    valid = [p for p in candidates if is_supported_audio(p)]
    if not valid:
        return None
    return sorted(valid, key=lambda p: p.stat().st_mtime)[-1]


def resolve_voice_path(
    project: Project,
    artifact_path: str | None = None,
) -> Path | None:
    """Озвучка: артефакт audio, meta или audio/voice*.{mp3,wav}."""
    if artifact_path:
        p = Path(artifact_path)
        if is_supported_audio(p):
            return p

    meta_p = _meta_audio_path(
        project, "assembly_voice_path", "voice_path", "narration_path"
    )
    if meta_p is not None:
        return meta_p

    audio_dir = project.data_dir / "audio"
    if not audio_dir.is_dir():
        return None
    return _latest_audio_glob(
        audio_dir,
        ("voice*.mp3", "voice*.wav", "narration*.mp3", "narration*.wav"),
    )


def resolve_bgm_path(project: Project) -> Path | None:
    """Фоновая музыка: meta → audio/bgm*.{mp3,wav} → audio/music*.{mp3,wav}."""
    meta_p = _meta_audio_path(project, "assembly_bgm_path", "mass_bgm_path", "bgm_path")
    if meta_p is not None:
        return meta_p

    audio_dir = project.data_dir / "audio"
    if not audio_dir.is_dir():
        return None
    return _latest_audio_glob(
        audio_dir,
        ("bgm*.mp3", "bgm*.wav", "music*.mp3", "music*.wav"),
    )


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


async def resolve_voice_artifact_path(
    session: AsyncSession,
    project: Project,
) -> Path:
    """Путь к озвучке для сборки (артефакт или файл на диске)."""
    audio_art = (
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
    art_path = audio_art.path if audio_art else None
    resolved = resolve_voice_path(project, art_path)
    if resolved is None:
        raise RuntimeError(
            "нет озвучки (mp3/wav): шаг 10 или положите audio/voice.mp3 "
            "или audio/voice.wav"
        )
    return resolved


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
