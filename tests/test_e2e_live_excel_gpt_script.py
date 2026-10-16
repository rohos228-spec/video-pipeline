import importlib.util
import json
from pathlib import Path

_PATH = Path(__file__).parent.parent / "scripts" / "e2e_live_excel_gpt_recovery.py"
_spec = importlib.util.spec_from_file_location("e2e_live_excel_gpt_recovery", _PATH)
script = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(script)


def test_load_env_reads_only_whitelisted_names(tmp_path):
    env = tmp_path / "x.env"
    env.write_text("# c\nGPT_API_KEY=abc\nKIE_API_KEY=other\nELEVENLABS_API_KEY=z\nGPT_BASE_URL='https://h'\n",
                   encoding="utf-8")
    assert script._load_env(str(env)) == {"GPT_API_KEY": "abc", "GPT_BASE_URL": "https://h"}


def test_runtime_config_is_valid_production_config(tmp_path):
    from app.services.decision_config import validate_config
    cfg = validate_config(json.loads(script._runtime_config(tmp_path).read_text(encoding="utf-8")))
    assert cfg["mode"] == "production" and "retry_profile" in cfg["allowed_actions"]
    assert cfg["endpoint"].startswith("https://api.kie.ai/")


def test_injected_response_is_a_completed_sse_with_the_malformed_text():
    body = script._sse(script.MALFORMED, "r1").decode()
    assert body.count("data: ") == 4 and script.MALFORMED.replace('"', '\\"') in body
    assert '"status": "completed"' in body


def test_default_mode_is_dry_and_live_needs_explicit_flag(monkeypatch):
    monkeypatch.setattr("sys.argv", ["x", "--seed-dir", "a", "--work-dir", "b"])
    args = script._args()
    assert args.live is False and args.max_real_posts == 6 and args.max_usd == 0.10
