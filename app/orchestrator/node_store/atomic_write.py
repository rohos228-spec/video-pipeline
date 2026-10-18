"""Атомарная запись файла через dir_fd и os.replace.

Прямой open('w') по назначению запрещён. Запись сверяет expected_version
с версией уже лежащего файла. Несовпадение — 409, файл не меняется.
Перед replace прежние байты копируются в old/.
Файл без обёртки {"version","text"} — старый формат, версия 0.

Каталог allowlist открывается с O_DIRECTORY|O_NOFOLLOW. lock, копия и tmp
идут через dir_fd, чтобы symlink old/ или .lock не писал наружу.
"""

from __future__ import annotations

import errno
import fcntl
import json
import os
import secrets
from pathlib import Path

from app.orchestrator.node_store.errors import VersionConflict


def _versioned_payload(payload: object) -> dict | None:
    if not isinstance(payload, dict):
        return None
    version = payload.get("version")
    text = payload.get("text")
    if isinstance(version, bool) or not isinstance(version, int):
        return None
    if not isinstance(text, str):
        return None
    return payload


def _stored_version(target: Path) -> int:
    """Версия обёртки. Нет файла или старое тело без version — 0."""
    if not target.is_file():
        return 0
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return 0
    wrapped = _versioned_payload(payload)
    if wrapped is None:
        return 0
    return int(wrapped["version"])


def _allow_roots() -> list[Path]:
    from app.settings import settings

    base = Path(settings.data_dir)
    return [base / "reports", base / "node_overrides"]


def _logical(dest: Path) -> Path:
    return dest if dest.is_absolute() else Path.cwd() / dest


def _collapse(parts: tuple[str, ...] | list[str]) -> list[str]:
    collapsed: list[str] = []
    for part in parts:
        if part in {"", "."}:
            continue
        if part == "..":
            if not collapsed:
                raise ValueError("путь вне allowlist reports/node_overrides")
            collapsed.pop()
            continue
        collapsed.append(part)
    return collapsed


def _classify(dest: Path) -> tuple[Path, list[str]]:
    """Корень allowlist и части пути под ним. Symlink проверяется от корня."""
    logical = _logical(Path(dest))
    matched: tuple[Path, list[str]] | None = None
    for root in _allow_roots():
        root_abs = _logical(root)
        root_parts = root_abs.parts
        dest_parts = logical.parts
        if dest_parts[: len(root_parts)] != root_parts:
            continue
        collapsed = _collapse(dest_parts[len(root_parts) :])
        real_root = Path(os.path.realpath(root_abs))
        candidate = root_abs.joinpath(*collapsed) if collapsed else root_abs
        real = Path(os.path.realpath(candidate))
        try:
            shared = os.path.commonpath([str(real), str(real_root)])
        except ValueError:
            continue
        if shared != str(real_root):
            raise ValueError("путь вне allowlist reports/node_overrides")
        matched = (root_abs, collapsed)
        break
    if matched is None:
        raise ValueError("путь вне allowlist reports/node_overrides")
    root_abs, collapsed = matched
    if not collapsed:
        raise ValueError("dest равен корню allowlist")
    if any(part == "old" for part in collapsed):
        raise ValueError("dest в old/ запрещён")
    leaf = collapsed[-1]
    if leaf.endswith(".lock") or leaf.endswith(".tmp"):
        raise ValueError("dest .lock или .tmp запрещён")
    current = root_abs
    for part in collapsed:
        current = current / part
        if current.is_symlink():
            raise ValueError("symlink в пути назначения: отказ allowlist")
    return root_abs, collapsed


def _assert_allowlisted(dest: Path) -> Path:
    root, parts = _classify(dest)
    return root.joinpath(*parts)


def read_override(dest: Path) -> str:
    """Читает override: text из обёртки или сырое тело старого файла."""
    target = _assert_allowlisted(Path(dest))
    raw = target.read_text(encoding="utf-8")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    wrapped = _versioned_payload(payload)
    if wrapped is None:
        return raw
    return str(wrapped["text"])


def _open_dir(path: str, dir_fd: int | None = None) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        if dir_fd is None:
            return os.open(path, flags)
        return os.open(path, flags, dir_fd=dir_fd)
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
            raise ValueError("symlink в пути назначения: отказ allowlist") from exc
        raise


def _lexists(name: str, dir_fd: int) -> bool:
    try:
        os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    return True


def _descend(root: Path, dirs: list[str]) -> tuple[int, list[int]]:
    opened: list[int] = []
    try:
        current = _open_dir(str(root))
        opened.append(current)
        for part in dirs:
            if not _lexists(part, current):
                os.mkdir(part, dir_fd=current)
            nxt = _open_dir(part, current)
            opened.append(nxt)
            current = nxt
        return current, opened
    except Exception:
        for fd in opened:
            os.close(fd)
        raise


