"""W1-1 (A01, A03): диск и логи. Юнит и сухой прогон, без сети и без реальных данных.

Файл назван ``*_test.py``, чтобы pytest его собирал при префиксе ``w1_1_``.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from collections import namedtuple
from pathlib import Path

import pytest

from app.services import disk_guard

Usage = namedtuple("Usage", "total used free")
GB = disk_guard.GB
HOUR = 3600.0


def _usage(free_gb: float, total_gb: float = 500.0):
    free = int(free_gb * GB)
    total = int(total_gb * GB)
    return Usage(total, total - free, free)


@pytest.fixture(autouse=True)
def _clean_guard_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "DISK_GUARD_LOW_FREE_GB",
        "DISK_GUARD_CRITICAL_FREE_GB",
        "DISK_GUARD_TEMP_MAX_AGE_H",
        "LOG_FILE_MAX_MB",
        "LOG_FILE_BACKUPS",
        "DISK_GUARD_DISABLE",
        "VP_TEMP_ROOT",
    ):
        monkeypatch.delenv(name, raising=False)


def _dead_pid() -> int:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def _mkdir_aged(root: Path, name: str, age_h: float, payload: int = 100) -> Path:
    d = root / name
    d.mkdir(parents=True)
    (d / "blob.bin").write_bytes(b"x" * payload)
    ts = time.time() - age_h * HOUR
    os.utime(d, (ts, ts))
    return d


# ----------------------------------------------------------------- check()


def test_check_ok_low_critical_paid_and_free(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(shutil, "disk_usage", lambda _p: _usage(50))
    ok = disk_guard.check(tmp_path)
    assert ok.allowed and ok.level == "ok" and ok.free_bytes == 50 * GB

    monkeypatch.setattr(shutil, "disk_usage", lambda _p: _usage(6))
    low_paid = disk_guard.check(tmp_path, paid=True)
    assert not low_paid.allowed and low_paid.level == "low"
    assert "платные" in low_paid.reason and "6,0 ГБ" in low_paid.reason
    low_free = disk_guard.check(tmp_path, paid=False)
    assert low_free.allowed and low_free.level == "low"
    assert disk_guard.debug_allowed(tmp_path) is False

    monkeypatch.setattr(shutil, "disk_usage", lambda _p: _usage(1))
    crit_free = disk_guard.check(tmp_path, paid=False)
    assert not crit_free.allowed and crit_free.level == "critical"
    assert "остановлены" in crit_free.reason
    assert not disk_guard.check(tmp_path, paid=True).allowed


def test_check_thresholds_from_env_and_args(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(shutil, "disk_usage", lambda _p: _usage(20))
    assert disk_guard.check(tmp_path).allowed
    monkeypatch.setenv("DISK_GUARD_LOW_FREE_GB", "25")
    res = disk_guard.check(tmp_path)
    assert not res.allowed and res.level == "low"
    monkeypatch.setenv("DISK_GUARD_CRITICAL_FREE_GB", "21,5")  # запятая допустима
    assert disk_guard.check(tmp_path).level == "critical"
    # явные аргументы сильнее окружения
    assert disk_guard.check(tmp_path, low_free_gb=5, critical_free_gb=1).allowed
    # мусор в окружении: значения по умолчанию
    monkeypatch.setenv("DISK_GUARD_LOW_FREE_GB", "abc")
    monkeypatch.delenv("DISK_GUARD_CRITICAL_FREE_GB")
    assert disk_guard.thresholds_bytes() == (10 * GB, 3 * GB)
    # критичный не может быть выше низкого
    assert disk_guard.thresholds_bytes(2, 9) == (2 * GB, 2 * GB)


def test_check_unknown_when_usage_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def boom(_p):
        raise OSError("нет доступа")

    monkeypatch.setattr(shutil, "disk_usage", boom)
    res = disk_guard.check(tmp_path / "ещё" / "нет")
    assert (not res.allowed) and res.level == "unknown" and "не удалось измерить" in res.reason
    assert disk_guard.check(tmp_path / "ещё" / "нет", paid=False).allowed  # бесплатный запуск проходит
    assert disk_guard.debug_allowed(tmp_path) is True


def test_check_uses_nearest_existing_parent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: list[str] = []

    def spy(p):
        seen.append(p)
        return _usage(40)

    monkeypatch.setattr(shutil, "disk_usage", spy)
    disk_guard.check(tmp_path / "a" / "b" / "c")
    assert seen == [str(tmp_path)]


# --------------------------------------------------------- pid-* (A01, U)


def test_cleanup_three_old_dead_dirs_removed_fresh_stays(tmp_path: Path) -> None:
    temp = tmp_path / "temp"
    for i, pid in enumerate((900001, 900002, 900003)):
        _mkdir_aged(temp, f"pid-{pid}", age_h=10 + i, payload=1000)
    fresh = _mkdir_aged(temp, "pid-900004", age_h=0.1)
    res = disk_guard.cleanup_stale_pid_temp(temp, max_age_h=6, alive_fn=lambda _p: False)
    assert res.removed == 3
    assert res.freed_bytes == 3000
    assert sorted(p.name for p in temp.iterdir()) == [fresh.name]


def test_cleanup_never_touches_alive_own_or_foreign_names(tmp_path: Path) -> None:
    temp = tmp_path / "temp"
    alive = _mkdir_aged(temp, "pid-4242", age_h=100)
    own = _mkdir_aged(temp, f"pid-{os.getpid()}", age_h=100)
    other = _mkdir_aged(temp, "pid-abc", age_h=100)
    tmp_hf = _mkdir_aged(temp, "tmpxyz", age_h=100)
    keepfile = temp / "pid-777"
    keepfile.write_text("file, not dir")
    res = disk_guard.cleanup_stale_pid_temp(temp, max_age_h=6, alive_fn=lambda p: p == 4242)
    assert res.removed == 0 and res.skipped_alive == 1
    for p in (alive, own, other, tmp_hf, keepfile):
        assert p.exists()


def test_cleanup_real_dead_pid_and_real_alive_pid(tmp_path: Path) -> None:
    """Без подмены alive_fn: настоящий мёртвый PID удаляется, живой (этот процесс-родитель) нет."""
    temp = tmp_path / "temp"
    dead = _dead_pid()
    d_dead = _mkdir_aged(temp, f"pid-{dead}", age_h=50)
    d_alive = _mkdir_aged(temp, f"pid-{os.getppid()}", age_h=50)
    assert disk_guard.pid_alive(os.getpid()) is True
    res = disk_guard.cleanup_stale_pid_temp(temp, max_age_h=6)
    assert not d_dead.exists()
    assert d_alive.exists() or os.getppid() == dead  # родитель жив, пока идёт тест
    assert res.removed >= 1


def test_cleanup_max_dirs_limits_work_per_call(tmp_path: Path) -> None:
    temp = tmp_path / "temp"
    for pid in range(910001, 910006):
        _mkdir_aged(temp, f"pid-{pid}", age_h=20)
    res = disk_guard.cleanup_stale_pid_temp(temp, max_age_h=6, max_dirs=2, alive_fn=lambda _p: False)
    assert res.removed == 2
    assert len(list(temp.iterdir())) == 3


def test_cleanup_age_from_env_default_six_hours(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    temp = tmp_path / "temp"
    young = _mkdir_aged(temp, "pid-920001", age_h=5)
    old = _mkdir_aged(temp, "pid-920002", age_h=7)
    disk_guard.cleanup_stale_pid_temp(temp, alive_fn=lambda _p: False)
    assert young.exists() and not old.exists()
    monkeypatch.setenv("DISK_GUARD_TEMP_MAX_AGE_H", "1")
    disk_guard.cleanup_stale_pid_temp(temp, alive_fn=lambda _p: False)
    assert not young.exists()


def test_cleanup_missing_root_is_noop(tmp_path: Path) -> None:
    assert disk_guard.cleanup_stale_pid_temp(tmp_path / "нет").removed == 0
    assert disk_guard.cleanup_pytest_temp(tmp_path / "нет").removed == 0


# ------------------------------------------------------------ pytest-of-*


def test_cleanup_pytest_of_removes_old_runs_keeps_fresh_and_protected(tmp_path: Path) -> None:
    temp = tmp_path / "temp"
    root = temp / "pytest-of-user"
    old = _mkdir_aged(root, "pytest-1", age_h=30, payload=500)
    fresh = _mkdir_aged(root, "pytest-2", age_h=0.2)
    mine = _mkdir_aged(root, "pytest-3", age_h=40)
    inside = mine / "test_x0"
    inside.mkdir()
    os.utime(mine, (time.time() - 40 * HOUR,) * 2)
    res = disk_guard.cleanup_pytest_temp(temp, max_age_h=6, protect=[inside])
    assert res.removed == 1 and res.freed_bytes == 500
    assert not old.exists() and fresh.exists() and mine.exists()


def test_cleanup_pytest_of_drops_empty_root(tmp_path: Path) -> None:
    temp = tmp_path / "temp"
    _mkdir_aged(temp / "pytest-of-user", "pytest-1", age_h=30)
    disk_guard.cleanup_pytest_temp(temp, max_age_h=6)
    assert not (temp / "pytest-of-user").exists()


# ----------------------------------------------------------------- ротация


def test_rotate_file_shifts_and_caps_backups(tmp_path: Path) -> None:
    log = tmp_path / "a.log"
    for gen in range(6):
        log.write_text(f"gen{gen}" + "x" * 100)
        assert disk_guard.rotate_file(log, max_bytes=50, backups=3) is True
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == ["a.log.1", "a.log.2", "a.log.3"]
    assert (tmp_path / "a.log.1").read_text().startswith("gen5")
    assert (tmp_path / "a.log.3").read_text().startswith("gen3")
    log.write_text("small")
    assert disk_guard.rotate_file(log, max_bytes=50, backups=3) is False


def test_rotate_file_zero_backups_truncates(tmp_path: Path) -> None:
    log = tmp_path / "b.log"
    log.write_text("x" * 100)
    assert disk_guard.rotate_file(log, max_bytes=10, backups=0)
    assert not log.exists()


def test_append_rotating_sum_below_ceiling(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """U A03: малый лимит, сумма всех файлов не выше (копии + 1) * (лимит + строка)."""
    monkeypatch.setenv("LOG_FILE_MAX_MB", "0.002")  # ~2 КБ
    monkeypatch.setenv("LOG_FILE_BACKUPS", "2")
    log = tmp_path / "logs" / "status.log"
    line = "строка-лога " + "z" * 80
    for _ in range(500):
        disk_guard.append_rotating(log, line)
    limit, backups = disk_guard.log_limits()
    files = list(log.parent.iterdir())
    assert {f.name for f in files} <= {"status.log", "status.log.1", "status.log.2"}
    total = sum(f.stat().st_size for f in files)
    assert total <= (backups + 1) * (limit + len(line.encode()) + 2)
    assert total < 500 * len(line.encode()) / 5  # фактически срезано


def test_append_rotating_adds_newline_and_utf8(tmp_path: Path) -> None:
    log = tmp_path / "e.log"
    disk_guard.append_rotating(log, "один")
    disk_guard.append_rotating(log, "два\n")
    assert log.read_text(encoding="utf-8") == "один\nдва\n"


def test_enforce_total_cap_deletes_oldest_first_and_respects_protect(tmp_path: Path) -> None:
    now = time.time()
    names = ["old.log", "mid.log", "new.log", "live.log"]
    for i, n in enumerate(names):
        f = tmp_path / n
        f.write_bytes(b"x" * 100)
        os.utime(f, (now - (10 - i) * HOUR,) * 2)
    res = disk_guard.enforce_total_cap(
        tmp_path, max_total_bytes=250, patterns=("*.log",), protect=[tmp_path / "old.log"]
    )
    left = sorted(p.name for p in tmp_path.iterdir())
    assert "old.log" in left  # защищён
    assert res.removed == 2 and sum(p.stat().st_size for p in tmp_path.iterdir()) <= 200 + 100
    assert "new.log" in left or "live.log" in left


def test_enforce_total_cap_age_limit(tmp_path: Path) -> None:
    now = time.time()
    a = tmp_path / "a.log"
    b = tmp_path / "b.log"
    a.write_bytes(b"1")
    b.write_bytes(b"1")
    os.utime(a, (now - 10 * 86400,) * 2)
    res = disk_guard.enforce_total_cap(tmp_path, max_total_bytes=10**9, patterns=("*.log",), max_age_days=3)
    assert res.removed == 1 and not a.exists() and b.exists()


# ------------------------------------------------------------------ VACUUM


def test_vacuum_sqlite_shrinks_file(tmp_path: Path) -> None:
    db = tmp_path / "tracker.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    con.executemany("INSERT INTO t (v) VALUES (?)", [("x" * 2000,) for _ in range(500)])
    con.commit()
    con.execute("DELETE FROM t")
    con.commit()
    con.close()
    before = db.stat().st_size
    ok, msg = disk_guard.vacuum_sqlite(db)
    assert ok, msg
    assert db.stat().st_size < before
    ok2, msg2 = disk_guard.vacuum_sqlite(tmp_path / "нет.db")
    assert not ok2 and "не найдена" in msg2


def test_vacuum_refused_without_free_space(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE t (id INTEGER)")
    con.commit()
    con.close()
    monkeypatch.setattr(shutil, "disk_usage", lambda _p: _usage(0.5))
    ok, msg = disk_guard.vacuum_sqlite(db)
    assert not ok and "Не хватает места" in msg


# ------------------------------------------------------------ housekeeping


def test_housekeeping_dry_run_on_data_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data = tmp_path / "data"
    logs = tmp_path / "logs"
    logs.mkdir()
    temp = data / ".cache" / "temp"
    dead = _dead_pid()
    old_pid = _mkdir_aged(temp, f"pid-{dead}", age_h=24, payload=300)
    fresh_pid = _mkdir_aged(temp, "pid-930001", age_h=0.1)
    old_py = _mkdir_aged(temp / "pytest-of-u", "pytest-1", age_h=48, payload=300)
    # backend логи: общий остаётся всегда, старые сессионные уходят по сроку
    data.mkdir(exist_ok=True)
    (data / "backend.log").write_bytes(b"s" * 100)
    old_sess = data / "backend-111.log"
    old_sess.write_bytes(b"o" * 100)
    os.utime(old_sess, (time.time() - 30 * 86400,) * 2)
    new_sess = data / "backend-222.log"
    new_sess.write_bytes(b"n" * 100)
    # logs/ ротация
    monkeypatch.setenv("LOG_FILE_MAX_MB", "0.002")
    monkeypatch.setenv("LOG_FILE_BACKUPS", "1")
    (logs / "errors.log").write_bytes(b"e" * 5000)
    # монитор: суммарный потолок
    monkeypatch.setenv("MONITOR_TOTAL_CAP_MB", "0")
    mon = data / "monitor" / "logs"
    mon.mkdir(parents=True)
    (mon / "pipeline_2020-01-01.log").write_bytes(b"m" * 1000)
    tracker = tmp_path / "no-tracker.db"

    rep = disk_guard.housekeeping(data, logs_dir=logs, tracker_db=tracker)
    assert rep["skipped"] is False
    assert rep["pid_temp_removed"] == 1 and rep["pytest_removed"] == 1
    assert not old_pid.exists() and fresh_pid.exists() and not old_py.exists()
    assert not old_sess.exists() and new_sess.exists() and (data / "backend.log").exists()
    assert rep["logs_rotated"] == 1 and (logs / "errors.log.1").exists()
    assert not (mon / "pipeline_2020-01-01.log").exists()

    again = disk_guard.housekeeping(data, logs_dir=logs, tracker_db=tracker, min_interval_s=3600)
    assert again == {"skipped": True}


def test_housekeeping_backend_total_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("BACKEND_LOG_TOTAL_CAP_MB", "0")
    for pid in (1, 2, 3):
        f = data / f"backend-{pid}.log"
        f.write_bytes(b"x" * 100)
        os.utime(f, (time.time() - (4 - pid) * HOUR,) * 2)
    (data / "backend.log").write_bytes(b"y" * 100)
    disk_guard.housekeeping(data, logs_dir=tmp_path / "logs", tracker_db=tmp_path / "x.db")
    assert sorted(p.name for p in data.iterdir() if p.is_file() and p.suffix == ".log") == ["backend.log"]


def test_housekeeping_timer_starts_once_and_runs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[int] = []
    monkeypatch.setattr(disk_guard, "housekeeping", lambda *a, **k: calls.append(1) or {})
    monkeypatch.setattr(disk_guard, "_timer_thread", None)
    try:
        assert disk_guard.start_housekeeping_timer(interval_s=1.0, data_dir=tmp_path) is True
        assert disk_guard.start_housekeeping_timer(interval_s=1.0, data_dir=tmp_path) is False
        deadline = time.time() + 6
        while not calls and time.time() < deadline:
            time.sleep(0.1)
        assert calls, "таймер не вызвал housekeeping"
    finally:
        disk_guard.stop_housekeeping_timer()


# ------------------------------------------------------------ bootstrap_env


@pytest.fixture
def isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """apply_nvidia_env меняет os.environ и tempfile глобально: возвращаем всё как было."""
    snapshot = dict(os.environ)
    old_tempdir = tempfile.tempdir
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    yield
    os.environ.clear()
    os.environ.update(snapshot)
    tempfile.tempdir = old_tempdir


def test_bootstrap_temp_root_outside_data_under_pytest(isolated_env, tmp_path: Path, monkeypatch) -> None:
    import app.bootstrap_env as bootstrap

    data = tmp_path / "data"
    monkeypatch.setenv("DATA_DIR", str(data))
    monkeypatch.setenv("ASR_BACKEND", "nvidia")
    cache = bootstrap.apply_nvidia_env(force=True)
    temp = Path(os.environ["TEMP"])
    assert temp.name == f"pid-{os.getpid()}"
    assert Path(tempfile.gettempdir()) == temp
    assert data.resolve() not in temp.resolve().parents  # pytest basetemp вне data/.cache
    assert (tmp_path / "localappdata" / "video-pipeline-temp") == temp.parent
    assert not (cache / "temp" / f"pid-{os.getpid()}").exists()
    assert (cache / "huggingface" / "hub").is_dir()  # HF/NeMo кэш остался в data/.cache


def test_bootstrap_vp_temp_root_override(isolated_env, tmp_path: Path, monkeypatch) -> None:
    import app.bootstrap_env as bootstrap

    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("VP_TEMP_ROOT", str(tmp_path / "mytemp"))
    bootstrap.apply_nvidia_env(force=True)
    assert Path(os.environ["TEMP"]) == (tmp_path / "mytemp" / f"pid-{os.getpid()}").resolve()


def test_bootstrap_startup_cleanup_removes_old_dead_pid_dirs(isolated_env, tmp_path: Path, monkeypatch) -> None:
    import app.bootstrap_env as bootstrap

    data = tmp_path / "data"
    temp = data / ".cache" / "temp"
    dead = _dead_pid()
    old = [_mkdir_aged(temp, f"pid-{dead}", age_h=30)]
    old.append(_mkdir_aged(temp, f"pid-{dead + 100000}", age_h=30))
    fresh = _mkdir_aged(temp, f"pid-{dead + 200000}", age_h=0.1)
    monkeypatch.setenv("DATA_DIR", str(data))
    monkeypatch.setenv("VP_TEMP_ROOT", str(tmp_path / "ext"))
    bootstrap.apply_nvidia_env(force=True)
    assert not old[0].exists() and not old[1].exists()
    assert fresh.exists()


def test_bootstrap_startup_cleanup_can_be_disabled(isolated_env, tmp_path: Path, monkeypatch) -> None:
    import app.bootstrap_env as bootstrap

    data = tmp_path / "data"
    old = _mkdir_aged(data / ".cache" / "temp", f"pid-{_dead_pid()}", age_h=30)
    monkeypatch.setenv("DATA_DIR", str(data))
    monkeypatch.setenv("DISK_GUARD_DISABLE", "1")
    monkeypatch.setenv("VP_TEMP_ROOT", str(tmp_path / "ext"))
    bootstrap.apply_nvidia_env(force=True)
    assert old.exists()


def test_bootstrap_startup_cleanup_swallows_errors(isolated_env, tmp_path: Path, monkeypatch) -> None:
    import app.bootstrap_env as bootstrap

    def boom(*_a, **_k):
        raise RuntimeError("сломано")

    monkeypatch.setattr(disk_guard, "cleanup_stale_pid_temp", boom)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("VP_TEMP_ROOT", str(tmp_path / "ext"))
    bootstrap.apply_nvidia_env(force=True)  # не падает


# --------------------------------------------------------- nvidia_asr_env


def test_nvidia_env_clear_stale_pid_temp_dirs(tmp_path: Path) -> None:
    from app.services.nvidia_asr_env import clear_stale_pid_temp_dirs

    cache = tmp_path / ".cache"
    temp = cache / "temp"
    old = _mkdir_aged(temp, f"pid-{_dead_pid()}", age_h=12)
    fresh = _mkdir_aged(temp, "pid-940001", age_h=0.1)
    own = _mkdir_aged(temp, f"pid-{os.getpid()}", age_h=99)
    assert clear_stale_pid_temp_dirs(cache, max_age_h=6) >= 1
    assert not old.exists() and fresh.exists() and own.exists()


def test_nvidia_env_configure_runs_pid_cleanup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.nvidia_asr_env import configure_nvidia_asr_environment

    snapshot = dict(os.environ)
    old_tempdir = tempfile.tempdir
    try:
        monkeypatch.setattr("app.settings.settings.data_dir", tmp_path / "data")
        old = _mkdir_aged(tmp_path / "data" / ".cache" / "temp", f"pid-{_dead_pid()}", age_h=12)
        configure_nvidia_asr_environment(force=True)
        assert not old.exists()
    finally:
        os.environ.clear()
        os.environ.update(snapshot)
        tempfile.tempdir = old_tempdir


# ------------------------------------------------------- логи ботов и статусов


def test_status_log_rotation(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from app.services import node_status_machine as nsm

    monkeypatch.setenv("LOG_FILE_MAX_MB", "0.002")
    monkeypatch.setenv("LOG_FILE_BACKUPS", "2")
    path = tmp_path / "logs" / "status.log"
    monkeypatch.setattr(nsm, "STATUS_LOG_PATH", path)
    for i in range(400):
        nsm._write_status_log(f"2026-01-01T00:00:00\tOK\tproject={i}\tnode=a/b\tpending → queued")
    names = {p.name for p in path.parent.iterdir()}
    assert names <= {"status.log", "status.log.1", "status.log.2"} and "status.log.1" in names
    limit, backups = disk_guard.log_limits()
    assert sum(p.stat().st_size for p in path.parent.iterdir()) <= (backups + 1) * (limit + 200)
    assert path.read_text(encoding="utf-8").rstrip().endswith("pending → queued")


def test_status_log_write_error_does_not_raise(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from app.services import node_status_machine as nsm

    def deny(*_a, **_k):
        raise OSError("диск полон")

    monkeypatch.setattr(nsm, "append_rotating", deny)
    nsm._write_status_log("x")  # логирует предупреждение, не падает


@pytest.mark.parametrize(
    ("module", "func", "bot"),
    [
        ("app.bots.outsee", "_log_outsee_error", "outsee"),
        ("app.bots.chatgpt", "_log_chatgpt_error", "chatgpt"),
        ("app.bots.elevenlabs", "_log_elevenlabs_error", "elevenlabs"),
    ],
)
def test_bot_errors_log_rotates(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, module: str, func: str, bot: str) -> None:
    import importlib

    mod = importlib.import_module(module)
    path = tmp_path / "logs" / "errors.log"
    monkeypatch.setattr(mod, "ERRORS_LOG_PATH", path)
    monkeypatch.setenv("LOG_FILE_MAX_MB", "0.002")
    monkeypatch.setenv("LOG_FILE_BACKUPS", "1")
    fn = getattr(mod, func)
    for i in range(300):
        fn(kind="timeout", text=f"ошибка номер {i} " + "q" * 60)
    names = {p.name for p in path.parent.iterdir()}
    assert names == {"errors.log", "errors.log.1"}
    limit, _ = disk_guard.log_limits()
    assert sum(p.stat().st_size for p in path.parent.iterdir()) <= 2 * (limit + 200)
    last = path.read_text(encoding="utf-8").splitlines()[-1]
    assert f"bot={bot}" in last and "kind=timeout" in last and "ошибка номер 299" in last


# ----------------------------------------------------------------- log_sink


@pytest.fixture
def sink_env(monkeypatch: pytest.MonkeyPatch):
    from app.monitor import log_sink

    yield log_sink
    log_sink.remove_sinks()
    log_sink.close()


def test_log_sink_limits_from_env(sink_env, monkeypatch: pytest.MonkeyPatch) -> None:
    lim = sink_env.sink_limits()
    assert lim["rotation_bytes"] == 20 * disk_guard.MB and lim["retention"] == "3 days"
    monkeypatch.setenv("MONITOR_LOG_ROTATION_MB", "5")
    monkeypatch.setenv("MONITOR_LOG_RETENTION_DAYS", "1")
    lim = sink_env.sink_limits()
    assert lim["rotation_bytes"] == 5 * disk_guard.MB and lim["retention"] == "1 days"


def test_log_sink_init_twice_does_not_duplicate_sinks(sink_env, tmp_path: Path) -> None:
    sink_env.init(tmp_path / "m")
    first = list(sink_env._sink_ids)
    sink_env.init(tmp_path / "m")
    assert len(sink_env._sink_ids) == 2 and not set(first) & set(sink_env._sink_ids)


def test_log_sink_debug_off_when_disk_low(sink_env, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from loguru import logger

    monkeypatch.setattr(shutil, "disk_usage", lambda _p: _usage(2))
    sink_env.init(tmp_path / "m")
    logger.debug("дебаг-сообщение-не-должно-попасть")
    logger.warning("варнинг-должен-попасть")
    logger.complete()
    sink_env.remove_sinks()
    text = "".join(f.read_text(encoding="utf-8") for f in (tmp_path / "m" / "logs").glob("*.log"))
    assert "варнинг-должен-попасть" in text and "дебаг-сообщение-не-должно-попасть" not in text


def test_log_sink_events_rotate_by_size_and_day(sink_env, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MONITOR_EVENTS_MAX_MB", "1")  # минимум 1 МБ: подменим предел напрямую
    monkeypatch.setattr(
        sink_env, "sink_limits", lambda: {
            "rotation_bytes": disk_guard.MB, "retention": "3 days", "retention_days": 3,
            "events_max_bytes": 2000, "total_cap_bytes": 10**9,
        },
    )
    monkeypatch.setenv("LOG_FILE_BACKUPS", "2")
    mon = tmp_path / "m"
    sink_env.init(mon)
    for i in range(200):
        sink_env.emit_event("step", project_id=i, detail={"pad": "p" * 40})
    events = sorted(p.name for p in (mon / "events").iterdir())
    today = [n for n in events if n.endswith(".jsonl") or ".jsonl." in n]
    assert any(".jsonl.1" in n for n in today) and len(today) <= 3
    # смена суток: новая запись идёт в файл новой даты, старый не трогается
    old_day_file = mon / "events" / "events_2000-01-01.jsonl"
    old_day_file.write_text('{"event":"old"}\n', encoding="utf-8")
    sink_env._events_day = "2000-01-01"
    sink_env.emit_event("after-midnight")
    assert old_day_file.read_text(encoding="utf-8") == '{"event":"old"}\n'
    assert sink_env._events_day != "2000-01-01"
    assert "after-midnight" in "".join(
        p.read_text(encoding="utf-8") for p in (mon / "events").glob("events_*.jsonl") if p != old_day_file
    )


def test_log_sink_total_cap_keeps_sum_below_ceiling(sink_env, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    mon = tmp_path / "m"
    (mon / "logs").mkdir(parents=True)
    (mon / "events").mkdir()
    now = time.time()
    for i in range(10):
        f = (mon / "logs" if i % 2 else mon / "events") / f"f{i}.jsonl"
        f.write_bytes(b"x" * 1000)
        os.utime(f, (now - (20 - i) * HOUR,) * 2)
    monkeypatch.setattr(
        sink_env, "sink_limits", lambda: {
            "rotation_bytes": disk_guard.MB, "retention": "3 days", "retention_days": 3,
            "events_max_bytes": disk_guard.MB, "total_cap_bytes": 3500,
        },
    )
    removed = sink_env.enforce_monitor_cap(mon)
    left = [p for p in mon.rglob("*") if p.is_file()]
    assert removed == 7 and sum(p.stat().st_size for p in left) <= 3500
    assert {p.name for p in left} == {"f7.jsonl", "f8.jsonl", "f9.jsonl"}  # новые остались


def test_log_sink_write_error_does_not_raise(sink_env, tmp_path: Path) -> None:
    sink_env.init(tmp_path / "m")

    class Broken:
        def write(self, _s):
            raise OSError("ENOSPC")

        def flush(self):
            pass

        def close(self):
            pass

    sink_env._events_file = Broken()
    sink_env.emit_event("x")  # событие теряется, процесс жив


# ------------------------------------------------------------ run-backend.ps1

_PS1 = Path(__file__).resolve().parent.parent / "scripts" / "run-backend.ps1"


def _powershell() -> str | None:
    return shutil.which("powershell") or shutil.which("pwsh")


def _rotation_block() -> str:
    text = _PS1.read_text(encoding="utf-8-sig")
    a = text.index("# --- LOG-ROTATION-BEGIN")
    b = text.index("# --- LOG-ROTATION-END ---")
    return text[a:b]


def test_run_backend_ps1_has_rotation_and_cap() -> None:
    text = _PS1.read_text(encoding="utf-8-sig")
    assert "Invoke-BackendLogRotation $logDir $sessionLog" in text
    assert text.index("Invoke-BackendLogRotation $logDir") < text.index("Write-BackendLogLine \"=== backend start")
    assert "$LogTotalCapBytes" in text and "Rotate-LogFile $sharedLog" in text


@pytest.mark.skipif(_powershell() is None, reason="нет PowerShell")
def test_run_backend_ps1_rotation_block_runs_in_powershell(tmp_path: Path) -> None:
    """D: настоящий блок ротации из скрипта на файлах в tmp_path."""
    d = tmp_path / "data"
    d.mkdir()
    shared = d / "backend.log"
    shared.write_bytes(b"s" * 5000)
    cur = d / "backend-100.log"
    cur.write_bytes(b"c" * 100)
    old = d / "backend-1.log"
    old.write_bytes(b"o" * 100)
    stamp = time.time() - 20 * 86400
    os.utime(old, (stamp, stamp))
    mids = []
    for n in (2, 3, 4):
        f = d / f"backend-{n}.log"
        f.write_bytes(b"m" * 400)
        t = time.time() - (10 - n) * HOUR
        os.utime(f, (t, t))
        mids.append(f)
    script = tmp_path / "rot.ps1"
    body = _rotation_block() + (
        f'\n$r = Invoke-BackendLogRotation "{d}" "{cur}" 1000 2 5600 7\n'
        'Write-Output "REMOVED=$r"\n'
    )
    script.write_bytes(b"\xef\xbb\xbf" + body.encode("utf-8"))
    proc = subprocess.run(
        [_powershell(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert (d / "backend.log.1").exists() and not shared.exists()  # общий лог ротирован
    assert cur.exists() and not old.exists()  # текущий цел, просроченный удалён
    remaining = [f for f in d.iterdir() if f.is_file()]
    total_deletable = sum(f.stat().st_size for f in remaining if f.name not in ("backend.log", cur.name))
    assert total_deletable + cur.stat().st_size <= 5600  # суммарный потолок соблюдён
    assert not mids[0].exists() and mids[2].exists()  # старые удалены первыми
    assert "REMOVED=" in proc.stdout
