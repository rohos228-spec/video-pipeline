"""Guards for scripts/e2e_vibecode_text_nodes.py (pure helpers + a real --dry run per node).

Nothing here sends anything: the dry runs refuse every non-loopback request inside the httpx send hook and use a
synthetic key. Each dry run starts the whole application in a SUBPROCESS (isolated memory and port), so run this
file alone: one pytest at a time.

Nodes that the stand cannot run in dry are listed in UNSUPPORTED with the reason; the test pins that list so an
unsupported node cannot silently turn into a green one.
"""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_PATH = ROOT / "scripts" / "e2e_vibecode_text_nodes.py"
_spec = importlib.util.spec_from_file_location("e2e_vibecode_text_nodes", _PATH)
script = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = script
_spec.loader.exec_module(script)

WANTED = ("plan", "script", "split", "image_prompts", "sfx_plan", "animation_prompts", "sd_agent", "sd_assemble")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _stand_workdir import make_workdir, remove_workdir  # noqa: E402


@pytest.fixture
def tmp_path():
    """Outside the repository: the e2e guard refuses a work folder inside live data."""
    path = make_workdir()
    try:
        yield path
    finally:
        remove_workdir(path)


# ---------- the node list ----------

def test_node_list_is_the_requested_eight():
    assert script.NODES == WANTED


def test_unsupported_and_blocked_lists_are_pinned():
    """A node cannot silently move between 'unsupported', 'live refused' and 'fully supported'."""
    assert set(script.UNSUPPORTED) == {"split", "image_prompts"}
    assert set(script.KNOWN_BLOCKERS) == set()  # the double-incident blocker of plan/script/animation_prompts is fixed
    assert not set(script.UNSUPPORTED) & set(script.KNOWN_BLOCKERS)
    assert all(len(r) > 80 for r in script.KNOWN_BLOCKERS.values())


def test_live_is_refused_for_a_known_blocker_unless_allowed():
    base = ["--work-dir", "w", "--live", "--env-file", "e"]
    for node in script.KNOWN_BLOCKERS:
        with pytest.raises(SystemExit):
            script._args(["--node", node, *base])
        assert script._args(["--node", node, *base, "--allow-known-blocker"]).live is True
    for node in ("sfx_plan", "sd_agent", "sd_assemble"):
        assert script._args(["--node", node, *base]).live is True


def test_every_unsupported_node_has_a_reason_and_is_a_known_node():
    assert set(script.UNSUPPORTED) <= set(script.NODES)
    assert all(isinstance(r, str) and len(r) > 30 for r in script.UNSUPPORTED.values())
    assert set(script.supported_nodes()) | set(script.UNSUPPORTED) == set(script.NODES)


def test_list_nodes_text_names_every_node_and_the_reasons():
    text = script.list_nodes_text()
    for node in script.NODES:
        assert node + ":" in text
    for reason in (*script.UNSUPPORTED.values(), *script.KNOWN_BLOCKERS.values()):
        assert reason in text


def test_node_types_exist_in_the_capability_registry():
    from app.services.node_capabilities import capability_for
    for node in script.NODES:
        cap = capability_for(script.SPECS[node].canvas_type)
        assert cap is not None and "retry_same" in cap.actions, node


def test_canvas_nodes_are_single_and_keyed_per_node():
    keys = {script.node_key_for(n) for n in script.NODES}
    assert len(keys) == len(script.NODES)
    assert script.canvas_node("sd_agent", "world")["data"]["sd_agent"] == "world"
    assert script.canvas_node("sd_assemble")["data"]["sd_agent"] == "assemble"
    assert script.node_key_for("sd_agent", "world") != script.node_key_for("sd_agent", "characters")
    for node in script.NODES:
        assert script.canvas_node(node)["type"] == script.SPECS[node].canvas_type