def _open_lock(parent_fd: int, name: str) -> int:
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        return os.open(name + ".lock", flags, 0o644, dir_fd=parent_fd)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise ValueError("symlink .lock: отказ allowlist") from exc
        raise


def _acquire_lock(fd: int) -> None:
    fcntl.flock(fd, fcntl.LOCK_EX)


def _release_lock(fd: int) -> None:
    fcntl.flock(fd, fcntl.LOCK_UN)


def _recheck(root: Path, parts: list[str], parent_fd: int) -> None:
    _classify(root.joinpath(*parts))
    parent = root.joinpath(*parts[:-1]) if len(parts) > 1 else root
    held = os.fstat(parent_fd)
    now = os.stat(parent, follow_symlinks=False)
    if (held.st_ino, held.st_dev) != (now.st_ino, now.st_dev):
        raise ValueError("symlink в пути назначения: отказ allowlist")
    real_root = Path(os.path.realpath(root))
    real = Path(os.path.realpath(root.joinpath(*parts)))
    if os.path.commonpath([str(real), str(real_root)]) != str(real_root) or real == real_root:
        raise ValueError("путь вне allowlist reports/node_overrides")


def _open_old(parent_fd: int) -> int:
    if not _lexists("old", parent_fd):
        os.mkdir("old", dir_fd=parent_fd)
    return _open_dir("old", parent_fd)


def _backup_name(old_fd: int, name: str, current: int) -> str:
    base = f"{name}.v{current}"
    if not _lexists(base, old_fd):
        return base
    suffix = 2
    while _lexists(f"{name}.v{current}.{suffix}", old_fd):
        suffix += 1
    return f"{name}.v{current}.{suffix}"


def _copy_nofollow(src_dir: int, src_name: str, dst_dir: int, dst_name: str) -> None:
    src = os.open(src_name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=src_dir)
    try:
        mode = os.fstat(src).st_mode & 0o777 or 0o644
        dst = os.open(
            dst_name,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
            mode,
            dir_fd=dst_dir,
        )
        try:
            while True:
                chunk = os.read(src, 1024 * 1024)
                if not chunk:
                    break
                view = memoryview(chunk)
                while view:
                    written = os.write(dst, view)
                    view = view[written:]
            os.fsync(dst)
        finally:
            os.close(dst)
    finally:
        os.close(src)


def _write_tmp(parent_fd: int, name: str, payload: bytes, mode: int) -> str:
    tmp_name = f".{name}.{secrets.token_hex(8)}.tmp"
    flags = os.O_CREAT | os.O_EXCL | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(tmp_name, flags, 0o644, dir_fd=parent_fd)
    try:
        view = memoryview(payload)
        while view:
            written = os.write(fd, view)
            view = view[written:]
        os.fsync(fd)
        os.fchmod(fd, mode)
    except Exception:
        os.close(fd)
        os.unlink(tmp_name, dir_fd=parent_fd)
        raise
    else:
        os.close(fd)
    return tmp_name


def atomic_write_text(dest: Path, text: str, *, expected_version: int) -> None:
    """Публикует text, если expected_version совпала с текущей версией файла."""
    root, parts = _classify(Path(dest))
    root.mkdir(parents=True, exist_ok=True)
    parent_fd, opened = _descend(root, parts[:-1])
    name = parts[-1]
    target = root.joinpath(*parts)
    lock_fd: int | None = None
    old_fd: int | None = None
    tmp_name: str | None = None
    try:
        lock_fd = _open_lock(parent_fd, name)
        _acquire_lock(lock_fd)
        _recheck(root, parts, parent_fd)
        current = _stored_version(target)
        if current != int(expected_version):
            raise VersionConflict(
                f"версия {expected_version} не совпала с текущей {current}"
            )
        if _lexists(name, parent_fd):
            old_fd = _open_old(parent_fd)
            backup = _backup_name(old_fd, name, current)
            _copy_nofollow(parent_fd, name, old_fd, backup)
            mode = os.stat(name, dir_fd=parent_fd, follow_symlinks=False).st_mode & 0o777
        else:
            if _lexists("old", parent_fd):
                probe = _open_dir("old", parent_fd)
                os.close(probe)
            mode = 0o644
        payload = json.dumps(
            {"version": current + 1, "text": text},
            ensure_ascii=False,
        ).encode("utf-8")
        tmp_name = _write_tmp(parent_fd, name, payload, mode or 0o644)
        os.replace(tmp_name, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        tmp_name = None
        os.fsync(parent_fd)
    except Exception:
        if tmp_name is not None:
            try:
                os.unlink(tmp_name, dir_fd=parent_fd)
            except OSError:
                pass
        raise
    finally:
        if old_fd is not None:
            os.close(old_fd)
        if lock_fd is not None:
            _release_lock(lock_fd)
            os.close(lock_fd)
        for fd in opened:
            os.close(fd)
