"""Сторож сторожа (A18): пульс цикла надзора и независимая проверка его молчания.

Цикл надзора (`Observer.loop`) раз в `VP_HEARTBEAT_INTERVAL_S` (по умолчанию 5 с) перезаписывает ОДИН маленький
файл `observer_heartbeat.json` (атомарно, без роста базы). Проверяющий читает только этот файл:
  * `check_once()` - отдельная asyncio-задача в веб-процессе (`run_checker`) и автономный
    `scripts/watchdog_check.py` (Планировщик заданий / cron): второй переживает смерть самого процесса.
Если пульс старше окна `VP_WATCHDOG_HEARTBEAT_WINDOW_S` (по умолчанию 300 с), поднимается тревога ОДИН раз за
эпизод молчания: файл-метка `observer_alarm.json` создаётся атомарно (O_EXCL), поэтому две проверки
(задача и скрипт) не отправят дубль. Тревога = строка в `operator_audit` (actor `watchdog`) + сообщение через
единственную точку `notification_log.notify` (без Telegram строка помечена «не доставлено», как везде).
Когда пульс вернулся, метка снимается, пишется строка «восстановлено» и сообщение об этом.
Чистая остановка приложения помечает пульс `stopped`: это не тревога.

Каталог: `VP_HEARTBEAT_DIR` или папка базы `SQLITE_PATH`. Живую доставку в Telegram модуль не проверял.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from pathlib import Path

from loguru import logger

HEARTBEAT_FILE = "observer_heartbeat.json"
ALARM_FILE = "observer_alarm.json"
KIND_ALARM = "observer_silent"
KIND_RESOLVED = "observer_silent_resolved"
DEFAULT_WINDOW_S = 300.0
DEFAULT_BEAT_S = 5.0
_last_beat = 0.0


# ------------------------------------------------------------------ настройки (свои env)

def _float_env(name: str, default: float, minimum: float) -> float:
    try:
        return max(minimum, float(os.environ.get(name) or default))
    except (TypeError, ValueError):
        return default


def window_s() -> float:
    return _float_env("VP_WATCHDOG_HEARTBEAT_WINDOW_S", DEFAULT_WINDOW_S, 2.0)


def beat_interval_s() -> float:
    return _float_env("VP_HEARTBEAT_INTERVAL_S", DEFAULT_BEAT_S, 0.2)


def check_interval_s() -> float:
    return _float_env("VP_WATCHDOG_CHECK_INTERVAL_S", max(2.0, min(30.0, window_s() / 3)), 0.5)


def enabled() -> bool:
    return (os.environ.get("VP_SUPERVISOR_HEARTBEAT") or "1").strip().lower() not in {"0", "false", "no", "off"}


def store_dir() -> Path:
    raw = os.environ.get("VP_HEARTBEAT_DIR")
    if raw:
        return Path(raw)
    sq = os.environ.get("SQLITE_PATH")
    if not sq:
        from app.settings import settings
        sq = str(settings.sqlite_path)
    p = Path(sq)
    return (p if p.is_absolute() else Path.cwd() / p).parent


# ------------------------------------------------------------------ пульс (запись)

def _atomic_write(path: Path, payload: dict) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
        return True
    except OSError as exc:  # пульс не должен ронять цикл
        logger.warning("heartbeat: запись не удалась: {}", type(exc).__name__)
        return False


def beat(*, force: bool = False, now: float | None = None, directory: Path | None = None) -> bool:
    """Записать пульс (не чаще раза в `beat_interval_s`). Возвращает True, если запись выполнена."""
    global _last_beat
    if not enabled():
        return False
    t = time.time() if now is None else now
    if not force and time.monotonic() - _last_beat < beat_interval_s():
        return False
    _last_beat = time.monotonic()
    return _atomic_write((directory or store_dir()) / HEARTBEAT_FILE,
                         {"ts": t, "pid": os.getpid(), "stopped": False})


def mark_stopped(directory: Path | None = None) -> None:
    """Чистая остановка: молчание после неё не тревога."""
    if enabled():
        _atomic_write((directory or store_dir()) / HEARTBEAT_FILE,
                      {"ts": time.time(), "pid": os.getpid(), "stopped": True})


def read_heartbeat(directory: Path | None = None) -> dict | None:
    """None - файла нет или он временно нечитаем (решение не принимается)."""
    try:
        data = json.loads(((directory or store_dir()) / HEARTBEAT_FILE).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) and isinstance(data.get("ts"), (int, float)) else None
    except (OSError, ValueError):
        return None


# ------------------------------------------------------------------ чистая логика окна

def evaluate(heartbeat: dict | None, alarm_open: bool, now: float, window: float) -> str:
    """Решение одной проверки: no_heartbeat | stopped | ok | silent_new | silent_known | resolve."""
    if not heartbeat:
        return "no_heartbeat"
    if heartbeat.get("stopped"):  # чистая остановка: не тревога, а открытый эпизод закрывается
        return "resolve" if alarm_open else "stopped"
    if (now - float(heartbeat["ts"])) <= window:
        return "resolve" if alarm_open else "ok"
    return "silent_known" if alarm_open else "silent_new"


# ------------------------------------------------------------------ тревога

def _claim_alarm(directory: Path, info: dict) -> bool:
    """Атомарно занять эпизод: True только у первого."""
    path = directory / ALARM_FILE
    try:
        directory.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(info, fh, ensure_ascii=False)
    return True


def _audit(path: str, outcome: str, status: int) -> None:
    from datetime import datetime
    from app.services import operator_audit
    try:
        operator_audit.insert_row({
            "ts": datetime.utcnow(), "request_id": "wd-" + uuid.uuid4().hex[:16], "method": "WATCHDOG",
            "path": path, "route_template": path, "project_id": None, "actor": "watchdog",
            "status_code": status, "duration_ms": 0, "body_sha256": None, "outcome": outcome})
    except Exception as exc:  # noqa: BLE001 - аудит не должен скрыть саму тревогу
        logger.error("watchdog audit not written: {}: {}", type(exc).__name__, exc)


async def _send(text: str, kind: str, project_id: int | None = None) -> None:
    from app.services.notification_log import notify
    from app.settings import settings
    real = None
    if settings.telegram_active:
        from app.telegram.bot import build_bot
        real, _ = await build_bot()
    from app.telegram.noop_bot import get_worker_bot
    try:
        await notify(get_worker_bot(real), "send_message", settings.telegram_owner_chat_id, text, kind=kind,
                     project_id=project_id)
    finally:
        if real is not None:
            await real.session.close()


async def check_once(*, directory: Path | None = None, now: float | None = None,
                     window: float | None = None) -> dict:
    """Одна независимая проверка. Возвращает {'decision', 'age_s', 'sent'}."""
    d = directory or store_dir()
    t = time.time() if now is None else now
    w = window if window is not None else window_s()
    hb = read_heartbeat(d)
    alarm_open = (d / ALARM_FILE).exists()
    decision = evaluate(hb, alarm_open, t, w)
    age = None if hb is None else round(t - float(hb["ts"]), 1)
    sent = False
    if decision == "silent_new":
        if _claim_alarm(d, {"since": t, "last_heartbeat": hb["ts"], "pid": hb.get("pid")}):
            text = (f"Сторож: цикл надзора молчит {int(age)} с (окно {int(w)} с). Процесс мог упасть или зависнуть. "
                    "Проверьте приложение и перезапустите его.")
            _audit("/watchdog/observer_silent", "alarm", 503)
            try:
                await _send(text, KIND_ALARM)
                sent = True
            except Exception as exc:  # noqa: BLE001 - строка ok=0 уже записана notify
                logger.error("watchdog alarm delivery failed: {}", type(exc).__name__)
        else:
            decision = "silent_known"
    elif decision == "resolve":
        try:
            (d / ALARM_FILE).unlink()
        except FileNotFoundError:
            return {"decision": "ok", "age_s": age, "sent": False}
        _audit("/watchdog/observer_silent", "resolved", 200)
        try:
            await _send("Сторож: цикл надзора снова подаёт признаки жизни.", KIND_RESOLVED)
            sent = True
        except Exception as exc:  # noqa: BLE001
            logger.error("watchdog resolve delivery failed: {}", type(exc).__name__)
    return {"decision": decision, "age_s": age, "sent": sent}


async def run_checker() -> None:
    """Независимая задача веб-процесса: живёт отдельно от цикла надзора."""
    while True:
        try:
            await check_once()
        except Exception as exc:  # noqa: BLE001
            logger.warning("heartbeat checker error: {}", type(exc).__name__)
        await asyncio.sleep(check_interval_s())
