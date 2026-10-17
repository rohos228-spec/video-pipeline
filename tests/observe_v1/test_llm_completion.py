from app.services.llm_completion import response_complete, response_truncated


def test_responses_api_completed_status():
    assert response_complete({"status": "completed"})
    assert not response_complete({"status": "in_progress"})
    assert not response_complete({"status": "incomplete"})
    assert response_truncated({"status": "incomplete"})


def test_chat_completions_finish_reason_stop_is_complete():
    raw = {"choices": [{"finish_reason": "stop", "message": {"content": "{}"}}]}
    assert response_complete(raw)
    assert not response_truncated(raw)


def test_chat_completions_finish_reason_length_is_truncated_not_complete():
    raw = {"choices": [{"finish_reason": "length"}]}
    assert not response_complete(raw)
    assert response_truncated(raw)


def test_explicit_finish_reason_argument_wins_over_missing_raw():
    assert response_complete({}, "stop")
    assert response_truncated({}, "length")


def test_missing_signal_is_not_complete():
    assert not response_complete({})
    assert not response_complete(None)
    assert not response_truncated(None)


def test_salvaged_sse_is_never_complete():
    assert not response_complete({"status": "completed", "sse_salvaged": True})
    assert not response_complete({"choices": [{"finish_reason": "stop"}], "sse_salvaged": True})
