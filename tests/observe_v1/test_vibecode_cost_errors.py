"""Cost estimate, telemetry, error codes and diagnosis for Vibecode text requests (stubs only)."""
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.services import api_tracker_hook
from app.services.diagnosis import derive_incidents
from app.services.error_catalog import ERROR_CATALOG, all_error_codes, describe_error
from app.services.gpt_api import GptApiError
from app.services.observation import _track_api_cost
from app.settings import settings

REAL_VK = "vk-real-looking-key-0123456789"
REAL_KIE = "sk-real-looking-kie-key"


class SyncThread:
    """Runs the tracker's write immediately so the SQLite row can be read back."""
    def __init__(self, target=None, daemon=None, **_):
        self.target = target

    def start(self):
        self.target()


class TrackApiCost(unittest.TestCase):
    def track(self, *, provider="vibecode", vk=REAL_VK, kie="", usage=None, model="gpt-5.6-sol"):
        with patch.object(settings, "vibecode_api_key", vk), patch.object(settings, "gpt_api_key", kie), \
                patch("app.services.api_tracker_hook.record_api_call") as record:
            _track_api_cost(model=model, usage=usage, duration_sec=1.0, project_id=5, provider=provider)
        return record

    def test_vibecode_row_uses_vibecode_label_and_not_calibrated_metadata(self):
        record = self.track(usage={"prompt_tokens": 100, "completion_tokens": 20})
        record.assert_called_once()
        kwargs = record.call_args.kwargs
        self.assertEqual(kwargs["provider"], "vibecode")
        self.assertEqual(kwargs["model"], "gpt-5.6-sol")
        self.assertIs(kwargs["metadata"]["calibrated"], False)
        self.assertEqual(kwargs["metadata"]["pricing_basis"], api_tracker_hook.VIBECODE_PRICING_BASIS)
        self.assertNotIn("usage_known", kwargs["metadata"])

    def test_gate_is_the_vibecode_key_not_gpt_api_key(self):
        # A real Kie key must not enable Vibecode rows, and vice versa.
        self.track(vk="", kie=REAL_KIE).assert_not_called()
        self.track(vk=REAL_VK, kie="").assert_called_once()
        self.track(provider="kie", vk=REAL_VK, kie="").assert_not_called()
        self.track(provider="kie", vk="", kie=REAL_KIE).assert_called_once()

    def test_synthetic_vibecode_key_is_recorded_and_tagged(self):
        # A08: a synthetic* key no longer switches accounting off; the row is tagged.
        meta = self.track(vk="synthetic-vibecode-key").call_args.kwargs["metadata"]
        self.assertIs(meta["synthetic_key"], True)

    def test_unknown_provider_never_records(self):
        self.track(provider="unknown", kie=REAL_KIE).assert_not_called()

    def test_empty_usage_is_flagged_as_unknown_cost(self):
        meta = self.track(usage=None).call_args.kwargs["metadata"]
        self.assertEqual(meta["cost_basis"], "unknown")
        self.assertIs(meta["usage_known"], False)

    def test_model_missing_from_price_snapshot_is_flagged(self):
        meta = self.track(usage={"prompt_tokens": 5, "completion_tokens": 5}, model="not-a-model").call_args.kwargs["metadata"]
        self.assertEqual(meta["cost_basis"], "unknown")
        self.assertIs(meta["price_known"], False)

    def test_kie_call_keeps_kie_provider_and_no_vibecode_metadata(self):
        record = self.track(provider="kie", vk="", kie=REAL_KIE, model="gpt-5-6-sol", usage={"input_tokens": 1, "output_tokens": 1})
        self.assertEqual(record.call_args.kwargs["provider"], "kie")
        meta = record.call_args.kwargs["metadata"]
        self.assertEqual(meta, {"cost_kind": "estimated"})  # no Vibecode pricing fields on a Kie row


class CalcCost(unittest.TestCase):
    def test_vibecode_uses_snapshot_list_price_not_kie_measured_rates(self):
        vibecode = api_tracker_hook._calc_cost("gpt-5.6-sol", 1_000_000, 1_000_000, "text", 1.0, provider="vibecode")
        kie = api_tracker_hook._calc_cost("gpt-5.6-sol", 1_000_000, 1_000_000, "text", 1.0, provider="kie")
        self.assertAlmostEqual(vibecode, 0.426462 + 2.558774, places=6)
        self.assertAlmostEqual(kie, 1.6 + 8.4, places=6)

    def test_vibecode_cached_tokens_use_cache_read_price(self):
        cost = api_tracker_hook._calc_cost("gpt-5.6-sol", 1_000_000, 0, "text", 1.0, cached_tok=400_000, provider="vibecode")
        self.assertAlmostEqual(cost, 0.6 * 0.426462 + 0.4 * 0.042646, places=6)

    def test_vibecode_unknown_model_is_zero_never_a_kie_or_default_rate(self):
        self.assertEqual(api_tracker_hook._calc_cost("mystery-model", 10**6, 10**6, "text", 1.0, provider="vibecode"), 0.0)
        # a Kie-looking name sent through Vibecode still must not match Kie's measured table
        self.assertEqual(api_tracker_hook._calc_cost("gpt-5-6-sol", 10**6, 10**6, "text", 1.0, provider="vibecode"), 0.0)

    def test_price_source_constant_documents_the_uncertainty(self):
        self.assertIn("not calibrated", api_tracker_hook.VIBECODE_PRICE_SOURCE)


