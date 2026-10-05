"""План: упорядоченные шаги, preview без исполнения, детерминированный хэш."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from app.services.action_layer.spec import ActionRegistry, CostEstimate, max_risk

PLAN_HASH_VERSION = 1


class PlanFormatError(ValueError):
    pass


@dataclass
class PlanStep:
    index: int
    action_id: str
    params: dict = field(default_factory=dict)
    depends_on: list[int] = field(default_factory=list)

    def to_json(self) -> dict:
        return {"index": self.index, "action_id": self.action_id,
                "params": self.params, "depends_on": list(self.depends_on)}


@dataclass
class Plan:
    steps: list[PlanStep]
    request_text: str = ""

    @classmethod
    def from_json(cls, data: Any, *, max_steps: int, request_text: str = "") -> "Plan":
        """Строгий разбор: лишние поля, нет шагов или их слишком много = ошибка."""
        if not isinstance(data, list) or not data:
            raise PlanFormatError("steps must be a non-empty list")
        if len(data) > max_steps:
            raise PlanFormatError(f"too many steps (max {max_steps})")
        steps: list[PlanStep] = []
        for i, raw in enumerate(data):
            if not isinstance(raw, dict):
                raise PlanFormatError(f"step {i}: must be an object")
            extra = set(raw) - {"action_id", "params", "depends_on"}
            if extra:
                raise PlanFormatError(f"step {i}: unknown fields {sorted(extra)}")
            action_id = raw.get("action_id")
            params = raw.get("params", {})
            deps = raw.get("depends_on", [])
            if not isinstance(action_id, str) or not action_id:
                raise PlanFormatError(f"step {i}: action_id required")
            if not isinstance(params, dict):
                raise PlanFormatError(f"step {i}: params must be an object")
            if (not isinstance(deps, list)
                    or any(not isinstance(d, int) or isinstance(d, bool) for d in deps)):
                raise PlanFormatError(f"step {i}: depends_on must be a list of integers")
            steps.append(PlanStep(i, action_id, params, sorted(set(deps))))
        return cls(steps, request_text)

    def steps_json(self) -> list[dict]:
        return [{"action_id": s.action_id, "params": s.params, "depends_on": s.depends_on}
                for s in self.steps]

    def project_id(self) -> int | None:
        ids = {s.params.get("project_id") for s in self.steps}
        ids.discard(None)
        if len(ids) == 1:
            value = next(iter(ids))
            return value if isinstance(value, int) and not isinstance(value, bool) else None
        return None


def plan_hash(plan: Plan, registry: ActionRegistry) -> str:
    """Хэш содержимого шагов и свойств безопасности их действий.

    Текст запроса не входит: подтверждается то, что будет исполнено.
    Смена риска/схемы действия после подтверждения меняет хэш.
    """
    body = {
        "v": PLAN_HASH_VERSION,
        "steps": [
            {"i": s.index, "a": s.action_id, "p": s.params, "d": s.depends_on,
             "f": (registry.find(s.action_id).fingerprint()
                   if registry.find(s.action_id) else "unknown")}
            for s in plan.steps
        ],
    }
    raw = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def build_preview(plan: Plan, registry: ActionRegistry, ctx) -> dict:
    """Проверка параметров и предусловий, оценка стоимости. Ничего не исполняет.

    ``ctx`` обязан быть read-only: запись через него бросает WriteNotAllowed.
    """
    ro_ctx = ctx.readonly_copy()
    errors: list[str] = []
    steps_out: list[dict] = []
    risks: list[str] = []
    low = high = 0.0
    unknown = 0
    destructive_effects: list[dict] = []
    for step in plan.steps:
        spec = registry.find(step.action_id)
        item: dict = {"index": step.index, "action_id": step.action_id,
                      "params": step.params, "depends_on": step.depends_on,
                      "valid": True, "errors": [], "precondition_failures": []}
        for dep in step.depends_on:
            if dep >= step.index or dep < 0:
                item["errors"].append(f"depends_on {dep} must reference an earlier step")
        if spec is None:
            item["errors"].append(f"unknown action {step.action_id!r}")
            item["valid"] = False
            steps_out.append(item)
            errors.append(f"step {step.index}: unknown action {step.action_id!r}")
            continue
        item.update({"title_ru": spec.title_ru, "risk": spec.risk,
                     "spends_money": spec.spends_money, "destructive": spec.destructive,
                     "reversible": spec.reversible})
        risks.append(spec.risk)
        item["errors"].extend(registry.validate_params(spec.id, step.params))
        if not item["errors"]:
            for pre in spec.preconditions:
                try:
                    reason = await pre.check(ro_ctx, step.params)
                except Exception as exc:  # noqa: BLE001 - неизвестное = не выполнено
                    reason = f"check failed: {type(exc).__name__}: {exc}"
                if reason:
                    item["precondition_failures"].append(
                        {"id": pre.id, "reason": str(reason)})
        est: CostEstimate = spec.estimate(step.params) if not item["errors"] else CostEstimate.unknown()
        item["cost"] = est.to_json()
        if spec.spends_money:
            if est.known:
                low += est.low
                high += est.high
            else:
                unknown += 1
        if spec.destructive and not item["errors"]:
            text = (spec.destructive_effects(step.params) if spec.destructive_effects
                    else spec.description_ru)
            destructive_effects.append(
                {"index": step.index, "action_id": spec.id, "text": text})
        if item["errors"] or item["precondition_failures"]:
            item["valid"] = False
            for e in item["errors"]:
                errors.append(f"step {step.index}: {e}")
            for f in item["precondition_failures"]:
                errors.append(f"step {step.index}: precondition {f['id']}: {f['reason']}")
        steps_out.append(item)
    needs_approval = any(
        registry.find(s.action_id) is None or registry.find(s.action_id).needs_approval
        for s in plan.steps)
    return {
        "ok": not errors,
        "errors": errors,
        "plan_hash": plan_hash(plan, registry),
        "risk_max": max_risk(risks),
        "needs_approval": needs_approval,
        "steps": steps_out,
        "cost": {"low": low, "high": high, "unknown_steps": unknown,
                 "known": unknown == 0},
        "destructive_effects": destructive_effects,
    }
