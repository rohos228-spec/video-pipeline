from __future__ import annotations

from app.services.action_layer.context import ActionCallError, ActionContext
from app.services.action_layer.spec import (
    RISK_DESTRUCTIVE, RISK_LOCAL_SAFE, RISK_READ, ActionRegistry, ActionSpec, Precondition)

_PID = {"type": "integer", "minimum": 1, "description": "ID проекта"}
_RESUME_NODES = ["videos", "items", "music"]
_ALL_NODES = ["videos", "hero", "items", "images", "music", "audio", "sfx_gen", "assemble"]
_NODE_DESC = {"description": "Медиа-нода: videos (клипы), items (предметы), music (музыка)"}

_DENIED_RU = {
    "node_not_admitted": "для этой ноды возобновление по задаче провайдера не допускается",
    "verdict_denied": "исход прошлой платной отправки неизвестен или повтор исчерпан: решает человек",
    "retry_same_not_admitted": "все прежние задачи упали, но платную повторную отправку надзор не делает: "
                               "её запускает человек обычным запуском ноды",
    "nothing_to_do": "возобновлять нечего: открытых задач нет, результат уже скачан или ничего не отправлялось",
}


def _schema(nodes: list[str]) -> dict:
    return {"type": "object", "additionalProperties": False, "required": ["project_id", "node_type"],
            "properties": {"project_id": _PID, "node_type": {"type": "string", "enum": nodes, **_NODE_DESC}}}


def _path(params: dict) -> str:
    return f"/projects/{params['project_id']}/media-recovery/{params['node_type']}"


async def _assessment(ctx: ActionContext, params: dict) -> dict:
    return await ctx.call("GET", _path(params))


async def _pre_project(ctx, params):
    await ctx.call("GET", f"/projects/{params['project_id']}")
    return None


async def _pre_idle(ctx, params):
    from app.services.project_state import is_running_status
    from app.models import ProjectStatus
    proj = await ctx.call("GET", f"/projects/{params['project_id']}")
    try:
        running = is_running_status(ProjectStatus(proj.get("status")))
    except ValueError:
        running = False
    if running:
        return f"в проекте выполняется шаг (статус {proj.get('status')}): дождитесь его конца или остановите проект"
    return None


async def _pre_resumable(ctx, params):
    try:
        data = await _assessment(ctx, params)
    except ActionCallError as exc:
        return f"не удалось прочитать состояние задач: {exc.detail}"
    adm = data.get("supervisor_admission") or {}
    if adm.get("operation") == "resume_poll":
        return None
    code = adm.get("denied") or ("node_not_admitted" if not adm.get("admitted_node") else "nothing_to_do")
    return _DENIED_RU.get(code, f"возобновление недоступно ({code})")


async def _resume(ctx: ActionContext, params: dict) -> dict:
    data = await ctx.call("POST", _path(params) + "/resume-poll", timeout=150.0)
    return {"node_type": data.get("node_type"), "verified": bool(data.get("verified")),
            "operation": data.get("operation"), "problems": data.get("problems", []),
            "resumed": data.get("resumed", []), "files": data.get("files", []),
            "new_paid_requests": data.get("new_paid_requests", 0),
            "assessment_after": data.get("assessment_after")}


_KEY = {"type": "string", "minLength": 1, "maxLength": 120, "pattern": r"^[A-Za-z0-9_.\-]+$",
        "description": "Ключ задачи из media.list_unknown_outcomes: clip_005 (клип), frame_012 (картинка кадра), "
                       "predmet3 (предмет), hero_c1 (герой), music"}
_PROVIDER = {"type": "string", "enum": ["kie", "outsee"],
             "description": "Провайдер, у которого сверена история: kie или outsee (снимается блокировка только его)"}
_REASON = {"type": "string", "minLength": 10, "maxLength": 500, "pattern": r"^\S[\s\S]{8,}\S$",
           "description": "Письменная причина человека: что проверено в кабинете провайдера (задачи и баланс) "
                          "и почему списания не было"}
_PROVIDER_RU = {"kie": "Kie", "outsee": "Outsee"}


async def _unknown_list(ctx: ActionContext, params: dict) -> dict:
    return await ctx.call("GET", f"/projects/{params['project_id']}/media-recovery/unknown-outcomes")


async def _pre_blocked(ctx, params):
    try:
        data = await _unknown_list(ctx, params)
    except ActionCallError as exc:
        return f"не удалось прочитать блокировки: {exc.detail}"
    if any(row["key"] == params["key"] and row["provider"] == params["provider"] for row in data.get("blocked", [])):
        return None
    if not data.get("window_enabled", True):
        return "журнал возобновления выключен (MEDIA_RESUME_WINDOW_S=0): блокировок нет"
    return (f"по ключу {params['key']} у провайдера {_PROVIDER_RU.get(params['provider'], params['provider'])} "
            "нет блокировки неизвестного исхода в окне возобновления")


