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


async def _once(project_id: int, allow_repair: bool) -> int:
    from app.db import SessionLocal
    from app.models import Project
    from app.services.agent_harness import run_harness_verify

    async with SessionLocal() as session:
        p = await session.get(Project, project_id)
        if p is None:
            print(f"project {project_id} not found")
            return 2
        report = await run_harness_verify(session, p, allow_repair=allow_repair)
        await session.commit()
        print(
            f"harness #{project_id} ok={report.ok} status={report.status} "
            f"next={report.next_action} checks={len(report.checks)}"
        )
        for c in report.checks:
            mark = "OK" if c.ok else "FAIL"
            print(f"  {mark} {c.name}: {c.detail}")
        return 0 if report.ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Night autonomous harness")
    ap.add_argument("--project-id", type=int, default=50)
    ap.add_argument("--allow-repair", action="store_true")
    ap.add_argument("--interval", type=int, default=0, help="seconds between cycles; 0=once")
    ap.add_argument("--max-cycles", type=int, default=1)
    args = ap.parse_args()

    cycles = max(1, int(args.max_cycles))
    interval = max(0, int(args.interval))
    last = 1
    for i in range(cycles):
        print(f"=== cycle {i + 1}/{cycles} ===")
        last = asyncio.run(_once(args.project_id, args.allow_repair))
        if last == 0 and interval > 0:
            print("green — stop loop")
            break
        if i + 1 < cycles and interval > 0:
            time.sleep(interval)
    return last


if __name__ == "__main__":
    raise SystemExit(main())
