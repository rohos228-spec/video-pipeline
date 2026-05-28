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


def _bgm_enabled(project: Project) -> bool:
    meta = project.meta or {}
    explicit = _meta_bool(meta, "bgm_enabled")
    if explicit is not None:
        return explicit
    if project.batch_id is not None:
        mass = _meta_bool(meta, "mass_bgm_enabled")
        if mass is not None:
            return mass
    return settings.bgm_default_enabled


def resolve_bgm(project: Project) -> BgmConfig | None:
    """BGM только из явного файла проекта — без ambient-заглушки."""
    if not _bgm_enabled(project):
        logger.info("[#{}] BGM: выключен", project.id)
        return None

    meta = project.meta or {}
    candidates: list[Path] = [
        project.data_dir / "bgm.mp3",
        project.data_dir / "bgm.wav",
        project.data_dir / "bgm.m4a",
    ]
    meta_path = meta.get("bgm_path") or meta.get("mass_bgm_path")
    if meta_path:
        candidates.insert(0, Path(str(meta_path)))
    if settings.bgm_path is not None and settings.bgm_path.is_file():
        candidates.append(settings.bgm_path)

    for path in candidates:
        try:
            resolved = path if path.is_absolute() else path.resolve()
        except OSError:
            resolved = path
        if resolved.is_file():
            level = _meta_level(meta) / 100.0
            logger.info("[#{}] BGM: {} (level {}%)", project.id, resolved.name, int(level * 100))
            return BgmConfig(path=resolved, level=level)

    logger.info(
        "[#{}] BGM: нет bgm.mp3 в {} — положите свой трек или BGM_PATH в .env",
        project.id,
        project.data_dir,
    )
    return None
