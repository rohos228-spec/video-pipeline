"""Публикации проекта: список по площадкам, подтверждение человека и ручные действия (W6-1: E01, E02, E11).

GET  /projects/{id}/publications                        - факты: файл (sha256), подпись, площадки, статусы, ссылки.
POST /projects/{id}/publications/approve                - подтверждение человека на публикацию (площадка x sha256 x подпись).
POST /projects/{id}/publications/{platform}/confirm-manual - «опубликовано» по проверке человека (ссылка ИЛИ причина).
POST /projects/{id}/publications/{platform}/mark-failed    - «не опубликовано» по проверке человека (причина).

Ни один из обработчиков не открывает браузер, не кликает и не обращается к площадкам. Изменяющие ручки
требуют токен оператора (X-VP-Operator); каждый POST попадает в operator_audit (слой аудита), а причина
и ссылка - в publication_manual_actions. Живой платформой не проверялось.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.bots.publishers import ALL_PUBLISHERS
from app.models import Project, ProjectStatus
from app.orchestrator.steps import publish as publish_step
from app.services import access_guard
from app.services import publications_store as pstore
from app.services.action_layer.context import normalize_operator
from app.telegram import publish_texts as ptexts
from app.web.deps import get_session

router = APIRouter(prefix="/projects/{project_id}/publications", tags=["publications"])

_PLATFORMS = [c.platform for c in ALL_PUBLISHERS]
_APPROVABLE_STATUSES = (ProjectStatus.assembled, ProjectStatus.publishing, ProjectStatus.published)


def require_operator_token(token: str | None = Header(default=None, alias=access_guard.OPERATOR_HEADER)) -> None:
    """Изменяющие ручки публикации всегда требуют токен оператора (в том числе когда общий слой его не требует)."""
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
    """Человек действует над конкретным файлом: sha256 из тела должен совпасть с текущим финальным видео."""
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


class MarkFailedIn(BaseModel):
    sha256: str = Field(min_length=8, max_length=64)
    reason: str | None = Field(default=None, max_length=pstore.MAX_REASON_CHARS)
    allow_republish: bool = False


@router.get("")
async def list_publications(project_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    """Только чтение: файл, подпись, площадки со статусом, ссылкой на пост, ошибкой и ручными отметками."""
    project = await _project(session, project_id)
    return await publish_step.publication_facts(session, project)


async def _recompute(session: AsyncSession, project: Project, sha: str) -> dict:
    final = await publish_step._latest_final(session, project)
    rows = await pstore.list_for(session, project.id, sha)
    caption_sha = publish_step.caption_sha256(publish_step.caption_for(project))
    approvals = await pstore.list_approvals(session, project.id, sha, caption_sha)
    state = await publish_step.apply_project_state(
        session, project, rows=rows, platforms=_PLATFORMS, sha=sha,
        artifact_uuid=final.uuid if final is not None else None,
        approved={p for p, a in approvals.items() if a["valid"]})
    publish_step._sync_meta(project, rows, _PLATFORMS)
    await session.commit()
    return state


@router.post("/approve", dependencies=[Depends(require_operator_token)])
async def approve_publication(project_id: int, body: ApproveIn, session: AsyncSession = Depends(get_session),
                              operator: str | None = Header(default=None, alias="X-Action-Layer-Operator")) -> dict:
    """Подтверждение человека: «да» на каждую площадку отдельно, для ЭТОГО файла и ЭТОЙ подписи, одноразово.

    Сама публикация здесь не выполняется. Если автопубликация включена и проект ждёт, он ставится в очередь
    шага публикации; если выключена, подтверждение лишь записывается."""
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
    granted: list[str] = []
    for p in dict.fromkeys(body.platforms):
        try:
            await pstore.grant_approval(session, project_id=project.id, platform=p, sha256=sha,
                                        caption_sha256=caption_sha, approved_by=actor)
        except pstore.PublicationRefused as exc:
            raise _refused(exc) from exc
        granted.append(p)
    queued = False
    from app.settings import settings
    if settings.social_publish_enabled and project.status is ProjectStatus.assembled:
        publish_step._set_markers(project, **{publish_step._AWAITING_KEY: None, publish_step._HUMAN_KEY: None,
                                              publish_step._CLOSED_KEY: None})
        project.status = ProjectStatus.publishing
        queued = True
    await session.commit()
    return {"approved": granted, "sha256": sha, "queued_for_publish_step": queued,
            "publish_enabled": bool(settings.social_publish_enabled),
            "message": ("Подтверждено. Публикация пойдёт на шаге публикации." if queued else
                        "Подтверждение записано. Автопубликация выключена или шаг уже идёт: сейчас ничего не публикуется.")}


@router.post("/{platform}/confirm-manual", dependencies=[Depends(require_operator_token)])
async def confirm_manual(project_id: int, platform: str, body: ConfirmManualIn,
                         session: AsyncSession = Depends(get_session),
                         operator: str | None = Header(default=None, alias="X-Action-Layer-Operator")) -> dict:
    """Человек проверил площадку и подтверждает «опубликовано». Нужна ссылка на пост ИЛИ причина. Клик не повторяется."""
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
                      session: AsyncSession = Depends(get_session),
                      operator: str | None = Header(default=None, alias="X-Action-Layer-Operator")) -> dict:
    """Человек проверил площадку и помечает «не опубликовано». Причина обязательна. Клик не повторяется."""
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
