"""Версионирование project.xlsx: перед каждой подменой файла GPT-ответом
старая версия сохраняется в `data/projects/<id>/old/<timestamp>.xlsx`.

Использование:
    from app.services.xlsx_versioning import backup_to_old, replace_with
    backup_to_old(current_path)              # сохранил предыдущую версию
    replace_with(current_path, new_xlsx)     # подменил current на новую

Или одной операцией:
    replace_with_backup(current_path, new_xlsx)
"""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

from loguru import logger


def _ts() -> str:
    return datetime.utcnow().strftime("%Y%m%d_%H%M%S")


def old_dir_for(project_xlsx: Path) -> Path:
    """Папка для архивных версий рядом с project.xlsx (data/projects/<id>/old/)."""
    return project_xlsx.parent / "old"


def backup_to_old(project_xlsx: Path) -> Path | None:
    """Копирует текущий project.xlsx в old/<timestamp>_<orig>.xlsx.

    Возвращает путь к копии или None если исходного файла не было.
    """
    project_xlsx = Path(project_xlsx)
    if not project_xlsx.exists():
        return None
    old_dir = old_dir_for(project_xlsx)
    old_dir.mkdir(parents=True, exist_ok=True)
    dest = old_dir / f"{_ts()}_{project_xlsx.name}"
    shutil.copy2(project_xlsx, dest)
    logger.info("xlsx_versioning: backup {} -> {}", project_xlsx, dest)
    return dest


def replace_with(project_xlsx: Path, new_file: Path) -> None:
    """Заменяет project.xlsx содержимым `new_file`. БЕЗ бэкапа предыдущей версии."""
    project_xlsx = Path(project_xlsx)
    new_file = Path(new_file)
    if not new_file.exists():
        raise FileNotFoundError(f"replace_with: исходный файл не найден {new_file}")
    project_xlsx.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(new_file, project_xlsx)
    logger.info("xlsx_versioning: replace {} <- {}", project_xlsx, new_file)


def replace_with_backup(project_xlsx: Path, new_file: Path) -> Path | None:
    """Бэкапит текущую версию project.xlsx и заменяет её на `new_file`.

    Возвращает путь к бэкапу (или None если предыдущей версии не было).
    """
    backup = backup_to_old(project_xlsx)
    replace_with(project_xlsx, new_file)
    return backup
