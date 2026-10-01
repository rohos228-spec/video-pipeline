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

    parts = [
        f"Node '{node_key}' requires human intervention.",
        f"Incident: {incident_title} ({incident_type}, id={incident_id}).",
    ]
    if provider_error_title:
        parts.append(f"Provider-reported cause: {provider_error_title} ({catalog_code}).")
    if circuit_breaker_tripped:
        parts.append(
            f"Circuit breaker tripped: {model_steps_used}/{max_model_steps} decision "
            "steps used without a verified resolution."
        )
    parts.append(f"Escalation reason: {escalation_reason}")
    parts.append(f"Evidence available: {evidence_event_count} recorded event(s).")
    if recommended_next_step:
        parts.append(f"Suggested next step: {recommended_next_step}")
    parts.append(
        "No further automated action will be taken on this node until an "
        "operator reviews and explicitly resumes it."
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
    )