@pytest.mark.parametrize("node", [n for n in WANTED if WANTED and n not in ("animation_prompts", "sd_assemble")])
def test_local_valid_answer_is_json_for_nodes_that_have_one(node):
    spec = script.SPECS[node]
    frames = list(spec.frames)
    answer = script.build_answer(spec, frames)
    assert answer is not None
    assert isinstance(json.loads(answer), dict)


def test_animation_prompts_answers_the_phase_one_message_and_the_frame_batch():
    spec = script.SPECS["animation_prompts"]
    frames = list(spec.frames)
    assert script.build_answer(spec, frames, text="no frame ids here") == script.ANIMATION_READY
    batch = json.loads(script.build_answer(spec, frames, text=" ".join(f["uuid"] for f in frames)))
    assert [op["frame_uuid"] for op in batch["ops"]] == [f["uuid"] for f in frames]
    assert all("промт_видео" in op["fields"] for op in batch["ops"])


def test_sd_agent_answers_differ_per_agent():
    spec = script.SPECS["sd_agent"]
    assert "characters" in json.loads(script.build_answer(spec, [], "characters"))
    assert "locations" in json.loads(script.build_answer(spec, [], "world"))


# ---------- argument handling and safety ----------

def test_default_mode_is_offline_and_live_is_opt_in():
    args = script._args(["--node", "plan", "--work-dir", "w"])
    assert args.live is False and args.dry is False
    assert (args.max_real_posts, args.max_usd) == (6, 0.10)
    assert script._args(["--node", "sfx_plan", "--work-dir", "w", "--live", "--env-file", "e"]).live is True


def test_live_needs_env_file_excludes_dry_and_validates_caps():
    for bad in (["--live"], ["--live", "--dry", "--env-file", "e"], ["--max-real-posts", "0"], ["--max-usd", "0"]):
        with pytest.raises(SystemExit):
            script._args(["--node", "plan", "--work-dir", "w", *bad])


def test_node_and_work_dir_are_required_and_node_is_validated():
    for bad in ([], ["--node", "plan"], ["--work-dir", "w"], ["--node", "excel_gpt", "--work-dir", "w"]):
        with pytest.raises(SystemExit):
            script._args(bad)
    assert script._args(["--list-nodes"]).list_nodes is True


def test_unsupported_node_is_refused_by_the_arguments(monkeypatch):
    monkeypatch.setitem(script.UNSUPPORTED, "plan", "x" * 40)
    with pytest.raises(SystemExit):
        script._args(["--node", "plan", "--work-dir", "w"])


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", "[::1]"])
def test_loopback_hosts_are_allowed_offline(host):
    assert script.refusal_reason("POST", host) is None


@pytest.mark.parametrize("host", ["vibecode.moe", "api.kie.ai", "example.com", "10.0.0.5", "", None, "127.0.0.1.evil.com"])
def test_every_non_loopback_host_is_refused_offline(host):
    assert "refused" in script.refusal_reason("GET", host)


def test_only_vibecode_or_the_relay_may_receive_a_live_request():
    hosts = script.allowed_hosts({})
    assert hosts == frozenset({"vibecode.moe"})
    assert script.real_post_refusal("vibecode.moe", allowed=hosts, real_posts=0, max_posts=6, spent_usd=0, max_usd=0.1) is None
    for bad in ("api.kie.ai", "example.com", "", None):
        assert script.real_post_refusal(bad, allowed=hosts, real_posts=0, max_posts=6, spent_usd=0, max_usd=0.1).startswith("host:")
    assert script.real_post_refusal("vibecode.moe", allowed=hosts, real_posts=6, max_posts=6, spent_usd=0, max_usd=0.1).startswith("cap:")
    assert script.real_post_refusal("vibecode.moe", allowed=hosts, real_posts=0, max_posts=6, spent_usd=0.1, max_usd=0.1).startswith("cap:")


