"""Native text nodes ONE AT A TIME on Vibecode chat/completions: real app, incident -> Decision -> recovery.

Sibling of scripts/e2e_vibecode_script_frames_qc.py (imported, NOT edited: its pure helpers, constants and the
Decision stub are reused) and of scripts/e2e_text_nodes_recovery.py (the Kie version of the same idea). Here the
provider is Vibecode and the node is chosen with --node: plan, script, split, image_prompts, sfx_plan,
animation_prompts, sd_agent (characters / world, --sd-agent), sd_assemble.

Three modes, as in the sibling script.
DEFAULT (offline): every non-loopback HTTP request is refused inside the httpx send hook (RuntimeError) and
counted; nothing leaves the machine, nothing is paid for; credentials are synthetic and no .env file is read.
A local stub plays Vibecode: the first worker attempt gets a broken answer, the Decision stub proposes
read_events -> retry_same -> finish, the recovery attempt gets the local valid answer of the node.
--dry: the live-shaped flow with every provider-bound request (Decision, recovery worker) refused and counted, no
network at all; proves the seeding of the node, the injection plumbing and the caps. --env-file is optional.
--live (PAID, capped; this script was NOT run in live mode when written): the broken answer of the first worker
attempts is injected locally (reality cannot be made to return broken JSON on demand; free), and ONLY the Decision
request(s) and the RECOVERY worker request go to the real Vibecode /v1/chat/completions. Hard caps:
--max-real-posts (default 6), --max-usd (default 0.10, estimated from the recorded usage with
api_tracker_hook.vibecode_cost x3), only vibecode.moe (or the configured relay host) may receive a request, any
other request is refused and counted. Keys are read from --env-file ONLY by the names in LIVE_ENV_NAMES and are
never printed or written.

What is real: the whole application in-process (FastAPI, worker, observer, diagnosis, Decision supervisor,
recovery verifier), the real native executor of the chosen node and its prompts, the gpt_api Vibecode transport
(URL, headers, SSE parser, observe_model). In --live also the Decision model and the recovery answer.
What is NOT real: the seeded project (synthetic, built here, not a production project), the first worker attempts
(broken locally on purpose), in offline the Decision and the recovery answer (stubs). The quality of a real
recovery answer is only checked by the node's own validators, not judged for meaning.

Not every node can run here; see UNSUPPORTED (printed by --list-nodes) for the nodes that cannot and why.

Usage (repository root, project venv):
    python scripts/e2e_vibecode_text_nodes.py --list-nodes
    python scripts/e2e_vibecode_text_nodes.py --node plan --work-dir <empty dir>
    python scripts/e2e_vibecode_text_nodes.py --node plan --work-dir <dir> --dry
    python scripts/e2e_vibecode_text_nodes.py --node script --work-dir <dir> --live --env-file <env file>
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import sqlite3
import sys
import time
import uuid as _uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

_HERE = Path(__file__).resolve().parent
ROOT = _HERE.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import e2e_guard as guard


def _load_sibling(name: str):
    spec = importlib.util.spec_from_file_location(name + "_for_text_nodes", _HERE / (name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


qc = _load_sibling("e2e_vibecode_script_frames_qc")

PORT = 18767
MALFORMED = qc.MALFORMED
LOOPBACK_HOSTS = qc.LOOPBACK_HOSTS
PROVIDER_HOSTS = qc.PROVIDER_HOSTS
TERMINAL_DECISION = qc.TERMINAL_DECISION
LIVE_ENV_NAMES = qc.LIVE_ENV_NAMES
COST_MARKUP = qc.COST_MARKUP
RECOVERY_INCIDENTS = {"invalid_json", "missing_payload"}
EXPECTED_HITL: dict[str, tuple[str, ...]] = {"plan": ("approve_plan",), "script": ("approve_script",)}

TEXT = ("Синтетический текст: человек находит папку, читает записку и возвращает её на стол. " * 6).strip()
_WORDS = TEXT.split()
VO_PARTS = (" ".join(_WORDS[: len(_WORDS) // 2]), " ".join(_WORDS[len(_WORDS) // 2:]))
IMAGE_PROMPT = ("Синтетический кадр: человек читает записку у стола. Полная сцена, освещение, план камеры и фон. " * 4).strip()
SCENE_VO = ("Альфа долго открывает старую книгу и читает вслух первую главу.",
            "Бета медленно закрывает книгу и уходит из читального зала.")

NODES = ("plan", "script", "split", "image_prompts", "sfx_plan", "animation_prompts", "sd_agent", "sd_assemble")
SD_AGENTS = ("characters", "world")

UNSUPPORTED: dict[str, str] = {
    "split": ("при неразбираемом ответе нативный split сам делит текст локально (в журнале «кадров из GPT/fallback»), "
              "нода завершается done без инцидента invalid_json: сценарий «сломанная первая попытка, Decision, "
              "восстановление» на ней не воспроизводится. Проверено сухим прогоном стенда: 1 запрос, node done, "
              "6 кадров. Другой сбой (потеря текста при валидном JSON) повтором не лечится и в этот стенд не входит."),
    "image_prompts": ("после первой же проваленной попытки диагностика добавляет инцидент verification_hold («у кадров промпт "
                      "пустой или короче 200 символов, повтор не поможет, нужна правка человека»), запуск переходит в "
                      "удержание (execution_held), рабочий процесс не берёт ноду, нативные повторы не доходят до порога, "
                      "Decision не создаётся. Проверено сухим прогоном стенда: после 1 из 9 ошибок нода остаётся queued. "
                      "Это поведение приложения, а не стенда."),
}

KNOWN_BLOCKERS: dict[str, str] = {}


def _frame(number: int, vo: str, *, image_prompt: str | None = None, duration: float | None = None) -> dict:
    return {"number": number, "uuid": (f"{number:02x}" * 12), "voiceover_text": vo, "image_prompt": image_prompt,
            "duration_seconds": duration}


def _ops_project(field_name: str) -> Callable[[list[dict]], str]:
    return lambda _frames: json.dumps({"ops": [{"target": "project", "fields": {field_name: TEXT}}]}, ensure_ascii=False)


def _ans_split(_frames: list[dict]) -> str:
    return json.dumps({"ops": [{"target": "replace_frames", "frames": [{"закадр": vo} for vo in VO_PARTS]}]},
                      ensure_ascii=False)


def _ans_image_prompts(frames: list[dict]) -> str:
    return json.dumps({"ops": [{"frame_uuid": f["uuid"], "fields": {"промт_картинки": IMAGE_PROMPT}} for f in frames]},
                      ensure_ascii=False)


def _ans_sfx(frames: list[dict]) -> str:
    kinds = ("whoosh", "hit")
    events = [{"frame_number": f["number"], "t_start": 0.5 + 3.0 * i, "duration": 1.0, "kind": kinds[i % 2],
               "prompt": "soft paper whoosh" if i % 2 == 0 else "light desk hit", "gain": 0.5, "duck": False}
              for i, f in enumerate(frames)]
    return json.dumps({"events": events}, ensure_ascii=False)


GOOD_CHARACTERS = {"characters": [{"id": "c01", "имя": "Альфа", "внешность": "высокий, около 45",
                                   "одежда": "серое пальто", "характер": "сдержанный"}]}
WORLD_SKELETON = {
    "scenes": [
        {"id_scene": "scene_01", "id_cell": "cell_01", "кадры": [1], "место_id": "loc01", "суть": "чтение",
         "фокус": "чтение", "биты": [], "персонажи": [], "предметы": []},
        {"id_scene": "scene_02", "id_cell": "cell_02", "кадры": [2], "место_id": "loc01", "суть": "чтение",
         "фокус": "чтение", "биты": [], "персонажи": [], "предметы": []},
    ],
    "locations_seed": [{"id": "loc01", "name": "Читальный зал"}],
    "characters_seed": [],
    "items_seed": [],
    "threads": [],
}
GOOD_WORLD = {"locations": [{"id": "loc01", "name": "Читальный зал", "описание": "высокие окна, дубовые столы"}],
              "report": "локаций:1"}


ASM_VO = ("Альфа долго открывает старую книгу и читает вслух первую главу.",
          "Бета медленно закрывает книгу и уходит из читального зала.")
ASM_CHARACTERS = {"characters": [{"id": "c01", "имя": "Альфа", "внешность": "высокий", "одежда": "пальто",
                                  "характер": "сдержанный"}]}
ASM_WORLD = {"locations": [{"id": "loc01", "name": "Зал", "описание": "высокие окна"}]}
ASM_SCENE = {"время_сек": 8, "структура_сцены": "continuity", "тип_стыка": "none", "переход_в_сцену": "cut",
             "смысл_сцены": "чтение", "location": "loc01", "кто_в_кадре": "c01"}
ASM_ACTION = {"scenes": [
    {**ASM_SCENE, "id_scene": "scene_01", "start_words": "Альфа долго открывает",
     "end_words": "читает вслух первую главу.", "цепь_действия": "открывает книгу; читает"},
    {**ASM_SCENE, "id_scene": "scene_02", "start_words": "Бета медленно закрывает",
     "end_words": "уходит из читального зала.", "цепь_действия": "закрывает книгу; уходит"}]}
_ASM_SHOT = {"особенность_сцены": "Интерьер", "композиция": "центр", "угол": "ровный", "движение": "статика",
             "переход": "cut", "тип_стыка": "none", "набор": "", "мотив": "книга", "кто_в_кадре": "c01"}
ASM_CAMERA = {"shot_plan": [
    {**_ASM_SHOT, "id_scene": "scene_01", "phase_index": 1, "beat": "открывает",
     "цитата": "Альфа долго открывает старую книгу", "крупность": "VLS→MS→CU"},
    {**_ASM_SHOT, "id_scene": "scene_02", "phase_index": 1, "beat": "закрывает",
     "цитата": "Бета медленно закрывает книгу", "крупность": "MS→CU"}]}
ASM_STYLE = {"style_arc": [
    {"scene_hint": "Альфа долго открывает", "тип_сцены": "continuity", "освещение": "тёплый свет из окон",
     "палитра": "охра", "тон": "спокойный", "сдвиг": "нет", "сцены": ["scene_01", "scene_02"]}]}
ANIMATION_TEXT = "A continuous medium shot: the person slowly opens the blue folder on the wooden desk. NO VOICE."
ANIMATION_READY = "Ready to receive the original frame batch."


@dataclass(frozen=True)
class NodeSpec:
    node: str
    canvas_type: str
    frames: tuple = ()
    script_text: bool = False
    general_plan: bool = False
    meta: dict = field(default_factory=dict)
    env: dict = field(default_factory=dict)
    settings: dict = field(default_factory=dict)
    answer: Callable[[list[dict]], str] | None = None
    output: str = ""
    note: str = ""


SPECS: dict[str, NodeSpec] = {
    "plan": NodeSpec("plan", "plan", answer=_ops_project("общий_план"), output="projects.general_plan"),
    "script": NodeSpec("script", "script", general_plan=True, answer=_ops_project("закадровый_текст"),
                       output="projects.script_text"),
    "split": NodeSpec("split", "split", general_plan=True, script_text=True, answer=_ans_split, output="frames"),
    "image_prompts": NodeSpec("image_prompts", "image_prompts", general_plan=True, script_text=True,
                              frames=(_frame(1, VO_PARTS[0]), _frame(2, VO_PARTS[1])), answer=_ans_image_prompts,
                              output="frames.image_prompt"),
    "sfx_plan": NodeSpec("sfx_plan", "sfx_plan", general_plan=True, script_text=True,
                         frames=(_frame(1, SCENE_VO[0], duration=3.0), _frame(2, SCENE_VO[1], duration=3.0)),
                         env={"SFX_ENABLED": "true"}, answer=_ans_sfx, output="sfx_plan.json"),
    "animation_prompts": NodeSpec(
        "animation_prompts", "animation_prompts", general_plan=True, script_text=True,
        frames=(_frame(1, SCENE_VO[0], image_prompt=IMAGE_PROMPT), _frame(2, SCENE_VO[1], image_prompt=IMAGE_PROMPT)),
        answer=None, output="frames.animation_prompt"),
    "sd_agent": NodeSpec(
        "sd_agent", "sd_agent", general_plan=True, script_text=True,
        frames=(_frame(1, SCENE_VO[0], duration=3.0), _frame(2, SCENE_VO[1], duration=3.0)),
        meta={"scene_design_enabled": True}, answer=None, output="scene_design_cells"),
    "sd_assemble": NodeSpec(
        "sd_assemble", "sd_assemble", general_plan=True, script_text=True,
        frames=(_frame(1, ASM_VO[0], duration=8.0), _frame(2, ASM_VO[1], duration=8.0)),
        meta={"scene_design_enabled": True}, settings={"scene_design_assemble_chunk_frames": 2},
        answer=None, output="frames.attrs + scenes",
        note="agent checkpoints and cells are seeded by the application's own writers (post_seed)"),
}


def _assemble_answer(text: str) -> str:
    """The valid assembler answer for THIS chunk: the application's own local assembler builds it (offline only)."""
    from types import SimpleNamespace
    from app.services.scene_design import assembler as sd_assembler
    start = text.index("# ЯЧЕЙКИ АГЕНТОВ")
    start = text.index(chr(10), start) + 1
    chunk, _end = json.JSONDecoder().raw_decode(text[start:])
    kids = [SimpleNamespace(uuid=k["uuid"], voiceover_text=k.get("закадр") or "", number=k["number"])
            for scene in chunk["scenes_chrono"] for k in scene["кадры"]]
    return json.dumps(sd_assembler.build_local_assembler_payload(chunk, kids), ensure_ascii=False)


