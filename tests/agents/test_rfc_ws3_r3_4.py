"""RFC ws3-r3.4 совпадает с контрактом после бампа версии."""

from __future__ import annotations

from pathlib import Path

from app.orchestrator.contracts import CONTRACT_VERSION

_RFC = Path(__file__).resolve().parents[2] / "docs" / "fable" / "rfc_ws3_r3_4.md"


def test_rfc_ws3_r3_4_topics_match_the_contract() -> None:
    text = _RFC.read_text(encoding="utf-8")
    assert CONTRACT_VERSION == "ws3-r3.5"
    assert "ws3-r3.4" in text
    assert "kill_switch" in text
    assert "missing_project_id" in text
    assert "message_start" in text
    assert "message_end" in text
    assert "message_id" in text
    for heading in (
        "## (a) Kill switch полем `MessageIn`",
        "## (b) Поздний `project_id`",
        "## (c) Граница сообщения в SSE",
    ):
        assert heading in text
    assert "POST /api/orchestrator/chat/{session}/project" in text
    assert "CONTRACT_VERSION" in text
