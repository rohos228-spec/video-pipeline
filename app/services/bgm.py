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


def find_bgm_file(project: Project) -> Path | None:
    """Ищет музыку в проекте и типовых путях."""
    meta = project.meta or {}
    candidates: list[Path] = []

    meta_path = meta.get("bgm_path") or meta.get("mass_bgm_path")
    if meta_path:
        candidates.append(Path(str(meta_path)))

    data = project.data_dir
    for name in _BGM_FILENAMES:
        candidates.append(data / name)
        candidates.append(data / "audio" / name)

    if settings.bgm_path is not None:
        candidates.append(settings.bgm_path)

    repo_bgm = settings.data_dir / "bgm"
    if repo_bgm.is_dir():
        for name in _BGM_FILENAMES:
            candidates.append(repo_bgm / name)

    seen: set[str] = set()
    for path in candidates:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        try:
            resolved = path if path.is_absolute() else path.resolve()
        except OSError:
            resolved = path
        if resolved.is_file():
            return resolved
    return None


def resolve_bgm(project: Project) -> BgmConfig | None:
    """Если bgm/music файл найден — микшируем (если не выключено явно)."""
    if _explicitly_disabled(project):
        logger.info("[#{}] BGM: выключено флагом bgm_enabled / mass_bgm_enabled", project.id)
        return None

    path = find_bgm_file(project)
    if path is None:
        logger.warning(
            "[#{}] BGM: файл не найден — положите bgm.mp3 или music.mp3 в {}",
            project.id,
            project.data_dir,
        )
        return None

    level = _meta_level(project.meta or {}) / 100.0
    logger.info("[#{}] BGM: {} (level {}%)", project.id, path, int(level * 100))
    return BgmConfig(path=path, level=level)