class TrackerRowLabel(unittest.TestCase):
    def test_row_is_written_with_vibecode_provider_and_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "tracker.db"
            with patch.object(api_tracker_hook, "_TRACKER_DB", db), \
                    patch.object(api_tracker_hook, "_push_to_supabase", return_value=False), \
                    patch.object(api_tracker_hook, "_get_identity", return_value=("tester", "device")), \
                    patch.object(api_tracker_hook.threading, "Thread", SyncThread):
                api_tracker_hook.record_api_call(provider="vibecode", model="gpt-5.6-sol", prompt_tokens=1000,
                    completion_tokens=100, metadata=api_tracker_hook.vibecode_cost_metadata("gpt-5.6-sol", usage_known=True))
            row = sqlite3.connect(str(db)).execute("SELECT provider, cost_usd, metadata_json FROM api_calls").fetchone()
        self.assertEqual(row[0], "vibecode")
        self.assertAlmostEqual(row[1], 1000 / 1e6 * 0.426462 + 100 / 1e6 * 2.558774, places=6)
        self.assertIs(json.loads(row[2])["calibrated"], False)


class TelemetryCost(unittest.TestCase):
    @staticmethod
    def row(**kw):
        base = dict(model="gpt-5.6-sol", provider="vibecode", cost_usd=0.0, prompt_tokens=0, completion_tokens=0,
                    cached_tokens=0, status_code=200, metadata_json=None)
        base.update(kw)
        return base

    def test_groups_by_provider_and_counts_unknown_cost(self):
        from app.services.telemetry import cost_summary
        rows = [self.row(cost_usd=0.002, prompt_tokens=100, completion_tokens=20),
                self.row(metadata_json=json.dumps({"cost_basis": "unknown", "usage_known": False})),
                self.row(status_code=402, metadata_json=json.dumps({"cost_basis": "unknown"})),  # failed: not "unknown cost"
                self.row(model="gpt-5-6-sol", provider="Kie.ai", cost_usd=0.01)]
        summary = cost_summary(rows)
        self.assertEqual(summary["by_provider"]["vibecode"]["calls"], 3)
        self.assertEqual(summary["by_provider"]["kie"]["calls"], 1)
        self.assertEqual(summary["unknown_cost_calls"], 1)
        self.assertEqual(summary["by_provider"]["vibecode"]["unknown_cost_calls"], 1)
        self.assertIn("не откалибрована", summary["sources"]["vibecode"])  # operator sees Russian
        self.assertIn("NOT calibrated", summary["sources_raw"]["vibecode"])  # raw string kept for old readers
        self.assertIn("откалиброваны", summary["sources"]["kie"])

    def test_sources_only_list_providers_that_are_present(self):
        from app.services.telemetry import cost_summary
        self.assertEqual(set(cost_summary([self.row(provider="Kie.ai")])["sources"]), {"kie"})
        self.assertEqual(cost_summary([])["sources"], {})

    def test_rows_without_provider_column_still_summarize(self):
        from app.services.telemetry import cost_summary
        legacy = {"model": "gpt-5-6-sol", "cost_usd": 0.01, "prompt_tokens": 1, "completion_tokens": 1,
                  "cached_tokens": 0, "status_code": 200}
        summary = cost_summary([legacy])
        self.assertEqual(summary["total_calls"], 1)
        self.assertEqual(summary["unknown_cost_calls"], 0)

    def test_query_returns_provider_and_metadata_and_falls_back_on_old_schema(self):
        from app.services.telemetry import _query_cost_rows_sync
        with tempfile.TemporaryDirectory() as tmp:
            for name, extra in (("new.db", ", provider TEXT, metadata_json TEXT"), ("old.db", "")):
                db = Path(tmp) / name
                with sqlite3.connect(str(db)) as conn:
                    conn.execute("CREATE TABLE api_calls (id INTEGER PRIMARY KEY, timestamp TEXT, model TEXT, cost_usd REAL, "
                                 "prompt_tokens INTEGER, completion_tokens INTEGER, cached_tokens INTEGER, "
                                 f"status_code INTEGER, project_source TEXT{extra})")
                    conn.execute("INSERT INTO api_calls (timestamp, model, cost_usd, project_source) VALUES (?, 'm', 0.1, '7')",
                                 (datetime.utcnow().isoformat(),))
                with patch.object(api_tracker_hook, "_TRACKER_DB", db):
                    rows = _query_cost_rows_sync(7, None)
                self.assertEqual(len(rows), 1)
                self.assertEqual("provider" in rows[0], name == "new.db")