async def _release(ctx: ActionContext, params: dict) -> dict:
    from app.services.action_layer.builtin_change import _approver
    approved_by = await _approver(ctx)
    data = await ctx.call("POST", f"/projects/{params['project_id']}/media-recovery/release-unknown", json={
        "key": params["key"], "provider": params["provider"], "reason": params["reason"],
        "approved_by": approved_by, "plan_id": ctx.plan_id or ""})
    return {"key": data["key"], "provider": data["provider"], "released": data["released"],
            "intent_ids": data.get("intent_ids", []), "approved_by": data.get("approved_by"),
            "audit_event": data.get("audit_event"), "second_charge_possible": True,
            "next": "следующий запуск ноды отправит новый платный запрос по этому ключу"}


def _release_effects(params: dict) -> str:
    provider = _PROVIDER_RU.get(params["provider"], params["provider"])
    return (f"Будет снята блокировка повторной отправки по ключу {params['key']} у провайдера {provider} "
            f"(проект {params['project_id']}). Прошлая отправка могла быть принята и оплачена. После снятия "
            "следующий запуск ноды отправит НОВЫЙ платный запрос, и ВТОРОЕ СПИСАНИЕ за тот же результат "
            "возможно. Снимайте блокировку только после сверки истории задач и баланса в кабинете провайдера. "
            f"Причина будет записана в журнал: «{str(params['reason'])[:200]}».")


def _specs() -> list[ActionSpec]:
    S = ActionSpec
    return [
        S(id="media.get_recovery_assessment", title_ru="Состояние задач провайдера медиа-ноды",
          description_ru="Читает журнал платных задач и файлы: есть ли открытая задача Kie для докачивания, "
                         "готовый файл, провалившаяся задача или неизвестный исход отправки, и что из этого "
                         "допускает надзор. Ничего не отправляет и не меняет.",
          params_schema=_schema(_ALL_NODES), executor=_assessment, risk=RISK_READ, idempotent=True,
          scope="project", tags=("media", "recovery", "kie"),
          preconditions=(Precondition("project_exists", "Проект существует", _pre_project),)),
        S(id="media.resume_poll", title_ru="Докачать медиа по известной задаче провайдера",
          description_ru="Спрашивает Kie про уже отправленную задачу (клипы, предметы, музыка) и скачивает готовый "
                         "результат. Новых платных запросов не отправляет: внутри режима «только возобновление», "
                         "журнал задач сверяется до и после. Повторную платную отправку не делает. Ноду не запускает.",
          params_schema=_schema(_RESUME_NODES), executor=_resume, risk=RISK_LOCAL_SAFE, idempotent=True,
          scope="project", tags=("media", "recovery", "kie"),
          preconditions=(
              Precondition("project_exists", "Проект существует", _pre_project),
              Precondition("project_idle", "В проекте не выполняется шаг", _pre_idle),
              Precondition("resume_available", "Есть открытая задача провайдера, которую допустимо "
                           "докачать без новой оплаты", _pre_resumable))),
        S(id="media.list_unknown_outcomes", title_ru="Блокировки неизвестного исхода платной отправки",
          description_ru="Показывает ключи проекта (клипы, картинки, предметы, музыка), у которых прошлая платная "
                         "отправка в Kie или Outsee не имеет известного исхода и поэтому блокирует повтор: провайдер, "
                         "возраст блокировки и окно, на которое она действует. Ничего не отправляет и не меняет.",
          params_schema={"type": "object", "additionalProperties": False, "required": ["project_id"],
                         "properties": {"project_id": _PID}},
          executor=_unknown_list, risk=RISK_READ, idempotent=True, scope="project",
          tags=("media", "recovery", "kie", "outsee"),
          preconditions=(Precondition("project_exists", "Проект существует", _pre_project),)),
        S(id="media.release_unknown", title_ru="Снять блокировку неизвестного исхода отправки",
          description_ru="Решение человека после сверки кабинета провайдера (Kie или Outsee): снимает блокировку "
                         "повторной платной отправки по одному ключу этого провайдера. Это СНОВА ОТКРЫВАЕТ платную "
                         "отправку: второе списание возможно. Нужны провайдер, ключ и письменная причина; без "
                         "подтверждения оператора план не запускается. Само действие ничего не отправляет.",
          params_schema={"type": "object", "additionalProperties": False,
                         "required": ["project_id", "key", "provider", "reason"],
                         "properties": {"project_id": _PID, "key": _KEY, "provider": _PROVIDER, "reason": _REASON}},
          executor=_release, risk=RISK_DESTRUCTIVE, destructive=True, reversible=False, idempotent=False,
          scope="project", destructive_effects=_release_effects, tags=("media", "recovery", "kie", "outsee"),
          preconditions=(
              Precondition("project_exists", "Проект существует", _pre_project),
              Precondition("project_idle", "В проекте не выполняется шаг", _pre_idle),
              Precondition("blocked", "У этого провайдера по этому ключу есть блокировка неизвестного исхода "
                           "в окне возобновления", _pre_blocked))),
    ]


def register_media_recovery_actions(registry: ActionRegistry) -> None:
    registry.register_many(_specs())
