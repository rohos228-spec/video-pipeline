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
tracked_unit_targets={'native-checkpoint','runtime-projection','native-entities','scene-contracts',
    'managed-restart','native-sfx-plan','native-animation','hitl-authority','animation-observation','scene-observation'}
unit_executions=[]
if os.environ.get('OBSERVE_TEST_TARGET') in tracked_unit_targets:
    import unittest
    original_start_test=unittest.TestResult.startTest
    def record_started_test(result,test):
        unit_executions.append(test.id())
        return original_start_test(result,test)
    unittest.TestResult.startTest=record_started_test
try:
    sys.path.insert(0, str(SOURCE / "tests/observe_v1"))
    sys.argv = [str(SOURCE / "tests/observe_v1/test_journal.py")]
    if os.environ.get("OBSERVE_TEST_TARGET") == "runtime":
        sys.argv = [str(SOURCE / "tests/observe_v1/test_runtime.py")]
    if os.environ.get("OBSERVE_TEST_TARGET") == "media-contract":
        sys.argv = [str(SOURCE / "tests/observe_v1/test_media_contract.py")]
    extra_targets={'native-checkpoint':'test_image_prompt_checkpoint.py',
        'runtime-projection':'test_runtime_projection.py','native-entities':'test_native_entity_contract.py',
        'scene-contracts':'test_scene_contracts.py','managed-restart':'test_managed_restart_safety.py',
        'native-sfx-plan':'test_native_sfx_plan_checkpoint.py','native-animation':'test_native_animation_contract.py'}
    if os.environ.get('OBSERVE_TEST_TARGET') in extra_targets:
        sys.argv=[str(SOURCE/'tests/observe_v1'/extra_targets[os.environ['OBSERVE_TEST_TARGET']])]
    if os.environ.get('OBSERVE_TEST_TARGET')=='hitl-authority':
        sys.argv=[str(SOURCE/'tests/observe_v1/test_hitl_project_authority.py')]
    if os.environ.get('OBSERVE_TEST_TARGET')=='animation-observation':
        sys.argv=[str(SOURCE/'tests/observe_v1/test_native_animation_observation.py')]
    if os.environ.get('OBSERVE_TEST_TARGET')=='scene-observation':
        sys.argv=[str(SOURCE/'tests/observe_v1/test_native_scene_observation.py')]
    if os.environ.get("OBSERVE_TEST_TARGET") == "media-jobs":
        sys.argv = [str(SOURCE / "tests/observe_v1/test_media_jobs.py")]
    if os.environ.get("OBSERVE_TEST_TARGET") == "media-observation":
        sys.argv = [str(SOURCE / "tests/observe_v1/test_media_observation.py")]
    if os.environ.get("OBSERVE_TEST_TARGET") == "media-transport":
        sys.argv = [str(SOURCE / "tests/observe_v1/test_media_transport.py")]
    if os.environ.get("OBSERVE_TEST_TARGET") == "diagnose":
        sys.argv = [str(SOURCE / "tests/observe_v1/test_diagnosis.py")]
    if os.environ.get("OBSERVE_TEST_TARGET") == "managed-launch":
        sys.argv = [str(SOURCE / "tests/observe_v1/test_managed_launch.py")]
    if os.environ.get("OBSERVE_TEST_TARGET") == "owner-budget":
        sys.argv = [str(SOURCE / "tests/observe_v1/test_owner_budget.py")]
    if os.environ.get("OBSERVE_TEST_TARGET") == "actions":
        sys.argv = [str(SOURCE / "tests/observe_v1/test_actions_v1_contract.py")]
    if os.environ.get("OBSERVE_TEST_TARGET",'').startswith("decision"):
        sys.argv = [str(SOURCE / "tests/observe_v1/test_decision_v1.py")]
        if os.environ.get('OBSERVE_TEST_TARGET')=='decision-audit':
            sys.argv.extend(['DecisionDurability.test_missing_mandatory_body_blocks_http_before_charge',
                'DecisionDurability.test_contract_uses_actual_target_count_not_example_batch'])
        if os.environ.get('OBSERVE_TEST_TARGET')=='decision-native-contracts':
            sys.argv.extend(['DecisionDurability.test_native_read_contract_preserves_hitl_without_recovery_profile_or_writes',
                'DecisionDurability.test_split_read_contract_has_no_media_job_or_inferred_success',
                'DecisionDurability.test_image_prompt_contract_hash_tracks_actual_output_without_claiming_quality'])
        if os.environ.get('OBSERVE_TEST_TARGET')=='decision-scene-contracts':
            sys.argv.extend(['DecisionDurability.test_marked_scene_node_never_inherits_structured_text_recovery',
                'DecisionDurability.test_unsupported_native_contract_does_not_claim_media_verification'])
        if os.environ.get('OBSERVE_TEST_TARGET')=='decision-family-contracts':
            sys.argv.extend(['DecisionDurability.test_hero_descriptive_contract_cannot_authorize_generation',
                'DecisionDurability.test_animation_descriptive_contract_cannot_authorize_generation',
                'DecisionDurability.test_camera_descriptive_contract_cannot_inherit_excel_tools',
                'DecisionDurability.test_assembler_descriptive_contract_cannot_inherit_excel_tools'])
    if os.environ.get("OBSERVE_TEST_TARGET")=="decision-runtime":
        sys.argv=[str(SOURCE / "tests/observe_v1/test_decision_runtime.py")]
    if os.environ.get("OBSERVE_TEST_TARGET")=="profiles":
        sys.argv=[str(SOURCE / "tests/observe_v1/test_profiles_v1.py")]
    if os.environ.get("OBSERVE_TEST_TARGET")=="planner-boundary":
        sys.argv=[str(SOURCE / "tests/observe_v1/test_planner_boundary.py")]
    if os.environ.get("OBSERVE_TEST_TARGET")=="readonly-pilot":
        sys.argv=[str(SOURCE / "tests/observe_v1/test_readonly_pilot.py")]
    if os.environ.get("OBSERVE_TEST_TARGET", "").startswith("qc-noop"):
        sys.argv=[str(SOURCE / "tests/observe_v1/test_qc_noop.py")]
        if os.environ.get('OBSERVE_TEST_TARGET')=='qc-noop-rules':
            sys.argv.append('NoopEvidence')
        if os.environ.get('OBSERVE_TEST_TARGET')=='qc-noop-uncertain':
            sys.argv.append('NoopRecoveryVerify.test_incomplete_sse_holds_managed_execution_before_next_post')
        if os.environ.get('OBSERVE_TEST_TARGET')=='qc-noop-audit':
            sys.argv.append('NoopRecoveryVerify.test_real_journal_body_limit_after_http_blocks_managed_noop')
    if os.environ.get('OBSERVE_TEST_TARGET')=='operator-regression':
        sys.path.append(str(SOURCE/'test-vendor'))
        os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD']='1'
        import pytest
        raise SystemExit(pytest.main(['-q', '-p', 'pytest_asyncio.plugin', str(SOURCE/'tests/test_gpt_operator_apply_ops.py'),
                                     str(SOURCE/'tests/test_apply_ops_batches.py')]))
    if os.environ.get('OBSERVE_TEST_TARGET')=='decision-final':
        import importlib,unittest
        results=[]
        for name in ('test_decision_runtime','test_decision_v1','test_actions_v1_contract','test_profiles_v1','test_planner_boundary','test_journal'):
            result=unittest.TextTestRunner(verbosity=1).run(unittest.defaultTestLoader.loadTestsFromModule(importlib.import_module(name)))
            results.append({'module':name,'tests':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'green':result.wasSuccessful()})
        (RESULTS/'regression-summary.json').write_text(json.dumps(results,indent=2))
        raise SystemExit(0 if all(r['green'] for r in results) else 1)
    runpy.run_path(sys.argv[0], run_name="__main__")
except SystemExit as exc:
    exit_code = int(exc.code or 0)
except BaseException:
    import traceback
    traceback.print_exc()
    exit_code = 1
finally:
    if os.environ.get('OBSERVE_TEST_TARGET') in tracked_unit_targets:
        if exit_code==0 and not unit_executions:
            exit_code=2
            print('Acceptance refused: no unittest cases executed',file=sys.stderr)
        (RESULTS/'test-executions.json').write_text(json.dumps({
            'started_test_count':len(unit_executions),'cases':unit_executions,
            'test_exit_code':exit_code},indent=2))
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
