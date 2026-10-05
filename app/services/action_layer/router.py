"""REST под /api/action-layer. Все ручки за AuthDep (как остальные роутеры).

Подтверждение создаётся только здесь, вызовом оператора. Тело плана и запуска
не содержит полей подтверждения; лишние поля запрещены (extra=forbid).
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from app.services.action_layer import approval, executor, macros, planner, service
from app.services.action_layer.plan import PlanFormatError
from app.services.action_layer.spec import ensure_builtin_actions
from app.services.action_layer.spec import registry as default_registry
from app.services.access_guard import LocalOnlyDep
from app.web.auth_sessions import AuthDep

router = APIRouter(prefix="/action-layer", tags=["action-layer"])


class StepIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action_id: str = Field(..., min_length=1, max_length=120)
    params: dict = Field(default_factory=dict)
    depends_on: list[int] = Field(default_factory=list)


class PlanIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    steps: list[StepIn] = Field(..., min_length=1)
    request_text: str = Field("", max_length=2000)


class EmptyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FromTextIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(..., min_length=1, max_length=2000)
    project_id: int | None = Field(None, ge=1)
    selected_scene: int | None = Field(None, ge=1)
    selected_frame: int | None = Field(None, ge=1)


class MacroIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str | None = Field(None, max_length=80)
    title_ru: str = Field(..., min_length=1, max_length=200)
    description_ru: str = Field("", max_length=2000)
    steps: list[StepIn] = Field(..., min_length=1)
    params_schema: dict = Field(...)
    shared: bool = False


class FromMacroIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    macro_id: str = Field(..., min_length=1, max_length=80)
    params: dict = Field(default_factory=dict)
    request_text: str = Field("", max_length=2000)
    selected_scene: int | None = Field(None, ge=1)
    selected_frame: int | None = Field(None, ge=1)


def _stale(exc: Exception) -> HTTPException:
    return HTTPException(409, detail={"code": "stale_context", "message": str(exc)})


def _operator(token: str | None) -> approval.OperatorIdentity:
    # Тот же формат, что observations._operator_id.
    return approval.OperatorIdentity.from_token(token)


@router.get("/catalog")
async def catalog(operator: AuthDep, risk: str | None = Query(None),
                  prefix: str | None = Query(None)) -> dict:
    ensure_builtin_actions()
    items = default_registry.list(risk=risk, prefix=prefix)
    return {"count": len(items), "actions": [s.to_json() for s in items]}


@router.post("/plans/preview", dependencies=[LocalOnlyDep])
async def preview_plan(request: Request, body: PlanIn, operator: AuthDep) -> dict:
    who = _operator(operator)
    try:
        plan_id, preview = await service.create_plan(
            request.app, [s.model_dump() for s in body.steps], operator=who,
            request_text=body.request_text)
    except PlanFormatError as exc:
        raise HTTPException(422, detail={"code": "bad_plan", "message": str(exc)}) from exc
    if plan_id is None:
        raise HTTPException(422, detail={"code": "plan_invalid", "preview": preview})
    return {"plan_id": plan_id, "preview": preview}


@router.post("/plans/from-text", dependencies=[LocalOnlyDep])
async def plan_from_text(request: Request, body: FromTextIn, operator: AuthDep) -> dict:
    """Запрос -> планировщик -> только preview (proposed). Не подтверждает и не запускает."""
    who = _operator(operator)
    try:
        outcome = await planner.plan_from_text(
            body.text, operator=who.id, project_id=body.project_id,
            selected_scene=body.selected_scene, selected_frame=body.selected_frame)
    except planner.PlannerRateLimited as exc:
        raise HTTPException(429, detail={"code": exc.code, "message": str(exc)}) from exc
    except planner.PlannerRefused as exc:
        status = 409 if exc.code == "kill_switch_active" else 503
        raise HTTPException(status, detail={"code": exc.code, "message": str(exc)}) from exc
    except planner.PlannerRejected as exc:
        status = 404 if exc.code == "project_not_found" else 422
        raise HTTPException(status, detail={
            "code": "planner_rejected", "reason": exc.code, "message": str(exc)}) from exc
    except planner.PlannerFailed as exc:
        raise HTTPException(502, detail={"code": exc.code, "message": str(exc)}) from exc
    reply = outcome.reply
    extra = {"explanation_ru": reply.explanation_ru,
             "needs_clarification": reply.needs_clarification,
             "context_version": outcome.context["version"], "planner": outcome.meta}
    if reply.needs_clarification:
        return {"plan_id": None, "preview": None, **extra}
    try:
        plan_id, preview = await service.create_plan(
            request.app, reply.steps, operator=who, request_text=body.text)
    except PlanFormatError as exc:
        raise HTTPException(422, detail={"code": "bad_plan", "message": str(exc)}) from exc
    if plan_id is None:
        raise HTTPException(422, detail={"code": "plan_invalid", "preview": preview, **extra})
    await planner.link_plan(plan_id, who.id, outcome)
    return {"plan_id": plan_id, "preview": preview, **extra}


@router.get("/macros")
async def list_macros(operator: AuthDep) -> dict:
    who = _operator(operator)
    ensure_builtin_actions()
    items = await macros.list_macros(who.id)
    return {"count": len(items),
            "macros": [m.to_json(default_registry, who.id) for m in items]}


@router.post("/macros", dependencies=[LocalOnlyDep])
async def save_macro(body: MacroIn, operator: AuthDep) -> dict:
    who = _operator(operator)
    try:
        macro = await macros.save_macro(
            who.id, title_ru=body.title_ru, description_ru=body.description_ru,
            steps=[s.model_dump() for s in body.steps], params_schema=body.params_schema,
            shared=body.shared, macro_id=body.id)
    except macros.MacroError as exc:
        raise HTTPException(422, detail={"code": "macro_invalid", "errors": exc.errors}) from exc
    except macros.MacroForbidden as exc:
        raise HTTPException(403, detail={"code": "macro_forbidden", "message": str(exc)}) from exc
    return macro.to_json(default_registry, who.id)


@router.delete("/macros/{macro_id}", dependencies=[LocalOnlyDep])
async def delete_macro(macro_id: str, operator: AuthDep) -> dict:
    who = _operator(operator)
    try:
        await macros.delete_macro(macro_id, who.id)
    except LookupError as exc:
        raise HTTPException(404, detail="macro not found") from exc
    except macros.MacroForbidden as exc:
        raise HTTPException(403, detail={"code": "macro_forbidden", "message": str(exc)}) from exc
    return {"deleted": macro_id}


@router.post("/plans/from-macro", dependencies=[LocalOnlyDep])
async def plan_from_macro(request: Request, body: FromMacroIn, operator: AuthDep) -> dict:
    """Макрос -> шаги -> тот же preview, что у /plans/preview. Ничего не запускает."""
    who = _operator(operator)
    ensure_builtin_actions()
    try:
        macro = await macros.get_macro(body.macro_id, who.id)
        steps = macros.expand(macro, body.params, default_registry)
    except LookupError as exc:
        raise HTTPException(404, detail="macro not found") from exc
    except macros.MacroUnavailable as exc:
        raise HTTPException(409, detail={
            "code": "macro_unavailable", "missing_action_ids": exc.missing,
            "message": str(exc)}) from exc
    except macros.MacroError as exc:
        raise HTTPException(422, detail={"code": "macro_params_invalid",
                                         "errors": exc.errors}) from exc
    pid = body.params.get("project_id")
    pid = pid if isinstance(pid, int) and not isinstance(pid, bool) else None
    try:
        ctx = await planner.load_context(pid, body.selected_scene, body.selected_frame)
    except planner.PlannerRejected as exc:
        raise HTTPException(404, detail={"code": exc.code, "message": str(exc)}) from exc
    try:
        plan_id, preview = await service.create_plan(
            request.app, steps, operator=who,
            request_text=body.request_text or f"[макрос {macro.id}] {macro.title_ru}")
    except PlanFormatError as exc:
        raise HTTPException(422, detail={"code": "bad_plan", "message": str(exc)}) from exc
    if plan_id is None:
        raise HTTPException(422, detail={"code": "plan_invalid", "preview": preview})
    await planner.save_plan_context(plan_id, source="macro", ctx=ctx, macro_id=macro.id)
    return {"plan_id": plan_id, "preview": preview, "macro_id": macro.id,
            "context_version": ctx["version"]}


@router.post("/plans/{plan_id}/approve", dependencies=[LocalOnlyDep])
async def approve(request: Request, plan_id: str, operator: AuthDep,
                  body: EmptyBody | None = None) -> dict:
    who = _operator(operator)
    try:
        return await service.approve_plan(request.app, plan_id, who)
    except planner.StalePlan as exc:
        raise _stale(exc) from exc
    except LookupError as exc:
        raise HTTPException(404, detail="plan not found") from exc
    except approval.ApprovalError as exc:
        raise HTTPException(409, detail={"code": exc.code, "message": str(exc)}) from exc


@router.post("/plans/{plan_id}/run", dependencies=[LocalOnlyDep])
async def run(request: Request, plan_id: str, operator: AuthDep,
              body: EmptyBody | None = None) -> dict:
    who = _operator(operator)
    try:
        await service.prepare_run(plan_id, who)
    except planner.StalePlan as exc:
        raise _stale(exc) from exc
    except LookupError as exc:
        raise HTTPException(404, detail="plan not found") from exc
    except approval.ApprovalError as exc:
        raise HTTPException(403, detail={"code": exc.code, "message": str(exc)}) from exc
    except service.RunRefused as exc:
        raise HTTPException(409, detail={"code": exc.code, "message": str(exc)}) from exc
    executor.launch(request.app, plan_id, who.id)
    return {"plan_id": plan_id, "status": "running"}


@router.get("/plans")
async def list_plans(operator: AuthDep, status: str | None = Query(None, max_length=20),
                     project_id: int | None = Query(None, ge=1),
                     limit: int = Query(20, ge=1, le=100)) -> dict:
    return await service.list_plans(status=status, project_id=project_id, limit=limit)


@router.get("/plans/{plan_id}")
async def get_plan(plan_id: str, operator: AuthDep) -> dict:
    try:
        return await service.plan_view(plan_id)
    except LookupError as exc:
        raise HTTPException(404, detail="plan not found") from exc


@router.post("/plans/{plan_id}/stop", dependencies=[LocalOnlyDep])
async def stop(plan_id: str, operator: AuthDep, body: EmptyBody | None = None) -> dict:
    who = _operator(operator)
    try:
        return await executor.request_stop(plan_id, who.id)
    except LookupError as exc:
        raise HTTPException(404, detail="plan not found") from exc


@router.get("/history")
async def get_history(operator: AuthDep, plan_id: str | None = Query(None),
                      limit: int = Query(100, ge=1, le=500),
                      before_id: int | None = Query(None, ge=1)) -> dict:
    return await service.history(plan_id=plan_id, limit=limit, before_id=before_id)