def build_answer(spec: NodeSpec, frames: list[dict], agent: str = "characters", text: str = "") -> str | None:
    """The local valid answer for the node (None = no local valid answer is known for this node).

    `text` is the request text: animation_prompts asks twice (a phase-1 message and the frame batch) and
    sd_assemble builds the answer from its own chunk."""
    if spec.node == "sd_agent":
        return json.dumps(GOOD_CHARACTERS if agent == "characters" else GOOD_WORLD, ensure_ascii=False)
    if spec.node == "animation_prompts":
        low = (text or "").lower()
        batch = [f for f in frames if str(f.get("uuid") or "").lower() and str(f["uuid"]).lower() in low]
        if not batch:
            return ANIMATION_READY
        return json.dumps({"ops": [{"frame_uuid": str(f["uuid"]), "fields": {"промт_видео": ANIMATION_TEXT}}
                                   for f in batch]}, ensure_ascii=False)
    if spec.node == "sd_assemble":
        return _assemble_answer(text)
    return spec.answer(frames) if spec.answer else None


def node_key_for(node: str, agent: str = "characters") -> str:
    return f"native-{node}-{agent}-A" if node == "sd_agent" else f"native-{node}-A"


def canvas_node(node: str, agent: str = "characters") -> dict:
    spec = SPECS[node]
    data: dict[str, Any] = {"label": "Native " + node}
    if node == "sd_agent":
        data["sd_agent"] = agent
    elif node == "sd_assemble":
        data["sd_agent"] = "assemble"
    return {"id": node_key_for(node, agent), "type": spec.canvas_type, "position": {"x": 100.0, "y": 100.0}, "data": data}


