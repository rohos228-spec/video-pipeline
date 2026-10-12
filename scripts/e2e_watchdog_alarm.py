r"""Живой стенд A18: убитый процесс цикла надзора даёт тревогу сторожа.

    python scripts/e2e_watchdog_alarm.py [--port 8798] [--work-dir <пустая папка вне репозитория>] [--window 5]

Настоящий ``python -m app.main`` на loopback с НОВОЙ базой в рабочей папке (вне репозитория), без Telegram и
ключей. Шаги: пульс растёт -> проверка ``scripts/watchdog_check.py`` молчит -> процесс убивается (os-уровень) ->
через окно автономная проверка поднимает тревогу ОДИН раз (строка notification_log ok=0, канал telegram_off, строка
operator_audit) -> вторая проверка не дублирует -> процесс запущен заново -> проверка снимает тревогу.
Реальную доставку в Telegram стенд не проверяет. Код выхода 0 - всё пройдено.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _env(port: int, work: Path, window: float) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not re.search(r"(?i)(key|token|secret|password)", k)}
    env.update({"WEB_HOST": "127.0.0.1", "WEB_PORT": str(port), "TELEGRAM_ENABLED": "false",
                "SQLITE_PATH": str(work / "state.db"), "PYTHONUTF8": "1",
                "VP_HEARTBEAT_INTERVAL_S": "1", "VP_WATCHDOG_HEARTBEAT_WINDOW_S": str(window),
                "VP_WATCHDOG_CHECK_INTERVAL_S": "3600"})  # в стенде тревогу даёт только автономный скрипт
    return env


def _start(env: dict, work: Path, tag: str) -> tuple[subprocess.Popen, object]:
    log = open(work / f"server_{tag}.log", "wb")
    return subprocess.Popen([sys.executable, "-u", "-m", "app.main"], cwd=str(ROOT), env=env,
                            stdout=log, stderr=subprocess.STDOUT), log


def _ready(port: int, proc: subprocess.Popen, seconds: float = 120.0) -> bool:
    end = time.time() + seconds
    while time.time() < end and proc.poll() is None:
        try:
            if httpx.get(f"http://127.0.0.1:{port}/api/health", timeout=3).status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(1.0)
    return False


def _hb(work: Path) -> dict | None:
    try:
        return json.loads((work / "observer_heartbeat.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _check(env: dict) -> tuple[int, dict]:
    r = subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "scripts" / "watchdog_check.py"), "--json"],
                       cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=120)
    line = [x for x in r.stdout.splitlines() if x.startswith("{")][-1:] or ["{}"]
    return r.returncode, json.loads(line[0])


def _rows(work: Path, table: str, where: str) -> list[dict]:
    con = sqlite3.connect(str(work / "state.db"))
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(f"SELECT * FROM {table} WHERE {where}")]
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()


def run(port: int, work: Path, window: float) -> int:
    env = _env(port, work, window)
    report: list[dict] = []

    def check(name: str, ok: bool, detail=None) -> None:
        report.append({"name": name, "ok": bool(ok), "detail": detail})
        print(("PASS " if ok else "FAIL ") + name + (f"  {detail}" if detail is not None and not ok else ""))

    proc, log = _start(env, work, "1")
    procs = [(proc, log)]
    try:
        if not _ready(port, proc):
            check("server_started", False, f"см. {work / 'server_1.log'}")
            return 1
        check("server_started", True)
        a = _hb(work)
        time.sleep(3.5)
        b = _hb(work)
        check("heartbeat_advances", bool(a and b and b["ts"] > a["ts"]), (a, b))
        code, res = _check(env)
        check("no_alarm_while_alive", code == 0 and res.get("decision") == "ok", res)

        proc.kill()  # os-уровень: без чистой остановки
        proc.wait(timeout=20)
        killed_at = time.time()
        time.sleep(window + 2)
        code, res = _check(env)
        check("alarm_raised_after_window", code == 2 and res.get("decision") == "silent_new" and res.get("sent"), res)
        check("alarm_within_window_budget", time.time() - killed_at < window + 30, round(time.time() - killed_at, 1))
        nl = _rows(work, "notification_log", "kind='observer_silent'")
        check("notification_log_row_once", len(nl) == 1 and nl[0]["ok"] == 0 and nl[0]["channel"] == "telegram_off"
              and "Telegram отключён" in (nl[0]["error"] or ""), nl)
        au = _rows(work, "operator_audit", "method='WATCHDOG' AND outcome='alarm'")
        check("audit_row_once", len(au) == 1 and au[0]["actor"] == "watchdog", au)

        code, res = _check(env)
        check("second_check_deduplicated", code == 2 and res.get("decision") == "silent_known" and not res.get("sent"), res)
        check("no_duplicate_rows", len(_rows(work, "notification_log", "kind='observer_silent'")) == 1
              and len(_rows(work, "operator_audit", "method='WATCHDOG' AND outcome='alarm'")) == 1)

        proc2, log2 = _start(env, work, "2")
        procs.append((proc2, log2))
        if not _ready(port, proc2):
            check("server_restarted", False, f"см. {work / 'server_2.log'}")
            return 1
        time.sleep(2.5)
        code, res = _check(env)  # тревогу мог снять уже первый проход встроенной проверки нового процесса
        check("resolved_after_restart", code == 0 and res.get("decision") in {"resolve", "ok"}, res)
        rs = _rows(work, "notification_log", "kind='observer_silent_resolved'")
        check("resolved_row_written_once", len(rs) == 1 and rs[0]["ok"] == 0, rs)
        check("alarm_flag_cleared", not (work / "observer_alarm.json").exists())
        code, res = _check(env)
        check("quiet_after_resolve", code == 0 and res.get("decision") == "ok", res)

        proc2.terminate()  # на Windows это тоже жёсткая остановка; чистую остановку проверяют юнит-тесты
        proc2.wait(timeout=30)
    finally:
        for p, lg in procs:
            if p.poll() is None:
                p.kill()
            lg.close()
    failed = [c["name"] for c in report if not c["ok"]]
    (work / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"проверок: {len(report)}; не пройдено: {len(failed)}")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8798)
    ap.add_argument("--work-dir", default=r"<пустая папка вне репозитория>")
    ap.add_argument("--window", type=float, default=5.0)
    args = ap.parse_args(argv)
    work = Path(args.work_dir)
    if ROOT in work.resolve().parents or work.resolve() == ROOT:
        print("work-dir должен быть вне репозитория", file=sys.stderr)
        return 2
    if work.exists() and any(work.iterdir()):
        print("work-dir должен быть пустым (нужна новая база)", file=sys.stderr)
        return 2
    work.mkdir(parents=True, exist_ok=True)
    return run(args.port, work, args.window)


if __name__ == "__main__":
    sys.exit(main())