def test_environment_is_synthetic_loopback_without_relay(tmp_path):
    for node in script.NODES:
        env = script.env_overrides(tmp_path, 18767, node)
        assert env["VIBECODE_BASE_URL"] == "http://127.0.0.1:18767/v1"
        assert env["VIBECODE_API_KEY"].startswith("synthetic") and env["TEXT_LLM_PROVIDER"] == "vibecode"
        assert not any(k in env for k in ("GPT_RELAY_TOKEN", "GPT_PROXY_URL"))
    assert script.env_overrides(tmp_path, 18767, "sfx_plan")["SFX_ENABLED"] == "true"


def test_env_file_is_read_only_by_the_whitelist_and_never_echoed(tmp_path):
    env_file = tmp_path / "e.env"
    env_file.write_text("VIBECODE_API_KEY=secret-vibe\nKIE_API_KEY=other\nOPENAI_API_KEY=zzz\n# c\nGPT_BASE_URL=http://relay.example\n",
                        encoding="utf-8")
    got = script.load_env_whitelist(str(env_file))
    assert set(got) <= set(script.LIVE_ENV_NAMES)
    assert "KIE_API_KEY" not in got and "OPENAI_API_KEY" not in got and got["VIBECODE_API_KEY"] == "secret-vibe"
    reality = json.dumps(script.reality_report("live", {"real_decision": 1, "real_worker_recovery": 1}, "plan"))
    assert "secret-vibe" not in reality


def test_live_endpoint_is_the_vibecode_chat_completions_route():
    assert script.live_endpoint({}) == "https://vibecode.moe/v1/chat/completions"


def test_runtime_config_is_the_vibecode_production_shape():
    cfg = script._runtime_config(18767, 60, "https://vibecode.moe/v1/chat/completions")
    assert cfg["decision"]["catalog_id"] == "gpt-5.6-sol-vibecode" and "retry_same" in cfg["allowed_actions"]
    assert cfg["endpoint"].startswith("https://vibecode.moe/")
    assert script._runtime_config(18767, 60, None)["endpoint"].startswith("http://127.0.0.1:")


# ---------- honest reporting ----------

def test_reality_report_separates_real_from_local():
    live = script.reality_report("live", {"real_decision": 2, "real_worker_recovery": 1}, "split")
    assert any("2 real Decision" in x for x in live["real"]) and any("1 real recovery" in x for x in live["real"])
    assert any("broken locally" in x for x in live["not_real"])
    dry = script.reality_report("dry", {"real_decision": 0, "real_worker_recovery": 0}, "split")
    assert not any("real Decision" in x or "real recovery" in x for x in dry["real"])
    assert any("refused" in x for x in dry["not_real"])
    offline = script.reality_report("offline", {}, "split")
    assert any("local stubs" in x for x in offline["not_real"])
    assert "verified live" not in json.dumps(dry) and "проверено вживую" not in json.dumps(dry)


def test_dry_ok_requires_nothing_sent_and_a_refused_provider_request():
    good = {"real_total": 0, "injected": 3, "refused_dry": 1, "refused_cap": 0}
    assert script.dry_ok(good)
    assert not script.dry_ok({**good, "real_total": 1})
    assert not script.dry_ok({**good, "injected": 0})
    assert not script.dry_ok({**good, "refused_dry": 0})


def _snapshot(**over):
    target = "native-plan-A"
    snap = {
        "counters": {"kie_responses": 0, "worker_total": 4, "decision": 2, "worker_recovery": 1, "injected": 3},
        "diagnoses": {"incidents": [{"node_key": target, "type": "invalid_json", "status": "resolved_confirmed"}]},
        "incident_seen": {"i": {"type": "invalid_json", "node_key": target, "evidence_incomplete": False}},
        "decisions": {"records": [{"status": "done", "model_steps": 3}]},
        "actions": {"recoveries": [{"status": "verified", "preconditions": {"action_type": "retry_same"},
                                    "verification_result": {"verified": True}}]},
        "node_status": {target: "done"},
        "events": [{"kind": "model_start", "node_key": target, "payload": {}},
                   {"kind": "model_end", "node_key": target, "payload": {"complete": True}}],
        "refused_hosts": [], "hitl_pending": [],
    }
    snap.update(over)
    return snap


