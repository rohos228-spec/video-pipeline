"""Живая проверка защиты API от чужих страниц (шаг A04, L): настоящий процесс ``python -m app.main`` на тестовом порту.

    python scripts/verify_access_guard_live.py [--port 8799] [--work-dir <пустая папка вне репозитория>]

Процесс стартует с НОВОЙ пустой базой во временной папке (боевая ``data/state.db`` не открывается),
без Telegram и без ключей провайдеров. Затем по ``/api/openapi.json`` берутся все изменяющие маршруты
(POST/PUT/PATCH/DELETE под ``/api``) и каждому отправляется:
  1. запрос с чужим ``Origin`` - ждём 403 ``bad_origin``;
  2. запрос с чужим ``Host`` - ждём 403 ``bad_host``;
  3. запрос со своим Origin без токена оператора на маршрут из списка «нужен токен» - ждём 403 ``operator_token_required``.
Контроль «своё работает»: токен берётся у ``GET /api/auth/operator-token`` со своего origin, и с ним запрос
на маршрут слоя действий с пустым телом доходит до обработчика (не 403).
Никаких платных и внешних вызовов процесс не делает. Код выхода 0 - все проверки прошли.
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

FOREIGN_ORIGIN = "http://evil.example"
MUTATING = ("post", "put", "patch", "delete")


def _fill(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "1", path)


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


def deny_code(resp: httpx.Response) -> str | None:
    if resp.status_code != 403:
        return None
    try:
        detail = resp.json().get("detail")
        return detail.get("code") if isinstance(detail, dict) else None
    except ValueError:
        return None


def run(port: int, work: Path, extra_env: dict[str, str] | None = None) -> int:
    from app.services.access_guard import path_needs_operator_token

    env = {k: v for k, v in os.environ.items() if not re.search(r"(?i)(key|token|secret|password)", k)}
    env.update({
        "WEB_HOST": "127.0.0.1", "WEB_PORT": str(port), "TELEGRAM_ENABLED": "false",
        "SQLITE_PATH": str(work / "state.db"), "PYTHONUTF8": "1",
        "MEDIA_SPEND_RECORDING": "true",
    })
    env.update(extra_env or {})
    log = open(work / "server.log", "wb")
    proc = subprocess.Popen([sys.executable, "-u", "-m", "app.main"], cwd=str(ROOT), env=env,
                            stdout=log, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    own_origin = base
    report: dict = {"port": port, "checks": []}

    def check(name: str, ok: bool, detail=None) -> None:
        report["checks"].append({"name": name, "ok": bool(ok), "detail": detail})
        print(("PASS " if ok else "FAIL ") + name + (f"  {detail}" if detail and not ok else ""))

    try:
        if not wait_ready(base, proc):
            check("server_started", False, f"см. {work / 'server.log'}")
            return 1
        check("server_started", True)
        spec = httpx.get(f"{base}/api/openapi.json", timeout=20).json()
        routes = [(p, m.upper()) for p, ops in spec.get("paths", {}).items()
                  if p.startswith("/api") for m in ops if m in MUTATING]
        check("mutating_routes_found", len(routes) > 50, len(routes))
        bad_origin_miss, bad_host_miss, token_miss, token_routes = [], [], [], 0
        with httpx.Client(timeout=15) as c:
            for path, method in routes:
                url = base + _fill(path)
                r = c.request(method, url, headers={"Origin": FOREIGN_ORIGIN})
                if deny_code(r) != "bad_origin":
                    bad_origin_miss.append((method, path, r.status_code))
                r = c.request(method, url, headers={"Host": "evil.example"})
                if deny_code(r) != "bad_host":
                    bad_host_miss.append((method, path, r.status_code))
                if path_needs_operator_token(_fill(path), method):
                    token_routes += 1
                    r = c.request(method, url, headers={"Origin": own_origin})
                    if deny_code(r) != "operator_token_required":
                        token_miss.append((method, path, r.status_code))
            check("foreign_origin_denied_on_every_mutating_route", not bad_origin_miss, bad_origin_miss[:5])
            check("foreign_host_denied_on_every_mutating_route", not bad_host_miss, bad_host_miss[:5])
            check("token_required_on_every_dangerous_route", not token_miss and token_routes > 0,
                  {"routes": token_routes, "missed": token_miss[:5]})
            tok = c.get(f"{base}/api/auth/operator-token", headers={"Origin": own_origin})
            token = (tok.json() or {}).get("token") if tok.status_code == 200 else None
            check("own_origin_gets_operator_token", bool(token), tok.status_code)
            foreign = c.get(f"{base}/api/auth/operator-token", headers={"Origin": FOREIGN_ORIGIN})
            check("foreign_origin_gets_no_token", not (foreign.status_code == 200 and "token" in (foreign.json() or {})),
                  foreign.status_code)
            ok = c.post(f"{base}/api/action-layer/plans", headers={"Origin": own_origin, "X-VP-Operator": token or ""},
                        content=b"{}")
            check("own_request_with_token_reaches_handler", ok.status_code != 403, ok.status_code)
        report["routes_checked"] = len(routes)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()
    failed = [c["name"] for c in report["checks"] if not c["ok"]]
    (work / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"маршрутов проверено: {report.get('routes_checked', 0)}; не пройдено: {len(failed)}")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8799)
    ap.add_argument("--work-dir")
    ap.add_argument("--set-env", action="append", default=[], metavar="K=V",
                    help="контроль «защита выключена»: например VP_OPERATOR_TOKEN_ENFORCE=0 обязан дать FAIL")
    args = ap.parse_args(argv)
    work = Path(args.work_dir) if args.work_dir else Path(tempfile.mkdtemp(prefix="vp_guard_live_"))
    work.mkdir(parents=True, exist_ok=True)
    if ROOT in work.resolve().parents or work.resolve() == ROOT:
        print("work-dir должен быть вне репозитория", file=sys.stderr)
        return 2
    extra = dict(kv.split("=", 1) for kv in args.set_env)
    return run(args.port, work, extra)


if __name__ == "__main__":
    sys.exit(main())
