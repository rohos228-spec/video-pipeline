"""Run only synthetic journal tests and record the modules actually imported."""
from __future__ import annotations

import hashlib
import json
import os
import runpy
import sys
from pathlib import Path

SOURCE = Path(os.environ.get("OBSERVE_SOURCE", "/observe-source"))
RESULTS = Path("/results")

if not Path("/.dockerenv").exists():
    raise SystemExit("This acceptance target must run inside the isolated Docker container")

# No dotenv file is mounted; all credentials are intentionally synthetic/empty.
os.environ.update({
    "DATA_DIR": "/results/synthetic-data", "SQLITE_PATH": "/results/synthetic-data/state.db",
    "GPT_API_KEY": "synthetic-observe-test-key", "GPT_BASE_URL": "http://127.0.0.1:18766",
    "TELEGRAM_ENABLED": "false", "TELEGRAM_BOT_TOKEN": "", "FLEET_ENABLED": "false",
})
RESULTS.mkdir(parents=True, exist_ok=True)
expected = json.loads((SOURCE / "source-manifest.json").read_text())
sys.path.insert(0, str(SOURCE / "tests/observe_v1"))
from coverage_capture import begin
finish_coverage = begin(SOURCE)
exit_code = 0
try:
    sys.path.insert(0, str(SOURCE / "tests/observe_v1"))
    sys.argv = [str(SOURCE / "tests/observe_v1/test_journal.py")]
    if os.environ.get("OBSERVE_TEST_TARGET") == "runtime":
        sys.argv = [str(SOURCE / "tests/observe_v1/test_runtime.py")]
    if os.environ.get("OBSERVE_TEST_TARGET") == "diagnose":
        sys.argv = [str(SOURCE / "tests/observe_v1/test_diagnosis.py")]
    if os.environ.get("OBSERVE_TEST_TARGET") == "actions":
        sys.argv = [str(SOURCE / "tests/observe_v1/test_actions_v1_contract.py")]
    if os.environ.get("OBSERVE_TEST_TARGET",'').startswith("decision"):
        sys.argv = [str(SOURCE / "tests/observe_v1/test_decision_v1.py")]
        if os.environ.get('OBSERVE_TEST_TARGET')=='decision-audit':
            sys.argv.extend(['DecisionDurability.test_missing_mandatory_body_blocks_http_before_charge',
                'DecisionDurability.test_contract_uses_actual_target_count_not_example_batch'])
    if os.environ.get("OBSERVE_TEST_TARGET")=="decision-runtime":
        sys.argv=[str(SOURCE / "tests/observe_v1/test_decision_runtime.py")]
    if os.environ.get("OBSERVE_TEST_TARGET")=="profiles":
        sys.argv=[str(SOURCE / "tests/observe_v1/test_profiles_v1.py")]
    if os.environ.get("OBSERVE_TEST_TARGET")=="planner-boundary":
        sys.argv=[str(SOURCE / "tests/observe_v1/test_planner_boundary.py")]
    runpy.run_path(sys.argv[0], run_name="__main__")
except SystemExit as exc:
    exit_code = int(exc.code or 0)
except BaseException:
    import traceback
    traceback.print_exc()
    exit_code = 1
finally:
    finish_coverage(RESULTS)
    loaded = []
    mismatch = []
    for name, module in sorted(sys.modules.items()):
        if not name.startswith("app."):
            continue
        filename = getattr(module, "__file__", None)
        if not filename:
            continue
        path = Path(filename).resolve()
        if not path.is_relative_to(SOURCE):
            mismatch.append({"module": name, "problem": "loaded outside new source mount"})
            continue
        relative = path.relative_to(SOURCE).as_posix()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        loaded.append({"module": name, "path": relative, "sha256": digest})
        if expected.get(relative) != digest:
            mismatch.append({"module": name, "problem": "source hash differs"})
    (RESULTS / "loaded-modules.json").write_text(json.dumps({
        "loaded": loaded, "mismatches": mismatch, "test_exit_code": exit_code,
    }, indent=2))
    if mismatch:
        exit_code = 2
raise SystemExit(exit_code)
