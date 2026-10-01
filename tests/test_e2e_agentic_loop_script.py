"""Runs scripts/e2e_test_agentic_loop.py end to end as a real subprocess in
its default DRY_RUN mode (scripted model replies, isolated disposable SQLite,
zero network, zero cost) and asserts it reaches PASS for both scenarios:

  1. invalid_json -> recommended repair profile -> verified -> done
  2. uncertain_request (timeout) -> no retry offered -> REQUIRES_HUMAN_INTERVENTION

--live is deliberately never exercised here (it spends real tokens).
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "e2e_test_agentic_loop.py"


def test_dry_run_reaches_pass_for_both_scenarios():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--dry-run"],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=180,
    )
    out = result.stdout
    assert result.returncode == 0, (out + result.stderr)[-2000:]
    assert "Mode: DRY_RUN" in out
    assert '"final_status": "done"' in out
    assert "REQUIRES_HUMAN_INTERVENTION" in out
    assert out.strip().splitlines()[-1].strip() == "PASS"
