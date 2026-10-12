#!/usr/bin/env python
"""Legacy suite (tests/test_*.py, tests/*_test.py) in chunks: one fresh pytest process per chunk, ONE at a time.

    python scripts/run_legacy_chunked.py [--chunk 40] [--only-chunk N] [--junit-dir reports/legacy]

Same file selection as the local C:/video-pipeline/_run_legacy_chunked.py. Never spends money. With
--junit-dir every chunk writes chunk_NN.xml, which scripts/test_report.py turns into the numbers block.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def legacy_files() -> list[str]:
    return sorted(
        p.relative_to(ROOT).as_posix()
        for p in (ROOT / "tests").glob("*.py")
        if p.name.startswith("test_") or p.name.endswith("_test.py")
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--chunk", type=int, default=40)
    ap.add_argument("--only-chunk", type=int, default=0, help="1-based, 0 = all")
    ap.add_argument("--junit-dir", default="")
    ap.add_argument("--per-chunk-timeout", type=int, default=1500)
    args = ap.parse_args()
    files = legacy_files()
    chunks = [files[i:i + args.chunk] for i in range(0, len(files), args.chunk)]
    junit = Path(args.junit_dir) if args.junit_dir else None
    if junit:
        junit.mkdir(parents=True, exist_ok=True)
    bad = 0
    for n, chunk in enumerate(chunks, 1):
        if args.only_chunk and n != args.only_chunk:
            continue
        cmd = [sys.executable, "-X", "utf8", "-m", "pytest", *chunk, "-p", "no:cacheprovider", "-p", "no:warnings",
               "-q", "--tb=short", "-o", "faulthandler_timeout=420"]
        if junit:
            cmd.append(f"--junitxml={junit / f'chunk_{n:02d}.xml'}")
        try:
            code = subprocess.call(cmd, cwd=str(ROOT), timeout=args.per_chunk_timeout)
        except subprocess.TimeoutExpired:
            code = 124
        print(f"chunk {n}/{len(chunks)} exit={code}", flush=True)
        bad += code != 0
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
