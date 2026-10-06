#!/usr/bin/env python
"""One command for the orchestrator regression suite:

    python scripts/run_orchestrator_tests.py          # fast + acceptance
    python scripts/run_orchestrator_tests.py --fast   # pure unit tests only (~10s)

Never spends money: everything runs on synthetic credentials / scripted
model replies against disposable SQLite databases. The one real-API check
is scripts/e2e_test_agentic_loop.py --live, which is run by hand and only
with explicit approval.

Excludes the ~90 legacy tests whose failures predate this work and are
unrelated (frontend string drift, missing fixtures; documented in
docs/ORCHESTRATOR_STATUS_CURRENT.md).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

FAST = [
    "tests/test_telemetry.py",
    "tests/test_observation_cost_tracking.py",
    "tests/test_human_review_summary.py",
    "tests/test_node_spec_registration.py",
    # Node coverage: every registered node type has a spec ("add a node" guard), levels, reasons.
    "tests/observe_v1/test_node_coverage.py",
]

ACCEPTANCE = [
    # Decision / Self-Healing / Profiles / escalation summary / recommendation
    "tests/observe_v1/test_decision_v1.py",
    "tests/observe_v1/test_profiles_v1.py",
    "tests/observe_v1/test_decision_runtime.py",
    # sfx_plan: observation -> diagnosis -> decision -> one verified retry, fake model boundary only
    "tests/observe_v1/test_sfx_plan_dry_run.py",
    # Telemetry endpoint + SSE generator
    "tests/observe_v1/test_telemetry_endpoint.py",
    # Seedance vs Kling routing fix (needs ffmpeg; skips cleanly without it)
    "tests/observe_v1/test_native_video_routing.py",
    # Full scripted agentic loop (invalid_json repair + timeout escalation)
    "tests/test_e2e_agentic_loop_script.py",
    # Media tranche: resume-only guard, videos resume dry run, result verification, audio / sfx / assemble dry runs
    "tests/test_media_tranche_resume.py",
    "tests/test_media_verification.py",
    "tests/test_media_tranche_local.py",
]


def main() -> int:
    fast_only = "--fast" in sys.argv
    targets = FAST if fast_only else FAST + ACCEPTANCE
    cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *targets]
    print("Running:", " ".join(cmd), flush=True)
    return subprocess.call(cmd, cwd=str(ROOT))


if __name__ == "__main__":
    sys.exit(main())
