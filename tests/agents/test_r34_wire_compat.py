"""ws3-r3.4 добавляет поля и оставляет старые payload r3.3 валидными."""

from __future__ import annotations

from app.orchestrator.contracts import (
    CONTRACT_VERSION,
    EPHEMERAL_EVENT_TYPES,
    ApprovalDecision,
    ApprovalDecisionBody,
    ApprovalEvent,
    MessageMode,
    export_contract_schemas,
)
from app.web.routers.orchestrator_chat import MessageIn


def test_r34_keeps_r33_payloads_and_adds_boundary_events() -> None:
    schema = export_contract_schemas()
    assert schema["version"] == CONTRACT_VERSION == "ws3-r3.4"
    assert schema["enums"]["MessageMode"] == [mode.value for mode in MessageMode]
    assert "kill_switch" not in schema["enums"]["MessageMode"]
    assert schema["enums"]["ApprovalDecision"] == [item.value for item in ApprovalDecision]
    assert "ApprovalDecisionBody" in schema["models"]
    labels = schema["rules"]["approval_decision_labels"]
    assert labels["approve"] == "Шаг выполнен, продолжить"
    assert labels["retry_step"] == "Перезапустить шаг"
    assert labels["deny"] == "Остановить задачу"
    assert "message_start" in schema["enums"]["EventType"]
    assert "message_end" in schema["enums"]["EventType"]
    assert {"token"} == EPHEMERAL_EVENT_TYPES
    note33 = next(item["note"] for item in schema["changelog"] if item["version"] == "ws3-r3.3")
    note34 = next(item["note"] for item in schema["changelog"] if item["version"] == "ws3-r3.4")
    assert "paid_outcome_unknown" not in note33
    assert "retry_grant" not in note33
    assert "kill_switch" in note34
    assert "message_start" in note34
    assert "project_id" in note34

    body = ApprovalDecisionBody.model_validate({})
    assert body.decision is None
    assert body.retry_grant is False
    event = ApprovalEvent.model_validate({"reason": "risk", "action": "look", "task_id": 1})
    assert event.change_id is None
    message = MessageIn.model_validate({"text": "привет", "mode": "append", "project_id": 13})
    assert message.kill_switch is False
    assert message.mode is MessageMode.append
    assert message.project_id == 13
    old_token = {"text": "кусок"}
    assert old_token["text"] == "кусок"
    assert "message_id" not in old_token
