"""CLI: top-down plan SVG (and PNG) per frame.

  python scripts/scene_space_plan.py --scene SCENE_ID --out DIR
  python scripts/scene_space_plan.py --scene SCENE_ID --out DIR --from-json PATH
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import sys
from contextlib import suppress
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.scene_space.render_plan import load_json_dump, write_plans  # noqa: E402

STORE_MISSING = (
    "ERROR: cannot import app.services.scene_space.store (stream 1). "
    "Pass --from-json PATH with a scene dump to run without DB."
)


def _die(msg: str, code: int = 2) -> int:
    sys.stderr.write(msg.rstrip() + "\n")
    return code


async def _load_store(scene_id: str) -> tuple[dict, list[dict]]:
    try:
        store = importlib.import_module("app.services.scene_space.store")
        get_scene_space = store.get_scene_space
        list_frame_spaces = store.list_frame_spaces
    except ImportError as exc:
        raise ImportError(STORE_MISSING + f" Detail: {exc}") from exc

    from app.db import session_scope

    async with session_scope() as session:
        space = await get_scene_space(session, scene_id)
        if space is None:
            raise LookupError(f"ERROR: scene {scene_id!r} not found in scenes_space")
        frames = await list_frame_spaces(session, scene_id)
        try:
            state_at = store.state_at
        except AttributeError:
            state_at = None
        if state_at is not None:
            for row in frames:
                with suppress(Exception):
                    row["_state_plan"] = await state_at(session, scene_id, int(row["shot_order"]))
        try:
            from sqlalchemy import select

            from app.models import Frame

            uuids = [r.get("uuid") for r in frames if r.get("uuid")]
            if uuids:
                result = await session.execute(select(Frame.uuid, Frame.attrs).where(Frame.uuid.in_(uuids)))
                accents = {
                    uuid: str(attrs.get("accent"))
                    for uuid, attrs in result.all()
                    if isinstance(attrs, dict) and attrs.get("accent")
                }
                for row in frames:
                    if not row.get("accent") and row.get("uuid") in accents:
                        row["accent"] = accents[row["uuid"]]
        except Exception:
            pass
        return space, frames


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render top-down scene plan SVG per frame.")
    parser.add_argument("--scene", required=True, help="scene_id (e.g. fix:dialogue)")
    parser.add_argument("--out", required=True, help="output directory")
    parser.add_argument("--from-json", dest="from_json", default=None, help="scene dump JSON (no DB)")
    parser.add_argument("--no-png", action="store_true", help="skip Pillow PNG")
    args = parser.parse_args(argv)

    if args.from_json:
        dump_id, space, frames, _violations = load_json_dump(Path(args.from_json))
        scene_id = args.scene or dump_id
        if not frames:
            return _die(f"ERROR: dump has no frames ({args.from_json})")
    else:
        try:
            space, frames = asyncio.run(_load_store(args.scene))
        except ImportError as exc:
            return _die(str(exc))
        except LookupError as exc:
            return _die(str(exc))
        scene_id = args.scene
        if not frames:
            return _die(f"ERROR: no frames_space rows for scene {scene_id!r}")

    out_dir = Path(args.out)
    try:
        written = write_plans(out_dir, space, frames, png=not args.no_png)
    except ValueError as exc:
        return _die(f"ERROR: {exc}")

    sys.stdout.write(
        json.dumps(
            {
                "scene_id": scene_id,
                "out": str(out_dir),
                "svg": [p.name for p in written],
                "count": len(written),
            },
            ensure_ascii=False,
        )
        + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
