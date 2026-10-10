"""Пересчёт корзин ошибок живого прогона планировщика из сохранённого отчёта (офлайн, без сети и без модели).

    python scripts/recompute_eval_buckets.py _evalout/heldout7_run1.json [--fixture tests/fixtures/action_planner_heldout7.json]

Читает отчёт ``eval_action_planner.py --out`` и печатает одну строку: число запросов, точность (как в отчёте),
транспортные сбои и корзины ``guard_confirmations`` (ответ - вопрос защиты), ``unsafe_errors`` (исполнимый план
не тот / отказной случай дал план / платный шаг вместо вопроса) и ``conservative_errors`` (вопрос или пусто
там, где ждали план). Сам счёт ``accuracy`` не меняется. Фикстура нужна только для пометки случаев, правленных
после живого прогона (``post_live_edit``); без ``--fixture`` она берётся по названию набора в строках отчёта.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _eval_module():
    sys.path.insert(0, str(ROOT))
    spec = importlib.util.spec_from_file_location("eval_action_planner", ROOT / "scripts" / "eval_action_planner.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def recompute(report: dict, fixture: dict | None = None) -> dict:
    """Корзины по строкам отчёта; ``fixture`` - необязательная пометка правленных после прогона случаев."""
    ev = _eval_module()
    rows = report["rows"]
    out = {"requests": len(rows), "ok": sum(1 for r in rows if r.get("ok")),
           "accuracy": round(sum(1 for r in rows if r.get("ok")) / max(1, len(rows)), 3),
           "transport_failures": sum(1 for r in rows if r.get("transport_error") and not r.get("ok"))}
    out.update(ev.failure_buckets(rows))
    if fixture is not None:
        edited = {c["id"] for c in fixture.get("cases", []) if "post_live_edit" in c}
        out["edited_after_run_among_failures"] = sorted(
            r["id"] for r in rows if r["id"] in edited and not r.get("ok"))
    return out


def format_line(name: str, res: dict) -> str:
    return (f"{name}: requests={res['requests']} ok={res['ok']} accuracy={res['accuracy']} "
            f"transport_failures={res['transport_failures']} guard_confirmations={res['guard_confirmations']} "
            f"unsafe_errors={res['unsafe_errors']} conservative_errors={res['conservative_errors']}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("report", help="JSON, записанный eval_action_planner.py --out")
    parser.add_argument("--fixture", default="", help="фикстура набора (по умолчанию по названию набора в отчёте)")
    parser.add_argument("--ids", action="store_true", help="печатать ещё id по корзинам")
    args = parser.parse_args()
    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    name = next((r.get("set") for r in report["rows"] if r.get("set")), Path(args.report).stem)
    fixture_path = Path(args.fixture) if args.fixture else ROOT / "tests" / "fixtures" / f"action_planner_{name}.json"
    fixture = json.loads(fixture_path.read_text(encoding="utf-8")) if fixture_path.exists() else None
    res = recompute(report, fixture)
    sys.stdout.reconfigure(encoding="utf-8")
    print(format_line(name, res))
    if args.ids:
        print("  unsafe:", ", ".join(res["unsafe_error_ids"]) or "-")
        print("  conservative:", ", ".join(res["conservative_error_ids"]) or "-")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
