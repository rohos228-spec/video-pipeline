"""Структурированный JSON-лог: перехватывает loguru-вывод и пишет в
data/monitor/logs/ с ротацией по дате и размеру.

Также сохраняет «событийный» лог (events.jsonl) — каждая строка = одно
бизнес-событие (шаг начался / шаг завершён / ошибка / скриншот / клик).
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock

from loguru import logger

from app.services import disk_guard
from app.services.redact import install_log_redaction, redact, redact_text

_lock = Lock()
_events_file = None
_events_day: str | None = None
_monitor_dir: Path | None = None
_sink_id: int | None = None
_sink_ids: list[int] = []
_last_cap_check = 0.0

_CAP_PATTERNS = ("*.log", "*.jsonl", "*.log.*", "*.jsonl.*", "*.zip", "*.gz")


def _env_mb(name: str, default_mb: float) -> int:
    return int(disk_guard._env_float(name, default_mb) * disk_guard.MB)


def sink_limits() -> dict[str, int | str]:
    """Лимиты файловых логов монитора (A03), переопределяются окружением."""
    days = max(1, disk_guard._env_int("MONITOR_LOG_RETENTION_DAYS", 3))
    return {
        "rotation_bytes": max(disk_guard.MB, _env_mb("MONITOR_LOG_ROTATION_MB", 20)),
        "retention": f"{days} days",
        "retention_days": days,
        "events_max_bytes": max(disk_guard.MB, _env_mb("MONITOR_EVENTS_MAX_MB", 20)),
        "total_cap_bytes": _env_mb("MONITOR_TOTAL_CAP_MB", disk_guard.DEFAULT_MONITOR_TOTAL_CAP / disk_guard.MB),
    }


def enforce_monitor_cap(monitor_dir: Path | None = None) -> int:
    """Суммарный потолок ``data/monitor``: старые файлы удаляются первыми."""
    base = monitor_dir or get_monitor_dir()
    lim = sink_limits()
    res = disk_guard.enforce_total_cap(
        base,
        max_total_bytes=int(lim["total_cap_bytes"]),
        patterns=_CAP_PATTERNS,
        max_age_days=float(lim["retention_days"]) + 1.0,
    )
    return res.removed


def _ensure_dir(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p


def remove_sinks() -> None:
    """Снять файловые приёмники loguru, созданные ``init`` (повторный init не плодит дубли)."""
    global _sink_id
    for sid in _sink_ids:
        try:
            logger.remove(sid)
        except ValueError:
            pass
    _sink_ids.clear()
    _sink_id = None


def init(monitor_dir: Path | None = None) -> Path:
    """Инициализирует лог-систему. Возвращает путь к monitor_dir."""
    global _monitor_dir, _sink_id

    if monitor_dir is None:
        monitor_dir = Path("data/monitor")
    _monitor_dir = monitor_dir

    logs_dir = _ensure_dir(monitor_dir / "logs")
    _ensure_dir(monitor_dir / "events")

    remove_sinks()
    install_log_redaction()  # A16: глобальный фильтр секретов для всех приёмников loguru

    lim = sink_limits()
    rotation = f"{int(lim['rotation_bytes']) // disk_guard.MB} MB"
    # DEBUG при нехватке места отключается: самый объёмный источник записи.
    level = "DEBUG" if disk_guard.debug_allowed(monitor_dir) else "INFO"

    _sink_id = logger.add(
        str(logs_dir / "pipeline_{time:YYYY-MM-DD}.log"),
        rotation=rotation,
        retention=str(lim["retention"]),
        format=(
            "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<8} | "
            "{name}:{function}:{line} | {message}"
        ),
        level=level,
        enqueue=True,
        diagnose=False,  # A16: diagnose печатает значения локальных переменных (секреты)
    )
    _sink_ids.append(_sink_id)

    _sink_ids.append(
        logger.add(
            str(logs_dir / "pipeline_{time:YYYY-MM-DD}.jsonl"),
            rotation=rotation,
            retention=str(lim["retention"]),
            level=level,
            enqueue=True,
            serialize=True,
            diagnose=False,
        )
    )

    with _lock:
        _open_events_locked(force=True)
    try:
        enforce_monitor_cap(monitor_dir)
    except Exception:  # noqa: BLE001
        pass

    logger.info("monitor log_sink initialized → {}", monitor_dir)
    return monitor_dir


def _open_events_locked(*, force: bool = False) -> None:
    """Открыть events_<сегодня>.jsonl; смена суток и превышение размера роллят файл."""
    global _events_file, _events_day, _last_cap_check
    base = _monitor_dir or Path("data/monitor")
    today = datetime.now(tz=UTC).strftime("%Y-%m-%d")
    path = base / "events" / f"events_{today}.jsonl"
    if _events_file is not None and not force:
        if _events_day == today:
            try:
                too_big = path.stat().st_size > int(sink_limits()["events_max_bytes"])
            except OSError:
                too_big = False
            if not too_big:
                return
            _close_events_locked()
            disk_guard.rotate_file(
                path, max_bytes=0, backups=disk_guard.log_limits()[1]
            )
        else:
            _close_events_locked()
    elif _events_file is not None:
        _close_events_locked()
    path.parent.mkdir(parents=True, exist_ok=True)
    _events_file = open(path, "a", encoding="utf-8")  # noqa: SIM115
    _events_day = today
    now = time.monotonic()
    if now - _last_cap_check > 300:
        _last_cap_check = now
        try:
            enforce_monitor_cap(base)
        except Exception:  # noqa: BLE001
            pass


def _close_events_locked() -> None:
    global _events_file
    if _events_file is not None:
        try:
            _events_file.close()
        except Exception:  # noqa: BLE001
            pass
        _events_file = None


def _json_format(record: dict) -> str:
    rec = record.get("record", record)
    entry = {
        "ts": str(rec.get("time", "")),
        "level": str(rec.get("level", "")),
        "module": rec.get("name", ""),
        "function": rec.get("function", ""),
        "line": rec.get("line", 0),
        "message": redact_text(str(rec.get("message", ""))),
    }
    return json.dumps(entry, ensure_ascii=False, default=str) + "\n"


def emit_event(
    event_type: str,
    *,
    project_id: int | None = None,
    step: str | None = None,
    detail: dict | None = None,
    screenshot_path: str | None = None,
) -> None:
    """Записывает одно бизнес-событие в events.jsonl."""
    entry = {
        "ts": datetime.now(tz=UTC).isoformat(),
        "mono": time.monotonic(),
        "event": event_type,
    }
    if project_id is not None:
        entry["project_id"] = project_id
    if step:
        entry["step"] = step
    if screenshot_path:
        entry["screenshot"] = screenshot_path
    if detail:
        entry["detail"] = redact(detail)

    line = json.dumps(entry, ensure_ascii=False, default=str) + "\n"

    with _lock:
        if _events_file is not None:
            try:
                _open_events_locked()
                _events_file.write(line)
                _events_file.flush()
            except OSError as exc:  # диск полон: событие теряем, процесс не роняем
                logger.warning("monitor log_sink: не удалось записать событие: {}", exc)

    logger.debug("monitor event: {}", event_type)


def get_monitor_dir() -> Path:
    return _monitor_dir or Path("data/monitor")


def close() -> None:
    with _lock:
        _close_events_locked()
