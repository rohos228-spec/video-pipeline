"""Охрана прогона pytest: один процесс за раз и никакой боевой базы.

1. Замок запуска (``acquire_run_lock``). Два параллельных pytest на этой машине роняют память, а договорённость
   «занять папку _pytest.lock» держалась на слове. Теперь замок настоящий: файл рядом с репозиторием
   (``<папка над репозиторием>/_pytest.run.lock``), в нём pid держателя. Живой держатель -> второй прогон сразу
   останавливается с понятным сообщением (или ждёт ``VP_PYTEST_LOCK_WAIT`` секунд). Мёртвый pid (упавший прогон) -> замок
   снимается сам. Дочерние pytest внутри теста держателя замок не берут (``VP_PYTEST_LOCK_HELD_BY``).
   Выключить: ``VP_PYTEST_NO_LOCK=1``. Старая папка ``_pytest.lock`` остаётся очередью между агентами и этому замку не мешает.

2. Охрана боевой базы (``live_db_violation``). Глобальный ``app.db.engine`` создаётся при импорте и смотрит на
   ``SQLITE_PATH``. Если переменной нет, это боевой ``data/state.db``: один тест уже записал в него проект. Поэтому conftest
   до импорта приложения кладёт ``SQLITE_PATH`` и ``DATA_DIR`` во временную папку, а явный путь внутрь ``data/``
   репозитория отклоняет.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIVE_DATA = ROOT / "data"
DEFAULT_LOCK = ROOT.parent / "_pytest.run.lock"
HELD_ENV = "VP_PYTEST_LOCK_HELD_BY"


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        process_query_limited_information = 0x1000
        still_active = 259
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True
            return code.value == still_active
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def read_holder(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def busy_message(path: Path, holder: dict | None) -> str:
    pid = (holder or {}).get("pid", "?")
    since = (holder or {}).get("started", "?")
    cmd = str((holder or {}).get("cmd", "?"))[:160]
    return (
        f"Уже идёт другой прогон pytest (pid {pid}, с {since}, команда: {cmd}). Одновременно ровно один процесс pytest: "
        f"два параллельных прогона роняют память. Дождитесь окончания или задайте VP_PYTEST_LOCK_WAIT=секунды. "
        f"Файл замка: {path}"
    )


def acquire_run_lock(path: Path | None = None, wait_s: float = 0.0, *, pid: int | None = None) -> tuple[bool, str]:
    """Берёт замок. Возвращает (взят, сообщение). Не взят, если держатель жив и ожидание вышло."""
    path = path or DEFAULT_LOCK
    me = pid or os.getpid()
    deadline = time.monotonic() + max(0.0, wait_s)
    while True:
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            holder = read_holder(path)
            holder_pid = int((holder or {}).get("pid") or 0)
            if holder_pid == me:
                return True, "замок уже наш"
            if not holder_pid or not pid_alive(holder_pid):
                try:
                    path.unlink()  # держатель умер (или файл битый): замок осиротел
                except FileNotFoundError:
                    pass
                continue
            if time.monotonic() >= deadline:
                return False, busy_message(path, holder)
            time.sleep(min(2.0, max(0.1, deadline - time.monotonic())))
            continue
        except OSError as exc:
            return True, f"замок недоступен ({exc}), прогон без замка"
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"pid": me, "started": time.strftime("%Y-%m-%d %H:%M:%S"), "cmd": " ".join(sys.argv)[:300]}, fh)
        return True, "замок взят"


def release_run_lock(path: Path | None = None, *, pid: int | None = None) -> bool:
    """Снимает замок только если он наш."""
    path = path or DEFAULT_LOCK
    holder = read_holder(path)
    if holder is None or int(holder.get("pid") or 0) != (pid or os.getpid()):
        return False
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    return True


def lock_wanted(env: dict | None = None) -> bool:
    """Замок нужен корневому процессу pytest: не рабочему процессу xdist, не дочернему прогону и не при VP_PYTEST_NO_LOCK=1."""
    env = os.environ if env is None else env
    if env.get("VP_PYTEST_NO_LOCK") == "1":
        return False
    held_by = env.get(HELD_ENV)
    if held_by and held_by.isdigit() and pid_alive(int(held_by)):
        return False
    return True


def _inside(path: Path, base: Path) -> bool:
    try:
        path.resolve().relative_to(base.resolve())
    except ValueError:
        return False
    return True


def live_db_violation(db_file: str | os.PathLike | None, *, live_data: Path | None = None) -> str | None:
    """Текст ошибки, если файл базы лежит в боевой папке data/ репозитория (None - всё в порядке)."""
    if not db_file or str(db_file) == ":memory:":
        return None
    live = live_data or LIVE_DATA
    if _inside(Path(db_file), live):
        return (
            f"Тесты смотрят в боевую базу {db_file} (внутри {live}). Прогон остановлен: боевая data/ не место для тестов. "
            f"Задайте SQLITE_PATH и DATA_DIR во временной папке вне репозитория или не задавайте их вовсе: conftest подставит временные."
        )
    return None


def prepare_isolated_env(env: dict | None = None, *, live_data: Path | None = None, base: Path | None = None) -> str | None:
    """Ставит SQLITE_PATH и DATA_DIR во временную папку, если они не заданы. Явный путь в боевую data/ - ошибка (текст).

    Вызывается из conftest ДО импорта app.settings: глобальный движок БД создаётся при импорте и берёт путь из окружения.
    """
    import tempfile

    env = os.environ if env is None else env
    live = live_data or LIVE_DATA
    for name, fallback in (("SQLITE_PATH", "state.db"), ("DATA_DIR", "")):
        value = env.get(name)
        if value:
            candidate = Path(value)
            if not candidate.is_absolute():
                candidate = ROOT / candidate
            if _inside(candidate, live):
                return live_db_violation(candidate, live_data=live)
    if not env.get("DATA_DIR") or not env.get("SQLITE_PATH"):
        root = Path(tempfile.mkdtemp(prefix="vp-pytest-", dir=str(base) if base else None))
        env.setdefault("DATA_DIR", str(root / "data"))
        env.setdefault("SQLITE_PATH", str(root / "state.db"))
    return None