def supported_nodes() -> tuple[str, ...]:
    return tuple(n for n in NODES if n not in UNSUPPORTED)



is_loopback = qc.is_loopback
refusal_reason = qc.refusal_reason
load_env_whitelist = qc.load_env_whitelist
relay_base = qc.relay_base
live_endpoint = qc.live_endpoint
allowed_hosts = qc.allowed_hosts
real_post_refusal = qc.real_post_refusal
estimate_usd = qc.estimate_usd
live_env_overrides = qc.live_env_overrides
is_chat_completions = qc.is_chat_completions
is_kie_responses = qc.is_kie_responses
check = qc.check
verdict = qc.verdict


def env_overrides(data: Path, port: int, node: str = "plan") -> dict[str, str]:
    """Environment for the in-process app: the sibling's synthetic loopback one plus the node's own switches."""
    out = qc.env_overrides(data, port)
    out.update(SPECS[node].env)
    return out


def classify_requests(request_log: list[dict]) -> dict:
    """Which requests were real and which local (the honest part of the report)."""
    real = [r for r in request_log if r.get("real")]
    return {"real_requests": len(real), "real_routes": sorted({r["route"] for r in real}),
            "local_injected": sum(1 for r in request_log if r.get("route") == "worker" and r.get("broken")),
            "local_valid_stub": sum(1 for r in request_log if r.get("route") == "worker" and not r.get("broken")
                                    and not r.get("real")),
            "local_decision_stub": sum(1 for r in request_log if r.get("route") == "decision" and not r.get("real")),
            "refused": sum(1 for r in request_log if r.get("route") == "refused")}


def reality_report(mode: str, counters: dict, node: str) -> dict:
    """What is real and what is not in this run, stated from the counters (never 'verified live' by itself)."""
    live = mode == "live"
    real_decision = counters.get("real_decision", 0) if live else 0
    real_recovery = counters.get("real_worker_recovery", 0) if live else 0
    return {
        "real": [x for x in (
            "application, worker, observer, diagnosis, Decision supervisor and recovery verifier in-process",
            f"native executor and prompts of node {node}",
            f"{real_decision} real Decision request(s) to Vibecode" if real_decision else None,
            f"{real_recovery} real recovery request(s) to Vibecode" if real_recovery else None) if x],
        "not_real": [x for x in (
            "seeded project: synthetic, built by this script",
            "first worker attempts: broken locally on purpose (free)",
            "Decision request and recovery answer: refused, nothing was sent to a provider" if mode == "dry" else
            None if live else "Decision model and recovery answer: local stubs, nothing was sent to a provider",
            "meaning/quality of any answer: not judged, only the node's own validators") if x],
    }



