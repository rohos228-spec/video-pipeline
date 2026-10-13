"""Фаза 3 (Q-12, Q-15): замок одного pytest, охрана боевой базы, явная метка ffmpeg.

Замок проверяется на временном файле (не на настоящем замке этого прогона): тест не должен мешать ни себе, ни соседям.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import _pytest_guard as guard

ROOT = Path(__file__).resolve().parents[1]


def _sleeper() -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])


# --- замок запуска --------------------------------------------------------------------------------------------------

def test_lock_is_taken_and_released_by_its_owner(tmp_path):
    lock = tmp_path / "run.lock"
    ok, _ = guard.acquire_run_lock(lock)
    assert ok and lock.exists()
    assert guard.read_holder(lock)["pid"] == os.getpid()
    assert guard.release_run_lock(lock) and not lock.exists()


def test_live_holder_blocks_a_second_run_with_a_clear_message(tmp_path):
    lock = tmp_path / "run.lock"
    other = _sleeper()
    try:
        assert guard.acquire_run_lock(lock, pid=other.pid)[0]
        ok, message = guard.acquire_run_lock(lock, wait_s=0)
        assert not ok
        assert "Уже идёт другой прогон pytest" in message and str(other.pid) in message and "VP_PYTEST_LOCK_WAIT" in message
        assert not guard.release_run_lock(lock), "чужой замок снимать нельзя"
        assert lock.exists()
    finally:
        other.kill()
        other.wait()


def test_lock_of_a_dead_run_is_taken_over(tmp_path):
    lock = tmp_path / "run.lock"
    dead = _sleeper()
    guard.acquire_run_lock(lock, pid=dead.pid)
    dead.kill()
    dead.wait()
    time.sleep(0.2)
    ok, _ = guard.acquire_run_lock(lock, wait_s=0)
    assert ok and guard.read_holder(lock)["pid"] == os.getpid()
    guard.release_run_lock(lock)


def test_garbage_lock_file_does_not_block(tmp_path):
    lock = tmp_path / "run.lock"
    lock.write_text("не json", encoding="utf-8")
    ok, _ = guard.acquire_run_lock(lock, wait_s=0)
    assert ok
    guard.release_run_lock(lock)


def test_wait_returns_when_the_holder_finishes(tmp_path):
    lock = tmp_path / "run.lock"
    holder = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(2)"])
    try:
        guard.acquire_run_lock(lock, pid=holder.pid)
        t0 = time.monotonic()
        ok, _ = guard.acquire_run_lock(lock, wait_s=20)
        assert ok and time.monotonic() - t0 < 15
    finally:
        holder.kill()
        holder.wait()
        guard.release_run_lock(lock)


def test_child_run_of_the_holder_skips_the_lock():
    me = str(os.getpid())
    assert guard.lock_wanted({}) is True
    assert guard.lock_wanted({"VP_PYTEST_NO_LOCK": "1"}) is False
    assert guard.lock_wanted({guard.HELD_ENV: me}) is False  # дочерний pytest внутри теста держателя
    assert guard.lock_wanted({guard.HELD_ENV: "999999999"}) is True  # держатель мёртв: замок снова нужен


def test_this_run_holds_the_real_lock():
    """Тест идёт под настоящим замком (если не выключен VP_PYTEST_NO_LOCK=1): файл есть и принадлежит процессу pytest."""
    if os.environ.get("VP_PYTEST_NO_LOCK") == "1" or not os.environ.get(guard.HELD_ENV):
        return
    holder = guard.read_holder(guard.DEFAULT_LOCK)
    assert holder is not None and holder["pid"] == int(os.environ[guard.HELD_ENV])


# --- охрана боевой базы ---------------------------------------------------------------------------------------------

def test_live_data_paths_are_rejected(tmp_path):
    live = tmp_path / "repo" / "data"
    assert guard.live_db_violation(live / "state.db", live_data=live)
    assert guard.live_db_violation(live / "videos" / "p" / "project.db", live_data=live)
    assert guard.live_db_violation(tmp_path / "elsewhere" / "state.db", live_data=live) is None
    assert guard.live_db_violation(":memory:", live_data=live) is None
    assert guard.live_db_violation(None, live_data=live) is None


def test_unset_environment_gets_temporary_paths_outside_live_data(tmp_path):
    env: dict[str, str] = {}
    live = tmp_path / "repo" / "data"
    assert guard.prepare_isolated_env(env, live_data=live, base=tmp_path) is None
    assert env["SQLITE_PATH"] and env["DATA_DIR"]
    assert guard.live_db_violation(env["SQLITE_PATH"], live_data=live) is None
    assert guard.live_db_violation(env["DATA_DIR"], live_data=live) is None


def test_explicit_path_into_live_data_is_refused(tmp_path):
    live = tmp_path / "repo" / "data"
    env = {"SQLITE_PATH": str(live / "state.db")}
    message = guard.prepare_isolated_env(env, live_data=live, base=tmp_path)
    assert message and "боевую базу" in message
    env = {"SQLITE_PATH": str(tmp_path / "ok" / "state.db"), "DATA_DIR": str(live / "videos")}
    assert guard.prepare_isolated_env(env, live_data=live, base=tmp_path)


def test_explicit_outside_paths_are_kept(tmp_path):
    env = {"SQLITE_PATH": str(tmp_path / "a" / "state.db"), "DATA_DIR": str(tmp_path / "a" / "data")}
    before = dict(env)
    assert guard.prepare_isolated_env(env, live_data=tmp_path / "live", base=tmp_path) is None
    assert env == before


def test_this_session_engine_does_not_point_at_live_data():
    from app.db import engine

    assert guard.live_db_violation(engine.url.database) is None
    assert guard.live_db_violation(os.environ["SQLITE_PATH"]) is None


# --- метка ffmpeg ---------------------------------------------------------------------------------------------------

def test_tests_that_need_ffmpeg_carry_an_explicit_marker():
    """conftest сам ставит метку ``ffmpeg`` на тесты со skipif по ffmpeg/ffprobe: ``-m ffmpeg`` выбирает именно их."""
    out = subprocess.run(
        [sys.executable, "-X", "utf8", "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", "-p", "no:warnings",
         "-m", "ffmpeg", "tests/w5h_assemble_local_script_test.py"],
        cwd=str(ROOT), capture_output=True, text=True, timeout=300,
    )
    selected = [ln for ln in out.stdout.splitlines() if "::" in ln]
    assert out.returncode == 0 and selected, out.stdout[-1500:] + out.stderr[-500:]
    assert all("w5h_assemble_local_script_test.py" in ln for ln in selected)
