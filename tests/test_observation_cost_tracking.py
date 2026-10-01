from unittest.mock import patch

from app.services.observation import _track_api_cost, _usage_tokens


def test_usage_tokens_responses_api_naming():
    assert _usage_tokens({"input_tokens": 100, "output_tokens": 40}) == (100, 40, 0)


def test_usage_tokens_chat_completions_naming():
    assert _usage_tokens({"prompt_tokens": 50, "completion_tokens": 20}) == (50, 20, 0)


def test_usage_tokens_cached_tokens_top_level():
    assert _usage_tokens({"input_tokens": 100, "output_tokens": 40, "cached_tokens": 30}) == (100, 40, 30)


def test_usage_tokens_cached_tokens_in_details():
    usage = {"input_tokens": 100, "output_tokens": 40, "input_tokens_details": {"cached_tokens": 25}}
    assert _usage_tokens(usage) == (100, 40, 25)


def test_usage_tokens_none_or_missing_is_zero():
    assert _usage_tokens(None) == (0, 0, 0)
    assert _usage_tokens({}) == (0, 0, 0)


def _with_real_key():
    """api_tracker_hook.record_api_call must only ever fire for a real
    (non-synthetic, non-empty) GPT_API_KEY -- see _track_api_cost's
    docstring for why: it pushes to the operator's own cross-project
    Supabase tracker, and every test in this repo otherwise runs with a
    synthetic or absent key by convention.
    """
    from app.settings import settings
    return patch.object(settings, "gpt_api_key", "sk-real-looking-key-not-synthetic")


def test_track_api_cost_delegates_to_api_tracker_hook_with_real_key():
    with _with_real_key(), patch("app.services.api_tracker_hook.record_api_call") as mock_record:
        _track_api_cost(model="gpt-5-6-sol", usage={"input_tokens": 10, "output_tokens": 5},
            duration_sec=1.5, project_id=7)
    mock_record.assert_called_once()
    _, kwargs = mock_record.call_args
    assert kwargs["provider"] == "kie"
    assert kwargs["model"] == "gpt-5-6-sol"
    assert kwargs["prompt_tokens"] == 10
    assert kwargs["completion_tokens"] == 5
    assert kwargs["duration_sec"] == 1.5
    assert kwargs["project_source"] == "7"


def test_track_api_cost_unknown_project_is_empty_string():
    with _with_real_key(), patch("app.services.api_tracker_hook.record_api_call") as mock_record:
        _track_api_cost(model="gpt-5-6-sol", usage=None, duration_sec=0.1, project_id=None)
    assert mock_record.call_args.kwargs["project_source"] == ""


def test_track_api_cost_never_raises_even_if_hook_fails():
    with _with_real_key(), patch("app.services.api_tracker_hook.record_api_call", side_effect=RuntimeError("boom")):
        _track_api_cost(model="x", usage=None, duration_sec=0.1, project_id=1)  # must not raise


def test_track_api_cost_skips_synthetic_key():
    from app.settings import settings
    with patch.object(settings, "gpt_api_key", "synthetic-observe-test-key"), \
         patch("app.services.api_tracker_hook.record_api_call") as mock_record:
        _track_api_cost(model="gpt-5-6-sol", usage={"input_tokens": 10, "output_tokens": 5},
            duration_sec=1.5, project_id=7)
    mock_record.assert_not_called()


def test_track_api_cost_skips_empty_key():
    from app.settings import settings
    with patch.object(settings, "gpt_api_key", ""), \
         patch("app.services.api_tracker_hook.record_api_call") as mock_record:
        _track_api_cost(model="gpt-5-6-sol", usage=None, duration_sec=0.1, project_id=7)
    mock_record.assert_not_called()


def _real_kie_usage():
    import json
    from pathlib import Path
    path = Path(__file__).parent / "fixtures" / "kie_responses_usage_real.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_usage_tokens_real_kie_response_reports_input_only_under_attribution():
    # Captured from a real paid call to https://api.kie.ai/codex/v1/responses
    # (gpt-5-6-sol, prompt "Reply with exactly the single word: OK").
    usage = _real_kie_usage()
    assert usage["input_tokens"] == 0  # the top-level figure really is zero
    prompt, completion, cached = _usage_tokens(usage)
    assert (prompt, completion, cached) == (4394, 5, 4224)


def test_calc_cost_matches_kie_dashed_model_names_to_the_rate_table():
    from app.services.api_tracker_hook import _calc_cost
    dotted = _calc_cost("gpt-5.6-sol", 4394, 5, "text", 1.0)
    dashed = _calc_cost("gpt-5-6-sol", 4394, 5, "text", 1.0)
    assert dashed == dotted == round(4394 / 1e6 * 1.20 + 5 / 1e6 * 4.80, 6)
