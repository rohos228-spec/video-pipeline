"""Запись золотых LLM-фикстур контура A. Внутри только ScriptedRouter."""

from __future__ import annotations

import json
from pathlib import Path

from app.orchestrator.rag.llm_fixtures import RecordingRouter, ScriptedRouter
from app.orchestrator.rag.types import LLMResponse, LLMUsage
from evals.orchestrator.classifier import classifier_messages
from evals.orchestrator.e6_dataset import e6_queries

FIXTURE_DIR = Path(__file__).resolve().parent / "llm_fixtures"
RECORDED_AT = "2026-10-17T00:00:00Z"

E1_USER = "Запусти Кадры для сцен 3–5 и проверь закадр"
E2_USER = "Создай ноду QC-отчёта по длинам закадра"
E3_USER = "Усили R-NO-OVERSPLIT в промпте scenes_to_frames"
E4_USER = "Посреди задачи: сцены 6–8, уже сделанные кадры не пересчитывай"

E2_NODE = {
    "node_key": "n_qc_vo_length",
    "group": "script_frames_qc",
    "title": "QC длин закадра",
    "prompt_template": (
        "Проверь, что закадр сцены 200–300 символов. Якорь qcvolengthnode. Ничего не удаляй."
    ),
    "params": {"model": "gpt-5.6-sol", "reasoning_effort": "high"},
}

E3_SECTION = "Соседние кадры не дробить. R-NO-OVERSPLIT: один смысл — один набор, без лишней нарезки."


def _response(text: str, *, tier: str, call_id: str) -> LLMResponse:
    return LLMResponse(
        text=text,
        call_id=call_id,
        model="gpt-5.6-sol",
        provider="scripted",
        tier=tier,
        finish_reason="stop",
        usage=LLMUsage(prompt_tokens=32, completion_tokens=64, reasoning_tokens=0),
        cost_usd=0.0,
        latency_ms=1,
        fallback_index=0,
        deterministic=True,
        fixture_key=None,
    )


def _scripted(
    tier: str,
    messages: list[dict[str, str]],
    stream: bool = False,
    *,
    temperature: float | None = None,
    max_tokens: int | None = None,
    reasoning_effort: str | None = None,
) -> LLMResponse:
    del stream, temperature, max_tokens, reasoning_effort
    user = messages[-1]["content"]
    system = messages[0]["content"] if messages else ""
    if system == "classifier":
        gold = next(row for row in e6_queries() if row["text"] == user and not row["deterministic"])
        payload = {"tier": gold["tier"], "mode": gold["mode"]}
        return _response(json.dumps(payload, ensure_ascii=False), tier=tier, call_id="script-e6")
    if user == E1_USER:
        plan = {
            "steps": [
                {"action": "read_pipeline_code", "params": {"path": "app/services/node_groups.py"}},
                {"action": "get_node", "params": {"node_key": "n_excel_gpt_fw_shots"}},
                {
                    "action": "run_node",
                    "params": {"project_id": 65, "node_key": "n_excel_gpt_fw_shots", "scenes": [3, 4, 5]},
                },
                {
                    "action": "run_validators",
                    "params": {"project_id": 65, "validators": ["vo_length", "no_deletions"]},
                },
            ]
        }
        return _response(json.dumps(plan, ensure_ascii=False), tier=tier, call_id="script-e1")
    if user == E2_USER:
        plan = {"steps": [{"action": "create_node", "params": E2_NODE}]}
        return _response(json.dumps(plan, ensure_ascii=False), tier=tier, call_id="script-e2")
    if user == E3_USER:
        plan = {
            "steps": [
                {
                    "action": "edit_node_prompt",
                    "params": {
                        "node_key": "n_excel_gpt_fw_shots",
                        "patch_mode": "replace_section",
                        "section_anchor": "R-NO-OVERSPLIT",
                        "content": E3_SECTION,
                        "reason": "усилить запрет лишней нарезки",
                    },
                }
            ]
        }
        return _response(json.dumps(plan, ensure_ascii=False), tier=tier, call_id="script-e3")
    if user == E4_USER:
        plan = {
            "resume_from": 2,
            "steps": [
                {"action": "get_node", "params": {"node_key": "n_excel_gpt_fw_shots"}},
                {
                    "action": "run_node",
                    "params": {"project_id": 65, "node_key": "n_excel_gpt_fw_shots", "scenes": [6, 7, 8]},
                },
            ],
        }
        return _response(json.dumps(plan, ensure_ascii=False), tier=tier, call_id="script-e4")
    raise KeyError(user)


def messages_for(user: str, *, system: str) -> list[dict[str, str]]:
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def write_golden_fixtures(dest: Path | None = None) -> Path:
    directory = Path(dest) if dest is not None else FIXTURE_DIR
    directory.mkdir(parents=True, exist_ok=True)
    recorder = RecordingRouter(ScriptedRouter(_scripted), directory, clock=lambda: RECORDED_AT)
    for user in (E1_USER, E2_USER, E3_USER, E4_USER):
        recorder.call(
            "strong",
            messages_for(user, system="planner"),
            False,
            reasoning_effort="high",
        )
    # D6 (fast-tier без high) в правиле 2 не подтверждён. Классификатор пишется как strong + high.
    for row in e6_queries():
        if row["deterministic"]:
            continue
        recorder.call("strong", classifier_messages(row["text"]), False, reasoning_effort="high")
    return directory
