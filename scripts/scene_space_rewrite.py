#!/usr/bin/env python3
"""Rewrite scene meaning and rebuild bits/shots.

  python scripts/scene_space_rewrite.py --scene fix:dialogue --meaning "A wins then loses the folder"

Uses an isolated sqlite file (default tasks/out/scene_space.db), never data/state.db.
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


async def _run(scene_id: str, meaning: str, db: Path) -> dict:
    from app.services.scene_space.rewrite import isolated_session, rewrite, seed_fixture, load_fixture

    async with isolated_session(db) as session:
        if scene_id.startswith("fix:"):
            await seed_fixture(session, load_fixture(scene_id))
        result = await rewrite(session, scene_id, meaning)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="scene_space rewrite(meaning)")
    parser.add_argument("--scene", required=True)
    parser.add_argument("--meaning", required=True)
    parser.add_argument(
        "--db",
        default="",
        help="isolated sqlite path (default: tasks/out/scene_space.db)",
    )
    args = parser.parse_args(argv)
    db = Path(args.db) if args.db else ROOT / "tasks" / "out" / "scene_space.db"
    result = asyncio.run(_run(args.scene, args.meaning, db))
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