class CreditsAndStatusCodes(unittest.TestCase):
    def test_402_is_text_credits_for_both_providers_without_echoing_the_body(self):
        for provider, name in (("vibecode", "vibecode.moe"), ("kie", "kie.ai")):
            with self.subTest(provider=provider):
                exc = GptApiError("GPT HTTP 402: secret-body-detail", context={
                    "status_code": 402, "retryable": False, "llm_provider": provider})
                code, message = describe_error(exc)
                self.assertEqual(code, "gpt_credits")
                self.assertIn(name, message)
                self.assertNotIn("secret-body-detail", message)

    def test_402_without_provider_names_both(self):
        code, message = describe_error(GptApiError("GPT HTTP 402", context={"status_code": 402}))
        self.assertEqual(code, "gpt_credits")
        self.assertIn("vibecode.moe", message)
        self.assertIn("kie.ai", message)

    def test_credit_envelope_in_http_200_is_credits_too(self):
        from app.services.gpt_api import _check_provider_envelope
        for payload in ({"error": {"message": "insufficient balance", "code": "insufficient_quota"}},
                        {"code": 402, "msg": "Credits insufficient"}):
            with self.subTest(payload=payload):
                with self.assertRaises(GptApiError) as raised:
                    _check_provider_envelope(payload)
                self.assertEqual(describe_error(raised.exception)[0], "gpt_credits")
                self.assertEqual(raised.exception.context["status_code"], 402)
                self.assertFalse(raised.exception.retryable)

    def test_media_402_stays_media_credits(self):
        exc = GptApiError("x", context={"status_code": 402, "provider": "outsee"})
        self.assertEqual(describe_error(exc)[0], "media_credits")

    def test_credit_hint_names_the_provider_that_ran_the_request(self):
        from app.services.gpt_api import _check_provider_envelope
        from app.services.llm_override import NodeLlmOverride, use_override
        payload = {"code": 402, "msg": "Credits insufficient"}
        for provider, label in (("vibecode", "Vibecode"), ("kie", "Kie")):
            with use_override(NodeLlmOverride("m", "stable", "text", provider, "m")):
                with self.assertRaises(GptApiError) as raised:
                    _check_provider_envelope(payload)
            self.assertIn(f"на счёте {label} кончились кредиты", str(raised.exception))

    def test_other_statuses_map_to_catalog_codes(self):
        for status, code in ((401, "gpt_auth"), (429, "gpt_rate_limit"), (500, "gpt_server"), (503, "gpt_server"),
                             (408, "gpt_timeout")):
            with self.subTest(status=status):
                self.assertEqual(describe_error(GptApiError("x", context={"status_code": status}))[0], code)

    def test_stream_that_died_before_any_text_maps_to_timeout_or_network(self):
        for cause, code in (("ReadTimeout", "gpt_timeout"), ("ReadError", "gpt_network")):
            exc = GptApiError("empty", context={"error_kind": "empty_stream", "stream_error": cause})
            self.assertEqual(describe_error(exc)[0], code)

    def test_code_is_in_catalog(self):
        self.assertIn("gpt_credits", all_error_codes())
        self.assertIn("Vibecode", ERROR_CATALOG["gpt_credits"].hint)

    def test_vibecode_401_has_no_grsai_hint(self):
        from app.services.gpt_api import _raise_http_status
        from app.services.llm_override import NodeLlmOverride, use_override
        for provider, expected in (("vibecode", False), ("kie", True)):
            with use_override(NodeLlmOverride("m", "stable", "text", provider, "m")):
                with self.assertRaises(GptApiError) as raised:
                    _raise_http_status(401, "apikey error", use_model="m")
            self.assertEqual("grsai" in str(raised.exception), expected)


