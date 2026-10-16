from __future__ import annotations

from app.services.action_layer import store
from app.services.action_layer.context import ActionContext
from app.services.action_layer.spec import (
    RISK_DESTRUCTIVE,
    RISK_LOCAL_SAFE,
    RISK_READ,
    ActionRegistry,
    ActionSpec,
    CostEstimate,
    Precondition,
)

_PID = {"type": "integer", "minimum": 1}
_NODE_KEY = {"type": "string", "minLength": 1, "maxLength": 120,
             "pattern": r"^[A-Za-z0-9_.\-]+$"}
_TARGET = {"type": "string", "minLength": 10, "maxLength": 300,
           "pattern": r"^project:[0-9]+:[A-Za-z0-9_.\-]+:(prompt|options|step_params)$"}
_PROPOSAL_ID = {"type": "string", "pattern": r"^[0-9a-f]{32}$"}
_DIGEST = {"type": "string", "pattern": r"^[0-9a-f]{64}$"}
_TIER = {"type": "string", "enum": ["T0", "T1", "T2", "T3"]}
_STATUS = {"type": "string", "enum": [
    "proposed", "approved", "applied", "rejected", "rolled_back", "failed", "developer_review"]}
_KIND = {"type": "string", "enum": [
    "suggestion", "prompt_variant", "node_options", "step_params", "prompt_text",
    "developer_review"]}
_FREE = lambda p: CostEstimate.free("изменение настроек: платных вызовов нет")


def _target_title(target: str) -> str:
    from app.services.change_control.targets import target_title
    return target_title(target)


def _gateway(ctx: ActionContext):
    from app.services.change_control.gateway import CtxGateway
    return CtxGateway(ctx)


async def _proposer(ctx: ActionContext) -> str:
    if ctx.plan_id:
        async with store.sessions()() as s:
            row = await s.get(store.ActionPlanContext, ctx.plan_id)
        if row is not None and row.source == "planner":
            return f"planner:{ctx.operator}"
    return ctx.operator


async def _approver(ctx: ActionContext) -> str:
    if not ctx.plan_id:
        raise RuntimeError("approval_missing: the action runs only inside an approved plan")
    async with store.sessions()() as s:
        plan = await s.get(store.ActionPlan, ctx.plan_id)
        appr = await s.get(store.ActionApproval, plan.approval_id) \
            if plan is not None and plan.approval_id else None
    if appr is None:
        raise RuntimeError("approval_missing: no consumed approval for this plan")
    return appr.operator


def _propose_args(params: dict) -> dict:
    return {"tier": params["tier"], "kind": params["kind"], "rationale": params["rationale"],
            "candidate": params["candidate"], "project_id": params.get("project_id"),
            "node_key": params.get("node_key", "")}


async def _pre_propose(ctx, params):
    from app.services.change_control import service
    from app.services.change_control.errors import ChangeRefused
    try:
        await service.prepare_proposal(_gateway(ctx), proposer=ctx.operator, **_propose_args(params))
    except ChangeRefused as exc:
        return str(exc)
    return None


async def _pre_apply(ctx, params):
    from app.services.change_control import service
    return await service.check_apply(_gateway(ctx), params["proposal_id"], params["expected_digest"])


async def _pre_rollback(ctx, params):
    from app.services.change_control import service
    return await service.check_rollback(_gateway(ctx), params["target"], params["expected_version"])


async def _pre_get_args(ctx, params):
    if bool(params.get("proposal_id")) == bool(params.get("target")):
        return "укажите ровно одно из: proposal_id или target"
    return None


async def _list(ctx, params):
    from app.services.change_control import service
    return await service.list_proposals(
        project_id=params.get("project_id"), status=params.get("status"),
        tier=params.get("tier"), node_key=params.get("node_key"), limit=params.get("limit", 50))


async def _get(ctx, params):
    from app.services.change_control import service
    if params.get("proposal_id"):
        return await service.get_proposal(params["proposal_id"])
    return await service.list_versions(params["target"])


async def _propose(ctx, params):
    from app.services.change_control import service
    view = await service.propose(
        _gateway(ctx), proposer=await _proposer(ctx), plan_id=ctx.plan_id or "",
        **_propose_args(params))
    return {"proposal_id": view["id"], "status": view["status"], "digest": view["digest"],
            "tier": view["tier"], "target": view["target"], "base_version": view["base_version"],
            "diff": view["diff"][:2000], "deduplicated": bool(view.get("deduplicated"))}


async def _apply(ctx, params):
    from app.services.change_control import service
    return await service.apply(
        _gateway(ctx), params["proposal_id"], params["expected_digest"],
        approved_by=await _approver(ctx), plan_id=ctx.plan_id or "")


async def _rollback(ctx, params):
    from app.services.change_control import service
    return await service.rollback(
        _gateway(ctx), params["target"], params["expected_version"],
        approved_by=await _approver(ctx), reason=params.get("reason", ""),
        plan_id=ctx.plan_id or "")


