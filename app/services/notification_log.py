"""Журнал уведомлений (B07): каждая отправка оператору в Telegram оставляет строку в `notification_log`.

Единственная точка отправки - `notify(bot, "send_message", chat_id, text, ...)`. Прямые вызовы
`bot.send_message/send_photo/send_document/send_video` в коде приложения запрещены (тест
`tests/w3_4_notify_test.py` ищет их по исходникам).

Что пишется: ts (UTC, как в operator_audit), project_id, run_id, channel, kind, text_redacted,
ok (1/0), error. Текст проходит через `redact_text` и обрезается до 2000 символов, ошибка - до 500.
Файлы и клавиатуры не хранятся: только текст/подпись.

Таблица append-only (триггеры как у operator_audit/spend_ledger). Миграцию делает W3-5 по константе
`NOTIFICATION_LOG_DDL`; сам модуль тоже создаёт таблицу при первой записи (CREATE ... IF NOT EXISTS),
поэтому порядок «миграция / первая отправка» не важен.

Сбой записи НИКОГДА не блокирует отправку и не меняет её результат: ошибка логируется, счётчик
`stats()["skipped"]` растёт. Сбой самой отправки пишется как ok=0 и пробрасывается как раньше.

Живую отправку в Telegram этот модуль не проверял: только юнит-тесты с заменителем бота.
"""
from __future__ import annotations

import asyncio
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

CHANNEL_TELEGRAM = "telegram"
CHANNEL_OFF = "telegram_off"
NOOP_ERROR_RU = "Telegram отключён (режим без бота): сообщение не отправлено"
TEXT_LIMIT = 2000
ERROR_LIMIT = 500

# Методы бота, которые считаются «отправкой оператору». Текст берётся из text или caption.
SEND_METHODS = frozenset({"send_message", "send_photo", "send_document", "send_video",
                          "send_audio", "send_voice", "send_animation"})

NOTIFICATION_LOG_DDL = """
CREATE TABLE IF NOT EXISTS notification_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    project_id INTEGER,
    run_id TEXT,
    channel TEXT NOT NULL,
    kind TEXT NOT NULL,
    text_redacted TEXT NOT NULL DEFAULT '',
    ok INTEGER NOT NULL,
    error TEXT
);
CREATE INDEX IF NOT EXISTS ix_notification_log_ts ON notification_log (ts);
CREATE INDEX IF NOT EXISTS ix_notification_log_project_id ON notification_log (project_id);
CREATE INDEX IF NOT EXISTS ix_notification_log_run_id ON notification_log (run_id);
CREATE TRIGGER IF NOT EXISTS notification_log_no_update BEFORE UPDATE ON notification_log
BEGIN SELECT RAISE(ABORT, 'notification log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS notification_log_no_delete BEFORE DELETE ON notification_log
BEGIN SELECT RAISE(ABORT, 'notification log is append-only'); END;
"""

COLUMNS = ("ts", "project_id", "run_id", "channel", "kind", "text_redacted", "ok", "error")

_LOCK = threading.Lock()
_STATS = {"written": 0, "skipped": 0, "last_error": ""}
_ensured: set[str] = set()


def stats() -> dict:
    with _LOCK:
        return dict(_STATS)


def reset_stats() -> None:
    with _LOCK:
        _STATS.update(written=0, skipped=0, last_error="")
    _ensured.clear()


def enabled() -> bool:
    return (os.environ.get("VP_NOTIFICATION_LOG") or "1").strip().lower() not in {"0", "false", "no", "off"}


def _db_path() -> Path:
    from app.settings import settings
    p = Path(settings.sqlite_path)
    return p if p.is_absolute() else Path.cwd() / p


def _clip(value: Any, limit: int) -> str:
    from app.services.redact import redact_text
    s = "" if value is None else str(value)
    s = redact_text(s)
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _now() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(sep=" ")


