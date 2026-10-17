
from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

GB = 1024**3
MB = 1024**2

DEFAULT_LOW_FREE_GB = 10.0
DEFAULT_CRITICAL_FREE_GB = 3.0
DEFAULT_TEMP_MAX_AGE_H = 6.0
DEFAULT_LOG_MAX_BYTES = 5 * MB
DEFAULT_LOG_BACKUPS = 3
DEFAULT_BACKEND_LOG_TOTAL_CAP = 200 * MB
DEFAULT_MONITOR_TOTAL_CAP = 500 * MB
DEFAULT_LOGS_DIR_TOTAL_CAP = 100 * MB
_SESSION_LOG_MAX_AGE_DAYS = 7.0
_CLEANUP_STAMP = ".housekeeping.stamp"
_VACUUM_STAMP = ".vacuum.stamp"

LEVEL_OK = "ok"
LEVEL_LOW = "low"
LEVEL_CRITICAL = "critical"
LEVEL_UNKNOWN = "unknown"

_io_lock = threading.Lock()




def _env_float(name: str, default: float) -> float:
    raw = (os.environ.get(name) or "").strip().replace(",", ".")
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value >= 0 else default


def _env_int(name: str, default: int) -> int:
    return int(_env_float(name, float(default)))


def _log(level: str, message: str, *args: object) -> None:
    try:
        from loguru import logger

        getattr(logger, level)(message, *args)
    except Exception:
        pass




@dataclass(frozen=True)
class DiskCheck:
    allowed: bool
    level: str
    reason: str
    free_bytes: int | None = None
    total_bytes: int | None = None
    path: str = ""
    low_free_bytes: int = 0
    critical_free_bytes: int = 0

    @property
    def free_gb(self) -> float | None:
        return None if self.free_bytes is None else self.free_bytes / GB


def thresholds_bytes(
    low_free_gb: float | None = None, critical_free_gb: float | None = None
) -> tuple[int, int]:
    low = low_free_gb if low_free_gb is not None else _env_float(
        "DISK_GUARD_LOW_FREE_GB", DEFAULT_LOW_FREE_GB
    )
    crit = critical_free_gb if critical_free_gb is not None else _env_float(
        "DISK_GUARD_CRITICAL_FREE_GB", DEFAULT_CRITICAL_FREE_GB
    )
    crit = min(crit, low)
    return int(low * GB), int(crit * GB)


def _default_data_dir() -> Path:
    raw = (os.environ.get("DATA_DIR") or "./data").strip()
    path = Path(raw)
    if not path.is_absolute():
        path = Path(__file__).resolve().parent.parent.parent / path
    return path


def _existing_parent(path: Path) -> Path:
    cur = path
    while not cur.exists():
        if cur.parent == cur:
            break
        cur = cur.parent
    return cur


def _fmt_gb(value: int) -> str:
    return f"{value / GB:.1f} ГБ".replace(".", ",")


def check(
    path: str | os.PathLike[str] | None = None,
    *,
    paid: bool = True,
    low_free_gb: float | None = None,
    critical_free_gb: float | None = None,
) -> DiskCheck:
    target = Path(path) if path is not None else _default_data_dir()
    low_b, crit_b = thresholds_bytes(low_free_gb, critical_free_gb)
    try:
        usage = shutil.disk_usage(str(_existing_parent(target)))
    except OSError as exc:
        return DiskCheck(
            not paid, LEVEL_UNKNOWN,
            f"Свободное место на диске не удалось измерить ({exc}); платный запуск не разрешён без проверки.",
            None, None, str(target), low_b, crit_b,
        )
    free, total = int(usage.free), int(usage.total)
    common = {
        "free_bytes": free, "total_bytes": total, "path": str(target),
        "low_free_bytes": low_b, "critical_free_bytes": crit_b,
    }
    if free < crit_b:
        return DiskCheck(
            False, LEVEL_CRITICAL,
            f"Диск почти заполнен: свободно {_fmt_gb(free)} (критичный порог "
            f"{_fmt_gb(crit_b)}). Новые запуски остановлены, освободите место.",
            **common,
        )
    if free < low_b:
        if paid:
            return DiskCheck(
                False, LEVEL_LOW,
                f"Мало места на диске: свободно {_fmt_gb(free)} (порог "
                f"{_fmt_gb(low_b)}). Новые платные запуски заблокированы, "
                "освободите место.",
                **common,
            )
        return DiskCheck(
            True, LEVEL_LOW,
            f"Мало места на диске: свободно {_fmt_gb(free)} (порог {_fmt_gb(low_b)}). "
            "Бесплатные запуски разрешены, платные заблокированы.",
            **common,
        )
    return DiskCheck(True, LEVEL_OK, f"Места на диске достаточно: свободно {_fmt_gb(free)}.", **common)