def test_evaluate_passes_a_good_snapshot_and_names_each_failure():
    checks = script.evaluate(_snapshot(), node="plan", target="native-plan-A")
    assert script.verdict(checks) == (True, [])
    bad = script.evaluate(_snapshot(node_status={"native-plan-A": "failed"},
                                    actions={"recoveries": [{"status": "human_review", "preconditions": {},
                                                             "verification_result": {"verified": False}}]}),
                          node="plan", target="native-plan-A")
    ok, failed = script.verdict(bad)
    assert not ok and {"target_node_done", "recovery_verified", "decision_done_with_retry_same"} <= set(failed)


def test_a_provider_host_attempt_fails_the_offline_checks():
    checks = script.evaluate(_snapshot(refused_hosts=["vibecode.moe"]), node="plan", target="native-plan-A")
    assert "no_provider_host_attempt" in script.verdict(checks)[1]


def test_evaluate_live_adds_cap_and_real_post_checks():
    snap = _snapshot(spend={"estimated_usd": 0.01, "rows": 2})
    snap["counters"].update(real_total=3, real_decision=2, real_worker_recovery=1)
    checks = script.evaluate_live(snap, node="plan", target="native-plan-A", hosts=frozenset({"vibecode.moe"}),
                                  max_real_posts=6, max_usd=0.1)
    assert script.verdict(checks) == (True, [])
    snap["counters"]["real_total"] = 7
    assert "caps_not_exceeded" in script.verdict(script.evaluate_live(
        snap, node="plan", target="native-plan-A", hosts=frozenset({"vibecode.moe"}), max_real_posts=6, max_usd=0.1))[1]


# ---------- a real dry run per node (whole app in a subprocess, nothing is sent) ----------

def _run_dry(node: str, tmp_path: Path, *extra: str) -> tuple[int, dict, str]:
    env = {**os.environ, "PYTHONUTF8": "1", "NO_COLOR": "1"}
    for name in ("GPT_RELAY_TOKEN", "GPT_PROXY_URL", "VIBECODE_API_KEY", "GPT_API_KEY"):
        env.pop(name, None)
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", str(_PATH), "--node", node, "--work-dir", str(tmp_path), "--dry",
         "--settle-s", "6", "--sleep-minutes", "0.01", "--timeout-s", "420", *extra],
        cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=560)
    result_path = tmp_path / "result.json"
    result = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else {}
    return proc.returncode, result, (proc.stdout or "")[-1500:]


@pytest.mark.parametrize("node", WANTED)
def test_dry_run_passes_for_every_supported_node(node, tmp_path):
    if node in script.UNSUPPORTED:
        pytest.skip("not supported in dry: " + script.UNSUPPORTED[node])
    code, result, tail = _run_dry(node, tmp_path)
    assert code == 0, tail
    counters = result["counters"]
    assert result["mode"] == "dry" and result["ok"] is True, result.get("failed")
    assert counters["real_total"] == 0 and counters["injected"] >= 1 and counters["refused_dry"] >= 1
    assert counters["kie_responses"] == 0 and result["refused_hosts"] == []
    assert result["summary"]["verdict"] == "DRY CHECK OK (nothing sent)"


@pytest.mark.slow
@pytest.mark.parametrize("node", sorted(script.UNSUPPORTED))
def test_unsupported_node_is_refused_with_its_reason(node, tmp_path):
    env = {**os.environ, "PYTHONUTF8": "1"}
    proc = subprocess.run([sys.executable, "-X", "utf8", str(_PATH), "--node", node, "--work-dir", str(tmp_path), "--dry"],
                          cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=120)
    assert proc.returncode == 2
    assert not (tmp_path / "result.json").exists()