def evaluate(snapshot: dict, *, node: str, target: str, live_hosts: frozenset[str] | None = None,
             recovery_is_real: bool = False) -> list[dict]:
    """Design assertions over a collected run snapshot (offline and live share them)."""
    counters = snapshot.get("counters", {})
    incidents = [i for i in (snapshot.get("diagnoses") or {}).get("incidents", []) if i.get("node_key") == target]
    records = (snapshot.get("decisions") or {}).get("records") or []
    recoveries = (snapshot.get("actions") or {}).get("recoveries") or []
    nodes = snapshot.get("node_status") or {}
    events = snapshot.get("events") or []
    started = [e for e in events if e["kind"] == "model_start" and e.get("node_key") == target]
    ended = [e for e in events if e["kind"] == "model_end" and e.get("node_key") == target]
    refused = snapshot.get("refused_hosts", [])
    return [
        check("no_provider_host_attempt", not refused if live_hosts is not None
              else not [h for h in refused if any(h.endswith(p) for p in PROVIDER_HOSTS)], refused),
        check("only_vibecode_chat_completions", counters.get("kie_responses", 0) == 0
              and counters.get("worker_total", 0) > 0,
              {"kie_responses": counters.get("kie_responses"), "worker_total": counters.get("worker_total")}),
        check("incident_invalid_json", any(i["type"] in RECOVERY_INCIDENTS for i in incidents),
              [(i["type"], i["status"], i.get("evidence_incomplete")) for i in incidents]),
        check("incident_current_and_complete_while_open", any(
            v["type"] in RECOVERY_INCIDENTS and v.get("node_key") == target and not v.get("evidence_incomplete")
            for v in (snapshot.get("incident_seen") or {}).values()),
            list((snapshot.get("incident_seen") or {}).values())[:4]),
        check("decision_on_vibecode_route", counters.get("decision", 0) >= 1, counters.get("decision")),
        check("decision_done_with_retry_same", bool(records) and records[0].get("status") == "done"
              and bool(recoveries) and (recoveries[0].get("preconditions") or {}).get("action_type") == "retry_same",
              {"records": [(r.get("status"), r.get("model_steps")) for r in records],
               "recovery_action": [(r.get("preconditions") or {}).get("action_type") for r in recoveries]},
              needs="decision_v1.py Vibecode route and a catalog that allows retry_same for this node type"),
        check("recovery_verified", bool(recoveries) and recoveries[0].get("status") == "verified"
              and (recoveries[0].get("verification_result") or {}).get("verified") is True,
              [(r.get("status"), (r.get("verification_result") or {}).get("verified")) for r in recoveries],
              needs="actions_recovery.py gates on chat/completions completeness"),
        check("incident_resolved_after_recovery", any(i["type"] in RECOVERY_INCIDENTS
              and i["status"] == "resolved_confirmed" for i in incidents), [(i["type"], i["status"]) for i in incidents]),
        check("journal_model_end_complete_on_chat_completions", bool(started) and bool(ended)
              and all(e["payload"].get("complete") is True for e in ended),
              {"model_start": len(started), "model_end": len(ended),
               "complete": [e["payload"].get("complete") for e in ended][:6]},
              needs="observation.py / gpt_api.py: model_end.complete for chat/completions"),
        check("recovery_request_on_vibecode", counters.get("worker_recovery", 0) >= 1,
              {"worker_recovery": counters.get("worker_recovery"), "kie_responses": counters.get("kie_responses")}),
        check("target_node_done", nodes.get(target) == "done", nodes.get(target)),
        check("hitl_none_pending", not [h for h in (snapshot.get("hitl_pending") or [])
                                        if h[0] not in EXPECTED_HITL.get(node, ())]
              and not any(v == "waiting_hitl" for v in nodes.values()),
              {"hitl": snapshot.get("hitl_pending"), "expected_after_node": sorted(EXPECTED_HITL.get(node, ()))}),
    ]


def evaluate_live(snapshot: dict, *, node: str, target: str, hosts: frozenset[str], max_real_posts: int,
                  max_usd: float) -> list[dict]:
    counters, spend = snapshot.get("counters", {}), snapshot.get("spend") or {}
    out = evaluate(snapshot, node=node, target=target, live_hosts=hosts, recovery_is_real=True)
    out += [
        check("caps_not_exceeded", counters.get("real_total", 0) <= max_real_posts
              and float(spend.get("estimated_usd") or 0) <= max_usd,
              {"real_total": counters.get("real_total"), "max_real_posts": max_real_posts,
               "estimated_usd": spend.get("estimated_usd"), "max_usd": max_usd}),
        check("broken_answers_injected_locally", counters.get("injected", 0) >= 1, counters.get("injected")),
        check("usage_recorded_for_real_posts", counters.get("real_total", 0) == 0 or int(spend.get("rows") or 0) >= 1,
              {"real_total": counters.get("real_total"), "tracker_rows": spend.get("rows")},
              needs="api_tracker_hook: a row with usage per real Vibecode request"),
        check("real_decision_request_sent", counters.get("real_decision", 0) >= 1, counters.get("real_decision")),
        check("real_recovery_request_sent", counters.get("real_worker_recovery", 0) >= 1,
              counters.get("real_worker_recovery")),
    ]
    return out


def dry_ok(counters: dict) -> bool:
    """Dry passes when the first attempt was broken locally, nothing was sent, and the provider request was refused."""
    return (counters.get("real_total", 0) == 0 and counters.get("injected", 0) >= 1
            and counters.get("refused_dry", 0) >= 1 and counters.get("refused_cap", 0) == 0)



def _args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--list-nodes", action="store_true", help="print the nodes, supported or not (with the reason)")
    p.add_argument("--node", choices=NODES, help="the native text node to break and recover")
    p.add_argument("--allow-known-blocker", action="store_true",
                   help="allow --live for a node listed in KNOWN_BLOCKERS (its chain cannot reach 'recovery verified')")
    p.add_argument("--sd-agent", choices=SD_AGENTS, default="characters", help="agent for --node sd_agent")
    p.add_argument("--dry", action="store_true",
                   help="live-shaped flow, every provider-bound request refused and counted (nothing is sent)")
    p.add_argument("--live", action="store_true",
                   help="PAID, capped: Decision + recovery requests go to the real Vibecode route (needs --env-file)")
    p.add_argument("--env-file", help="env file; only " + ", ".join(LIVE_ENV_NAMES) + " are read from it")
    p.add_argument("--max-real-posts", type=int, default=6, help="hard cap of real provider requests (live)")
    p.add_argument("--max-usd", type=float, default=0.10, help="hard cap of the estimated spend, catalog price x3 (live)")
    p.add_argument("--work-dir", help="scratch dir for data/, runtime.json, result.json (recreated)")
    p.add_argument("--sse-style", choices=("stream", "stream-usage"), default="stream")
    p.add_argument("--port", type=int, default=PORT)
    p.add_argument("--request-limit", type=int, default=60)
    p.add_argument("--timeout-s", type=int, default=600)
    p.add_argument("--settle-s", type=float, default=45.0, help="idle seconds after a terminal Decision before stopping")
    p.add_argument("--sleep-minutes", type=float, default=0.03, help="native step-failure sleep (patched for speed)")
    p.add_argument("--decision-delay-s", type=float, default=0.0,
                   help="offline: latency of the Decision stub (a real model answers in seconds; the context of the "
                        "request must not go stale while the model thinks)")
    args = p.parse_args(argv)
    if args.list_nodes:
        return args
    if not args.node or not args.work_dir:
        p.error("--node and --work-dir are required")
    if args.live and args.dry:
        p.error("--live and --dry are mutually exclusive")
    if args.live and not args.env_file:
        p.error("--live needs --env-file")
    if args.max_real_posts < 1 or args.max_usd <= 0:
        p.error("--max-real-posts must be >= 1 and --max-usd > 0")
    if args.node in UNSUPPORTED:
        p.error(f"node {args.node} is not supported by this stand: {UNSUPPORTED[args.node]}")
    if args.live and args.node in KNOWN_BLOCKERS and not args.allow_known_blocker:
        p.error(f"--live for node {args.node} is refused (known blocker): {KNOWN_BLOCKERS[args.node]} "
                "Add --allow-known-blocker to run it anyway.")
    return args


