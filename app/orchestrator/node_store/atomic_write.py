"""Атомарная запись файла: mkstemp в том же каталоге и os.replace.

Прямой open('w') по назначению запрещён. Запись сверяет expected_version
с версией уже лежащего файла. Несовпадение — 409, файл не меняется.
Перед replace прежние байты копируются в old/.
"""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import tempfile
from pathlib import Path


class VersionConflict(Exception):
    """Ожидаемая версия не совпала с версией файла."""

    status_code = 409


def _stored_version(target: Path) -> int:
    if not target.is_file():
        return 0
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise VersionConflict("прежний файл без читаемой версии") from exc
    version = payload.get("version") if isinstance(payload, dict) else None
    if isinstance(version, bool) or not isinstance(version, int):
        raise VersionConflict("прежний файл без версии")
    return version


def _assert_allowlisted(dest: Path) -> Path:
    """Путь только внутри data/reports или data/node_overrides, без symlink."""
    logical = dest if dest.is_absolute() else Path.cwd() / dest
    current = Path(logical.anchor)
    parts = logical.parts[1:] if logical.is_absolute() else logical.parts
    for part in parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("symlink в пути назначения: отказ allowlist")
    real = Path(os.path.realpath(logical))
    from app.settings import settings

    base = Path(settings.data_dir)
    for root in (base / "reports", base / "node_overrides"):
        real_root = Path(os.path.realpath(root))
        try:
            shared = os.path.commonpath([str(real), str(real_root)])
        except ValueError:
            continue
        if shared == str(real_root):
            return dest
    raise ValueError("путь вне allowlist reports/node_overrides")


def _exclusive_lock(target: Path):
    """Держит flock на <name>.lock на всё время чтения версии и replace."""
    lock_path = target.with_name(target.name + ".lock")
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


def atomic_write_text(dest: Path, text: str, *, expected_version: int) -> None:
    """Публикует text, если expected_version совпала с текущей версией файла."""
    target = _assert_allowlisted(Path(dest))
    target.parent.mkdir(parents=True, exist_ok=True)
    lock_fd = _exclusive_lock(target)
    try:
        current = _stored_version(target)
        if current != int(expected_version):
            raise VersionConflict(
                f"версия {expected_version} не совпала с текущей {current}"
            )
        if target.is_file():
            backup_dir = target.parent / "old"
            backup_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, backup_dir / f"{target.name}.v{current}")
        fd, tmp_name = tempfile.mkstemp(
            prefix=f"{target.name}.",
            suffix=".tmp",
            dir=target.parent,
        )
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                payload = json.dumps(
                    {"version": current + 1, "text": text},
                    ensure_ascii=False,
                )
                handle.write(payload)
            os.replace(tmp_path, target)
        except Exception:
            if tmp_path.exists():
                tmp_path.unlink()
            raise
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)
