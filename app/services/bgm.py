"""Фоновая музыка (BGM) при финальной сборке."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from app.models import Project
from app.settings import settings


@dataclass(frozen=True)
class BgmConfig:
    path: Path
    level: float  # 0.0..1.0 — громкость BGM относительно озвучки


def _meta_bool(meta: dict, *keys: str) -> bool | None:
    for key in keys:
        if key not in meta:
            continue
        return bool(meta[key])
    return None


def _meta_level(meta: dict) -> int:
    for key in ("mass_bgm_level", "bgm_level"):
        if key in meta:
            try:
                return max(0, min(100, int(meta[key])))
            except (TypeError, ValueError):
                break
    return settings.bgm_default_level


def resolve_bgm(project: Project) -> BgmConfig | None:
    """BGM для assemble: project/bgm.mp3 → meta path → assets default."""
    meta = project.meta or {}

    enabled = _meta_bool(meta, "mass_bgm_enabled", "bgm_enabled")
    if enabled is None:
        enabled = settings.bgm_default_enabled
    if not enabled:
        return None

    candidates: list[Path] = [
        project.data_dir / "bgm.mp3",
        project.data_dir / "bgm.wav",
        project.data_dir / "bgm.m4a",
    ]
    meta_path = meta.get("bgm_path") or meta.get("mass_bgm_path")
    if meta_path:
        candidates.insert(0, Path(str(meta_path)))
    candidates.append(settings.bgm_path)

    for path in candidates:
        if path.is_file():
            level = _meta_level(meta) / 100.0
            logger.info("[#{}] BGM: {} (level {}%)", project.id, path.name, int(level * 100))
            return BgmConfig(path=path, level=level)

    logger.warning(
        "[#{}] BGM включён, но файл не найден — положите bgm.mp3 в {} или {}",
        project.id,
        project.data_dir,
        settings.bgm_path,
    )
    return None
