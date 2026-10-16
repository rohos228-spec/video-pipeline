from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from app.services import media_resume, spend_guard

PROVIDERS = ("kie", "outsee")
MIN_REASON_CHARS = 10
MAX_REASON_CHARS = 500
EVENT_REQUESTED = "media_release_requested"
EVENT_DONE = "media_release_unknown"


class ReleaseRefused(ValueError):

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def _provider_of(task: dict) -> str:
    return "outsee" if task.get("api") == "outsee" else "kie"


def _window() -> float:
    return float(media_resume.window_s())


async def _keys(session, project_id: int, since: datetime) -> list[str]:
    from sqlalchemy import select
    await spend_guard.ensure_task_schema(session)
    rows = (await session.scalars(select(spend_guard.ProviderTaskLedger.key).where(
        spend_guard.ProviderTaskLedger.project_id == project_id,
        spend_guard.ProviderTaskLedger.created_at >= since).distinct())).all()
    return sorted(k for k in rows if k)


async def _unresolved(session, project_id: int, key: str, since: datetime) -> list[dict]:
    states = await spend_guard.find_task_states(session, project_id=project_id, key=key, model=None, since=since)
    return [t for t in states if t["intent"] and t["status"] == "submit_unknown"]


async def list_unknown(project_id: int, *, now: datetime | None = None) -> dict[str, Any]:
    from app.db import SessionLocal
    from app.services import media_spend
    await media_spend.drain()
    window = _window()
    now = now or datetime.utcnow()
    out: list[dict] = []
    if window > 0:
        since = now - timedelta(seconds=window)
        async with SessionLocal() as session:
            for key in await _keys(session, project_id, since):
                groups: dict[str, list[dict]] = {}
                for task in await _unresolved(session, project_id, key, since):
                    groups.setdefault(_provider_of(task), []).append(task)
                for provider, tasks in sorted(groups.items()):
                    first = min(t["first_at"] for t in tasks)
                    out.append({
                        "key": key, "provider": provider, "intent_count": len(tasks),
                        "intent_ids": sorted(t["task_id"] for t in tasks)[:10],
                        "models": sorted({t["model"] for t in tasks if t["model"]})[:5],
                        "first_at": first.isoformat(), "age_s": max(0, int((now - first).total_seconds())),
                        "window_s": int(window),
                        "blocked_until": (first + timedelta(seconds=window)).isoformat(),
                        "may_be_billed": True})
    return {"project_id": project_id, "window_s": int(window), "window_enabled": window > 0,
            "blocked": out, "count": len(out)}


def _clean_reason(reason: Any) -> str:
    text = " ".join(str(reason or "").split())
    if len(text) < MIN_REASON_CHARS:
        raise ReleaseRefused("reason_required", f"нужна письменная причина не короче {MIN_REASON_CHARS} символов: "
                                                "что именно проверено в кабинете провайдера")
    return text[:MAX_REASON_CHARS]


async def _audit(*, event: str, project_id: int, key: str, provider: str, reason: str, actor: str,
                 plan_id: str, step_index: int | None, outcome: str, error: str = "", extra: dict | None = None) -> None:
    from app.services.action_layer import store
    async with store.sessions()() as session:
        store.append_history(
            session, event=event, plan_id=plan_id or "", step_index=step_index, actor=actor or "local_web_operator",
            action_id="media.release_unknown",
            params={"project_id": project_id, "key": key, "provider": provider, "reason": reason, **(extra or {})},
            outcome=outcome, error=error)
        await session.commit()


async def _release_kie(session, project_id: int, key: str, tasks: list[dict]) -> int:
    for t in tasks:
        await spend_guard.resolve_intent(
            session, project_id=project_id, key=key, model=t["model"], api=t["api"],
            intent_id=t["task_id"][len(spend_guard.INTENT_PREFIX):], status="rejected", note="released")
    await session.commit()
    return len(tasks)


async def release(project_id: int, key: str, provider: str, *, reason: str, actor: str = "",
                  approved_by: str = "", plan_id: str = "", step_index: int | None = None) -> dict[str, Any]:
    from app.db import SessionLocal
    from app.services import media_spend, outsee_guard
    if provider not in PROVIDERS:
        raise ReleaseRefused("bad_provider", f"провайдер должен быть один из {PROVIDERS}")
    key = (key or "").strip()
    if not key:
        raise ReleaseRefused("key_required", "нужен ключ задачи (clip_005, frame_012, predmet3, music)")
    who = approved_by or actor or "local_web_operator"
    window = _window()
    if window <= 0:
        raise ReleaseRefused("window_disabled", "журнал возобновления выключен (MEDIA_RESUME_WINDOW_S=0): снимать нечего")
    clean = _clean_reason(reason)
    await media_spend.drain()
    since = datetime.utcnow() - timedelta(seconds=window)
    async with SessionLocal() as session:
        todo = [t for t in await _unresolved(session, project_id, key, since) if _provider_of(t) == provider]
    if not todo:
        await _audit(event=EVENT_DONE, project_id=project_id, key=key, provider=provider, reason=clean, actor=who,
                     plan_id=plan_id, step_index=step_index, outcome="refused", error="not_blocked")
        raise ReleaseRefused("not_blocked", f"по ключу {key} у провайдера {provider} нет блокировки "
                                            "неизвестного исхода в окне возобновления")
    ids = sorted(t["task_id"] for t in todo)
    await _audit(event=EVENT_REQUESTED, project_id=project_id, key=key, provider=provider, reason=clean, actor=who,
                 plan_id=plan_id, step_index=step_index, outcome="requested", extra={"intent_ids": ids[:10]})
    if provider == "outsee":
        released = await outsee_guard.release_unknown(project_id, key)
    else:
        async with SessionLocal() as session:
            released = await _release_kie(session, project_id, key, todo)
    await _audit(event=EVENT_DONE, project_id=project_id, key=key, provider=provider, reason=clean, actor=who,
                 plan_id=plan_id, step_index=step_index, outcome="released",
                 extra={"released": released, "intent_ids": ids[:10], "second_charge_possible": True})
    return {"project_id": project_id, "key": key, "provider": provider, "released": released,
            "intent_ids": ids[:10], "approved_by": who, "audit_event": EVENT_DONE,
            "second_charge_possible": True}
