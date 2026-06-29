"""Очистка артефактов монтажа перед каждым запуском."""

from __future__ import annotations

from pathlib import Path

from loguru import logger

from app.models import Project

WIPE_GLOBS = (
    "*.mp4",
    "*.json",
    "*.txt",
    "timeline_*",
    "montage_*",
    "concat_*",
    "r15_*",
    "ASSEMBLE_*",
    "variant*",
)


def wipe_montage_workspace(project: Project) -> list[str]:
    """Удалить всё из final/ и tmp montage в data_dir проекта."""
    removed: list[str] = []
    dirs = [
        project.data_dir / "final",
        project.data_dir / "montage_tmp",
    ]
    for d in dirs:
        if not d.is_dir():
            continue
        for pattern in WIPE_GLOBS:
            for p in d.glob(pattern):
                try:
                    if p.is_file():
                        p.unlink(missing_ok=True)
                        removed.append(str(p))
                except OSError as exc:
                    logger.warning("[#{}] wipe skip {}: {}", project.id, p, exc)
        # пустая montage_tmp — можно оставить
    if removed:
        logger.info("[#{}] wiped {} montage files", project.id, len(removed))
    return removed
