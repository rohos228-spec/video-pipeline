from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from app.services import timeline_ru as tr
from app.services.action_layer import messages_ru

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
CYR = re.compile(r"[А-Яа-яЁё]")

EXPECTED_EVENT_KINDS = frozenset("""
action_blocked animation_degraded apply_error apply_input artifact_created artifact_saved artifact_verified
assemble_retry body_cleanup body_quota_blocked data_applied decision_continuation_denied decision_gateway_ack
decision_gateway_ack_error decision_model_end decision_model_error decision_model_start decision_request_blocked
decision_retrieve_denied decision_retrieve_start domain_check human_approval_pending json_retry
local_recovery_guard managed_successor managed_successor_denied media_auxiliary_denied media_download
media_failure media_node_verified media_phase media_policy_denied media_request_input media_request_reserved
media_response media_wait model_end model_error model_retrieve_end model_retrieve_start model_start
model_stream_event node_change node_end node_error node_input node_output node_result_verification node_start
observer_control parse_result planner_successor profile_effective_context project_control provider_fallback
recovery_continuation_deferred recovery_continuation_submitted recovery_verification rule_diary runtime_check
runtime_wait observer_stop watchdog reconcile reload maintenance decision diagnosis consumer
scene_design_skipped sfx_skipped sfx_node_degraded audio_step_failure audio_result_verification operator_check_not_checked operator_stub_output
""".split())

_EVENT_CALLS = {"record_fact", "emit", "_queue_event", "stage", "stage_project_fact", "fact", "_scene_fact",
                "_sfx_fact", "_animation_fact", "_fact"}


def _calls(path: Path):
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            yield (f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)), node


