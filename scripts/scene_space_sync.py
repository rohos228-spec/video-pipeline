#!/usr/bin/env python3
"""Ingest live pipeline scenes into scenes_space / frames_space (overlay).

  python scripts/scene_space_sync.py --project 13
  python scripts/scene_space_sync.py --scene p13:s4

Does not insert Frame rows. After overlay:

  python scripts/scene_space_plan.py --scene p13:s4 --out tasks/out/p13-s4
  python scripts/scene_space_board.py --scene p13:s4 --out tasks/out/p13-s4/board.html
  python scripts/scene_space_validate.py --scene p13:s4
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


async def _run(project_id: int | None, scene_id: str | None) -> dict:
    from app.db import session_scope
    from app.models import Project
    from app.services.scene_space.pipeline import ingest_project, ingest_space_id

    async with session_scope() as session:
        if scene_id:
            report = await ingest_space_id(session, scene_id)
        else:
            assert project_id is not None
            project = await session.get(Project, project_id)
            if project is None:
                raise SystemExit(f"project {project_id} not found")
            report = await ingest_project(session, project)
        await session.commit()
        return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="scene_space overlay ingest from pipeline")
    parser.add_argument("--project", type=int, default=None)
    parser.add_argument("--scene", default=None, help="p{project}:s{scene_pk}")
    args = parser.parse_args(argv)
    if not args.project and not args.scene:
        parser.error("need --project ID or --scene pN:sM")
    report = asyncio.run(_run(args.project, args.scene))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
