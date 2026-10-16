#!/usr/bin/env python
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
    "tests/observe_v1/test_node_coverage.py",
]

ACCEPTANCE = [
    "tests/observe_v1/test_decision_v1.py",
    "tests/observe_v1/test_profiles_v1.py",
    "tests/observe_v1/test_decision_runtime.py",
    "tests/observe_v1/test_sfx_plan_dry_run.py",
    "tests/observe_v1/test_telemetry_endpoint.py",
    "tests/observe_v1/test_native_video_routing.py",
    "tests/test_e2e_agentic_loop_script.py",
    "tests/test_media_tranche_resume.py",
    "tests/test_media_verification.py",
    "tests/test_media_tranche_local.py",
]


def main() -> int:
    fast_only = "--fast" in sys.argv
    extra = [a for a in sys.argv[1:] if a != "--fast"]
    targets = FAST if fast_only else FAST + ACCEPTANCE
    cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *extra, *targets]
    print("Running:", " ".join(cmd), flush=True)
    return subprocess.call(cmd, cwd=str(ROOT))


if __name__ == "__main__":
    sys.exit(main())
