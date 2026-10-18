"""Прямой вызов одного ActionSpec. Ответ — ActionResult ws2-r2.3.

Слой планов Никиты и одобрение по plan_hash не используются.
change_id вида orch-appr-<task>-<n> адаптер не выдаёт.
"""

from __future__ import annotations

from typing import Any

from pydantic import ConfigDict

from app.orchestrator.action_contracts.contracts import ActionMetadata
from app.orchestrator.action_contracts.contracts import ActionResult
from app.orchestrator.action_contracts.contracts import metadata_of as contract_metadata
from app.services.action_layer.context import ActionContext
from app.services.action_layer.spec import ensure_builtin_actions, registry
from app.services.redact import redact_text


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


def metadata_of(name: str) -> ActionMetadata:
    """Флаги риска из audit_metadata.json, включая create_node@new_group."""

    return contract_metadata(str(name))


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
    return BoundResult(status="ok", result=body, error=None, warnings=warnings)
