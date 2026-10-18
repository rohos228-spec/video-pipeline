#!/usr/bin/env python3
"""Seed three scene_space fixtures, rewrite/rebuild twice, try validate/plan/board.

  python scripts/scene_space_e2e.py

Isolated sqlite: tasks/out/scene_space_e2e.db (not data/state.db).
If store/blocking/validate/render are missing, still seeds + rewrite API and
prints a request list for streams 1–4.
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


async def _try_cli_helpers(db: Path) -> dict:
    """Call stream 3/4 CLIs if present; document the request otherwise."""
    import subprocess

    out: dict = {}
    py = sys.executable
    validate = ROOT / "scripts" / "scene_space_validate.py"
    plan = ROOT / "scripts" / "scene_space_plan.py"
    board = ROOT / "scripts" / "scene_space_board.py"
    if validate.is_file():
        proc = subprocess.run(
            [py, str(validate), "--fixtures"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        out["validate"] = {"returncode": proc.returncode, "stdout": proc.stdout[-2000:]}
    else:
        out["validate"] = {
            "request": "stream 4: scripts/scene_space_validate.py --fixtures → tasks/VALIDATION.md"
        }
    if plan.is_file() and board.is_file():
        for sid, folder in (
            ("fix:dialogue", "fix-dialogue"),
            ("fix:cross", "fix-cross"),
            ("fix:turn", "fix-turn"),
        ):
            dest = ROOT / "tasks" / "out" / folder
            dest.mkdir(parents=True, exist_ok=True)
            dump = ROOT / "tests" / "fixtures" / "scene_space" / f"{folder.split('-', 1)[1]}.json"
            subprocess.run(
                [py, str(plan), "--scene", sid, "--out", str(dest), "--from-json", str(dump)],
                cwd=str(ROOT),
                check=False,
            )
            subprocess.run(
                [
                    py,
                    str(board),
                    "--scene",
                    sid,
                    "--out",
                    str(dest / "board.html"),
                    "--from-json",
                    str(dump),
                ],
                cwd=str(ROOT),
                check=False,
            )
        out["render"] = "from-json fixtures"
    else:
        out["render"] = {
            "request": (
                "stream 3: scripts/scene_space_plan.py and scene_space_board.py "
                "with --scene fix:dialogue|fix:cross|fix:turn"
            )
        }
    out["db"] = str(db)
    return out


async def _run(db: Path) -> dict:
    from app.services.scene_space.rewrite import isolated_session, run_e2e

    async with isolated_session(db) as session:
        report = await run_e2e(session)
    helpers = await _try_cli_helpers(db)
    report["helpers"] = helpers
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="scene_space fixture e2e")
    parser.add_argument("--db", default="")
    args = parser.parse_args(argv)
    db = Path(args.db) if args.db else ROOT / "tasks" / "out" / "scene_space_e2e.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    for leftover in (db, Path(str(db) + "-wal"), Path(str(db) + "-shm")):
        leftover.unlink(missing_ok=True)
    report = asyncio.run(_run(db))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    second_ok = (
        not report["rewrite_second"]["changed"]["inserted"]
        and not report["rewrite_second"]["changed"]["updated"]
        and not report["rebuild_second"]["changed"]["inserted"]
        and not report["rebuild_second"]["changed"]["updated"]
    )
    cov_ok = all(not v for v in report["coverage"].values())
    return 0 if second_ok and cov_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
