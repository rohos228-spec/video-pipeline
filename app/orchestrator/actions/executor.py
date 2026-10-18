"""Прямой вызов одного ActionSpec. Ответ — ActionResult ws2-r2.3.

Слой планов Никиты и одобрение по plan_hash не используются.
change_id вида orch-appr-<task>-<n> адаптер не выдаёт.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import ConfigDict

from app.orchestrator.action_contracts.contracts import ActionMetadata
from app.orchestrator.action_contracts.contracts import ActionResult
from app.orchestrator.action_contracts.contracts import ApprovalPreview
from app.orchestrator.action_contracts.contracts import ApprovalRequired
from app.orchestrator.action_contracts.contracts import metadata_of as contract_metadata
from app.services.action_layer.context import ActionContext
from app.services.action_layer.spec import ensure_builtin_actions, registry
from app.services.redact import redact_text

_CORE_CHANGE_ID = re.compile(r"^orch-appr-[0-9]+-[0-9]+$")


class BoundResult(ActionResult):
    """Обычный ответ ActionSpec: свободный dict не входит в союз r2.3.

    Поля конверта те же. Схема контракта не меняется: это подкласс вызова,
    а не новая модель провода для карточек правки и одобрения.
    """

    model_config = ConfigDict(extra="forbid")

    result: Any = None


def _failed(message: str, warnings: list[str] | None = None) -> ActionResult:
    text = redact_text(str(message or "")).strip() or "ошибка"
    return ActionResult(status="failed", result=None, error=text[:500], warnings=list(warnings or []))


def _warnings_of(raw: dict) -> list[str]:
    found: list[str] = []
    extra = raw.get("warnings")
    if isinstance(extra, list):
        found.extend(str(item) for item in extra if str(item).strip())
    one = raw.get("warning")
    if isinstance(one, str) and one.strip():
        found.append(one.strip())
    return found


def _action_context(ctx: Any) -> ActionContext:
    if isinstance(ctx, ActionContext):
        return ctx
    if not isinstance(ctx, dict):
        raise TypeError("ctx должен быть ActionContext или dict")
    return ActionContext(
        app=ctx.get("app"),
        operator=str(ctx.get("operator") or "system"),
        plan_id=ctx.get("plan_id"),
        step_index=ctx.get("step_index"),
        idempotency_key=ctx.get("idempotency_key"),
        read_only=bool(ctx.get("read_only")),
    )


def _refusal(raw: dict) -> str | None:
    if raw.get("rejected") or raw.get("ok") is False:
        detail = raw.get("error") or raw.get("detail") or raw.get("message") or "отказ"
        return str(detail)
    return None


_NIKITA_TO_CONTRACT: dict[str, tuple[str, bool, bool, bool]] = {
    "read": ("none", True, False, False),
    "local_safe": ("low", False, False, False),
    "paid": ("medium", False, False, True),
    "destructive": ("high", False, True, False),
}

_RISK_RANK = {"none": 0, "low": 1, "medium": 2, "high": 3}


def map_nikita_risk(nikita_risk: str, *, name: str) -> ActionMetadata:
    """Пол яруса Никиты на шкале контракта.

    paid → medium и флаг paid: при одобрении нужна оценка больше нуля.
    destructive → high, destructive и подтверждение.
    """

    try:
        risk, read_only, destructive, paid = _NIKITA_TO_CONTRACT[nikita_risk]
    except KeyError as exc:
        raise ValueError(f"неизвестный ярус {nikita_risk}") from exc
    return ActionMetadata(
        name=name,
        risk=risk,  # type: ignore[arg-type]
        read_only=read_only,
        destructive=destructive,
        paid=paid,
    )


def _raise_to_floor(published: ActionMetadata, floor: ActionMetadata) -> ActionMetadata:
    """Совпадение или ужесточение. Ослабить json или пол яруса нельзя."""

    risk = published.risk if _RISK_RANK[published.risk] >= _RISK_RANK[floor.risk] else floor.risk
    return ActionMetadata(
        name=published.name,
        risk=risk,
        read_only=published.read_only and floor.read_only,
        destructive=published.destructive or floor.destructive,
        paid=published.paid or floor.paid,
    )


def metadata_of(name: str) -> ActionMetadata:
    """Флаги из audit_metadata.json, не слабее пола яруса Никиты.

    create_node@new_group и prompt.create_master остаются destructive.
    """

    published = contract_metadata(str(name))
    spec = registry.find(str(name))
    if spec is None:
        return published
    return _raise_to_floor(published, map_nikita_risk(spec.risk, name=published.name))


def _approved(ctx: Any) -> bool:
    if not isinstance(ctx, dict):
        return False
    if ctx.get("approved") is True:
        return True
    return ctx.get("approval") == "granted"


def _preview_change(spec: Any, params: dict) -> str:
    effects = getattr(spec, "destructive_effects", None)
    text = ""
    if callable(effects):
        try:
            text = str(effects(params) or "")
        except Exception:
            text = ""
    cleaned = redact_text(text).strip()
    if cleaned:
        return cleaned[:500]
    title = str(getattr(spec, "title_ru", "") or spec.id).strip() or spec.id
    return f"{title}: нужно подтверждение"


async def _paid_estimate(spec: Any, params: dict, meta: ActionMetadata) -> float | None:
    """Платный ярус: положительная оценка, если она уже есть. Иначе поле пустое."""

    if not meta.paid or spec.estimator is None:
        return None
    try:
        estimate = spec.estimator(params)
        if hasattr(estimate, "__await__"):
            estimate = await estimate
    except Exception:
        return None
    high = getattr(estimate, "high", None)
    if isinstance(high, bool) or not isinstance(high, (int, float)):
        return None
    if float(high) <= 0:
        return None
    return float(high)


def _detach_cc(body: dict) -> str | None:
    """Идентификатор контроля изменений Никиты. orch-appr-* в ответ не попадает."""

    cc: str | None = None
    proposal = body.get("proposal_id")
    if isinstance(proposal, str) and proposal.strip() and _CORE_CHANGE_ID.match(proposal.strip()) is None:
        cc = proposal.strip()
    change = body.get("change_id")
    if isinstance(change, str) and change.strip():
        token = change.strip()
        if cc is None and _CORE_CHANGE_ID.match(token) is None:
            cc = token
    body.pop("change_id", None)
    return cc


async def _approval_required(spec: Any, params: dict, meta: ActionMetadata) -> ActionResult:
    preview = ApprovalPreview(
        change=_preview_change(spec, params),
        estimate=await _paid_estimate(spec, params, meta),
    )
    body = ApprovalRequired(
        action=meta.name,
        metadata=meta,
        reason="нужно подтверждение",
        preview=preview,
    )
    return ActionResult(status="approval_required", result=body, error=None, warnings=[])


async def execute(name: str, params: dict | None, ctx: Any) -> ActionResult:
    """Один ActionSpec. Исключения и отказы — status=failed."""

    ensure_builtin_actions()
    params = dict(params or {})
    spec = registry.find(str(name))
    if spec is None:
        return _failed(f"неизвестное действие {name}")
    problems = registry.validate_params(spec.id, params)
    if problems:
        return _failed("; ".join(problems))
    meta = metadata_of(spec.id)
    if meta.needs_approval and not _approved(ctx):
        return await _approval_required(spec, params, meta)
    try:
        action_ctx = _action_context(ctx)
        if spec.risk == "read":
            action_ctx.read_only = True
        for pre in spec.preconditions:
            refusal = await pre.check(action_ctx, params)
            if refusal:
                return _failed(str(refusal))
        raw = await spec.executor(action_ctx, params)
    except Exception as exc:
        return _failed(f"{type(exc).__name__}: {exc}")
    if not isinstance(raw, dict):
        raw = {"value": raw}
    warnings = _warnings_of(raw)
    refusal = _refusal(raw)
    if refusal:
        return _failed(refusal, warnings)
    body = {key: value for key, value in raw.items() if key not in {"warnings", "warning"}}
    cc_change_id = _detach_cc(body)
    result = BoundResult(status="ok", result=body, error=None, warnings=warnings)
    if cc_change_id:
        object.__setattr__(result, "cc_change_id", cc_change_id)
    return result


ensure_builtin_actions()
