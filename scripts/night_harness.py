#!/usr/bin/env python3
"""Ночной CLI-харнес: verify (+optional repair) в цикле.

  python scripts/night_harness.py --project-id 50
  python scripts/night_harness.py --project-id 50 --allow-repair --interval 300 --max-cycles 20

Никогда не вызывает reset_step / audio / music.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


async def _once(project_id: int, allow_repair: bool, include_http: bool) -> int:
    from app.db import SessionLocal
    from app.models import Project
    from app.services.agent_harness import run_harness_verify

    async with SessionLocal() as session:
        p = await session.get(Project, project_id)
        if p is None:
            print(f"проект {project_id} не найден")
            return 2
        report = await run_harness_verify(
            session,
            p,
            allow_repair=allow_repair,
            include_http=include_http,
        )
        await session.commit()
        print(
            f"харнес #{project_id} ok={report.ok} статус={report.status} "
            f"далее={report.next_action} проверок={len(report.checks)}"
        )
        for c in report.checks:
            mark = "ОК" if c.ok else "СБОЙ"
            print(f"  {mark} {c.name}: {c.detail}")
        return 0 if report.ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Ночной автономный харнес")
    ap.add_argument("--project-id", type=int, default=50, help="id проекта")
    ap.add_argument(
        "--allow-repair",
        action="store_true",
        help="мягкий re-run упавших шагов (не audio/music)",
    )
    ap.add_argument(
        "--interval",
        type=int,
        default=0,
        help="секунд между циклами; 0 = один раз",
    )
    ap.add_argument("--max-cycles", type=int, default=1, help="макс. циклов")
    ap.add_argument(
        "--http",
        action="store_true",
        help="добавить HTTP-проверки Studio API/artifacts/assets/xlsx",
    )
    args = ap.parse_args()

    cycles = max(1, int(args.max_cycles))
    interval = max(0, int(args.interval))
    last = 1
    for i in range(cycles):
        print(f"=== цикл {i + 1}/{cycles} ===")
        last = asyncio.run(_once(args.project_id, args.allow_repair, args.http))
        if last == 0 and interval > 0:
            print("зелёный — стоп")
            break
        if i + 1 < cycles and interval > 0:
            time.sleep(interval)
    return last


if __name__ == "__main__":
    raise SystemExit(main())
