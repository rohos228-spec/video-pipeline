"""Живая проверка журнала действий оператора (шаги B01/B05, L): настоящий сервер, около 20 разных кнопок Studio.

    python scripts/verify_operator_audit_live.py [--port 8796] [--work-dir <пустая папка вне репозитория>]

Сервер ``python -m app.main`` стартует с новой пустой базой, без Telegram и ключей. Через HTTP со своим Origin и
токеном оператора вызываются безопасные изменяющие ручки (создание, правка, пауза, сброс шага, настройки нод,
монтажная доска и т.п.; запуски шагов, публикации и всё, что открывает браузер или тратит деньги, в список не входят).
Потом читается лента проекта ``GET /api/projects/{id}/timeline`` и проверяется: каждое обращение оставило строку
оператора с русской фразой, фразы разные, экспорт ленты читается и не содержит секретов.
Код выхода 0 - все проверки прошли.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# (метод, путь с {p} = id проекта, тело или None). Все ручки не запускают шаги и не платят.
CALLS: list[tuple[str, str, dict | None]] = [
    ("PATCH", "/api/projects/{p}", {"title": "audit live check 2"}),
    ("POST", "/api/projects/{p}/pause", None),
    ("POST", "/api/projects/{p}/steps/script/reset", None),
    ("DELETE", "/api/projects/{p}/excel-hero", None),
    ("PATCH", "/api/projects/{p}/excel-gpt/n_excel_gpt_1", {}),
    ("PATCH", "/api/projects/{p}/gpt-operator/n_gpt_1", {}),
    ("DELETE", "/api/projects/{p}/gpt-operator/n_gpt_1/check-agent", None),
    ("PATCH", "/api/projects/{p}/storage/n_storage_1", {}),
    ("DELETE", "/api/projects/{p}/storage/n_storage_1/files", None),
    ("POST", "/api/projects/{p}/montage-board/queue", {"ops": []}),
    ("POST", "/api/projects/{p}/montage-board/coverage", {}),
    ("POST", "/api/projects/{p}/montage-board/swap-shots", None),
    ("POST", "/api/projects/{p}/montage-board/delete-image", None),
    ("POST", "/api/projects/{p}/montage-board/delete-video", None),
    ("POST", "/api/projects/{p}/montage-board/delete-ref", None),
    ("POST", "/api/projects/{p}/montage-board/link-ref", None),
    ("POST", "/api/projects/{p}/restore-original-voiceover", None),
    ("POST", "/api/projects/{p}/harness/verify", None),
    ("POST", "/api/projects/{p}/child", None),
    ("POST", "/api/projects/{p}/ensure-run", None),
    ("POST", "/api/projects/{p}/xlsx/reload", None),
    ("DELETE", "/api/projects/{p}", None),
]


def wait_ready(base: str, proc: subprocess.Popen, seconds: float = 120.0) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        if proc.poll() is not None:
            return False
        try:
            if httpx.get(f"{base}/api/health", timeout=3).status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(1.0)
    return False


def run(port: int, work: Path) -> int:
    env = {k: v for k, v in os.environ.items() if not re.search(r"(?i)(key|token|secret|password)", k)}
    env.update({"WEB_HOST": "127.0.0.1", "WEB_PORT": str(port), "TELEGRAM_ENABLED": "false",
                "SQLITE_PATH": str(work / "state.db"), "PYTHONUTF8": "1"})
    log = open(work / "server.log", "wb")
    proc = subprocess.Popen([sys.executable, "-u", "-m", "app.main"], cwd=str(ROOT), env=env,
                            stdout=log, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    report: dict = {"checks": [], "calls": []}

    def check(name: str, ok: bool, detail=None) -> None:
        report["checks"].append({"name": name, "ok": bool(ok), "detail": detail})
        print(("PASS " if ok else "FAIL ") + name + (f"  {detail}" if detail is not None and not ok else ""))

    try:
        if not wait_ready(base, proc):
            check("server_started", False, f"см. {work / 'server.log'}")
            return 1
        check("server_started", True)
        with httpx.Client(timeout=30) as c:
            tok = c.get(f"{base}/api/auth/operator-token", headers={"Origin": base}).json()["token"]
            h = {"Origin": base, "X-VP-Operator": tok}
            made = c.post(f"{base}/api/projects", headers=h, json={"title": "audit live check", "topic": "проверка журнала"})
            check("project_created", made.status_code in (200, 201), made.text[:200])
            pid = (made.json() or {}).get("id") if made.status_code in (200, 201) else None
            if pid is None:
                return 1
            time.sleep(1.0)
            for method, tpl, body in CALLS:
                path = tpl.replace("{p}", str(pid))
                if method == "DELETE" and tpl == "/api/projects/{p}":
                    # удаление проекта - последним; лента читается ДО него по уже накопленным строкам
                    snap = c.get(f"{base}/api/projects/{pid}/timeline", params={"limit": 500}, headers=h)
                    report["snapshot_before_delete"] = snap.status_code
                kwargs = {"json": body} if body is not None else {}
                r = c.request(method, base + path, headers=h, **kwargs)
                report["calls"].append({"method": method, "path": tpl, "status": r.status_code})
                time.sleep(0.15)
            time.sleep(2.0)
            tl = c.get(f"{base}/api/projects/{pid}/timeline", params={"limit": 500}, headers=h)
            ok_tl = tl.status_code == 200
            check("timeline_readable", ok_tl, tl.status_code)
            items = []
            if ok_tl:
                data = tl.json()
                items = data.get("items") or data.get("events") or data.get("rows") or []
                report["timeline_keys"] = sorted(data.keys()) if isinstance(data, dict) else None
            report["timeline_rows"] = len(items)
            if items:
                report["sample_row"] = items[0]
            ops = [i for i in items if "operator" in json.dumps(i, ensure_ascii=False).lower()
                   or i.get("source") in ("operator_audit", "operator")]
            report["operator_rows"] = len(ops)
            check("operator_rows_present_for_each_call", len(ops) >= len(CALLS) - 1, {"rows": len(ops), "calls": len(CALLS)})
            phrases = {str(i.get("title_ru") or "") for i in ops}
            report["distinct_phrases"] = len(phrases)
            check("phrases_are_russian_and_varied", len(phrases) >= 15 and all(re.search(r"[А-Яа-яЁё]", p) for p in phrases if p),
                  sorted(phrases)[:5])
            exp = c.get(f"{base}/api/projects/{pid}/timeline/export", params={"format": "json"}, headers=h)
            check("export_readable", exp.status_code == 200, exp.status_code)
            from scripts.scan_secrets import scan_file  # noqa: PLC0415
            (work / "export.json").write_bytes(exp.content)
            check("export_has_no_secrets", not scan_file(work / "export.json"), None)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()
    failed = [x["name"] for x in report["checks"] if not x["ok"]]
    (work / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"вызовов: {len(report['calls'])}; строк оператора в ленте: {report.get('operator_rows')}; не пройдено: {len(failed)}")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8796)
    ap.add_argument("--work-dir")
    args = ap.parse_args(argv)
    work = Path(args.work_dir) if args.work_dir else Path(tempfile.mkdtemp(prefix="vp_audit_live_"))
    work.mkdir(parents=True, exist_ok=True)
    if ROOT in work.resolve().parents or work.resolve() == ROOT:
        print("work-dir должен быть вне репозитория", file=sys.stderr)
        return 2
    return run(args.port, work)


if __name__ == "__main__":
    sys.exit(main())
