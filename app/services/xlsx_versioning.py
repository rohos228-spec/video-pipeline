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


def validate_xlsx(path: Path) -> str | None:
    """Проверяет что `path` — валидный xlsx-файл.

    Возвращает None если ок, иначе человекочитаемое сообщение об ошибке.
    Используется после `download_attachment_from_last_reply`, чтобы не
    подменить project.xlsx «пустышкой» (svg-иконкой, html-ошибкой и т.п.).
    """
    path = Path(path)
    if not path.exists():
        return f"файл не существует: {path}"
    size = path.stat().st_size
    # Минимальный валидный xlsx ≈ 5 КБ. Меньше 1 КБ — почти наверняка
    # это не xlsx, а svg/html/иконка которую ChatGPT отдал по ошибочному
    # клику.
    if size < 1024:
        return f"файл подозрительно мал ({size} байт)"
    with path.open("rb") as f:
        magic = f.read(4)
    # xlsx — это zip, у zip-архивов всегда первые 2 байта 'PK'.
    if magic[:2] != b"PK":
        preview = magic.hex()
        return (
            f"файл не является xlsx (первые 4 байта: {preview}, "
            f"ожидался zip-magic 'PK')"
        )
    # Финальная проверка — действительно ли openpyxl откроет файл.
    try:
        from openpyxl import load_workbook  # noqa: PLC0415

        wb = load_workbook(path, read_only=True)
        wb.close()
    except Exception as e:  # noqa: BLE001
        return f"openpyxl не смог открыть файл: {e}"
    return None
