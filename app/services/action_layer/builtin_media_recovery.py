"""Каталог: возобновление и чтение состояния медиа-задач провайдера (контур ``media_recovery``).

``media.get_recovery_assessment`` только читает журнал платных задач и файлы: что можно безопасно сделать
с медиа-нодой без новой оплаты (опросить открытую задачу, переиспользовать готовый файл) и почему остальное
остаётся за человеком.

``media.resume_poll`` спрашивает провайдера про УЖЕ известную задачу Kie и скачивает готовый результат.
Уровень риска ``local_safe`` выбран осознанно и подтверждён кодом, а не словами:

* сервис работает внутри режима «только возобновление» (``media_spend.resume_only``): любая новая платная
  отправка (Kie createTask, Kling, Outsee, ElevenLabs) отказывает ДО отправки, поэтому «платный» уровень с
  подтверждением и оценкой стоимости не нужен;
* после выполнения сервис сверяет журнал задач: ни одного нового task id и ни одного намерения отправки
  (``new_paid_requests`` в результате всегда 0, иначе результат не считается подтверждённым);
* провайдеру уходит только запрос состояния и скачивание результата, за которые уже заплачено.

Если бы действие могло отправить хотя бы один новый платный запрос, оно было бы ``paid`` с подтверждением.
Повторная платная отправка по провалу (``retry_same`` в ``media_recovery``) этим действием НЕ выполняется.
"""
from __future__ import annotations

from app.services.action_layer.context import ActionCallError, ActionContext
from app.services.action_layer.spec import (
    RISK_LOCAL_SAFE, RISK_READ, ActionRegistry, ActionSpec, Precondition)

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
    ]


def register_media_recovery_actions(registry: ActionRegistry) -> None:
    registry.register_many(_specs())
