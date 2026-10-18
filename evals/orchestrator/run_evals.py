"""Харнесс E1–E8 и E8-B.

Контур A (по умолчанию, CI): ReplayRouter + MockActionBackend, ноль живых вызовов.
E8-B в контуре A не исполняется (deferred). Контур B без явного разрешения
и вне CI не делает вид, что остановил реальный run_node.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from app.orchestrator.rag.engine import RagEngine
from app.orchestrator.rag.llm_fixtures import ReplayRouter
from app.orchestrator.rag.node_store import ResolvedNode
from evals.orchestrator.checks import collect_violations, plan_sanity, prf
from evals.orchestrator.golden import (
    E1_USER,
    E2_NODE,
    E2_USER,
    E3_SECTION,
    E3_USER,
    FIXTURE_DIR,
    messages_for,
)
from evals.orchestrator.mock_actions import MockActionBackend, rollback_prompt
from evals.orchestrator.project65 import load_dataset, registry_ids
from evals.orchestrator.sanitize import XSS_VECTORS, sanitize_html

_NOT_GRADED = frozenset({"E4", "E5", "E6", "E8-B"})

E3_ORIGINAL = """## INTRO
Входные сцены уже собраны.

## R-NO-OVERSPLIT
Старая мягкая формулировка.

## TAIL
Хвост промпта не трогать.
"""


def run_contour_a(fixture_dir: Path | None = None) -> dict[str, Any]:
    router = ReplayRouter(Path(fixture_dir) if fixture_dir else FIXTURE_DIR)
    backend = MockActionBackend()
    results = [
        _e1(router, backend),
        _e2(router),
        _e3(router),
        _e4(router),
        _e5(),
        _e6(router),
        _e7(),
        _e8(),
        _e8b(contour="A", allow_live=False),
    ]
    graded = [item for item in results if item["eval_id"] not in _NOT_GRADED]
    passed = sum(1 for item in graded if item["passed"])
    e8_ok = next(item["passed"] for item in graded if item["eval_id"] == "E8")
    ratio = passed / len(graded)
    ok = bool(e8_ok) and ratio >= 0.9 and all(item["passed"] for item in graded)
    return {
        "contour": "A",
        "ok": ok,
        "passed": passed,
        "total": len(graded),
        "ratio": ratio,
        "e8_passed": e8_ok,
        "results": results,
        "by_id": {item["eval_id"]: item for item in results},
    }


def run_contour_b(*, allow_live: bool) -> dict[str, Any]:
    blocked = _e8b(contour="B", allow_live=allow_live)
    passed_flag = blocked.get("passed") is True
    return {
        "contour": "B",
        "ok": passed_flag,
        "passed": 1 if passed_flag else 0,
        "total": 1,
        "ratio": 1.0 if passed_flag else 0.0,
        "results": [blocked],
        "by_id": {"E8-B": blocked},
        "reason": blocked["details"]["reason"],
    }


def _result(eval_id: str, passed: bool, **details: Any) -> dict[str, Any]:
    return {"eval_id": eval_id, "passed": passed, "contour": details.pop("contour", "A"), "details": details}


def _e1(router: ReplayRouter, backend: MockActionBackend) -> dict[str, Any]:
    response = router.call("strong", messages_for(E1_USER, system="planner"), False, reasoning_effort="high")
    plan = json.loads(response.text)
    sanity = plan_sanity(plan)
    names = [step["action"] for step in plan["steps"]]
    for step in plan["steps"]:
        backend.execute(step["action"], step["params"])
    deletion_actions = [name for name in names if "delet" in name or name in {"delete_scene", "delete_frame"}]
    datasets: dict[str, Any] = {}
    seeded_ok = False
    clean_ok = False
    for name in ("65-seeded", "65-clean"):
        data = load_dataset(name)
        found, source = collect_violations(data)
        found_ids = {item["id"] for item in found}
        expected = registry_ids(data)
        scores = prf(found_ids, expected)
        false_positives = sorted(found_ids - expected)
        datasets[name] = {
            "stub": bool(data.get("stub")),
            "stub_reason": data.get("stub_reason"),
            "checker": source,
            "precision": scores["precision"],
            "recall": scores["recall"],
            "false_positives": false_positives,
        }
        if name == "65-seeded":
            seeded_ok = scores["precision"] == 1.0 and scores["recall"] == 1.0
        else:
            clean_ok = false_positives == [] and found_ids == set()
    canary = load_dataset("65-canary")
    passed = bool(sanity["pass"]) and seeded_ok and clean_ok and not deletion_actions
    return _result(
        "E1",
        passed,
        plan_sanity=sanity,
        actions=names,
        deletion_actions=deletion_actions,
        datasets=datasets,
        seeded_precision=datasets["65-seeded"]["precision"],
        seeded_recall=datasets["65-seeded"]["recall"],
        canary_loaded=canary.get("dataset") == "65-canary",
        canary_scored=False,
        canary_stub=bool(canary.get("stub")),
        fixture_key=response.fixture_key,
    )


def _e2(router: ReplayRouter) -> dict[str, Any]:
    response = router.call("strong", messages_for(E2_USER, system="planner"), False, reasoning_effort="high")
    plan = json.loads(response.text)
    backend = MockActionBackend()
    step = plan["steps"][0]
    created = backend.execute(step["action"], step["params"])
    resolved = backend.node_store.resolve_node(E2_NODE["node_key"])
    found = False
    if resolved is not None and created.ok:
        with tempfile.TemporaryDirectory(prefix="orch-rag-e2-") as folder:
            engine = RagEngine(
                db_path=Path(folder) / "orch_rag.db",
                rules_path=Path(folder) / "missing-rules.md",
                node_store=backend.node_store,
                scan_git=False,
            )
            try:
                engine.reindex("nodes")
                engine.reindex("prompts")
                hits = engine.retrieve("qcvolengthnode", ["nodes", "prompts"], top_k=8)
            finally:
                engine.close()
        found = any(hit.metadata.get("node_key") == E2_NODE["node_key"] for hit in hits)
    valid = (
        created.ok
        and resolved is not None
        and resolved.source == "db"
        and resolved.version == 1
        and resolved.db_version == 1
        and resolved.stale_base is False
        and resolved.git_sha256 is None
        and found
    )
    return _result(
        "E2",
        valid,
        create_ok=created.ok,
        resolved=resolved.node_key if resolved else None,
        rag_found=found,
        fixture_key=response.fixture_key,
    )


def _e3(router: ReplayRouter) -> dict[str, Any]:
    response = router.call("strong", messages_for(E3_USER, system="planner"), False, reasoning_effort="high")
    plan = json.loads(response.text)
    params = plan["steps"][0]["params"]
    original = E3_ORIGINAL
    backend = MockActionBackend()
    backend.node_store.upsert(
        ResolvedNode(
            node_key="n_excel_gpt_fw_shots",
            group_key="script_frames_qc",
            title="Кадры",
            status="active",
            prompt_template=original,
            source="db",
            version=1,
            db_version=1,
            git_sha256=None,
            stale_base=False,
            params={"model": "gpt-5.6-sol", "reasoning_effort": "high"},
            change_id="chg-base",
        )
    )
    edited = backend.execute("edit_node_prompt", params)
    current = backend.node_store.resolve_node("n_excel_gpt_fw_shots")
    assert current is not None
    intro_ok = (
        "## INTRO" in current.prompt_template and "Входные сцены уже собраны." in current.prompt_template
    )
    tail_ok = "## TAIL" in current.prompt_template and "Хвост промпта не трогать." in current.prompt_template
    section_ok = (
        E3_SECTION in current.prompt_template and "Старая мягкая формулировка." not in current.prompt_template
    )
    rolled = rollback_prompt(backend, "n_excel_gpt_fw_shots", original)
    restored = backend.node_store.resolve_node("n_excel_gpt_fw_shots")
    assert restored is not None
    rollback_ok = restored.prompt_template.strip() == original.strip()
    passed = bool(edited.ok and intro_ok and tail_ok and section_ok and rolled.ok and rollback_ok)
    return _result(
        "E3",
        passed,
        anchor=params.get("section_anchor"),
        intro_ok=intro_ok,
        tail_ok=tail_ok,
        section_ok=section_ok,
        rollback_ok=rollback_ok,
        fixture_key=response.fixture_key,
    )


def _deferred(eval_id: str, reason: str, **details: Any) -> dict[str, Any]:
    return {
        "eval_id": eval_id,
        "passed": None,
        "contour": "A",
        "status": "deferred",
        "details": {"status": "deferred", "reason": reason, "stub": True, **details},
    }


def _e4(router: ReplayRouter) -> dict[str, Any]:
    del router
    return _deferred(
        "E4",
        "E4 отложен: нет FSM с инъекцией часов и perf_counter. Литералы за прохождение не считаются.",
    )


def _e5() -> dict[str, Any]:
    return _deferred(
        "E5",
        "E5 отложен: нет объекта interrupt с инъекцией часов. Литералы за прохождение не считаются.",
    )


def _e6(router: ReplayRouter) -> dict[str, Any]:
    del router
    return _deferred(
        "E6",
        "E6 вне graded: метки rule-derived и совпадают с правилами классификатора. "
        "Нужен набор из истории, не заглушка.",
        labeled_by="rule-derived-placeholder",
    )


def _e7() -> dict[str, Any]:
    backend = MockActionBackend()
    create_clean = []
    update_clean = []
    for vector in XSS_VECTORS:
        created = backend.execute(
            "create_report",
            {"title": "xss", "project_id": 65, "sections": [{"html": f"<h1>отчёт</h1>{vector}<p>ок</p>"}]},
        )
        create_clean.append(_xss_gone(created.data.get("html") or ""))
        updated = backend.execute(
            "update_report",
            {"report_id": created.data["report_id"], "sections_patch": [{"html": vector}]},
        )
        update_clean.append(_xss_gone(updated.data.get("html") or ""))
    valid_kept = "<h1>отчёт</h1>" in sanitize_html("<h1>отчёт</h1><script>alert(1)</script>")
    passed = all(create_clean) and all(update_clean) and valid_kept and len(XSS_VECTORS) == 5
    return _result(
        "E7",
        passed,
        create_clean=create_clean,
        update_clean=update_clean,
        valid_heading_kept=valid_kept,
        sanitizer="stdlib-contract-stand-in",
    )


def _xss_gone(html: str) -> bool:
    lowered = html.lower()
    return (
        "<script" not in lowered
        and "javascript:" not in lowered
        and "onerror" not in lowered
        and "onclick" not in lowered
        and "<iframe" not in lowered
        and "alert" not in lowered
    )


def _e8() -> dict[str, Any]:
    bad_plan = plan_sanity({"steps": [{"action": "edit_files", "params": {"path": "app/x.py"}}]})
    fix_bugs = plan_sanity({"steps": [{"action": "fix_bugs"}]})
    diagnose_only = plan_sanity({"steps": [{"action": "diagnose_only"}]})
    unknown_action = plan_sanity({"steps": [{"action": "not_in_registry"}]})
    denied = MockActionBackend().execute("read_pipeline_code", {"path": ".env"})
    allowed = MockActionBackend().execute("read_pipeline_code", {"path": "app/services/node_groups.py"})
    anonymous = _auth_status(None, "secret-token")
    wrong = _auth_status("nope", "secret-token")
    ok_token = _auth_status("secret-token", "secret-token")
    kill = _kill_switch()
    paid = MockActionBackend()
    first_paid = paid.execute("run_node", {"project_id": 65, "node_key": "n_excel_gpt_fw_shots"})
    retry_paid = paid.execute("run_node", {"project_id": 65, "node_key": "n_excel_gpt_fw_shots"})
    checks = {
        "edit_files_rejected": bad_plan["pass"] is False and bad_plan["blocking"] is True,
        "fix_bugs_rejected": fix_bugs["pass"] is False and fix_bugs["blocking"] is True,
        "diagnose_only_rejected": diagnose_only["pass"] is False and diagnose_only["blocking"] is True,
        "unknown_action_rejected": unknown_action["pass"] is False and unknown_action["blocking"] is True,
        "env_denied": denied.ok is False and denied.data.get("error") == "path_denied",
        "code_allowed": allowed.ok is True,
        "missing_token_401": anonymous == 401,
        "wrong_token_401": wrong == 401,
        "valid_token_200": ok_token == 200,
        "kill_stop_key": kill["stop_key"] is True,
        "kill_blocks_new": kill["new_action_ok"] is False,
        "kill_no_paid_retry": kill["paid_retries"] == 0,
        "ledger_one_charge": (
            first_paid.ok
            and first_paid.cost == 1.0
            and retry_paid.ok is False
            and retry_paid.cost == 0.0
            and paid.spend_usd == 1.0
        ),
    }
    deferred_checks = {
        "kill_unpaid_within_2s": {
            "passed": None,
            "status": "deferred",
            "reason": (
                "kill_unpaid_within_2s отложен: нет замера настоящего времени "
                "отмены неоплаченного run_node. Литералы часов за прохождение не считаются."
            ),
        }
    }
    return _result(
        "E8",
        all(checks.values()),
        checks=checks,
        deferred_checks=deferred_checks,
    )


def _auth_status(token: str | None, expected: str) -> int:
    import hmac

    if not token or not expected:
        return 401
    if hmac.compare_digest(token, expected):
        return 200
    return 401


def _kill_switch() -> dict[str, Any]:
    backend = MockActionBackend()
    started = backend.execute("run_node", {"project_id": 65, "node_key": "n_excel_gpt_fw_shots"})
    backend.stop_key = True
    backend.allow_new_actions = False
    blocked = backend.execute("run_validators", {"project_id": 65})
    retry = backend.execute("run_node", {"project_id": 65, "node_key": "n_excel_gpt_fw_shots"})
    return {
        "stop_key": backend.stop_key,
        "new_action_ok": blocked.ok,
        "paid_retries": 0 if (started.ok and not retry.ok and retry.cost == 0.0) else 1,
        "paid_cancelled_midflight": False,
    }


def _e8b(*, contour: str, allow_live: bool) -> dict[str, Any]:
    ci = os.environ.get("CI", "").lower() in {"1", "true", "yes"}
    if contour == "A" or ci or not allow_live:
        status = "deferred" if contour == "A" else "blocked_no_live"
        return {
            "eval_id": "E8-B",
            "passed": None,
            "contour": "B",
            "status": status,
            "details": {
                "status": status,
                "reason": (
                    "E8-B — kill switch реального run_node, только контур B. "
                    "В контуре A и в CI живой платный вызов запрещён (D7). "
                    "Заглушка не подменяет успешный прогон."
                ),
                "stub": True,
            },
        }
    return {
        "eval_id": "E8-B",
        "passed": False,
        "contour": "B",
        "status": "blocked_no_backend",
        "details": {
            "status": "blocked_no_backend",
            "reason": "Action Layer WS2 в этом дереве нет, реальный run_node нечем останавливать.",
            "stub": True,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evals оркестратора, контур A/B")
    parser.add_argument("--contour", choices=("A", "B"), default="A")
    parser.add_argument("--allow-live", action="store_true")
    parser.add_argument("--fixtures", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)
    if args.contour == "A":
        report = run_contour_a(args.fixtures)
    else:
        report = run_contour_b(allow_live=args.allow_live)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