def debug_allowed(path: str | os.PathLike[str] | None = None) -> bool:
    return check(path, paid=True).level in (LEVEL_OK, LEVEL_UNKNOWN)




def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _dir_size(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path, onerror=lambda _e: None):
        for name in files:
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                continue
    return total


def _rmtree(path: Path) -> bool:
    shutil.rmtree(path, ignore_errors=True)
    return not path.exists()


@dataclass
class CleanupResult:
    removed: int = 0
    freed_bytes: int = 0
    skipped_alive: int = 0
    removed_names: list[str] = field(default_factory=list)


def cleanup_stale_pid_temp(
    temp_root: Path,
    *,
    max_age_h: float | None = None,
    own_pid: int | None = None,
    max_dirs: int | None = None,
    now: float | None = None,
    alive_fn=pid_alive,
) -> CleanupResult:
    result = CleanupResult()
    if not temp_root.is_dir():
        return result
    age_s = (max_age_h if max_age_h is not None else _env_float(
        "DISK_GUARD_TEMP_MAX_AGE_H", DEFAULT_TEMP_MAX_AGE_H)) * 3600.0
    me = own_pid if own_pid is not None else os.getpid()
    t_now = now if now is not None else time.time()
    try:
        entries = list(temp_root.iterdir())
    except OSError:
        return result
    for entry in entries:
        if max_dirs is not None and result.removed >= max_dirs:
            break
        name = entry.name
        if not name.startswith("pid-") or not name[4:].isdigit():
            continue
        try:
            if entry.is_symlink() or not entry.is_dir():
                continue
            pid = int(name[4:])
            if pid == me:
                continue
            if t_now - entry.stat().st_mtime < age_s:
                continue
            if alive_fn(pid):
                result.skipped_alive += 1
                continue
            size = _dir_size(entry)
        except OSError:
            continue
        if _rmtree(entry):
            result.removed += 1
            result.freed_bytes += size
            result.removed_names.append(name)
    if result.removed:
        _log("warning", "disk_guard: удалено {} устаревших pid-temp папок, освобождено {} МБ",
             result.removed, result.freed_bytes // MB)
    return result


def _is_ancestor_or_same(candidate: Path, other: Path) -> bool:
    try:
        other.resolve().relative_to(candidate.resolve())
        return True
    except (ValueError, OSError):
        return False


def cleanup_pytest_temp(
    temp_root: Path,
    *,
    max_age_h: float | None = None,
    now: float | None = None,
    protect: Iterable[Path] = (),
) -> CleanupResult:
    result = CleanupResult()
    if not temp_root.is_dir():
        return result
    age_s = (max_age_h if max_age_h is not None else _env_float(
        "DISK_GUARD_TEMP_MAX_AGE_H", DEFAULT_TEMP_MAX_AGE_H)) * 3600.0
    t_now = now if now is not None else time.time()
    guards = [Path(p) for p in protect]
    try:
        roots = [e for e in temp_root.iterdir() if e.name.startswith("pytest-of-") and e.is_dir()]
    except OSError:
        return result
    for root in roots:
        try:
            children = list(root.iterdir())
        except OSError:
            continue
        for child in children:
            try:
                if child.is_symlink():
                    child.unlink(missing_ok=True)
                    continue
                if not child.is_dir():
                    continue
                if any(_is_ancestor_or_same(child, g) for g in guards):
                    continue
                if t_now - child.stat().st_mtime < age_s:
                    continue
                size = _dir_size(child)
            except OSError:
                continue
            if _rmtree(child):
                result.removed += 1
                result.freed_bytes += size
                result.removed_names.append(f"{root.name}/{child.name}")
        try:
            if not any(root.iterdir()) and not any(_is_ancestor_or_same(root, g) for g in guards):
                root.rmdir()
        except OSError:
            pass
    if result.removed:
        _log("warning", "disk_guard: удалено {} устаревших запусков pytest, освобождено {} МБ",
             result.removed, result.freed_bytes // MB)
    return result




def rotate_file(path: Path, *, max_bytes: int, backups: int) -> bool:
    try:
        if not path.is_file() or path.stat().st_size <= max_bytes:
            return False
    except OSError:
        return False
    try:
        if backups <= 0:
            path.unlink(missing_ok=True)
            return True
        oldest = path.with_name(f"{path.name}.{backups}")
        oldest.unlink(missing_ok=True)
        for i in range(backups - 1, 0, -1):
            src = path.with_name(f"{path.name}.{i}")
            if src.exists():
                os.replace(src, path.with_name(f"{path.name}.{i + 1}"))
        os.replace(path, path.with_name(f"{path.name}.1"))
        return True
    except OSError:
        return False


def log_limits() -> tuple[int, int]:
    max_mb = _env_float("LOG_FILE_MAX_MB", DEFAULT_LOG_MAX_BYTES / MB)
    return max(1024, int(max_mb * MB)), max(0, _env_int("LOG_FILE_BACKUPS", DEFAULT_LOG_BACKUPS))


def append_rotating(
    path: Path,
    line: str,
    *,
    max_bytes: int | None = None,
    backups: int | None = None,
) -> None:
    try:
        from app.services.redact import redact_text

        line = redact_text(line)
    except Exception:
        pass
    mb, bk = log_limits()
    mb = max_bytes if max_bytes is not None else mb
    bk = backups if backups is not None else bk
    with _io_lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        rotate_file(path, max_bytes=mb, backups=bk)
        with path.open("a", encoding="utf-8") as f:
            f.write(line if line.endswith("\n") else line + "\n")


def enforce_total_cap(
    directory: Path,
    *,
    max_total_bytes: int,
    patterns: Iterable[str] = ("*",),
    recursive: bool = True,
    protect: Iterable[Path] = (),
    max_age_days: float | None = None,
    now: float | None = None,
) -> CleanupResult:
    result = CleanupResult()
    if not directory.is_dir():
        return result
    guarded = {p.resolve() for p in protect if p}
    seen: dict[Path, tuple[float, int]] = {}
    for pattern in patterns:
        it = directory.rglob(pattern) if recursive else directory.glob(pattern)
        for f in it:
            try:
                if f.is_file() and not f.is_symlink():
                    st = f.stat()
                    seen[f] = (st.st_mtime, st.st_size)
            except OSError:
                continue
    files = sorted(seen.items(), key=lambda kv: kv[1][0])
    t_now = now if now is not None else time.time()
    total = sum(s for _m, s in seen.values())

    def _drop(f: Path, size: int) -> None:
        nonlocal total
        try:
            f.unlink()
        except OSError:
            return
        total -= size
        result.removed += 1
        result.freed_bytes += size
        result.removed_names.append(f.name)

    if max_age_days is not None:
        limit = max_age_days * 86400.0
        for f, (mtime, size) in files:
            if t_now - mtime > limit and f.resolve() not in guarded:
                _drop(f, size)
    for f, (_mtime, size) in files:
        if total <= max_total_bytes:
            break
        if f.exists() and f.resolve() not in guarded:
            _drop(f, size)
    return result




def vacuum_sqlite(db_path: Path, *, busy_timeout_s: float = 5.0) -> tuple[bool, str]:
    if not db_path.is_file():
        return False, f"База не найдена: {db_path}"
    size = db_path.stat().st_size
    _low, crit = thresholds_bytes()
    probe = check(db_path.parent, paid=False)
    if probe.free_bytes is not None and probe.free_bytes < size * 2 + crit:
        return False, "Не хватает места для VACUUM: нужен двойной размер базы и запас."
    try:
        conn = sqlite3.connect(str(db_path), timeout=busy_timeout_s)
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.execute("VACUUM")
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return False, f"VACUUM не выполнен: {exc}"
    after = db_path.stat().st_size
    return True, f"VACUUM выполнен: {size} -> {after} байт"




def _stamp_due(stamp: Path, interval_s: float, now: float) -> bool:
    try:
        return now - stamp.stat().st_mtime >= interval_s
    except OSError:
        return True


def _touch(stamp: Path) -> None:
    try:
        stamp.parent.mkdir(parents=True, exist_ok=True)
        stamp.touch()
        os.utime(stamp, None)
    except OSError:
        pass


def housekeeping(
    data_dir: Path | None = None,
    *,
    logs_dir: Path | None = None,
    tracker_db: Path | None = None,
    min_interval_s: float = 0.0,
    max_dirs: int | None = None,
    now: float | None = None,
) -> dict[str, int | bool | str]:
    data = Path(data_dir) if data_dir is not None else _default_data_dir()
    t_now = now if now is not None else time.time()
    cache = data / ".cache"
    stamp = cache / _CLEANUP_STAMP
    if min_interval_s > 0 and not _stamp_due(stamp, min_interval_s, t_now):
        return {"skipped": True}
    report: dict[str, int | bool | str] = {"skipped": False}
    temp_root = cache / "temp"

    pid_res = cleanup_stale_pid_temp(temp_root, max_dirs=max_dirs, now=t_now)
    report["pid_temp_removed"] = pid_res.removed
    report["pid_temp_freed_bytes"] = pid_res.freed_bytes
    guards = [Path(p) for p in (tempfile_dir(), os.getcwd()) if p]
    py_res = cleanup_pytest_temp(temp_root, now=t_now, protect=guards)
    report["pytest_removed"] = py_res.removed
    report["pytest_freed_bytes"] = py_res.freed_bytes

    bk = enforce_total_cap(
        data,
        max_total_bytes=_env_int("BACKEND_LOG_TOTAL_CAP_MB", DEFAULT_BACKEND_LOG_TOTAL_CAP // MB) * MB,
        patterns=("backend*.log", "backend*.log.*"),
        recursive=False,
        max_age_days=_SESSION_LOG_MAX_AGE_DAYS,
        protect=[data / "backend.log"],
        now=t_now,
    )
    report["backend_logs_removed"] = bk.removed

    lg = logs_dir if logs_dir is not None else Path("logs")
    mb, backups = log_limits()
    rotated = 0
    if lg.is_dir():
        for f in lg.glob("*.log"):
            rotated += int(rotate_file(f, max_bytes=mb, backups=backups))
    report["logs_rotated"] = rotated
    lc = enforce_total_cap(
        lg,
        max_total_bytes=_env_int("LOGS_DIR_TOTAL_CAP_MB", DEFAULT_LOGS_DIR_TOTAL_CAP // MB) * MB,
        patterns=("*.log", "*.log.*"),
        recursive=False,
        protect=[f for f in (lg.glob("*.log") if lg.is_dir() else [])],
        now=t_now,
    )
    report["logs_dir_removed"] = lc.removed

    mc = enforce_total_cap(
        data / "monitor",
        max_total_bytes=_env_int("MONITOR_TOTAL_CAP_MB", DEFAULT_MONITOR_TOTAL_CAP // MB) * MB,
        patterns=("*.log", "*.jsonl", "*.log.*", "*.jsonl.*", "*.zip", "*.gz"),
        now=t_now,
    )
    report["monitor_removed"] = mc.removed

    db = tracker_db
    if db is None:
        env_db = (os.environ.get("TRACKER_DB_PATH") or "").strip()
        db = Path(env_db) if env_db else Path(__file__).resolve().parent.parent.parent / "api-tracker" / "tracker.db"
    vstamp = cache / _VACUUM_STAMP
    if db.is_file() and _stamp_due(vstamp, 86400.0, t_now):
        ok, msg = vacuum_sqlite(db)
        report["vacuum"] = msg
        if ok:
            _touch(vstamp)
    _touch(stamp)
    return report


def tempfile_dir() -> str:
    import tempfile

    return tempfile.gettempdir()


_timer_thread: threading.Thread | None = None
_timer_stop = threading.Event()


def start_housekeeping_timer(
    *,
    interval_s: float | None = None,
    data_dir: Path | None = None,
    logs_dir: Path | None = None,
    tracker_db: Path | None = None,
) -> bool:
    global _timer_thread
    if _timer_thread is not None and _timer_thread.is_alive():
        return False
    period = interval_s if interval_s is not None else _env_float("DISK_GUARD_HOUSEKEEPING_INTERVAL_S", 3600.0)
    period = max(1.0, period)
    _timer_stop.clear()

    def _loop() -> None:
        while not _timer_stop.wait(period):
            try:
                housekeeping(data_dir, logs_dir=logs_dir, tracker_db=tracker_db, max_dirs=200)
            except Exception as exc:
                _log("warning", "disk_guard: сбой периодической чистки: {}", exc)

    _timer_thread = threading.Thread(target=_loop, name="disk-guard-housekeeping", daemon=True)
    _timer_thread.start()
    return True


def stop_housekeeping_timer() -> None:
    _timer_stop.set()
