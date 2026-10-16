from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.bots.publishers import ALL_PUBLISHERS
from app.models import Project, ProjectStatus
from app.orchestrator.steps import publish as publish_step
from app.services import access_guard
from app.services import hitl as hitl_service
from app.services import publications_store as pstore
from app.services.action_layer.context import normalize_operator
from app.telegram import publish_texts as ptexts
from app.web.deps import get_project_session

router = APIRouter(prefix="/projects/{project_id}/publications", tags=["publications"])

_PLATFORMS = [c.platform for c in ALL_PUBLISHERS]
_APPROVABLE_STATUSES = (ProjectStatus.assembled, ProjectStatus.publishing, ProjectStatus.published)


def require_operator_token(token: str | None = Header(default=None, alias=access_guard.OPERATOR_HEADER)) -> None:
    if not access_guard.check_operator_token(token):
        raise HTTPException(status_code=403, detail={
            "code": "operator_token_required",
            "message": "Нужен токен оператора: обновите страницу Studio и повторите."})


def _actor(named: str | None) -> str:
    return ("operator:" + normalize_operator(named))[:120] if named else "operator"


async def _project(session: AsyncSession, project_id: int) -> Project:
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    return project


def _platform(platform: str) -> str:
    if platform not in _PLATFORMS:
        raise HTTPException(status_code=404, detail={"code": "unknown_platform", "platform": platform})
    return platform


def _refused(exc: pstore.PublicationRefused) -> HTTPException:
    return HTTPException(status_code=409, detail={"code": exc.code, "message": exc.message})


async def _current_sha(session: AsyncSession, project: Project, sent: str) -> str:
    final = await publish_step._latest_final(session, project)
    if final is None:
        raise HTTPException(status_code=409, detail={"code": "no_final_video",
                                                     "message": "Нет финального видео для публикации."})
    sha = await publish_step._file_sha_cached(final.path)
    if sha == "no-file":
        raise HTTPException(status_code=409, detail={"code": "file_unreadable",
                                                     "message": "Файл финального видео не читается."})
    if sent != sha:
        raise HTTPException(status_code=409, detail={
            "code": "stale_artifact",
            "message": "Видео было пересобрано после того, как вы открыли список. Обновите список и проверьте заново."})
    return sha


class ApproveIn(BaseModel):
    platforms: list[str] = Field(min_length=1, max_length=len(_PLATFORMS))
    sha256: str = Field(min_length=8, max_length=64)
    caption_sha256: str = Field(min_length=8, max_length=64)


class ConfirmManualIn(BaseModel):
    sha256: str = Field(min_length=8, max_length=64)
    post_url: str | None = Field(default=None, max_length=1000)
    reason: str | None = Field(default=None, max_length=pstore.MAX_REASON_CHARS)


class RevokeIn(BaseModel):
    sha256: str = Field(min_length=8, max_length=64)


class MarkFailedIn(BaseModel):
    sha256: str = Field(min_length=8, max_length=64)
    reason: str | None = Field(default=None, max_length=pstore.MAX_REASON_CHARS)
    allow_republish: bool = False


@router.get("")
async def list_publications(project_id: int, session: AsyncSession = Depends(get_project_session)) -> dict:
    project = await _project(session, project_id)
    return await publish_step.publication_facts(session, project)


async def _recompute(session: AsyncSession, project: Project, sha: str) -> dict:
    return await hitl_service.recompute_publication_state(session, project, sha)


def _approve_message(queued: bool, publish_enabled: bool) -> str:
    if queued:
        return "Разрешено и поставлено в очередь: публикацию выполнит шаг публикации."
    if not publish_enabled:
        return ("Разрешено, но публикация выключена настройкой (автопубликация выключена): "
                "сейчас ничего не публикуется, пока её не включат.")
    return "Разрешено: шаг публикации уже идёт или ещё не дошёл до этой площадки, он учтёт ваше разрешение."