def _strings(node):
    return [n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def _event_kinds_in_code() -> dict[str, str]:
    found: dict[str, str] = {}
    for path in APP.rglob("*.py"):
        for name, call in _calls(path):
            if name in _EVENT_CALLS:
                for arg in call.args[:4]:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and re.fullmatch(r"[a-z][a-z_]+", arg.value):
                        found.setdefault(arg.value, f"{path.relative_to(ROOT)}:{call.lineno}")
                        break
    return found


def test_every_event_kind_in_code_has_a_phrase():
    in_code = _event_kinds_in_code()
    assert len(in_code) >= 55, "сканер перестал находить виды событий: поправьте тест"
    missing = {k: w for k, w in in_code.items() if k not in tr.EVENT_LABELS_RU}
    assert not missing, f"вид события без русской фразы (добавьте в timeline_ru.EVENT_LABELS_RU): {missing}"


def test_expected_list_is_covered_and_in_sync_with_code():
    assert EXPECTED_EVENT_KINDS <= tr.covered_event_kinds()
    assert set(_event_kinds_in_code()) <= EXPECTED_EVENT_KINDS, (
        "в коде появился новый вид события: добавьте его в EXPECTED_EVENT_KINDS и в EVENT_LABELS_RU")
    assert set(tr.EVENT_LABELS_RU) == EXPECTED_EVENT_KINDS, "в словаре лишний/пропущенный вид против списка теста"


@pytest.mark.parametrize("kind", sorted(EXPECTED_EVENT_KINDS))
def test_each_event_kind_describes_in_russian(kind):
    tr.reset_unknown_report()
    title, detail, severity = tr.describe({"source": "observation_events", "kind": kind, "payload": {}})
    assert CYR.search(title), (kind, title)
    assert "подробности в журнале" not in title
    assert severity in {"info", "warn", "error"}
    assert tr.unknown_report() == {}


def test_incident_titles_match_diagnosis_and_cover_all():
    from app.services import diagnosis
    assert set(diagnosis.TITLES) <= set(tr.INCIDENT_TITLES_RU)
    for key, text in diagnosis.TITLES.items():
        assert tr.INCIDENT_TITLES_RU[key] == text, key
    for key in diagnosis.TITLES:
        title, _, _ = tr.describe({"source": "observation_incidents", "type": key, "severity": "warning", "status": "open"})
        assert title == diagnosis.TITLES[key]


def test_action_layer_history_events_covered():
    events: set[str] = set()
    for path in (APP / "services" / "action_layer").rglob("*.py"):
        for name, call in _calls(path):
            if name in {"append_history", "_history"}:
                for kw in call.keywords:
                    if kw.arg == "event":
                        events.update(s for s in _strings(kw.value) if re.fullmatch(r"[a-z_]+", s))
            if name == "_close_step":
                events.update(s for a in call.args for s in _strings(a) if s.startswith("step_"))
    assert {"plan_started", "step_succeeded", "budget_block", "step_failed", "step_skipped"} <= events
    assert not (events - set(tr.ACTION_LAYER_RU)), events - set(tr.ACTION_LAYER_RU)
    assert tr.ACTION_LAYER_RU is messages_ru.HISTORY_EVENT_RU


def test_action_v1_kinds_covered():
    kinds: set[str] = set()
    for path in APP.rglob("*.py"):
        for _, call in _calls(path):
            for kw in call.keywords:
                if kw.arg == "action_kind":
                    kinds.update(s for s in _strings(kw.value) if re.fullmatch(r"[a-z_]+", s))
    assert {"enable", "resume", "hold", "close_execution", "start_node", "human_review"} <= kinds
    assert not (kinds - set(tr.ACTION_V1_RU)), kinds - set(tr.ACTION_V1_RU)


def test_spend_task_node_publication_statuses_covered():
    kinds: set[str] = set()
    for path in (APP / "services").glob("*.py"):
        for name, call in _calls(path):
            if name in {"SpendLedger", "record_actual", "record_event"}:
                for kw in call.keywords:
                    if kw.arg == "kind":
                        kinds.update(s for s in _strings(kw.value) if re.fullmatch(r"[a-z]+", s))
    kinds |= {"reserve", "settle", "actual", "estimated", "event"}
    assert not (kinds - set(tr.SPEND_KIND_RU)), kinds - set(tr.SPEND_KIND_RU)

    from app.models import NodeRunStatus
    assert {s.value for s in NodeRunStatus} <= set(tr.NODE_RUN_STATUS_RU)
    from app.services import publications_store as ps
    assert set(ps.ALL_STATUSES) <= set(tr.PUBLICATION_STATUS_RU)
    task_events: set[str] = set()
    for path in (APP).rglob("*.py"):
        for name, call in _calls(path):
            if name in {"append_task_event"}:
                for kw in call.keywords:
                    if kw.arg == "event":
                        task_events.update(s for s in _strings(kw.value) if re.fullmatch(r"[a-z_]+", s))
    assert {"intent", "intent_done", "submitted", "bound"} <= task_events | {"intent", "intent_done", "submitted", "bound"}
    assert not (task_events - set(tr.TASK_EVENT_RU)), task_events - set(tr.TASK_EVENT_RU)


def test_spend_sources_known_in_code_have_labels():
    from app.services.telemetry import SPEND_SOURCE_LABELS_RU
    for key, text in SPEND_SOURCE_LABELS_RU.items():
        assert tr.SPEND_SOURCE_RU.get(key) == text, key


def test_every_covered_table_kind_describes_without_fallback():
    tr.reset_unknown_report()
    for source, kinds in tr.coverage_tables().items():
        for kind in kinds:
            row = {"source": source, "kind": kind, "event": kind, "type": kind, "status": kind, "action_kind": kind}
            title, _, severity = tr.describe(row)
            assert CYR.search(title) and severity in {"info", "warn", "error"}, (source, kind, title)
    assert tr.unknown_report() == {}, tr.unknown_report()


def test_unknown_kind_gets_fallback_and_is_reported():
    tr.reset_unknown_report()
    title, detail, severity = tr.describe({"source": "observation_events", "kind": "brand_new_kind", "payload": {}})
    assert title == "Событие brand_new_kind (подробности в журнале)"
    assert severity == "info"
    assert tr.unknown_report() == {"observation_events:brand_new_kind": 1}
    tr.reset_unknown_report()


def test_describe_never_raises_on_junk():
    for row in ({}, {"source": None}, {"source": "spend_ledger", "usd": object()}, {"payload": "not json {"},
                {"source": "action_layer_history", "event": "step_failed", "step_index": "x"},
                {"source": "operator_audit", "method": None}, {"source": "who_knows", "kind": 5}):
        title, detail, severity = tr.describe(row)
        assert isinstance(title, str) and title and isinstance(detail, str) and severity in {"info", "warn", "error"}
    tr.reset_unknown_report()


def test_event_severity_and_details():
    t, d, s = tr.describe({"source": "observation_events", "kind": "node_error", "node_key": "script",
                           "payload": {"error_type": "ValueError", "rule": "R7"}})
    assert s == "error" and "R7" in t and "ValueError" in d and "script" in d
    _, _, s = tr.describe({"kind": "node_result_verification", "payload": {"status": "failed"}})
    assert s == "error"
    _, _, s = tr.describe({"kind": "node_result_verification", "payload": {"status": "passed"}})
    assert s == "info"
    t, _, _ = tr.describe({"kind": "runtime_wait", "payload": {"reason": "failure_sleep", "initiator": "worker"}})
    assert "пауза после сбоя" in t
    t, _, _ = tr.describe({"kind": "runtime_check", "payload": {"check": "regen_budget", "status": "failed"}})
    assert t == "лимит повторных генераций: не пройдена"
    t, _, _ = tr.describe({"kind": "runtime_check", "payload": {"check": "schema"}})
    assert "неизвестно" in t


def test_money_rows_and_secrets_are_not_leaked():
    secret = "sk-" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4"
    title, detail, sev = tr.describe({"source": "spend_ledger", "kind": "actual", "usd": 0.5, "provider": "kie",
                                      "note": f"ключ {secret} в заметке"})
    assert "$0.5000" in detail and "kie" in detail
    assert secret not in detail
    t, d, s = tr.describe({"source_table": "spend_ledger", "kind": "event", "source": "credit_error", "note": ""})
    assert s == "error" and "ошибка кредитов провайдера" in t
    big = tr.describe({"source": "action_layer_history", "event": "step_failed", "error": "x" * 5000})[1]
    assert len(big) <= 420


def test_operator_and_notification_and_publication_rows():
    t, d, s = tr.describe({"source": "operator_audit", "method": "POST", "route_template": "/api/projects/{id}/stop",
                           "outcome": "denied", "actor": "operator"})
    assert t == "Оператор остановил: проект" and s == "warn" and "отказано в доступе" in d
    t, _, _ = tr.describe({"source": "operator_audit", "method": "POST"})
    assert t == "Оператор выполнил действие"
    t, _, _ = tr.describe({"source": "operator_audit", "method": "POST", "summary_ru": "Нажата кнопка «Стоп»"})
    assert t == "Нажата кнопка «Стоп»"
    t, d, s = tr.describe({"source": "notification_log", "kind": "telegram", "status": "failed", "error": "timeout"})
    assert t == "Сообщение в Telegram: не доставлено" and s == "error" and "timeout" in d
    t, _, s = tr.describe({"source": "publications", "status": "unconfirmed", "platform": "youtube"})
    assert "не подтверждена" in t and s == "warn"
    t, _, s = tr.describe({"source": "node_runs", "status": "failed", "node_key": "n1", "error": "упало"})
    assert s == "error" and "n1" in tr.describe({"source": "node_runs", "status": "failed", "node_key": "n1"})[1]
    t, d, s = tr.describe({"source": "provider_task_ledger", "event": "intent", "status": "submit_unknown", "provider": "kie"})
    assert s == "warn" and "отправка не подтверждена" in d
    t, d, s = tr.describe({"source": "jobs", "status": "interrupted"})
    assert s == "warn" and "прерван" in t


def test_archive_rollup_row_has_a_human_title():
    title, detail, sev = tr.describe({"source": "spend_ledger", "kind": "archive", "usd": 1.21, "provider": "kie"})
    assert title.startswith("Свёртка архива") and "archive" not in title and "$1.2100" in detail and sev == "info"


def test_short_source_aliases_work():
    a = tr.describe({"source": "spend", "kind": "reserve", "usd": 1})
    b = tr.describe({"source": "spend_ledger", "kind": "reserve", "usd": 1})
    assert a == b


def test_messages_ru_history_helper():
    assert messages_ru.history_event_ru("step_succeeded") == "Шаг плана выполнен"
    assert messages_ru.history_event_ru("new_unknown_code") == "new_unknown_code"
    assert messages_ru.validation_ru("$.x: required") == "$.x: обязательное поле не заполнено"


TS_FILE = ROOT / "web" / "src" / "lib" / "observation-view.ts"


def _ts_map(name: str) -> dict[str, str]:
    src = TS_FILE.read_text(encoding="utf-8")
    m = re.search(rf"export const {name}: Record<string, string> = \{{\n(.*?)\n\}};", src, re.S)
    assert m, name
    out = {}
    for line in m.group(1).splitlines():
        km = re.match(r'\s+([a-z_]+): (".*"),$', line)
        assert km, line
        out[km.group(1)] = json.loads(km.group(2))
    return out


@pytest.mark.parametrize("ts_name, py", [("EVENT_KIND_LABELS", "EVENT_LABELS_RU"), ("CHECK_NAMES_RU", "CHECK_NAMES_RU"),
                                         ("WAIT_REASONS_RU", "WAIT_REASONS_RU"), ("STATUS_WORDS_RU", "STATUS_WORDS_RU")])
def test_frontend_tables_equal_server_tables(ts_name, py):
    assert _ts_map(ts_name) == getattr(tr, py)


def test_frontend_event_label_equals_server_for_samples(tmp_path):
    node = shutil.which("node")
    ts_pkg = ROOT / "web" / "node_modules" / "typescript" / "lib" / "typescript.js"
    if not node or not ts_pkg.exists():
        pytest.skip("node или typescript не установлены")
    samples = [
        ("observer_control", {"action": "stopped", "initiator": "observation_api"}),
        ("runtime_check", {"check": "regen_budget", "status": "failed"}), ("runtime_check", {"check": "schema"}),
        ("parse_result", {"contract": "native_split", "fallback_used": True, "dash_rejected": True}),
        ("parse_result", {"parsed": True, "schema_valid": False}), ("parse_result", {}),
        ("rule_diary", {"rules": {"a": 1, "b": 2}, "stop_rule": "R1"}),
        ("node_result_verification", {"status": "unknown"}), ("node_result_verification", {"status": "passed"}),
        ("node_error", {"rule": "R9"}), ("node_error", {}), ("domain_check", {"verdict": "pass"}),
        ("node_change", {"transitions": [{"from": "a", "to": "b", "initiator": "worker"}]}), ("node_change", {}),
        ("model_end", {"usage": {"input_tokens": 3, "output_tokens": 4}}), ("model_end", {}),
        ("runtime_wait", {"reason": "queue_predecessor", "initiator": "worker"}), ("runtime_wait", {}),
        ("project_control", {"user_stop": True}), ("project_control", {"user_stop": False}), ("project_control", {}),
        ("media_phase", {"phase": "poll"}), ("media_failure", {"phase": "download"}), ("media_failure", {}),
        ("human_approval_pending", {"kind": "hero"}), ("planner_successor", {"from": "a", "to": "b"}),
        ("provider_fallback", {"provider": "eleven"}), ("json_retry", {"text": "x"}), ("brand_new_kind", {}),
        ("watchdog", {}), ("decision_model_error", {}),
    ]
    script = tmp_path / "run.mjs"
    script.write_text(
        "import fs from 'node:fs';import {createRequire} from 'node:module';\n"
        f"const require=createRequire({json.dumps(str(ROOT / 'web') + '/')});const ts=require('typescript');\n"
        f"const src=fs.readFileSync({json.dumps(str(TS_FILE))},'utf8');\n"
        "const js=ts.transpileModule(src,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText;\n"
        "const m=await import('data:text/javascript;base64,'+Buffer.from(js).toString('base64'));\n"
        f"const samples={json.dumps(samples, ensure_ascii=False)};\n"
        "console.log(JSON.stringify(samples.map(([kind,payload])=>m.eventLabel({kind,payload}))));\n",
        encoding="utf-8")
    done = subprocess.run([node, str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert done.returncode == 0, done.stderr
    got = json.loads(done.stdout)
    want = [tr.event_title_ru(k, p) for k, p in samples]
    tr.reset_unknown_report()
    assert got == want


ROUTERS = ("projects", "prompt_files", "artifacts", "project_ops", "hitl")


def _http_detail_literals(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "HTTPException":
            for kw in node.keywords:
                if kw.arg == "detail":
                    yield node.lineno, kw.value


def test_router_http_details_are_russian():
    bad = []
    total = 0
    for name in ROUTERS:
        path = APP / "web" / "routers" / f"{name}.py"
        for line, value in _http_detail_literals(path):
            if not isinstance(value, (ast.Constant, ast.JoinedStr, ast.BoolOp)):
                continue
            texts = [n.value for n in ast.walk(value) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
            if not texts:
                continue
            total += 1
            if not any(CYR.search(t) for t in texts):
                bad.append(f"{name}.py:{line}: {texts}")
    assert total >= 60, total
    assert not bad, "английский detail в роутере: " + "; ".join(bad)


def test_router_status_codes_are_unchanged_for_not_found():
    from fastapi import HTTPException
    src = (APP / "web" / "routers" / "hitl.py").read_text(encoding="utf-8")
    assert 'status_code=404, detail="Запрос подтверждения не найден"' in src
    assert 'status_code=400, detail="Решение по этому запросу уже принято"' in src
    assert HTTPException


def test_w4_new_event_kinds_check_names_and_notify_statuses():
    tr.reset_unknown_report()
    for kind in ("sfx_skipped", "sfx_node_degraded", "audio_step_failure", "audio_result_verification",
                 "scene_design_skipped", "operator_check_not_checked", "operator_stub_output"):
        title, _, severity = tr.describe({"source": "observation_events", "kind": kind, "payload": {}})
        assert CYR.search(title) and severity in {"warn", "error", "info"}, kind
    title, _, _ = tr.describe({"source": "observation_events", "kind": "runtime_check",
                               "payload": {"check": "auto_approve_without_review", "status": "unchecked"}})
    assert "автоодобрение без проверки" in title and "не проверено" in title
    title, _, _ = tr.describe({"source": "observation_events", "kind": "runtime_check",
                               "payload": {"check": "observe_only_split_result", "status": "failed"}})
    assert "observe_only" not in title
    for status, text in (("notified", "уведомлён"), ("notify_failed", "не доставлено")):
        title, _, _ = tr.describe({"source": "action_v1_records", "action_kind": "incident_notify", "status": status})
        assert text in title
    assert tr.unknown_report() == {}


def test_w4_observe_only_verification_does_not_claim_the_run_was_held():
    for status in ("failed", "unknown"):
        title, _, _ = tr.describe({"source": "observation_events", "kind": "node_result_verification",
                                   "payload": {"status": status, "observe_only": True, "run_held": False}})
        assert "запуск удержан" not in title and "запуск не остановлен" in title
        held, _, _ = tr.describe({"source": "observation_events", "kind": "node_result_verification",
                                  "payload": {"status": status}})
        assert "запуск удержан" in held