class StepFailurePolicyCredits(unittest.IsolatedAsyncioTestCase):
    async def test_vibecode_402_pauses_without_soft_retry_and_keeps_credits_code(self):
        from app.models import Project, ProjectStatus
        from app.services.step_failure_policy import record_step_failure
        project = Project(id=3, slug="vk-402", topic="t", status=ProjectStatus.generating_images, meta={})
        session = MagicMock()
        session.flush = AsyncMock()
        error = GptApiError("GPT HTTP 402: x", context={"status_code": 402, "retryable": False, "llm_provider": "vibecode"})
        with patch("app.services.step_failure_policy._soft_retry_without_wipe", new_callable=AsyncMock) as soft, \
                patch("app.services.project_control.pause_project", new_callable=AsyncMock) as pause, \
                patch("app.services.run_sync.mark_running_node_failed", new_callable=AsyncMock) as failed:
            action = await record_step_failure(session, project, error=error)
        self.assertEqual(action, "pause_infra")
        soft.assert_not_awaited()
        pause.assert_awaited_once()
        self.assertEqual(failed.await_args.kwargs["error_code"], "gpt_credits")
        state = project.meta["step_failure"]
        self.assertEqual(state["last_error_code"], "gpt_credits")
        self.assertNotIn("GPT HTTP", state["last_error"])  # fixed text, not the provider body

    async def test_non_numeric_provider_code_does_not_break_the_policy(self):
        from app.models import Project, ProjectStatus
        from app.services.step_failure_policy import record_step_failure
        project = Project(id=4, slug="vk-env", topic="t", status=ProjectStatus.generating_images, meta={})
        session = MagicMock()
        session.flush = AsyncMock()
        error = GptApiError("GPT провайдер error: boom", context={"provider_code": "insufficient_quota", "retryable": False})
        with patch("app.services.step_failure_policy._soft_retry_without_wipe", new_callable=AsyncMock), \
                patch("app.services.run_sync.update_active_node_progress_text", new_callable=AsyncMock), \
                patch("app.services.run_sync.prepare_node_for_step_start", new_callable=AsyncMock), \
                patch("app.project_db.run_on_replica_project", new_callable=AsyncMock):
            action = await record_step_failure(session, project, error=error)
        self.assertEqual(action, "retry")  # not a crash inside is_credits_failure(int("insufficient_quota"))


SCOPE = SimpleNamespace(execution_id="execution", project_id=2, node_keys=["script"], observer_state="active", gaps=[])


def event(kind, *, request="r1", attempt="a1", node="script", **payload):
    event.counter += 1
    return dict(id=event.counter, event_id=f"e{event.counter}", execution_id="execution", project_id=2,
        node_key=node, attempt_id=attempt, request_id=request, kind=kind, payload=payload,
        db_scope="project", created_at="2026-10-03 12:00:00")
event.counter = 1000


class DiagnosisVibecode(unittest.TestCase):
    def test_http_402_is_a_certain_api_error_with_credits_title(self):
        rows = [event("model_start", provider="vibecode"),
                event("model_error", error_code="gpt_credits", status_code=402, uncertain=False,
                      catalog_source="structured_exception_context", provider="vibecode")]
        incident = derive_incidents(SCOPE, rows, {})[0]
        self.assertEqual(incident["type"], "api_error")
        self.assertEqual(incident["catalog_code"], "gpt_credits")
        self.assertEqual(incident["fact"], ERROR_CATALOG["gpt_credits"].title)

    def test_read_timeout_after_accept_is_uncertain_request(self):
        rows = [event("model_start", provider="vibecode"),
                event("model_error", error_code="gpt_timeout", uncertain=True, error_type="ReadTimeout",
                      catalog_source="exception_type_or_text")]
        incident = derive_incidents(SCOPE, rows, {})[0]
        self.assertEqual(incident["type"], "uncertain_request")
        self.assertTrue(incident["evidence_incomplete"])

    def test_node_error_rule_becomes_contract_rejected_with_rule_shown(self):
        rows = [event("model_start"), event("model_end", complete=True),
                event("node_error", request=None, rule="R-SHOT-COUNT", error_type="PassStopError")]
        incidents = derive_incidents(SCOPE, rows, {})
        self.assertEqual([i["type"] for i in incidents], ["contract_rejected"])
        self.assertEqual(incidents[0]["rule"], "R-SHOT-COUNT")
        self.assertIn("R-SHOT-COUNT", incidents[0]["fact"])
        self.assertEqual(incidents[0]["status"], "human_review")

    def test_node_error_without_rule_keeps_the_unknown_reason_wording(self):
        rows = [event("model_start"), event("model_end", complete=True), event("node_error", request=None)]
        incident = derive_incidents(SCOPE, rows, {})[0]
        self.assertEqual(incident["type"], "contract_rejected")
        self.assertNotIn("rule", incident)

    def test_rule_is_bounded_and_ignored_when_not_a_string(self):
        long_rule = "R-" + "X" * 500
        item = derive_incidents(SCOPE, [event("node_error", request=None, rule=long_rule)], {})[0]
        self.assertLessEqual(len(item["rule"]), 120)
        other = derive_incidents(SCOPE, [event("node_error", request=None, rule=["a"])], {})[0]
        self.assertNotIn("rule", other)
