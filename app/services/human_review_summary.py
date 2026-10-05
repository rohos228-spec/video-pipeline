"""Structured, human-readable summary for an operator at the moment a
decision escalates to human review. Additive only: it reads already-recorded
Incident/DecisionRecord data and never changes status, budget, or any other
field those rows already own.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.services.diagnosis import TITLES
from app.services.error_catalog import ERROR_CATALOG

STATUS_LABEL = "REQUIRES_HUMAN_INTERVENTION"


@dataclass(frozen=True)
class HumanReviewSummary:
    status_label: str
    node_key: str
    incident_id: str
    incident_type: str
    incident_title: str
    escalation_reason: str
    model_steps_used: int
    max_model_steps: int
    evidence_event_count: int
    provider_error_code: str | None
    provider_error_title: str | None
    recommended_next_step: str | None
    text: str
    # Optional detail (defaults keep older callers unchanged).
    rule: str | None = None
    rule_diary: dict[str, Any] | None = None
    hold_reason: str | None = None
    hold_failing_frames: tuple[dict[str, Any], ...] = ()
    frames_source: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "status_label": self.status_label,
            "node_key": self.node_key,
            "incident_id": self.incident_id,
            "incident_type": self.incident_type,
            "incident_title": self.incident_title,
            "escalation_reason": self.escalation_reason,
            "model_steps_used": self.model_steps_used,
            "max_model_steps": self.max_model_steps,
            "evidence_event_count": self.evidence_event_count,
            "provider_error_code": self.provider_error_code,
            "provider_error_title": self.provider_error_title,
            "recommended_next_step": self.recommended_next_step,
            "text": self.text,
            "rule": self.rule,
            "rule_diary": self.rule_diary,
            "hold_reason": self.hold_reason,
            "hold_failing_frames": list(self.hold_failing_frames),
            "frames_source": self.frames_source,
        }


def build_human_review_summary(
    *,
    node_key: str,
    incident_id: str,
    incident_type: str,
    incident_details: dict[str, Any],
    escalation_reason: str,
    model_steps_used: int,
    max_model_steps: int,
) -> HumanReviewSummary:
    """Pure function: no I/O, no DB access — caller supplies already-fetched
    Incident fields so this stays trivially unit-testable.
    """
    incident_title = TITLES.get(incident_type, incident_type)
    evidence_event_count = len(incident_details.get("event_ids") or [])
    catalog_code = incident_details.get("catalog_code")
    provider_spec = ERROR_CATALOG.get(catalog_code) if catalog_code else None
    provider_error_title = provider_spec.title if provider_spec else None
    recommendation = incident_details.get("recommendation") or {}
    recommended_next_step = recommendation.get("title") or recommendation.get("reason")
    circuit_breaker_tripped = model_steps_used >= max_model_steps
    rule = incident_details.get("rule") if isinstance(incident_details.get("rule"), str) else None
    rule_diary = incident_details.get("rule_diary") if isinstance(incident_details.get("rule_diary"), dict) else None
    hold = incident_details.get("hold_explanation") if isinstance(incident_details.get("hold_explanation"), dict) else None
    hold_reason = hold.get("summary") if hold else None
    hold_frames = tuple(f for f in (hold.get("failing_frames") or []) if isinstance(f, dict))[:20] if hold else ()
    frames_source = incident_details.get("frames_source") if isinstance(incident_details.get("frames_source"), str) else None

    parts = [
        f"Нода '{node_key}' требует решения человека.",
        f"Инцидент: {incident_title} ({incident_type}, id={incident_id}).",
    ]
    if provider_error_title:
        parts.append(f"Причина по данным провайдера: {provider_error_title} ({catalog_code}).")
    if circuit_breaker_tripped:
        parts.append(
            f"Лимит шагов модели исчерпан: использовано {model_steps_used} из {max_model_steps} "
            "без подтверждённого решения."
        )
    if rule:
        from app.services.node_rules import rule_title
        rule_line = f"Правило, остановившее результат: {rule} ({rule_title(rule)})."
        if rule_diary and rule_diary.get("rules"):
            fired = ", ".join(f"{k} x{v}" for k, v in rule_diary["rules"].items())
            rule_line += f" Правила, сработавшие в коде за эту попытку: {fired}."
        parts.append(rule_line)
    if hold_reason:
        parts.append(f"Почему встроенная проверка поставила запуск на паузу: {hold_reason}")
    if frames_source:
        parts.append(f"Кадры получены из: {frames_source}.")
    parts.append(f"Причина эскалации: {escalation_reason}")
    parts.append(f"Доказательства: записано событий - {evidence_event_count}.")
    if recommended_next_step:
        parts.append(f"Рекомендуемый следующий шаг: {recommended_next_step}")
    parts.append(
        "Автоматических действий по этой ноде больше не будет, пока оператор "
        "не изучит случай и явно не продолжит выполнение."
    )

    return HumanReviewSummary(
        status_label=STATUS_LABEL,
        node_key=node_key,
        incident_id=incident_id,
        incident_type=incident_type,
        incident_title=incident_title,
        escalation_reason=escalation_reason,
        model_steps_used=model_steps_used,
        max_model_steps=max_model_steps,
        evidence_event_count=evidence_event_count,
        provider_error_code=catalog_code,
        provider_error_title=provider_error_title,
        recommended_next_step=recommended_next_step,
        text=" ".join(parts),
        rule=rule,
        rule_diary=rule_diary,
        hold_reason=hold_reason,
        hold_failing_frames=hold_frames,
        frames_source=frames_source,
    )