def list_nodes_text() -> str:
    lines = []
    for n in NODES:
        if n in UNSUPPORTED:
            lines.append(f"{n}: NOT supported: {UNSUPPORTED[n]}")
        elif n in KNOWN_BLOCKERS:
            lines.append(f"{n}: supported (offline/dry); live refused without --allow-known-blocker: {KNOWN_BLOCKERS[n]}")
        else:
            lines.append(f"{n}: supported")
    return "\n".join(lines)


def _sqlite_ro(path: Path):
    return sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)


_frames_rows = qc._frames
_events = qc._events
_tracker_rows = qc._tracker_rows
_hitl_pending = qc._hitl_pending
_step_failure = qc._step_failure
_wait_api = qc._wait_api


def _node_status(project_db: Path) -> dict[str, str]:
    return qc._node_status(project_db)


def _output_state(project_db: Path) -> dict:
    """What the node left behind (for the report; not a quality verdict)."""
    if not project_db.exists():
        return {}
    out: dict[str, Any] = {}
    with sqlite3.connect(project_db) as db:
        row = db.execute("SELECT length(general_plan),length(script_text) FROM projects LIMIT 1").fetchone()
        out["plan_len"], out["script_len"] = (row or (None, None))
        out["frames"] = db.execute("SELECT count(*) FROM frames").fetchone()[0]
        out["image_prompts"] = db.execute(
            "SELECT count(*) FROM frames WHERE coalesce(image_prompt,'')<>''").fetchone()[0]
        out["animation_prompts"] = db.execute(
            "SELECT count(*) FROM frames WHERE coalesce(animation_prompt,'')<>''").fetchone()[0]
        try:
            out["scene_design_cells"] = db.execute("SELECT count(*) FROM scene_design_cells").fetchone()[0]
        except sqlite3.Error:
            out["scene_design_cells"] = None
    return out


async def _seed(node: str, agent: str) -> tuple[int, str, str]:
    """Workflow + Project + WorkflowRun + ONE pending node of the chosen type (programmatic, no seed dir)."""
    from app.db import session_scope
    from app.models import Frame, NodeRun, NodeRunStatus, Project, ProjectStatus, Workflow, WorkflowRun, WorkflowRunStatus

    spec = SPECS[node]
    cnode = canvas_node(node, agent)
    nodes, edges = [cnode], []
    slug = f"vibecode-{node.replace('_', '-')}-{_uuid.uuid4().hex[:6]}"
    from app.web.settings_default import seed_default_workflow
    async with session_scope() as session:
        await seed_default_workflow(session)
        wf = Workflow(name=f"wf-{slug}", is_default=False, nodes=nodes, edges=edges)
        session.add(wf)
        await session.flush()
        meta = {"canvas_graph": {"workflow_id": wf.id, "nodes": nodes, "edges": edges}, **spec.meta}
        if node == "sd_agent" and agent == "world":
            meta["scene_design_skeleton"] = True
        if node == "sd_agent":
            meta["scene_design"] = {"only_agent": agent}
        project = Project(slug=slug, topic=f"vibecode {node} e2e", status=ProjectStatus.new, auto_mode=False,
                          general_plan=TEXT if spec.general_plan else None,
                          script_text=TEXT if spec.script_text else None, meta=meta)
        session.add(project)
        await session.flush()
        run = WorkflowRun(project_id=project.id, workflow_id=wf.id, status=WorkflowRunStatus.new,
                          nodes_snapshot=nodes, edges_snapshot=edges)
        session.add(run)
        await session.flush()
        session.add(NodeRun(workflow_run_id=run.id, node_key=cnode["id"], node_type=spec.canvas_type,
                            status=NodeRunStatus.pending))
        for f in spec.frames:
            session.add(Frame(project_id=project.id, number=f["number"], uuid=f["uuid"], sort_key=float(f["number"]),
                              voiceover_text=f["voiceover_text"], image_prompt=f["image_prompt"],
                              duration_seconds=f["duration_seconds"], attrs={}))
        await session.flush()
        pid, data_dir = int(project.id), project.data_dir
    data_dir.mkdir(parents=True, exist_ok=True)
    if spec.script_text:
        (data_dir / "voiceover.txt").write_text(TEXT, encoding="utf-8")
    return pid, slug, cnode["id"]


async def _post_seed(node: str, pid: int, agent: str = "characters") -> None:
    """sd_assemble: what the earlier agent steps leave (backfilled frames, checkpoints and cells), written by
    the application's own writers into the project database, exactly as tests/observe_v1/test_scene_assemble_dry_run.py.
    sd_agent world: the skeleton checkpoint (locations_seed) the earlier wave leaves, written by the same writer."""
    if node == "sd_agent" and agent == "world":
        from app.models import Project
        from app.project_db import migrate_project_by_id, project_db_session_scope
        from app.services.scene_design import runner
        await migrate_project_by_id(pid, create_backup=False)
        async with project_db_session_scope(pid) as session:
            project = await session.get(Project, pid)
            runner.save_checkpoint(project, "skeleton", WORLD_SKELETON)
            await session.commit()
        from app.db import session_scope
        async with session_scope() as session:
            project = await session.get(Project, pid)
            runner.save_checkpoint(project, "skeleton", WORLD_SKELETON)
            await session.commit()
        return
    if node != "sd_assemble":
        return
    from app.models import Project
    from app.project_db import migrate_project_by_id, project_db_session_scope
    from app.services import db_v2
    from app.services.scene_design import cells as sd_cells
    from app.services.scene_design import runner
    await migrate_project_by_id(pid, create_backup=False)
    parts = (("characters", ASM_CHARACTERS), ("world", ASM_WORLD), ("action", ASM_ACTION), ("camera", ASM_CAMERA))
    full_vo = chr(10).join(ASM_VO)
    async with project_db_session_scope(pid) as session:
        project = await session.get(Project, pid)
        await db_v2.backfill_project_v2(session, project)
        for name, data in (*parts, ("style", ASM_STYLE)):
            runner.save_checkpoint(project, name, data)
        await session.commit()
        for name, data in parts:
            await sd_cells.store_cells(session, project, name, sd_cells.slice_to_cells(project, name, data, full_vo))
        await session.commit()


def _runtime_config(port: int, request_limit: int, endpoint: str | None) -> dict:
    cfg = qc.runtime_config(port, request_limit, "retry_same", endpoint=endpoint)
    cfg["budget_policy"] = {"policy_id": "vibecode-text-nodes-e2e", "max_requests": request_limit}
    return cfg


