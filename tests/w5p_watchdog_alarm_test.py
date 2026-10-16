from __future__ import annotations

import asyncio
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from app.services import supervisor_heartbeat as hb

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("heartbeat, alarm, now, expected", [
    (None, False, 100, "no_heartbeat"),
    ({"ts": 95}, False, 100, "ok"),
    ({"ts": 95}, True, 100, "resolve"),
    ({"ts": 100 - 10}, False, 100, "ok"),
    ({"ts": 100 - 10.5}, False, 100, "silent_new"),
    ({"ts": 50}, True, 100, "silent_known"),
    ({"ts": 50, "stopped": True}, False, 100, "stopped"),
    ({"ts": 50, "stopped": True}, True, 100, "resolve"),
])
def test_evaluate_window(heartbeat, alarm, now, expected):
    assert hb.evaluate(heartbeat, alarm, now, 10) == expected


def test_settings_from_own_env(monkeypatch):
    monkeypatch.delenv("VP_WATCHDOG_HEARTBEAT_WINDOW_S", raising=False)
    assert hb.window_s() == 300.0
    monkeypatch.setenv("VP_WATCHDOG_HEARTBEAT_WINDOW_S", "5")
    assert hb.window_s() == 5.0
    monkeypatch.setenv("VP_WATCHDOG_HEARTBEAT_WINDOW_S", "мусор")
    assert hb.window_s() == 300.0
    monkeypatch.setenv("VP_WATCHDOG_HEARTBEAT_WINDOW_S", "0")
    assert hb.window_s() == 2.0


def test_beat_is_one_small_file_and_throttled(tmp_path, monkeypatch):
    monkeypatch.setenv("VP_HEARTBEAT_INTERVAL_S", "60")
    hb._last_beat = 0.0
    assert hb.beat(directory=tmp_path, now=1000.0)
    assert not hb.beat(directory=tmp_path, now=1001.0)
    assert hb.beat(directory=tmp_path, now=1002.0, force=True)
    assert [p.name for p in tmp_path.iterdir()] == [hb.HEARTBEAT_FILE]
    assert hb.read_heartbeat(tmp_path)["ts"] == 1002.0
    monkeypatch.setenv("VP_SUPERVISOR_HEARTBEAT", "0")
    assert not hb.beat(directory=tmp_path, force=True)


def test_unreadable_heartbeat_is_not_an_alarm(tmp_path):
    (tmp_path / hb.HEARTBEAT_FILE).write_text("{обрыв", encoding="utf-8")
    assert hb.read_heartbeat(tmp_path) is None
    assert hb.evaluate(None, False, 1.0, 5) == "no_heartbeat"


def test_cancelled_loop_task_raises_one_alarm_then_resolves(tmp_path, monkeypatch):
    sent: list[tuple[str, str]] = []

    async def fake_send(text, kind):
        sent.append((kind, text))

    audits: list[str] = []
    monkeypatch.setattr(hb, "_send", fake_send)
    monkeypatch.setattr(hb, "_audit", lambda path, outcome, status: audits.append(outcome))
    monkeypatch.setenv("VP_HEARTBEAT_INTERVAL_S", "0.2")

    async def scenario():
        async def loop():
            while True:
                hb.beat(directory=tmp_path, force=True)
                await asyncio.sleep(0.1)

        task = asyncio.create_task(loop())
        await asyncio.sleep(0.3)
        assert (await hb.check_once(directory=tmp_path, window=2))["decision"] == "ok"
        task.cancel()
        await asyncio.sleep(0)
        t0 = time.time() + 5
        first = await hb.check_once(directory=tmp_path, window=2, now=t0)
        second = await hb.check_once(directory=tmp_path, window=2, now=t0 + 1)
        hb.beat(directory=tmp_path, force=True, now=t0 + 2)
        third = await hb.check_once(directory=tmp_path, window=2, now=t0 + 2.5)
        fourth = await hb.check_once(directory=tmp_path, window=2, now=t0 + 3)
        return first, second, third, fourth

    first, second, third, fourth = asyncio.run(scenario())
    assert first["decision"] == "silent_new" and first["sent"]
    assert second["decision"] == "silent_known" and not second["sent"]
    assert third["decision"] == "resolve" and fourth["decision"] == "ok"
    assert [k for k, _ in sent] == [hb.KIND_ALARM, hb.KIND_RESOLVED]
    assert audits == ["alarm", "resolved"]
    assert not (tmp_path / hb.ALARM_FILE).exists()


def test_clean_stop_is_not_an_alarm(tmp_path):
    hb.mark_stopped(tmp_path)
    r = asyncio.run(hb.check_once(directory=tmp_path, now=time.time() + 10_000, window=5))
    assert r["decision"] == "stopped" and not (tmp_path / hb.ALARM_FILE).exists()


@pytest.mark.slow
def test_checker_script_without_heartbeat_exits_zero(tmp_path):
    out = subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "scripts" / "watchdog_check.py"),
                          "--dir", str(tmp_path), "--json"], cwd=str(ROOT), capture_output=True, text=True,
                         env={**__import__("os").environ, "SQLITE_PATH": str(tmp_path / "s.db"), "PYTHONUTF8": "1"})
    assert out.returncode == 0 and json.loads(out.stdout.strip().splitlines()[-1])["decision"] == "no_heartbeat"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.slow
def test_live_stand_kill_alarm_dedup_resolve(outside_tmp):
    work = outside_tmp / f"pytest_{int(time.time())}"
    r = subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "scripts" / "e2e_watchdog_alarm.py"),
                        "--port", str(_free_port()), "--work-dir", str(work)], cwd=str(ROOT), capture_output=True, text=True,
                       timeout=600)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-1000:]
