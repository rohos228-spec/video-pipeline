#!/usr/bin/env python
"""Legacy and observe suites in chunks: one fresh pytest process per chunk, ONE at a time.

    python scripts/run_legacy_chunked.py [--suite legacy|observe|all] [--chunk 40] [--only-chunk N] [--junit-dir reports/legacy]

legacy  = tests/test_*.py and tests/*_test.py (top level); observe = tests/observe_v1 (without the four docker-only modules).
Default is ``all``: legacy first, then observe, so no test file stays outside an automatic suite (scripts/test_suites.py).
Never spends money. With --junit-dir legacy chunks write chunk_NN.xml and observe chunks write observe_NN.xml,
which scripts/test_report.py turns into the numbers block.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_suites import legacy_files, observe_files  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--suite", choices=("legacy", "observe", "all"), default="all")
    ap.add_argument("--chunk", type=int, default=40)
    ap.add_argument("--only-chunk", type=int, default=0, help="1-based, 0 = all")
    ap.add_argument("--junit-dir", default="")
    ap.add_argument("--per-chunk-timeout", type=int, default=1500)
    args = ap.parse_args()
    groups = []
    if args.suite in ("legacy", "all"):
        groups.append(("chunk", legacy_files()))
    if args.suite in ("observe", "all"):
        groups.append(("observe", observe_files()))
    chunks = []  # (junit file prefix, files)
    for prefix, files in groups:
        chunks += [(prefix, files[i:i + args.chunk]) for i in range(0, len(files), args.chunk)]
    junit = Path(args.junit_dir) if args.junit_dir else None
    if junit:
        junit.mkdir(parents=True, exist_ok=True)
    bad = 0
    numbers: dict[str, int] = {}
    for n, (prefix, chunk) in enumerate(chunks, 1):
        numbers[prefix] = numbers.get(prefix, 0) + 1
        if args.only_chunk and n != args.only_chunk:
            continue
        cmd = [sys.executable, "-X", "utf8", "-m", "pytest", *chunk, "-p", "no:cacheprovider", "-p", "no:warnings",
               "-q", "--tb=short", "-o", "faulthandler_timeout=420"]
        if junit:
            cmd.append(f"--junitxml={junit / f'{prefix}_{numbers[prefix]:02d}.xml'}")
        try:
            code = subprocess.call(cmd, cwd=str(ROOT), timeout=args.per_chunk_timeout)
        except subprocess.TimeoutExpired:
            code = 124
        print(f"chunk {n}/{len(chunks)} exit={code}", flush=True)
        bad += code != 0
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