def log_notification(*, project_id: int | None = None, run_id: str | None = None,
                     channel: str = CHANNEL_TELEGRAM, kind: str = "message", text: str | None = "",
                     ok: bool = True, error: str | None = None) -> bool:
    """Синхронная запись одной строки. Никогда не бросает исключение; возвращает True при успехе."""
    if not enabled():
        return False
    try:
        row = (_now(), int(project_id) if project_id is not None else None,
               str(run_id) if run_id else None, str(channel)[:40] or CHANNEL_TELEGRAM,
               str(kind)[:80] or "message", _clip(text, TEXT_LIMIT), 1 if ok else 0,
               _clip(error, ERROR_LIMIT) if error else None)
        path = _db_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(path), timeout=5)
        try:
            con.execute("PRAGMA busy_timeout=5000")
            key = str(path)
            if key not in _ensured:
                con.executescript(NOTIFICATION_LOG_DDL)
                _ensured.add(key)
            con.execute(f"INSERT INTO notification_log ({', '.join(COLUMNS)}) "
                        f"VALUES ({', '.join('?' * len(COLUMNS))})", row)
            con.commit()
        finally:
            con.close()
    except Exception as exc:  # noqa: BLE001 - журнал не должен ломать отправку
        with _LOCK:
            _STATS["skipped"] += 1
            _STATS["last_error"] = f"{type(exc).__name__}: {exc}"[:300]
        logger.error("notification_log: запись пропущена ({}): {}: {}", kind, type(exc).__name__, exc)
        return False
    with _LOCK:
        _STATS["written"] += 1
    return True


def list_rows(limit: int = 100, project_id: int | None = None) -> list[dict]:
    """Последние строки (для ленты и диагностики); пусто, если таблицы ещё нет."""
    path = _db_path()
    if not path.exists():
        return []
    con = sqlite3.connect(str(path), timeout=5)
    try:
        con.row_factory = sqlite3.Row
        sql = "SELECT id, " + ", ".join(COLUMNS) + " FROM notification_log"
        args: list = []
        if project_id is not None:
            sql += " WHERE project_id = ?"
            args.append(project_id)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(int(limit))
        try:
            return [dict(r) for r in con.execute(sql, args)]
        except sqlite3.OperationalError:
            return []
    finally:
        con.close()


def _text_of(method: str, args: tuple, kwargs: dict) -> str:
    """Текст сообщения: send_message(chat_id, text) или подпись медиа."""
    if method == "send_message":
        if "text" in kwargs:
            return str(kwargs["text"] or "")
        return str(args[1]) if len(args) > 1 else ""
    cap = kwargs.get("caption")
    return str(cap) if cap else ""


async def notify(bot: Any, method: str, chat_id: Any, *args: Any, kind: str | None = None,
                 project_id: int | None = None, run_id: str | None = None,
                 channel: str = CHANNEL_TELEGRAM, **kwargs: Any) -> Any:
    """Отправить через `getattr(bot, method)(chat_id, *args, **kwargs)` и записать доставку.

    `kind`, `project_id`, `run_id`, `channel` - служебные и боту не передаются. Результат бота
    возвращается как есть; исключение отправки записывается (ok=0) и пробрасывается как раньше."""
    if method not in SEND_METHODS:
        raise ValueError(f"notify: неизвестный метод отправки {method!r}")
    text = _text_of(method, (chat_id, *args), kwargs)
    k = kind or method
    if type(bot).__name__ == "NoopBot":
        # Режим без Telegram: вызов безвреден, но сообщение НЕ доставлено - так и записываем.
        result = await getattr(bot, method)(chat_id, *args, **kwargs)
        await asyncio.to_thread(log_notification, project_id=project_id, run_id=run_id, channel=CHANNEL_OFF,
                                kind=k, text=text, ok=False, error=NOOP_ERROR_RU)
        return result
    try:
        result = await getattr(bot, method)(chat_id, *args, **kwargs)
    except BaseException as exc:
        # BaseException: отмену тоже фиксируем, запись синхронная и короткая.
        log_notification(project_id=project_id, run_id=run_id, channel=channel, kind=k, text=text,
                         ok=False, error=f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__)
        raise
    try:
        await asyncio.to_thread(log_notification, project_id=project_id, run_id=run_id, channel=channel,
                                kind=k, text=text, ok=True)
    except Exception:  # noqa: BLE001 - сообщение уже ушло; сбой журнала не меняет результат
        logger.exception("notification_log: сбой записи после отправки ({})", k)
    return result
