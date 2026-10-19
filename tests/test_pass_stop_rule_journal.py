import asyncio
import json

import pytest

import app.services.apply_ops_batches as aob
from app.services.gpt_operator_client import OperatorApiResult
from app.services.node_rules import PASS_RULES, RULES_BY_ID


def _stop(rule="R-SHOT-COVERAGE", message="excel_gpt node=x: L1 call 1 нужен добор"):
    return aob.PassStopError(message, rule=rule, diary=[])


def test_pass_stop_rule_reads_rule_and_walks_wrappers():
    exc = _stop()
    assert aob.pass_stop_rule(exc) == "R-SHOT-COVERAGE"
    try:
        try:
            raise exc
        except aob.PassStopError as inner:
            raise RuntimeError("wrapped") from inner
    except RuntimeError as outer:
        assert aob.pass_stop_rule(outer) == "R-SHOT-COVERAGE"


def test_pass_stop_rule_is_none_for_other_errors():
    assert aob.pass_stop_rule(None) is None
    assert aob.pass_stop_rule(RuntimeError("boom")) is None
    plain = RuntimeError("has a rule attr but is not a PassStop")
    plain.rule = "R-UUID"
    assert aob.pass_stop_rule(plain) is None
    assert aob.pass_stop_rule(_stop(rule="")) is None


def test_shots_check_stop_rule_is_a_registered_rule():
    assert PASS_RULES["shots_check"] in RULES_BY_ID


@pytest.mark.parametrize("n_frames", [1, 4])
async def test_exhausted_undercount_reaches_caller_with_rule(tmp_path, monkeypatch, n_frames):
    frames = [{"uuid": f"u{i}", "number": i + 1, "voiceover_text": f"закадр {i}",
               "fields": {"закадр": f"закадр {i}"}} for i in range(n_frames)]
    ctx = {"frames": frames}
    path = tmp_path / "db_frames.json"
    path.write_text(json.dumps(ctx, ensure_ascii=False), encoding="utf-8")
    calls = []

    async def fake_api(**kw):
        calls.append(kw["input_paths"][0].name)
        return OperatorApiResult(reply_text="{}", output_paths=[path],
                                 apply_ops={"ops": [{"frame_uuid": "u0", "fields": {}}]})

    def stop(ops, chunk, **kw):
        raise _stop(message=f"excel_gpt node=n: L{kw['ctx'].level} call {kw['ctx'].call} нужен добор кадров")

    real_sleep = asyncio.sleep

    async def fast_sleep(_s):
        await real_sleep(0)
    monkeypatch.setattr(aob, "run_operator_api", fake_api)
    monkeypatch.setattr(aob, "run_code_passes", stop)
    monkeypatch.setattr(aob.asyncio, "sleep", fast_sleep)

    applied = []

    async def apply(payload):
        applied.append(payload)

    with pytest.raises(aob.PassStopError) as err:
        await aob.run_apply_ops_batched(
            project_dir=tmp_path, node_key="n_excel_gpt_fw_shots", role="assist", output_mode="project_file",
            prompt="p", accompanying="", db_ctx=ctx, ctx_path=path, project_id=1, dense=False,
            footer_kind="shots", force_full=True, apply_fn=apply)
    assert aob.pass_stop_rule(err.value) == "R-SHOT-COVERAGE"
    assert applied == [] and calls
    if n_frames > 1:
        # R0a: shots_short_packs не режет пачку шотов дальше L1.
        assert not any(name.endswith("_L2.json") for name in calls)
