from app.services.human_review_summary import STATUS_LABEL, build_human_review_summary


def test_summary_includes_provider_error_and_recommendation():
    summary = build_human_review_summary(
        node_key="n1",
        incident_id="inc-1",
        incident_type="api_error",
        incident_details={
            "event_ids": ["e1", "e2", "e3"],
            "catalog_code": "gpt_rate_limit",
            "recommendation": {"title": "Передать человеку", "reason": "api_error"},
        },
        escalation_reason="model step limit exhausted",
        model_steps_used=3,
        max_model_steps=3,
    )
    assert summary.status_label == STATUS_LABEL == "REQUIRES_HUMAN_INTERVENTION"
    assert summary.node_key == "n1"
    assert summary.evidence_event_count == 3
    assert summary.provider_error_code == "gpt_rate_limit"
    assert summary.provider_error_title == "GPT: лимит запросов (429)"
    assert summary.recommended_next_step == "Передать человеку"
    assert "использовано 3 из 3" in summary.text
    assert "gpt_rate_limit" in summary.text
    assert "автоматических действий" in summary.text.lower()


def test_summary_without_circuit_breaker_or_catalog_code():
    summary = build_human_review_summary(
        node_key="n2",
        incident_id="inc-2",
        incident_type="invalid_json",
        incident_details={"event_ids": []},
        escalation_reason="recovery failed verification; no further attempt",
        model_steps_used=1,
        max_model_steps=3,
    )
    assert "Лимит шагов модели исчерпан" not in summary.text
    assert summary.provider_error_code is None
    assert summary.provider_error_title is None
    assert summary.evidence_event_count == 0
    assert "Ответ не разобран как обязательный JSON" in summary.text


def test_as_dict_round_trips_all_fields():
    summary = build_human_review_summary(
        node_key="n3",
        incident_id="inc-3",
        incident_type="missing_payload",
        incident_details={"event_ids": ["e1"]},
        escalation_reason="x",
        model_steps_used=0,
        max_model_steps=3,
    )
    d = summary.as_dict()
    assert d["status_label"] == "REQUIRES_HUMAN_INTERVENTION"
    assert d["node_key"] == "n3"
    assert d["incident_id"] == "inc-3"
    assert d["evidence_event_count"] == 1
    assert isinstance(d["text"], str) and d["text"]
