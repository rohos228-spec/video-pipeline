"""W4 / D01: built-in production catalog of text recovery profiles and computed media flags.

Class U and D: real code, no network and no paid calls; model catalogs are the repository's own.
Nothing was run live.
"""
from __future__ import annotations

import asyncio
import copy
from types import SimpleNamespace

import pytest

from app.services import decision_config as dc
from app.services import media_jobs, profiles_v1

BASE_ACTIONS = ["read_events", "read_contract", "retry_same", "retry_profile", "human_review", "wait", "finish"]


def vibecode(**extra):
    d = {"version": 1, "mode": "production", "online_enabled": True,
         "endpoint": "https://vibecode.moe/v1/chat/completions", "model_transport": "real",
         "external_calls_enabled": True, "retrieval_allowed": False, "continuation_allowed": False,
         "decision": {"catalog_id": "gpt-5.6-sol-vibecode", "model_id": "gpt-5.6-sol",
                      "reasoning_effort": None, "max_output_tokens": 2048, "timeout_s": 60},
         "profiles": {}, "allowed_actions": list(BASE_ACTIONS)}
    d.update(extra)
    return d


def kie(**extra):
    d = {"version": 1, "mode": "production", "online_enabled": True,
         "endpoint": "https://api.kie.ai/codex/v1/responses",
         "decision": {"catalog_id": "gpt-kie", "model_id": "gpt-5-6-sol", "reasoning_effort": "low",
                      "max_output_tokens": 1024, "timeout_s": 30},
         "profiles": {}, "allowed_actions": list(BASE_ACTIONS)}
    d.update(extra)
    return d


# ---- catalog content ---------------------------------------------------------------------

def test_every_builtin_profile_validates_against_the_real_catalogs():
    from app.services.prompt_library import APPROVED_PROFILE_PROMPTS
    from app.services.vibecode_catalog import find_model
    assert profiles_v1.PRODUCTION_CATALOG
    for pid, c in profiles_v1.PRODUCTION_CATALOG.items():
        dc.validate_profile(c, "production")
        assert c["synthetic_only"] is False and "synthetic" not in c["model_id"] and "synthetic" not in pid
        assert c["kind"] == "text" and c["prompt_variant"] in APPROVED_PROFILE_PROMPTS
        assert c["prompt_variant"] in {"original-contract-v1", "repair-json-only-v1"}
        assert find_model(c["model_id"] if c["provider"] == "vibecode" else "gpt-5.6-sol")["kind"] == "text"
        assert profiles_v1.PROFILE_BINDINGS[pid]["incidents"] == {"invalid_json", "missing_payload"}


def test_kie_profiles_vary_effort_and_token_cap_but_vibecode_profile_does_not():
    kies = [c for c in profiles_v1.PRODUCTION_CATALOG.values() if c["provider"] == "kie"]
    assert len({(c["reasoning_effort"], c["max_output_tokens"]) for c in kies}) >= 2
    assert all(1 <= c["max_output_tokens"] <= 16384 for c in kies)
    vib = [c for c in profiles_v1.PRODUCTION_CATALOG.values() if c["provider"] == "vibecode"]
    assert vib and all(c["reasoning_effort"] is None and c["max_output_tokens"] is None for c in vib)


def test_bindings_cover_managed_text_types_and_never_media():
    assert {"excel_gpt", "plan", "script", "split", "image_prompts", "sfx_plan", "animation_prompts",
            "sd_agent", "sd_assemble"} == set(profiles_v1.BUILTIN_NODE_TYPES)
    assert not profiles_v1.BUILTIN_NODE_TYPES & profiles_v1.MEDIA_NODE_TYPES
    pid = "kie-json-repair-v1"
    for t in profiles_v1.BUILTIN_NODE_TYPES:
        assert profiles_v1.binding_allows(pid, node_type=t, incident_type="invalid_json")
        assert profiles_v1.binding_allows(pid, node_type=t, incident_type="missing_payload")
        assert not profiles_v1.binding_allows(pid, node_type=t, incident_type="api_error")
    for t in ("images", "videos", "music", "audio", "assemble", "sfx_gen", "publish", ""):
        assert not profiles_v1.binding_allows(pid, node_type=t, incident_type="invalid_json")
    assert profiles_v1.binding_allows("owner-defined", node_type="images", incident_type="x")


