
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.models import Project, ProjectStatus
from app.services import gpt_operator_client as client
from app.services.gpt_operator import gate_allows_successors, save_operator_result, verdict_edge_blocks
from app.services.gpt_operator_client import NOT_CHECKED_SUMMARY_RU, VERDICT_NOT_CHECKED, run_operator_api


@pytest.fixture(autouse=True)
def _no_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("app.services.gpt_api.gpt_api_enabled", lambda: False)
    monkeypatch.delenv(client.STUB_STRICT_ENV, raising=False)


async def _run(tmp_path: Path, *, role="review", check_mode=False, prompt="check", accompanying="go"):
    src = tmp_path / "project.xlsx"
    src.write_bytes(b"PK" + b"0" * 200)
    return await run_operator_api(
        project_dir=tmp_path, node_key="n_check", role=role, output_mode="text", prompt=prompt,
        accompanying=accompanying, input_paths=[src], check_mode=check_mode, check_fix=False,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("role,check_mode", [("review", False), ("gate", False), ("compare", False), ("assist", True)])
async def test_a_check_without_a_key_is_not_checked_and_never_pass(tmp_path: Path, role, check_mode) -> None:
    res = await _run(tmp_path, role=role, check_mode=check_mode)
    assert res.gate_status == VERDICT_NOT_CHECKED == "not_checked"
    assert res.analysis is not None and res.analysis.verdict == "not_checked"
    assert res.analysis.ok is False
    assert NOT_CHECKED_SUMMARY_RU in res.analysis.summary
    assert "pass" not in {res.gate_status, res.analysis.verdict}


@pytest.mark.asyncio
async def test_the_written_files_say_not_checked_in_russian(tmp_path: Path) -> None:
    res = await _run(tmp_path, role="review")
    analysis_path = next(p for p in res.output_paths if p.name == "analysis.json")
    data = json.loads(analysis_path.read_text(encoding="utf-8"))
    assert data["verdict"] == "not_checked" and data["checks"][0]["ok"] is False
    assert "Проверка НЕ выполнена" in data["summary"]
    report = next(p for p in res.output_paths if p.name == "check_report.txt").read_text(encoding="utf-8")
    assert "verdict: not_checked" in report and "verdict: pass" not in report
    assert "Проверка НЕ выполнена" in res.reply_text


@pytest.mark.asyncio
async def test_failure_marker_words_no_longer_change_the_verdict(tmp_path: Path) -> None:
    for prompt in ("всё хорошо", "fail: стоп, ошибка"):
        res = await _run(tmp_path, role="review", prompt=prompt)
        assert res.gate_status == "not_checked", prompt


@pytest.mark.asyncio
async def test_a_not_checked_gate_opens_no_branch(tmp_path: Path, monkeypatch) -> None:
    from app import settings as app_settings

    monkeypatch.setattr(app_settings.settings, "data_dir", tmp_path / "data")
    p = Project(slug="w44-stub", topic="t", status=ProjectStatus.new, hero_mode="no_hero", meta={})
    p.data_dir.mkdir(parents=True)
    a, b = "n_gate", "n_next"
    p.meta = {
        "canvas_graph": {
            "nodes": [{"id": a, "type": "excel_gpt", "position": {"x": 0, "y": 0}},
                      {"id": b, "type": "images", "position": {"x": 200, "y": 0}}],
            "edges": [{"id": "e1", "source": a, "target": b, "data": {"kind": "gate"}}],
        },
        "excel_gpt_nodes": {a: {"role": "gate", "transport": "api"}},
    }
    res = await run_operator_api(project_dir=p.data_dir, node_key=a, role="gate", output_mode="text",
                                 prompt="check", accompanying="", input_paths=[])
    entry = save_operator_result(p, a, input_paths=[], output_paths=list(res.output_paths), reply_text=res.reply_text,
                                 gate_status=res.gate_status, analysis=res.analysis.to_dict())
    assert entry["gateStatus"] == "not_checked"
    assert gate_allows_successors(p, a) is None
    assert verdict_edge_blocks(p, a, "pass") is True
    assert verdict_edge_blocks(p, a, "fail") is True


@pytest.mark.asyncio
async def test_the_event_is_written_to_the_journal(tmp_path: Path, monkeypatch) -> None:
    facts = []

    async def record_fact(kind, payload=None, **kw):
        facts.append((kind, payload))

    monkeypatch.setattr("app.services.observation.record_fact", record_fact)
    await _run(tmp_path, role="review")
    ((kind, payload),) = facts
    assert kind == "operator_check_not_checked"
    assert payload["verdict"] == "not_checked" and payload["reason"] == "gpt_api_disabled"
    assert payload["semantic_quality_verified"] is False and "Проверка НЕ выполнена" in payload["summary_ru"]


@pytest.mark.asyncio
async def test_a_text_step_without_a_key_records_a_stub_output_event(tmp_path: Path, monkeypatch) -> None:
    facts = []

    async def record_fact(kind, payload=None, **kw):
        facts.append((kind, payload))

    monkeypatch.setattr("app.services.observation.record_fact", record_fact)
    res = await _run(tmp_path, role="transform")
    assert res.gate_status is None and res.analysis is None
    ((kind, payload),) = facts
    assert kind == "operator_stub_output" and payload["verdict"] is None
    assert "заглушкой" in payload["summary_ru"]


@pytest.mark.asyncio
async def test_a_failing_journal_never_breaks_the_stub(tmp_path: Path, monkeypatch) -> None:
    async def broken(kind, payload=None, **kw):
        raise RuntimeError("journal down")

    monkeypatch.setattr("app.services.observation.record_fact", broken)
    res = await _run(tmp_path, role="review")
    assert res.gate_status == "not_checked"


@pytest.mark.asyncio
@pytest.mark.parametrize("role,check_mode", [("review", False), ("assist", True), ("transform", False)])
async def test_strict_mode_stops_with_a_russian_error_and_writes_nothing(tmp_path: Path, monkeypatch, role, check_mode) -> None:
    monkeypatch.setenv(client.STUB_STRICT_ENV, "1")
    with pytest.raises(RuntimeError) as err:
        await _run(tmp_path, role=role, check_mode=check_mode)
    assert "Нет ключа GPT-провайдера" in str(err.value)
    assert not (tmp_path / "excel_gpt_uploads").exists()


@pytest.mark.asyncio
async def test_a_real_key_still_goes_to_the_real_path_not_the_stub(tmp_path: Path, monkeypatch) -> None:
    called = []

    async def fake_real(**kwargs):
        called.append(kwargs["node_key"])
        return client.OperatorApiResult(reply_text="ok", output_paths=[], gate_status="pass")

    monkeypatch.setattr("app.services.gpt_api.gpt_api_enabled", lambda: True)
    monkeypatch.setattr(client, "_run_operator_api_real", fake_real)
    monkeypatch.setenv(client.STUB_STRICT_ENV, "1")
    res = await _run(tmp_path, role="review")
    assert called == ["n_check"] and res.gate_status == "pass"
