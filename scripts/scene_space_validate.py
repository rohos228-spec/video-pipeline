#!/usr/bin/env python3
"""Validate cinematic scene space (CANON §6).

  python scripts/scene_space_validate.py --fixtures
  python scripts/scene_space_validate.py --scene SCENE_ID

Writes tasks/VALIDATION.md. Exit 2 on any error, 1 if only warnings, 0 clean.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.scene_space.validate import (  # noqa: E402
    format_validation_md,
    issues_exit_code,
    load_fixture,
    validate_scene,
)

FIXTURE_DIR = ROOT / "tests" / "fixtures" / "scene_space"
REPORT_PATH = ROOT / "tasks" / "VALIDATION.md"


async def _load_from_store(scene_id: str):
    try:
        from app.services.scene_space.store import get_scene_space, list_frame_spaces
        from sqlalchemy import select

        from app.db import session_scope
        from app.models import Frame
    except ImportError as exc:
        raise RuntimeError(
            f"store missing: cannot load --scene {scene_id!r} ({exc})"
        ) from exc

    async with session_scope() as session:
        space = await get_scene_space(session, scene_id)
        if space is None:
            raise RuntimeError(f"scene {scene_id!r} not in scenes_space")
        if isinstance(space, dict) and "plan" not in space and "space_json" in space:
            inner = space["space_json"]
            space = inner if isinstance(inner, dict) else space
        rows = await list_frame_spaces(session, scene_id)
        uuids = [str(r.get("uuid") or "") for r in rows if r.get("uuid")]
        frames: dict = {}
        if uuids:
            result = await session.execute(select(Frame).where(Frame.uuid.in_(uuids)))
            frames = {f.uuid: f for f in result.scalars().all() if f.uuid}
        return space, rows, frames


def _collect_fixture_results() -> list[tuple[str, list]]:
    results: list[tuple[str, list]] = []
    if not FIXTURE_DIR.is_dir():
        return results
    for path in sorted(FIXTURE_DIR.glob("*.json")):
        scene_id, space, rows, frames = load_fixture(path)
        issues = validate_scene(scene_id, rows, space, frames)
        results.append((scene_id, issues))
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate scene_space (CANON §6)")
    parser.add_argument(
        "--fixtures",
        action="store_true",
        help="validate tests/fixtures/scene_space/*.json",
    )
    parser.add_argument("--scene", type=str, default=None, help="scene_id via store")
    parser.add_argument(
        "--out",
        type=str,
        default=str(REPORT_PATH),
        help="report path (default tasks/VALIDATION.md)",
    )
    args = parser.parse_args(argv)
    if not args.fixtures and not args.scene:
        parser.error("need --fixtures and/or --scene")

    results: list[tuple[str, list]] = []
    notes: list[str] = []
    if args.fixtures:
        found = _collect_fixture_results()
        if not found:
            notes.append("No fixture files in tests/fixtures/scene_space/*.json")
        results.extend(found)
    if args.scene:
        try:
            space, rows, frames = asyncio.run(_load_from_store(args.scene))
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_text(
                format_validation_md(
                    [(args.scene, [])],
                    note=f"error: {exc}",
                ),
                encoding="utf-8",
            )
            return 2
        results.append(
            (args.scene, validate_scene(args.scene, rows, space, frames))
        )

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    note = "\n".join(notes) if notes else None
    out.write_text(format_validation_md(results, note=note), encoding="utf-8")
    all_issues = [i for _, issues in results for i in issues]
    code = issues_exit_code(all_issues)
    print(f"wrote {out} exit={code}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