# ---- activation --------------------------------------------------------------------------

def test_production_with_retry_profile_gets_the_builtin_catalog_by_decision_provider():
    v = profiles_v1.builtin_production_profiles(dc.validate_config(vibecode()))
    assert set(v) == {"vibecode-json-repair-v1"}
    k = profiles_v1.builtin_production_profiles(dc.validate_config(kie()))
    assert set(k) == {"kie-original-v1", "kie-json-repair-v1", "kie-json-repair-roomy-v1"}
    assert all(c["provider"] == "kie" for c in k.values())


@pytest.mark.parametrize("change", [
    {"allowed_actions": ["read_events", "retry_same", "human_review"]},   # owner did not allow retry_profile
    {"builtin_profiles": False},
    {"incident_scope": {"execution_id": "e", "incident_id": "i", "node_key": "n", "source_attempt_id": "a",
                        "recovery_limit": 1}},
])
def test_builtin_catalog_is_off_without_owner_opt_in(change):
    cfg = dc.validate_config(kie(**change))
    assert profiles_v1.builtin_production_profiles(cfg) == {}


def test_synthetic_mode_and_disabled_decision_get_no_builtin_catalog():
    assert profiles_v1.builtin_production_profiles({"mode": "synthetic", "decision": {"x": 1}, "profiles": {},
                                                    "allowed_actions": ["retry_profile"]}) == {}
    off = {"version": 1, "mode": "production", "online_enabled": False, "endpoint": None, "decision": None,
           "profiles": {}, "allowed_actions": []}
    assert profiles_v1.builtin_production_profiles(dc.validate_config(off)) == {}


def test_explicit_owner_profiles_win_and_config_is_not_mutated():
    owner = {"owner-1": copy.deepcopy(profiles_v1.PRODUCTION_CATALOG["kie-json-repair-v1"])}
    cfg = dc.validate_config(kie(profiles=owner))
    assert profiles_v1.effective_profiles(cfg) == owner
    plain = dc.validate_config(kie())
    snapshot = copy.deepcopy(plain)
    profiles_v1.effective_profiles(plain)
    assert plain == snapshot and plain["profiles"] == {}


def test_builtin_profile_dropped_when_model_no_longer_in_catalog(monkeypatch):
    broken = {**profiles_v1.PRODUCTION_CATALOG, "kie-original-v1": {
        **profiles_v1.PRODUCTION_CATALOG["kie-original-v1"], "model_id": "no-such-model"}}
    monkeypatch.setattr(profiles_v1, "PRODUCTION_CATALOG", broken)
    got = profiles_v1.builtin_production_profiles(dc.validate_config(kie()))
    assert "kie-original-v1" not in got and "kie-json-repair-v1" in got


def test_active_catalog_and_status_report_builtin_profiles(monkeypatch):
    cfg = dc.validate_config(vibecode())
    monkeypatch.setattr(dc, "runtime_config", lambda: cfg)
    assert set(profiles_v1.active_catalog()) == {"vibecode-json-repair-v1"}
    st = dc.status()
    assert st["profile_count"] == 1 and st["profile_source"] == "builtin" and st["mode"] == "production"
    assert profiles_v1.catalog_hash() == profiles_v1.catalog_hash()
    profiles_v1.validate_candidate(profiles_v1.active_catalog()["vibecode-json-repair-v1"])
    # profiles outside the active catalog (synthetic or foreign provider) are still refused
    with pytest.raises(ValueError):
        profiles_v1.validate_candidate(profiles_v1.CATALOG["synthetic-json-v1"])
    with pytest.raises(ValueError):
        profiles_v1.validate_candidate(profiles_v1.PRODUCTION_CATALOG["kie-json-repair-v1"])


def test_status_without_retry_profile_has_zero_profiles(monkeypatch):
    cfg = dc.validate_config(kie(allowed_actions=["read_events", "retry_same", "human_review"]))
    monkeypatch.setattr(dc, "runtime_config", lambda: cfg)
    st = dc.status()
    assert st["profile_count"] == 0 and st["profile_source"] == "none"