def _specs() -> list[ActionSpec]:
    S = ActionSpec
    return [
        S(id="change.list", title_ru="Список предложений изменений",
          description_ru="Предложения изменений настроек и промптов: статус, уровень, цель, "
                         "diff. Ничего не меняет.",
          params_schema={"type": "object", "additionalProperties": False, "properties": {
              "project_id": _PID, "status": _STATUS, "tier": _TIER, "node_key": _NODE_KEY,
              "limit": {"type": "integer", "minimum": 1, "maximum": 100}}},
          executor=_list, risk=RISK_READ, idempotent=True, scope="global", tags=("change",)),
        S(id="change.get", title_ru="Предложение изменения или история версий",
          description_ru="По proposal_id: предложение целиком (diff, digest, статус, текущая версия "
                         "цели). По target: версии цели и текущая версия. Ровно одно из двух.",
          params_schema={"type": "object", "additionalProperties": False, "properties": {
              "proposal_id": _PROPOSAL_ID, "target": _TARGET}},
          executor=_get, risk=RISK_READ, idempotent=True, scope="global", tags=("change",),
          preconditions=(Precondition("one_selector", "Задан proposal_id или target",
                                      _pre_get_args),)),
        S(id="change.propose", title_ru="Предложить изменение (без применения)",
          description_ru="Записывает предложение: T0 совет, T1 параметр или выбор готового варианта "
                         "из явного списка, T2 новый текст промпта проекта (новый вариант, общие "
                         "файлы не трогаются), T3 запись для разработчика (код не применяется "
                         "никогда). Применяется только отдельным действием «Применить предложение изменения» с подтверждением оператора.",
          params_schema={"type": "object", "additionalProperties": False,
                         "required": ["tier", "kind", "rationale", "candidate"],
                         "properties": {
                             "tier": _TIER, "kind": _KIND,
                             "rationale": {"type": "string", "minLength": 1, "maxLength": 1000},
                             "candidate": {"type": "object"},
                             "project_id": _PID, "node_key": _NODE_KEY}},
          executor=_propose, risk=RISK_LOCAL_SAFE, idempotent=False, scope="project",
          preconditions=(Precondition("proposal_valid",
                                      "Предложение допустимо: уровень, allow-list, проверки текста "
                                      "и значений", _pre_propose),),
          tags=("change", "prompt")),
        S(id="change.apply", title_ru="Применить предложение изменения",
          description_ru="Перепроверяет уровень, allow-list, digest и базовую версию, пишет "
                         "версию и значение, читает обратно; при расхождении возвращает прежнее. "
                         "Только T1/T2 и только после подтверждения оператора.",
          params_schema={"type": "object", "additionalProperties": False,
                         "required": ["proposal_id", "expected_digest"],
                         "properties": {"proposal_id": _PROPOSAL_ID,
                                        "expected_digest": _DIGEST}},
          executor=_apply, risk=RISK_DESTRUCTIVE, destructive=True, reversible=True,
          idempotent=False, scope="global", estimator=_FREE,
          destructive_effects=lambda p: (
              f"Будет записано изменение настройки или промпта проекта по предложению "
              f"№{p['proposal_id'][:8]} (контрольная сумма {p['expected_digest'][:12]}...). "
              "Прежнее значение сохранится отдельной версией, его можно вернуть планом отката. "
              "Новые запросы к модели пойдут с новым значением, ожидающие билеты "
              "восстановления этой ноды устареют."),
          preconditions=(Precondition("apply_ready",
                                      "Предложение применимо: digest, уровень, базовая версия, "
                                      "проверки, проект не занят", _pre_apply),),
          tags=("change",)),
        S(id="change.rollback", title_ru="Откатить изменение",
          description_ru="Возвращает прежнюю версию значения цели новой строкой версии; история "
                         "не удаляется. Только если текущая версия равна expected_version.",
          params_schema={"type": "object", "additionalProperties": False,
                         "required": ["target", "expected_version"],
                         "properties": {"target": _TARGET,
                                        "expected_version": {"type": "integer", "minimum": 2},
                                        "reason": {"type": "string", "maxLength": 500}}},
          executor=_rollback, risk=RISK_DESTRUCTIVE, destructive=True, reversible=True,
          idempotent=False, scope="global", estimator=_FREE,
          destructive_effects=lambda p: (
              f"Значение: {_target_title(p['target'])} вернётся к версии, предшествующей "
              f"версии {p['expected_version']}. Данные, файлы и уже потраченные запросы "
              "не откатываются."),
          preconditions=(Precondition("rollback_ready",
                                      "Откат возможен: версия актуальна, значение не менялось "
                                      "вручную, проект не занят", _pre_rollback),),
          tags=("change",)),
    ]


def register_change_actions(registry: ActionRegistry) -> None:
    registry.register_many(_specs())