@router.post("/approve", dependencies=[Depends(require_operator_token)])
async def approve_publication(project_id: int, body: ApproveIn, session: AsyncSession = Depends(get_project_session),
                              operator: str | None = Header(default=None, alias="X-Action-Layer-Operator")) -> dict:
    project = await _project(session, project_id)
    if project.status not in _APPROVABLE_STATUSES:
        raise HTTPException(status_code=409, detail={"code": "project_not_ready", "status": project.status.value,
                                                     "message": "Проект ещё не собран: подтверждать нечего."})
    for p in body.platforms:
        _platform(p)
    sha = await _current_sha(session, project, body.sha256)
    caption_sha = publish_step.caption_sha256(publish_step.caption_for(project))
    if body.caption_sha256 != caption_sha:
        raise HTTPException(status_code=409, detail={
            "code": "stale_caption", "message": "Подпись изменилась после того, как вы открыли список. Обновите и проверьте."})
    actor = _actor(operator)
    try:
        res = await hitl_service.grant_publish_approvals(
            session, project, body.platforms, sha256=sha, caption_sha256=caption_sha, actor=actor)
    except pstore.PublicationRefused as exc:
        raise _refused(exc) from exc
    queued = res["queued_for_publish_step"]
    return {"approved": res["approved"], "sha256": sha, "queued_for_publish_step": queued,
            "publish_enabled": res["publish_enabled"],
            "message": _approve_message(queued, res["publish_enabled"])}


@router.post("/{platform}/revoke", dependencies=[Depends(require_operator_token)])
async def revoke_publication(project_id: int, platform: str, body: RevokeIn,
                             session: AsyncSession = Depends(get_project_session),
                             operator: str | None = Header(default=None, alias="X-Action-Layer-Operator")) -> dict:
    project = await _project(session, project_id)
    platform = _platform(platform)
    sha = await _current_sha(session, project, body.sha256)
    actor = _actor(operator)
    try:
        res = await pstore.revoke_approval(session, project_id=project.id, platform=platform, sha256=sha)
    except pstore.PublicationRefused as exc:
        raise _refused(exc) from exc
    state = await _recompute(session, project, sha)
    return {**res, "revoked_by": actor, "project_state": state["state"], "project_status": project.status.value,
            "message": f"Разрешение на {platform} отозвано: публикация туда не начнётся, пока вы не разрешите снова."}


@router.post("/{platform}/confirm-manual", dependencies=[Depends(require_operator_token)])
async def confirm_manual(project_id: int, platform: str, body: ConfirmManualIn,
                         session: AsyncSession = Depends(get_project_session),
                         operator: str | None = Header(default=None, alias="X-Action-Layer-Operator")) -> dict:
    project = await _project(session, project_id)
    platform = _platform(platform)
    sha = await _current_sha(session, project, body.sha256)
    actor = _actor(operator)
    try:
        res = await pstore.confirm_manual(session, project_id=project.id, platform=platform, sha256=sha,
                                          actor=actor, post_url=body.post_url, reason=body.reason)
    except pstore.PublicationRefused as exc:
        raise _refused(exc) from exc
    state = await _recompute(session, project, sha)
    all_published = state["state"] == pstore.PS_PUBLISHED
    return {**res, "project_state": state["state"], "project_status": project.status.value,
            "clicked_again": False,
            "message": ptexts.manual_action_text(project.id, platform, "confirm", actor, all_published=all_published)}


@router.post("/{platform}/mark-failed", dependencies=[Depends(require_operator_token)])
async def mark_failed(project_id: int, platform: str, body: MarkFailedIn,
                      session: AsyncSession = Depends(get_project_session),
                      operator: str | None = Header(default=None, alias="X-Action-Layer-Operator")) -> dict:
    project = await _project(session, project_id)
    platform = _platform(platform)
    sha = await _current_sha(session, project, body.sha256)
    actor = _actor(operator)
    try:
        res = await pstore.mark_failed_manual(session, project_id=project.id, platform=platform, sha256=sha,
                                              actor=actor, reason=body.reason,
                                              allow_republish=body.allow_republish)
    except pstore.PublicationRefused as exc:
        raise _refused(exc) from exc
    state = await _recompute(session, project, sha)
    action = "mark_failed_republish" if body.allow_republish else "mark_failed"
    return {**res, "project_state": state["state"], "project_status": project.status.value,
            "clicked_again": False,
            "message": ptexts.manual_action_text(project.id, platform, action, actor, all_published=False)}