def test_vibecode_retry_profile_needs_builtin_or_owner_profile():
    dc.validate_config(vibecode())
    with pytest.raises(ValueError, match="retry_profile"):
        dc.validate_config(vibecode(builtin_profiles=False))
    with pytest.raises(ValueError):
        dc.validate_config(vibecode(builtin_profiles="yes"))


def test_builtin_effective_body_is_bounded_and_deterministic(monkeypatch):
    cfg = dc.validate_config(kie())
    monkeypatch.setattr(dc, "runtime_config", lambda: cfg)
    cand = profiles_v1.active_catalog()["kie-json-repair-roomy-v1"]
    body = {"model": "gpt-5-6-sol", "input": "original prompt", "stream": True}
    out = profiles_v1.effective_body(body, cand)
    assert out["reasoning"] == {"effort": "high"} and out["max_output_tokens"] == 8192
    assert out["input"].startswith("original prompt") and out["input"] != "original prompt"
    assert profiles_v1.effective_body(body, cand) == out


# ---- media stays fail-closed, flags are computed ------------------------------------------

MEDIA = {"enabled": True, "provider": "kie", "origin": "http://127.0.0.1:9",
         "download_origins": ["http://127.0.0.1:9"], "model_ids": ["m"], "max_bytes": 4096,
         "timeout_s": 5, "poll_interval_s": 0.05, "max_polls": 3}


def test_media_is_not_allowed_by_default_and_production_media_is_rejected():
    for cfg in (dc.validate_config(kie()), dc.validate_config(vibecode()), None, {}):
        st = media_jobs.media_profile_state(cfg)
        assert st["execution_allowed"] is False and st["media_enabled"] is False
        assert st["production_profile_configured"] is False
    with pytest.raises(ValueError, match="no authorized network profile"):
        dc.validate_config(kie(media=MEDIA))
    for c in profiles_v1.PRODUCTION_CATALOG.values():
        assert c["kind"] == "text"


def test_media_flags_follow_configuration():
    st = media_jobs.media_profile_state({"mode": "synthetic", "media": MEDIA})
    assert st == {"mode": "synthetic", "media_enabled": True, "provider": "kie",
                  "production_profile_configured": False, "synthetic_only": True, "execution_allowed": True}
    disabled = media_jobs.media_profile_state({"mode": "synthetic", "media": {**MEDIA, "enabled": False}})
    assert disabled["execution_allowed"] is False
    # an (unvalidated) production-enabled dict is reported honestly, not as synthetic
    prod = media_jobs.media_profile_state({"mode": "production", "media": MEDIA})
    assert prod["production_profile_configured"] is True and prod["synthetic_only"] is False


def test_assert_entry_refuses_production_media(monkeypatch):
    from app.services import actions_v1
    ctx = SimpleNamespace(scope=SimpleNamespace(mode="managed", project_id=1, execution_id="e", node_keys=["n"]), node_key="n")
    monkeypatch.setattr(media_jobs, "_attempt", SimpleNamespace(get=lambda: ctx))
    monkeypatch.setattr(actions_v1, "get_permission", lambda pid: SimpleNamespace(execution_id="e", node_keys=["n"]))
    monkeypatch.setattr(actions_v1, "check_managed_start", lambda **k: SimpleNamespace(allowed=True, reason=""))
    monkeypatch.setattr(dc, "runtime_config", lambda: {"mode": "production", "media": MEDIA})
    with pytest.raises(media_jobs.MediaPolicyError, match="disabled or unsupported"):
        asyncio.run(media_jobs.assert_entry(provider="kie"))
    monkeypatch.setattr(dc, "runtime_config", lambda: dc.validate_config(kie()))
    with pytest.raises(media_jobs.MediaPolicyError, match="disabled or unsupported"):
        asyncio.run(media_jobs.assert_entry(provider="kie"))
    monkeypatch.setattr(dc, "runtime_config", lambda: {"mode": "synthetic", "media": MEDIA})
    from app.settings import settings
    monkeypatch.setattr(settings, "gpt_api_key", "real-looking-key")
    with pytest.raises(media_jobs.MediaPolicyError, match="synthetic application credentials"):
        asyncio.run(media_jobs.assert_entry(provider="kie"))
    monkeypatch.setattr(settings, "gpt_api_key", "synthetic-key")
    got = asyncio.run(media_jobs.assert_entry(provider="kie"))
    assert got[0] is ctx