async def main(argv: list[str] | None = None) -> int:
    pre = argparse.ArgumentParser(add_help=False)
    for flag in ("--work-dir", "--env-file", "--node", "--sd-agent"):
        pre.add_argument(flag)
    pre.add_argument("--port", type=int, default=PORT)
    pre.add_argument("--request-limit", type=int, default=60)
    pre.add_argument("--live", action="store_true")
    pre.add_argument("--dry", action="store_true")
    pre.add_argument("--list-nodes", action="store_true")
    early, _rest = pre.parse_known_args(argv)
    if early.list_nodes:
        print(list_nodes_text())
        return 0
    if not early.work_dir or not early.node or (early.live and (early.dry or not early.env_file)):
        print("--work-dir and --node are required; --live needs --env-file and excludes --dry")
        return 2
    if early.node in UNSUPPORTED:
        print(f"node {early.node} is not supported by this stand: {UNSUPPORTED[early.node]}")
        return 2
    mode = "live" if early.live else "dry" if early.dry else "offline"
    live_env: dict[str, str] = {}
    if mode != "offline" and early.env_file:
        live_env = load_env_whitelist(early.env_file)
    if mode == "live" and (not live_env.get("VIBECODE_API_KEY") or live_env["VIBECODE_API_KEY"].startswith("synthetic")):
        print("env file has no VIBECODE_API_KEY; nothing sent")
        return 3
    hosts = allowed_hosts(live_env)
    work = Path(early.work_dir).resolve()
    data = guard.reset_scratch_data(work)
    endpoint = live_endpoint(live_env) if mode != "offline" else None
    runtime_path = work / "runtime.json"
    runtime_path.write_text(json.dumps(_runtime_config(early.port, early.request_limit, endpoint), indent=2),
                            encoding="utf-8")
    os.chdir(ROOT)
    node = early.node
    if mode == "offline":
        os.environ.update(env_overrides(data, early.port, node))
    else:
        overrides, drop = live_env_overrides(env_overrides(data, early.port, node), live_env)
        for name in drop:
            os.environ.pop(name, None)
        os.environ.update(overrides)
    os.environ["ORCHESTRATOR_RUNTIME_CONFIG"] = str(runtime_path)
    args = _args(argv)
    agent = args.sd_agent
    spec = SPECS[node]

    import httpx
    from app.settings import settings
    settings.gpt_relay_token = live_env.get("GPT_RELAY_TOKEN", "") if mode != "offline" else ""
    settings.gpt_proxy_url = (live_env.get("GPT_PROXY_URL") or None) if mode != "offline" else None
    if mode != "offline":
        settings.vibecode_api_key = live_env.get("VIBECODE_API_KEY") or qc.SYNTHETIC_KEY
        settings.vibecode_base_url = live_env.get("VIBECODE_BASE_URL", "")
        settings.gpt_base_url = live_env.get("GPT_BASE_URL", "")
    for name, value in spec.env.items():
        setattr(settings, name.lower(), value.lower() == "true" if value in ("true", "false") else value)
    for name, value in spec.settings.items():
        setattr(settings, name, value)
    import app.services.api_tracker_hook as tracker
    tracker._TRACKER_DB = work / "tracker.db"
    if tracker._TRACKER_DB.exists():
        tracker._TRACKER_DB.unlink()
    from app.services import observation, step_failure_policy
    from app.services.decision_transport import current as decision_hook
    step_failure_policy.SLEEP_MINUTES = args.sleep_minutes
    step_failure_policy.XLSX_SHEET_FORMAT_SLEEP_MINUTES = args.sleep_minutes
    import app.services.xlsx_step_runners as xlsx_step_runners
    if hasattr(xlsx_step_runners, "_EMPTY_OPS_BACKOFF_S"):
        xlsx_step_runners._EMPTY_OPS_BACKOFF_S = (0.2, 0.2, 0.2)
    from app import main as app_main
    await app_main._init_db()
    pid, slug, target = await _seed(node, agent)
    await _post_seed(node, pid, agent)
    project_db = data / "videos" / slug / "project.db"
    base = f"http://127.0.0.1:{args.port}/api/projects/{pid}"

    counters: dict[str, Any] = {"worker_total": 0, "worker_broken": 0, "worker_recovery": 0, "decision": 0,
                                "kie_responses": 0, "refused": 0, "by_node": {}, "injected": 0, "stubbed_other": 0,
                                "real_decision": 0, "real_worker_recovery": 0, "real_total": 0, "refused_cap": 0,
                                "refused_dry": 0}
    aborted: list[str] = []
    refused_hosts: list[str] = []
    request_log: list[dict] = []
    recovery_requests: list[dict] = []
    from app.services.prompt_library import APPROVED_PROFILE_PROMPTS
    repair_suffix = APPROVED_PROFILE_PROMPTS[qc.PROFILE["prompt_variant"]]
    original_send = httpx.AsyncClient.send

    def spend_now() -> tuple[float, int, int]:
        rows = _tracker_rows(work / "tracker.db")
        usd, zero = estimate_usd(rows, tracker.vibecode_cost)
        return usd, zero, len(rows)

    def local_worker(request, body, text, key, recovery):
        """Answer a worker request locally: broken for the first attempts, the valid stub for a recovery (offline)."""
        stream = bool(body.get("stream"))
        rid = f"chatcmpl-{_uuid.uuid4().hex[:12]}"
        counters["worker_total"] += 1
        counters["worker_recovery"] += 1 if recovery else 0
        counters["by_node"][key] = counters["by_node"].get(key, 0) + 1
        broken = not recovery
        frames = _frames_rows(project_db)
        if qc.CONTINUE_MARKER in text:
            counters["continue_requests"] = counters.get("continue_requests", 0) + 1
            answer = ""
        elif broken:
            counters["worker_broken"] += 1
            counters["injected"] += 1
            answer = MALFORMED
        else:
            answer = build_answer(spec, frames, agent, text)
            if answer is None:
                raise RuntimeError(f"no local valid answer is known for node {node}; run with --dry or --live")
        if recovery:
            recovery_requests.append({"node": key, "path": request.url.path, "host": request.url.host,
                                      **qc.profile_request_facts(body, repair_suffix)})
        request_log.append({"route": "worker", "node": key, "recovery": recovery, "broken": broken, "real": False,
                            "stream": stream, "path": request.url.path})
        if stream:
            content = qc.sse_chat_body(answer, rid, style=args.sse_style)
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=content, request=request)
        return httpx.Response(200, headers={"content-type": "application/json"},
                              content=qc.chat_json_body(answer, rid), request=request)

    def refuse(request, kind: str, why: str, *, host_rule: bool):
        counters["refused"] += 1
        if host_rule:
            refused_hosts.append(str(request.url.host))
        elif kind == "dry":
            counters["refused_dry"] += 1
        else:
            counters["refused_cap"] += 1
        request_log.append({"route": "refused", "kind": kind, "host_rule": host_rule})
        raise RuntimeError(why)

    def attempt_info():
        attempt = observation._attempt.get()
        key = getattr(attempt, "node_key", None)
        recovery = bool(attempt and attempt.session.info.get("recovery_attempt_id") == attempt.attempt_id)
        return key, recovery

    async def send_live(client, request, **kwargs):
        host, path = request.url.host, request.url.path
        if is_loopback(host):
            if request.method == "POST" and (is_chat_completions(path) or is_kie_responses(path)):
                refuse(request, "loopback_model_route", "no local model route in live/dry mode", host_rule=True)
            return await original_send(client, request, **kwargs)
        if request.method != "POST" or not is_chat_completions(path) or str(host).lower() not in hosts:
            refuse(request, "foreign", f"refused: {request.method} {host}{path} is not an allowed provider request",
                   host_rule=True)
        body = json.loads(request.content.decode("utf-8") or "{}")
        text = qc.request_text(body)
        is_decision = decision_hook.get() is not None
        key, recovery = attempt_info()
        if not is_decision:
            if key != target:
                refuse(request, "outside_node", f"worker request outside the node (node_key={key!r})", host_rule=True)
            if not recovery:
                return local_worker(request, body, text, key, False)
        kind = "decision" if is_decision else "worker_recovery"
        if mode == "dry":
            refuse(request, "dry", f"dry: provider request refused ({kind})", host_rule=False)
        usd, _zero, _rows = spend_now()
        if usd >= args.max_usd and not aborted:
            aborted.append("cost")
        reason = (aborted and f"cap: aborted, estimate ${usd:.4f} passed ${args.max_usd}") or real_post_refusal(
            host, allowed=hosts, real_posts=counters["real_total"], max_posts=args.max_real_posts,
            spent_usd=usd, max_usd=args.max_usd)
        if reason:
            refuse(request, kind, reason, host_rule=reason.startswith("host:"))
        counters["real_total"] += 1
        counters["real_decision" if is_decision else "real_worker_recovery"] += 1
        if is_decision:
            counters["decision"] += 1
        else:
            counters["worker_total"] += 1
            counters["worker_recovery"] += 1
            counters["by_node"][key] = counters["by_node"].get(key, 0) + 1
            recovery_requests.append({"node": key, "path": path, "host": host,
                                      **qc.profile_request_facts(body, repair_suffix)})
        response = await original_send(client, request, **kwargs)
        request_log.append({"route": kind, "node": key, "real": True, "status": response.status_code, "path": path,
                            "stream": bool(body.get("stream"))})
        return response

    async def send(client, request, **kwargs):
        if mode != "offline":
            return await send_live(client, request, **kwargs)
        host, path = request.url.host, request.url.path
        reason = refusal_reason(request.method, host)
        if reason:
            counters["refused"] += 1
            refused_hosts.append(str(host))
            raise RuntimeError(reason)
        if request.method != "POST" or not (is_chat_completions(path) or is_kie_responses(path)):
            return await original_send(client, request, **kwargs)
        if is_kie_responses(path):
            counters["kie_responses"] += 1
            request_log.append({"route": "kie_responses", "path": path})
            raise RuntimeError("Kie Responses route used; this stand must run on Vibecode chat/completions")
        body = json.loads(request.content.decode("utf-8") or "{}")
        text = qc.request_text(body)
        if decision_hook.get() is not None:
            counters["decision"] += 1
            ctx = qc.decision_context(text)
            if ctx is None:
                raise RuntimeError("decision stub: no context in the request")
            if args.decision_delay_s > 0:
                await asyncio.sleep(args.decision_delay_s)
            answer = json.dumps(qc.decision_proposal(counters["decision"], ctx, "retry_same"), ensure_ascii=False)
            request_log.append({"route": "decision", "turn": counters["decision"], "real": False,
                                "allowed_actions": ctx.get("allowed_actions"), "incident_type": ctx.get("incident_type"),
                                "incident_status": ctx.get("incident_status")})
            return httpx.Response(200, headers={"content-type": "application/json"},
                                  content=qc.chat_json_body(answer, f"chatcmpl-{_uuid.uuid4().hex[:12]}"),
                                  request=request)
        key, recovery = attempt_info()
        if key != target:
            raise RuntimeError(f"worker stub: request outside the node (node_key={key!r})")
        return local_worker(request, body, text, key, recovery)

    admission_reasons: dict[str, int] = {}
    import app.services.actions_v1 as actions_v1
    original_admission = actions_v1.worker_entry_admission

    async def counted_admission(project_id):
        decision = await original_admission(project_id)
        if not decision.allowed:
            reason = str(getattr(decision, "reason", decision))
            admission_reasons[reason] = admission_reasons.get(reason, 0) + 1
        return decision

    actions_v1.worker_entry_admission = counted_admission
    httpx.AsyncClient.send = send
    task = asyncio.create_task(app_main.main())
    result: dict[str, Any] = {"mode": mode, "node": node, "target": target, "project_id": pid, "slug": slug,
                              "sd_agent": agent if node == "sd_agent" else None,
                              "args": {k: v for k, v in vars(args).items() if k != "env_file"}}
    execution = None
    try:
        await _wait_api(base)
        async with httpx.AsyncClient(timeout=30, trust_env=False,
                                     limits=httpx.Limits(max_keepalive_connections=0)) as client:
            from _operator_headers import attach_operator_token
            await attach_operator_token(client, base)
            options = (await client.get(base + "/observations/launch-options")).json()
            print("launch-options configured:", options["configured"], "mode:", options["mode"],
                  "strategies:", options["strategies"])
            result["launch_options"] = {k: options.get(k) for k in ("configured", "mode", "strategies", "reason")}
            if not options["configured"]:
                raise RuntimeError("Decision runtime not configured: " + str(options.get("reason")))
            info = next((n for n in options["nodes"] if n["node_key"] == target), None)
            result["node_capability"] = info
            print("node capability:", {k: info.get(k) for k in ("supported", "result_kind", "actions")} if info else None)
            if not info or not info["supported"]:
                raise RuntimeError(f"node {target} is not supported by the managed launch gate: {info}")
            response = await client.post(base + "/observations/launch", json={
                "workflow_run_id": options["workflow_run_id"], "graph_version": options["graph_version"],
                "node_keys": [target], "strategies": ["wait", "human_review", "retry_same"],
                "profile_ids": [], "request_limit": args.request_limit,
                "rationale": f"{mode} vibecode {node} recovery verification",
                "idempotency_key": f"vibecode-text-{node}-{_uuid.uuid4().hex[:8]}"})
            print("launch:", response.status_code, (response.text[:400] if response.status_code >= 300 else "ok"))
            if response.status_code >= 300:
                result["launch_error"] = response.text[:600]
                raise RuntimeError("launch refused: " + response.text[:300])
            execution = response.json()["execution_id"]
            result["execution_id"] = execution
            response = await client.post(base + f"/observations/{execution}/decisions/control", json={
                "enabled": True, "rationale": f"{mode} decision loop for the {node} recovery verification"})
            print("decisions/control:", response.status_code)
            response.raise_for_status()
            await _poll(client, base, execution, args, project_db, counters, result, target)
    except RuntimeError as exc:
        result["error"] = str(exc)
        print("STOP:", exc)
    finally:
        task.cancel()
        try:
            await task
        except BaseException:
            pass
        httpx.AsyncClient.send = original_send
        actions_v1.worker_entry_admission = original_admission
    await asyncio.sleep(2)
    result["admission_refusals"] = dict(admission_reasons)
    result.update(recovery_requests=recovery_requests, counters=counters, refused_hosts=sorted(set(refused_hosts)),
                  request_log=request_log[-80:], node_status=_node_status(project_db),
                  hitl_pending=_hitl_pending(project_db), output_state=_output_state(project_db),
                  step_failure={k: v for k, v in _step_failure(project_db).items()
                                if k in {"total_fails", "abandoned_at", "last_error"}},
                  events=_events(data / "state.db", execution))
    usd, zero_rows, rows = spend_now()
    result["spend"] = {"estimated_usd": usd, "rows": rows, "zero_usage_rows": zero_rows, "markup": COST_MARKUP,
                       "aborted_on_cost": bool(aborted)}
    if mode == "live":
        checks = evaluate_live(result, node=node, target=target, hosts=hosts, max_real_posts=args.max_real_posts,
                               max_usd=args.max_usd)
    elif mode == "offline":
        checks = evaluate(result, node=node, target=target)
    else:
        checks = [check("dry_nothing_sent", dry_ok(counters), {k: counters[k] for k in
                  ("real_total", "injected", "refused_dry", "refused_cap", "refused")}),
                  check("dry_node_started_on_vibecode_route", counters.get("worker_total", 0) >= 1
                        and counters.get("kie_responses", 0) == 0, counters.get("worker_total")),
                  check("dry_no_provider_host_attempt", not result["refused_hosts"], result["refused_hosts"])]
    ok, failed = verdict(checks)
    result["checks"], result["ok"], result["failed"] = checks, ok, failed
    result["reality"] = reality_report(mode, counters, node)
    result["request_classes"] = classify_requests(request_log)
    recs = (result.get("decisions") or {}).get("records") or []
    recoveries = (result.get("actions") or {}).get("recoveries") or []
    result["summary"] = {
        "mode": mode, "node": node, "route": "relay" if relay_base(live_env) else "direct",
        "posts": {k: counters[k] for k in ("injected", "real_total", "real_decision", "real_worker_recovery",
                                           "refused", "refused_cap", "refused_dry", "stubbed_other")},
        "caps": {"max_real_posts": args.max_real_posts, "max_usd": args.max_usd},
        "estimated_spend_usd": usd,
        "decision_record": qc.slim_record(recs[0]) if recs else None,
        "recovery_record": qc.slim_record(recoveries[0]) if recoveries else None,
        "verification": recoveries[0].get("verification_result") if recoveries else None,
        "node_done": result["node_status"].get(target) == "done",
        "output_state": result["output_state"],
        "verdict": ("PASS (all checks, live)" if ok and mode == "live" else
                    "DRY CHECK OK (nothing sent)" if mode == "dry" and ok else
                    "OFFLINE OK (stubs only, not a live proof)" if mode == "offline" and ok else "NOT PASSED"),
        "failed_checks": failed}
    slim = {k: v for k, v in result.items() if k != "events"}
    slim["event_kinds"] = sorted({e["kind"] for e in result["events"]})
    (work / "result.json").write_text(json.dumps(slim, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print("\n=== RESULT ===")
    print("node:", target, "| mode:", mode, "| status:", result["node_status"], "| output:", result["output_state"])
    print("counters:", {k: v for k, v in counters.items() if k != "by_node"}, "| by node:", counters["by_node"])
    for c in checks:
        print(("PASS " if c["ok"] else "FAIL ") + c["name"] + ("" if c["ok"] or not c["needs"] else f"   [needs: {c['needs']}]"))
        if not c["ok"]:
            print("     detail:", json.dumps(c["detail"], ensure_ascii=False, default=str)[:300])
    print("real:", result["reality"]["real"])
    print("not real:", result["reality"]["not_real"])
    print("spend (est. $%.4f) | verdict: %s" % (usd, result["summary"]["verdict"]))
    return 0 if ok else 1


async def _poll(client, base, execution, args, project_db, counters, result, target) -> None:
    deadline = time.monotonic() + args.timeout_s
    prefix = base + f"/observations/{execution}"
    last = None
    last_progress = None
    idle_since = time.monotonic()
    settled_since = None
    while time.monotonic() < deadline:
        decisions = (await client.get(prefix + "/decisions")).json()
        actions = (await client.get(prefix + "/actions")).json()
        diagnoses = (await client.get(prefix + "/diagnoses")).json()
        nodes = _node_status(project_db)
        result.update(decisions=decisions, actions=actions, diagnoses=diagnoses)
        seen = result.setdefault("incident_seen", {})
        for i in diagnoses.get("incidents", []):
            if i.get("currently_relevant"):
                seen[i["incident_id"]] = {k: i.get(k) for k in ("type", "status", "node_key", "blocking", "evidence_incomplete")}
        recs, recoveries = decisions["records"], actions["recoveries"]
        state = (nodes.get(target), [(d["status"], d["model_steps"]) for d in recs],
                 [r["status"] for r in recoveries], [(i["type"], i["status"]) for i in diagnoses.get("incidents", [])],
                 {k: v for k, v in counters.items() if k != "by_node"})
        if state != last:
            print(time.strftime("%H:%M:%S"), state, flush=True)
            last = state
        decision_done = bool(recs) and recs[0]["status"] in TERMINAL_DECISION
        progress = (state, tuple(sorted(nodes.items())))
        if progress != last_progress:
            last_progress, idle_since = progress, time.monotonic()
        idle = time.monotonic() - idle_since
        final = nodes.get(target) == "done" or (decision_done and idle > args.settle_s)
        if final:
            settled_since = settled_since or time.monotonic()
            if time.monotonic() - settled_since > 5:
                return
        else:
            settled_since = None
        await asyncio.sleep(1)
    result["stopped_at"] = "timeout"


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
