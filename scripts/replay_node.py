#!/usr/bin/env python3
"""Перепроверка прогона ноды без GPT: тот же ответ GPT → программа ещё раз.

Usage:
  python scripts/replay_node.py data/videos/<проект>/node_trace/<нода>/<время>
  python scripts/replay_node.py data/videos/<проект> --node shots
  python scripts/replay_node.py <папка прогона> --call call_02 --show
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.node_replay import replay_run, resolve_run_dir  # noqa: E402


def _entry_line(e: dict) -> str:
    where = " ".join(
        x
        for x in (
            str(e.get("frame_uuid") or "")[:8],
            f"кадр {e['shot']}" if e.get("shot") else "",
            f"«{e['field']}»" if e.get("field") else "",
        )
        if x
    )
    change = ""
    if e.get("before") is not None or e.get("after") is not None:
        change = f": {e.get('before') or '—'} → {e.get('after') or '—'}"
    note = f" ({e['note']})" if e.get("note") else ""
    return f"    [{e.get('kind')}] {e.get('rule')} {where}{change}{note}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("path", help="папка прогона или папка проекта")
    parser.add_argument("--node", default="", help="нода (shots, action, script…), если дана папка проекта")
    parser.add_argument("--call", default="", help="только этот вызов, напр. call_02")
    parser.add_argument("--show", action="store_true", help="показать дневник программы")
    args = parser.parse_args()

    run_dir = resolve_run_dir(args.path, node=args.node)
    print(f"Прогон: {run_dir}")
    reps = replay_run(run_dir, call=args.call)
    if not reps:
        print("Вызовов GPT в прогоне нет.")
        return 1
    bad = 0
    for rep in reps:
        if rep.same:
            status = "совпадает"
        elif rep.stopped and not rep.was_stopped:
            status = f"СЕЙЧАС ОСТАНОВИЛОСЬ: {rep.stopped}"
        elif rep.was_stopped and not rep.stopped:
            status = "тогда остановилось, сейчас проходит"
        else:
            status = f"отличается: {len(rep.diff)} изм."
        bad += 0 if rep.same else 1
        print(f"{rep.call}: {status}")
        for c in rep.diff[:50]:
            shot = f" кадр {c.shot}" if c.shot else ""
            print(f"    {c.frame_uuid[:8]}{shot} «{c.field}»: было {c.before!r} → сейчас {c.after!r}")
        if args.show:
            for e in rep.entries:
                print(_entry_line(e))
    print("Итог: всё совпадает." if not bad else f"Итог: отличий в {bad} вызов(ах).")
    return 0 if not bad else 2


if __name__ == "__main__":
    raise SystemExit(main())
