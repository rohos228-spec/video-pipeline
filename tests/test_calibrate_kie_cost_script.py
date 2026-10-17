import importlib.util
from pathlib import Path

_p = Path(__file__).parent.parent / "scripts" / "calibrate_kie_cost.py"
_s = importlib.util.spec_from_file_location("calibrate_kie_cost", _p)
cal = importlib.util.module_from_spec(_s)
_s.loader.exec_module(cal)


def test_load_env_whitelist_excludes_unrelated_secrets(tmp_path):
    f = tmp_path / "e.env"
    f.write_text("GPT_API_KEY=a\nKIE_API_KEY=b\nELEVENLABS_API_KEY=c\nYANDEX_STORAGE_SECRET_KEY=d\n", encoding="utf-8")
    assert cal.load_env(str(f)) == {"GPT_API_KEY": "a", "KIE_API_KEY": "b"}


def test_compare_ratio_and_zero_estimate_safe():
    assert cal.compare(2.0, 0.005, 0.01) == {"billed_usd": 0.01, "estimated_usd": 0.01, "ratio_billed_over_estimated": 1.0}
    assert cal.compare(1.0, 0.005, 0)["ratio_billed_over_estimated"] is None
