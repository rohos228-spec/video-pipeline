#!/usr/bin/env python3
"""Rebuild shot breakdown from current scene meaning.

  python scripts/scene_space_rebuild.py --scene fix:dialogue
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


async def _run(scene_id: str, db: Path) -> dict:
    from app.services.scene_space.rewrite import isolated_session, load_fixture, rebuild, seed_fixture

    async with isolated_session(db) as session:
        if scene_id.startswith("fix:"):
            await seed_fixture(session, load_fixture(scene_id))
        result = await rebuild(session, scene_id)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="scene_space rebuild shots")
    parser.add_argument("--scene", required=True)
    parser.add_argument("--db", default="")
    args = parser.parse_args(argv)
    db = Path(args.db) if args.db else ROOT / "tasks" / "out" / "scene_space.db"
    result = asyncio.run(_run(args.scene, db))
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
