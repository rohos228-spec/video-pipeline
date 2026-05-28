"""Фоновая музыка (BGM) при финальной сборке."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from app.models import Project
from app.settings import settings

_BGM_FILENAMES = (
    "bgm.mp3", "bgm.wav", "bgm.m4a",
    "music.mp3", "music.wav",
    "background.mp3", "fon.mp3",
)
_AUDIO_GLOB = ("*.mp3", "*.wav", "*.m4a", "*.ogg", "*.flac")


@dataclass(frozen=True)
class BgmConfig:
    path: Path
    level: float  # 0.0..1.0


def _meta_bool(meta: dict, key: str) -> bool | None:
    if key not in meta:
        return None
    return bool(meta[key])


def _meta_level(meta: dict) -> int:
    for key in ("bgm_level", "mass_bgm_level"):
        if key in meta:
            try:
                return max(0, min(100, int(meta[key])))
            except (TypeError, ValueError):
                break
    return settings.bgm_default_level


def _explicitly_disabled(project: Project) -> bool:
    meta = project.meta or {}
    if _meta_bool(meta, "bgm_enabled") is False:
        return True
    if project.batch_id is not None and _meta_bool(meta, "mass_bgm_enabled") is False:
        return True
    return False


def _first_audio_in_dir(directory: Path) -> Path | None:
    if not directory.is_dir():
        return None
    for pattern in _AUDIO_GLOB:
        matches = sorted(directory.glob(pattern))
        if matches:
            return matches[0]
    return None


def find_bgm_file(project: Project) -> Path | None:
    """Ищет музыку: data/videos/<slug>/music/*, bgm.mp3, …"""
    meta = project.meta or {}
    candidates: list[Path] = []

    meta_path = meta.get("bgm_path") or meta.get("mass_bgm_path")
    if meta_path:
        p = Path(str(meta_path))
        if p.is_dir():
            found = _first_audio_in_dir(p)
            if found:
                return found
        candidates.append(p)

    data = project.data_dir

    # главный путь пользователя: data/videos/test3/music/
    music_dir = data / "music"
    found = _first_audio_in_dir(music_dir)
    if found is not None:
        return found

    for name in _BGM_FILENAMES:
        candidates.append(data / name)
        candidates.append(data / "audio" / name)

    if settings.bgm_path is not None:
        p = settings.bgm_path
        if p.is_dir():
            found = _first_audio_in_dir(p)
            if found:
                return found
        candidates.append(p)

    repo_bgm = settings.data_dir / "bgm"
    found = _first_audio_in_dir(repo_bgm)
    if found is not None:
        return found

    for path in candidates:
        try:
            resolved = path if path.is_absolute() else path.resolve()
        except OSError:
            resolved = path
        if resolved.is_file():
            return resolved
    return None


def resolve_bgm(project: Project) -> BgmConfig | None:
    if _explicitly_disabled(project):
        logger.info("[#{}] BGM: выключено флагом", project.id)
        return None

    path = find_bgm_file(project)
    if path is None:
        logger.warning(
            "[#{}] BGM: нет файла в {}/music/ (положите .mp3 в папку music)",
            project.id,
            project.data_dir,
        )
        return None

    level = _meta_level(project.meta or {}) / 100.0
    logger.info("[#{}] BGM: {} (level {}%)", project.id, path, int(level * 100))
    return BgmConfig(path=path, level=level)
