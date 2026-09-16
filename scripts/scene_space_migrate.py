#!/usr/bin/env python3
"""Migrate cinematic scene space tables. --up / --down.

Uses app.db.engine when it already points at settings.sqlite_path; otherwise
opens that sqlite file. --down drops only frames_space and scenes_space.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migrate scenes_space / frames_space")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--up", action="store_true", help="create or alter space tables")
    group.add_argument("--down", action="store_true", help="drop space tables only")
    return parser.parse_args(argv)


def _paths_match(engine_url: str, sqlite_path: Path) -> bool:
    url = engine_url.replace("\\", "/").lower()
    posix = sqlite_path.as_posix().lower()
    try:
        resolved = sqlite_path.resolve().as_posix().lower()
    except OSError:
        resolved = posix
    return posix in url or resolved in url


async def _open_engine():
    from pathlib import Path as _Path

    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    from app.settings import settings

    target = _Path(settings.sqlite_path)
    if not target.is_absolute():
        target = _Path.cwd() / target
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        from app.db import engine as app_engine

        if _paths_match(str(app_engine.url), target):
            return app_engine, False
    except Exception:
        pass
    url = f"sqlite+aiosqlite:///{target.as_posix()}"
    return create_async_engine(url, future=True, poolclass=NullPool), True


async def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    from app.services.scene_space.migrate import (
        downgrade_scene_space_schema,
        migrate_scene_space_schema,
    )

    engine, dispose = await _open_engine()
    try:
        async with engine.begin() as conn:
            if args.up:
                await migrate_scene_space_schema(conn)
            else:
                await downgrade_scene_space_schema(conn)
    finally:
        if dispose:
            await engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
