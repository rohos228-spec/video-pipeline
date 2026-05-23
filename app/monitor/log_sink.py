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

_lock = Lock()
_events_file = None
_monitor_dir: Path | None = None
_sink_id: int | None = None


def _ensure_dir(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p


def init(monitor_dir: Path | None = None) -> Path:
    """Инициализирует лог-систему. Возвращает путь к monitor_dir."""
    global _events_file, _monitor_dir, _sink_id

    if monitor_dir is None:
        monitor_dir = Path("data/monitor")
    _monitor_dir = monitor_dir

    logs_dir = _ensure_dir(monitor_dir / "logs")
    events_dir = _ensure_dir(monitor_dir / "events")

    if _sink_id is not None:
        try:
            logger.remove(_sink_id)
        except ValueError:
            pass

    _sink_id = logger.add(
        str(logs_dir / "pipeline_{time:YYYY-MM-DD}.log"),
        rotation="50 MB",
        retention="7 days",
        format=(
            "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<8} | "
            "{name}:{function}:{line} | {message}"
        ),
        level="DEBUG",
        enqueue=True,
    )

    logger.add(
        str(logs_dir / "pipeline_{time:YYYY-MM-DD}.jsonl"),
        rotation="50 MB",
        retention="7 days",
        level="DEBUG",
        enqueue=True,
        serialize=True,
    )

    today = datetime.now(tz=UTC).strftime("%Y-%m-%d")
    events_path = events_dir / f"events_{today}.jsonl"
    with _lock:
        if _events_file is not None:
            try:
                _events_file.close()
            except Exception:
                pass
        _events_file = open(events_path, "a", encoding="utf-8")

    logger.info("monitor log_sink initialized → {}", monitor_dir)
    return monitor_dir


def _json_format(record: dict) -> str:
    rec = record.get("record", record)
    entry = {
        "ts": str(rec.get("time", "")),
        "level": str(rec.get("level", "")),
        "module": rec.get("name", ""),
        "function": rec.get("function", ""),
        "line": rec.get("line", 0),
        "message": rec.get("message", ""),
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
        entry["detail"] = detail

    line = json.dumps(entry, ensure_ascii=False, default=str) + "\n"

    with _lock:
        if _events_file is not None:
            try:
                _events_file.write(line)
                _events_file.flush()
            except OSError as exc:
                # Swallow I/O errors so that a full disk or broken file
                # handle never propagates into the wrapped pipeline methods
                # (emit_event is called both before and inside finally blocks
                # of the wrappers — an unhandled exception there would prevent
                # the actual pipeline work from running or discard its result).
                logger.warning("monitor: events write failed ({}): {}", event_type, exc)

    logger.debug("monitor event: {}", event_type)


def get_monitor_dir() -> Path:
    return _monitor_dir or Path("data/monitor")


def close() -> None:
    global _events_file
    with _lock:
        if _events_file is not None:
            try:
                _events_file.close()
            except Exception:
                pass
            _events_file = None
