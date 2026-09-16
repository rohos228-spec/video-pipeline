"""CLI: montage HTML board for a scene.

  python scripts/scene_space_board.py --scene SCENE_ID --out FILE.html
  python scripts/scene_space_board.py --scene SCENE_ID --out FILE.html --from-json PATH
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

from app.services.scene_space.render_board import (  # noqa: E402
    normalize_violations,
    try_call_validate,
    write_board,
)
from app.services.scene_space.render_plan import load_json_dump  # noqa: E402

STORE_MISSING = (
    "ERROR: cannot import app.services.scene_space.store (stream 1). "
    "Pass --from-json PATH with a scene dump to run without DB."
)
VALIDATE_MISSING = (
    "ERROR: cannot import app.services.scene_space.validate (stream 4). "
    "Pass --from-json PATH with optional 'violations' to run without the validator."
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
        state_at = getattr(store, "state_at", None)
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


def _missing_live_deps() -> str | None:
    msgs: list[str] = []
    try:
        importlib.import_module("app.services.scene_space.store")
    except ImportError as exc:
        msgs.append(STORE_MISSING + f" Detail: {exc}")
    try:
        importlib.import_module("app.services.scene_space.validate")
    except ImportError as exc:
        msgs.append(VALIDATE_MISSING + f" Detail: {exc}")
    if not msgs:
        return None
    return "\n".join(msgs)


def _load_validate():
    try:
        return importlib.import_module("app.services.scene_space.validate")
    except ImportError as exc:
        raise ImportError(VALIDATE_MISSING + f" Detail: {exc}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render montage HTML board for a scene.")
    parser.add_argument("--scene", required=True, help="scene_id (e.g. fix:dialogue)")
    parser.add_argument("--out", required=True, help="output HTML file")
    parser.add_argument("--from-json", dest="from_json", default=None, help="scene dump JSON (no DB)")
    args = parser.parse_args(argv)

    dump_violations: list = []
    if args.from_json:
        dump_id, space, frames, dump_violations = load_json_dump(Path(args.from_json))
        scene_id = args.scene or dump_id
        if not frames:
            return _die(f"ERROR: dump has no frames ({args.from_json})")
    else:
        missing = _missing_live_deps()
        if missing:
            return _die(missing)
        try:
            space, frames = asyncio.run(_load_store(args.scene))
        except ImportError as exc:
            return _die(str(exc))
        except LookupError as exc:
            return _die(str(exc))
        scene_id = args.scene
        if not frames:
            return _die(f"ERROR: no frames_space rows for scene {scene_id!r}")

    violations: list = []
    validate_mod = None
    try:
        validate_mod = _load_validate()
    except ImportError as exc:
        if not args.from_json:
            return _die(str(exc))
    if validate_mod is not None:
        try:
            violations = try_call_validate(validate_mod, scene_id, space, frames)
        except Exception as exc:
            if not args.from_json:
                return _die(f"ERROR: validate_scene failed: {exc}")
            violations = []
    if not violations and dump_violations:
        violations = normalize_violations(dump_violations)

    out_path = Path(args.out)
    write_board(out_path, scene_id, space, frames, violations)
    sys.stdout.write(
        json.dumps(
            {
                "scene_id": scene_id,
                "out": str(out_path),
                "shots": len(frames),
                "violations": len(violations),
            },
            ensure_ascii=False,
        )
        + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
