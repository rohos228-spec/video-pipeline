"""Stuck-work watchdog. Marks and notifies; never retries, resumes or repairs.

Detects, per observed execution (active or degraded observer):

1. ``model_request``: a ``model_start`` with no ``model_end`` / ``model_error`` of the
   same request older than ``watchdog_model_request_timeout_s`` while its attempt has
   not ended (an ended attempt is already diagnosed as ``uncertain_request``). This
   is also what a process crash in the middle of a provider call leaves behind.
2. ``execution_idle``: nodes are ``running`` but the execution journal has had no
   activity for ``watchdog_execution_idle_s``.

On detection it writes a visible ``stalled`` incident (status ``human_review``,
carrying the existing human-review summary text) and an audit ``ActionRecord`` with
initiator ``watchdog``. Nothing is held, retried or re-sent: the outcome of an
uncertain request is unknown and a human decides. The ``stalled`` type blocks
recovery and decisions (see ``recover_node`` / ``DecisionSupervisor._live``) until it
clears. Only when ``watchdog_kill_switch_on_stall`` is enabled (default off) does a
new incident also switch the global emergency stop on.

A late ``model_end``, a node end or fresh journal activity resolves the incident
automatically (``resolved_confirmed``, ``quality_verified=False``) - resolving a
mark is not a repair.

Hook: ``DecisionSupervisor.tick`` (background mode, i.e. the production observer
loop) calls ``get_watchdog(observer).tick()``; the call is rate limited internally.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime, timedelta

from loguru import logger
from sqlalchemy import exists, func, select
from sqlalchemy.orm import aliased

from app.services import kill_switch

INCIDENT_TYPE = "stalled"
MAX_NEW_PER_SCOPE = 25


class Watchdog:
    def __init__(self, observer):
        self.observer = observer
        self.failures: dict[str, str] = {}
        self.last_created: list[dict] = []
        self._last_run = 0.0
        self._lock = asyncio.Lock()

    async def tick(self, *, force: bool = False) -> list[dict]:
        from app.settings import settings
        if not settings.watchdog_enabled:
            return []
        if not force and time.monotonic() - self._last_run < max(1.0, float(settings.watchdog_interval_s)):
            return []
        if self._lock.locked():
            return []
        async with self._lock:
            self._last_run = time.monotonic()
            return await self.scan()

    async def scan(self, *, now: datetime | None = None) -> list[dict]:
        from app.services.observation import ExecutionControl
        now = now or datetime.utcnow()
        async with self.observer.master() as session:
            execution_ids = (await session.scalars(select(ExecutionControl.execution_id).where(
                ExecutionControl.observer_state.in_(["active", "degraded"])))).all()
        created: list[dict] = []
        for execution_id in execution_ids:
            try:
                created.extend(await self._scan_scope(execution_id, now))
            except Exception as exc:  # one execution never hides the others
                self.failures[execution_id] = type(exc).__name__
                logger.warning("watchdog scan failed: execution={} error_type={}", execution_id, type(exc).__name__)
            else:
                self.failures.pop(execution_id, None)
        self.last_created = created
        if created:
            await self._maybe_trigger_kill_switch(created)
        return created

    async def _maybe_trigger_kill_switch(self, created: list[dict]) -> None:
        from app.settings import settings
        if not settings.watchdog_kill_switch_on_stall:
            return
        try:
            kinds = sorted({c["stall"]["kind"] for c in created})
            await kill_switch.set_state(
                self.observer.master, active=True, actor="watchdog", source="watchdog",
                rationale=f"watchdog: {len(created)} stalled item(s) ({', '.join(kinds)}); "
                          "emergency stop enabled by configuration")
        except Exception as exc:
            logger.warning("watchdog kill switch trigger failed: {}", type(exc).__name__)

    async def _scan_scope(self, execution_id: str, now: datetime) -> list[dict]:
        from app.models import NodeRun, NodeRunStatus
        from app.services.actions_v1 import ActionRecord
        from app.services.diagnosis import Incident
        from app.services.observation import ExecutionControl, ObservationEvent
        from app.settings import settings

        request_timeout = float(settings.watchdog_model_request_timeout_s)
        idle_timeout = float(settings.watchdog_execution_idle_s)
        created: list[dict] = []
        async with self.observer.master() as session:
            scope = await session.get(ExecutionControl, execution_id)
            if scope is None or scope.observer_state not in {"active", "degraded"}:
                return created
            stored = (await session.scalars(select(Incident).where(
                Incident.execution_id == execution_id, Incident.type == INCIDENT_TYPE))).all()
            known = {i.incident_id: i for i in stored}

            # -- 1. model requests that never finished ---------------------------------
            closing = aliased(ObservationEvent)
            node_end = aliased(ObservationEvent)
            cutoff = now - timedelta(seconds=request_timeout)
            candidates = (await session.scalars(select(ObservationEvent).where(
                ObservationEvent.execution_id == execution_id,
                ObservationEvent.kind == "model_start",
                ObservationEvent.request_id.is_not(None),
                ObservationEvent.created_at < cutoff,
                ~exists().where(closing.execution_id == ObservationEvent.execution_id,
                                closing.request_id == ObservationEvent.request_id,
                                closing.kind.in_(("model_end", "model_error"))),
                ~exists().where(node_end.execution_id == ObservationEvent.execution_id,
                                node_end.node_key == ObservationEvent.node_key,
                                node_end.attempt_id == ObservationEvent.attempt_id,
                                node_end.kind.in_(("node_end", "node_error"))),
            ).order_by(ObservationEvent.id).limit(MAX_NEW_PER_SCOPE))).all()
            for event in candidates:
                identity = self._identity(execution_id, "model_request", event.request_id)
                if identity in known:
                    continue
                age = (now - event.created_at).total_seconds()
                created.append(self._open(session, scope, identity, event=event, node_key=event.node_key,
                    attempt_id=event.attempt_id, fact=(
                        f"Запрос модели {event.request_id} начат {int(age)} с назад и не завершён "
                        "(нет model_end/model_error). Исход неизвестен; повтор запрещён."),
                    stall={"kind": "model_request", "request_id": event.request_id,
                           "started_at": event.created_at.isoformat(), "age_s": int(age),
                           "threshold_s": request_timeout}, ActionRecord=ActionRecord, Incident=Incident))
                known[identity] = True

            # -- 2. running nodes with a silent journal ------------------------------
            open_request_marks = any(
                (i.details or {}).get("stall", {}).get("kind") == "model_request"
                and i.status != "resolved_confirmed" for i in stored) or any(
                c["stall"]["kind"] == "model_request" for c in created)
            running = (await session.scalars(select(NodeRun).where(
                NodeRun.workflow_run_id == scope.workflow_run_id,
                NodeRun.node_key.in_(scope.node_keys or []),
                NodeRun.status == NodeRunStatus.running))).all()
            last_at = last_id = None
            if running:
                row = (await session.execute(select(func.max(ObservationEvent.created_at),
                    func.max(ObservationEvent.id)).where(ObservationEvent.execution_id == execution_id))).one()
                last_at, last_id = row
                started = [n.started_at for n in running if n.started_at is not None]
                reference = max([t for t in [last_at, *started] if t is not None], default=None)
                if reference is not None and not open_request_marks:
                    idle = (now - reference).total_seconds()
                    identity = self._identity(execution_id, "execution_idle", last_id)
                    if idle > idle_timeout and identity not in known:
                        node = min(running, key=lambda n: n.started_at or now)
                        attempt = await session.scalar(select(ObservationEvent.attempt_id).where(
                            ObservationEvent.execution_id == execution_id,
                            ObservationEvent.node_key == node.node_key,
                            ObservationEvent.kind == "node_start").order_by(ObservationEvent.id.desc()).limit(1))
                        created.append(self._open(session, scope, identity, event=None, node_key=node.node_key,
                            attempt_id=attempt, fact=(
                                f"Нода {node.node_key} в состоянии running, а в журнале выполнения нет "
                                f"активности {int(idle)} с. Прогресс неизвестен; автоповтор запрещён."),
                            stall={"kind": "execution_idle", "last_event_id": last_id,
                                   "last_event_at": reference.isoformat(), "age_s": int(idle),
                                   "threshold_s": idle_timeout,
                                   "running_nodes": [n.node_key for n in running]},
                            ActionRecord=ActionRecord, Incident=Incident))

            # -- 3. clear marks whose condition no longer holds ---------------------
            for row in stored:
                if row.status == "resolved_confirmed":
                    continue
                reason = await self._cleared(session, row, ObservationEvent, NodeRun, NodeRunStatus)
                if reason:
                    row.status = "resolved_confirmed"
                    row.updated_at = datetime.utcnow()
                    row.details = {**row.details, "status": "resolved_confirmed", "blocking": False,
                                   "currently_relevant": False,
                                   "resolution": {"kind": "condition_cleared", "reason": reason,
                                                  "quality_verified": False, "automatic_action": False}}
            await session.commit()
        if created:
            await self._notify(created)
        return created

    @staticmethod
    def _identity(execution_id: str, kind: str, key) -> str:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{execution_id}|{INCIDENT_TYPE}|{kind}|{key}"))

    async def _cleared(self, session, row, ObservationEvent, NodeRun, NodeRunStatus) -> str | None:
        stall = (row.details or {}).get("stall") or {}
        if stall.get("kind") == "model_request":
            closed = await session.scalar(select(ObservationEvent.id).where(
                ObservationEvent.execution_id == row.execution_id,
                ObservationEvent.request_id == stall.get("request_id"),
                ObservationEvent.kind.in_(("model_end", "model_error"))).limit(1))
            if closed:
                return "request_completion_recorded"
            ended = await session.scalar(select(ObservationEvent.id).where(
                ObservationEvent.execution_id == row.execution_id,
                ObservationEvent.node_key == row.node_key, ObservationEvent.attempt_id == row.attempt_id,
                ObservationEvent.kind.in_(("node_end", "node_error"))).limit(1))
            return "attempt_ended_diagnosed_as_uncertain_request" if ended else None
        if stall.get("kind") == "execution_idle":
            latest = await session.scalar(select(func.max(ObservationEvent.id)).where(
                ObservationEvent.execution_id == row.execution_id))
            if latest is not None and latest > int(stall.get("last_event_id") or 0):
                return "journal_activity_resumed"
            from app.services.observation import ExecutionControl
            scope = await session.get(ExecutionControl, row.execution_id)
            still = await session.scalar(select(NodeRun.id).where(
                NodeRun.workflow_run_id == scope.workflow_run_id if scope else NodeRun.id < 0,
                NodeRun.node_key.in_(stall.get("running_nodes") or []),
                NodeRun.status == NodeRunStatus.running).limit(1))
            return None if still else "no_node_running"
        return None

    def _open(self, session, scope, identity, *, event, node_key, attempt_id, fact, stall, ActionRecord, Incident) -> dict:
        from app.services.decision_v1 import MAX_STEPS
        from app.services.diagnosis import RECOMMENDATIONS, TITLES
        from app.services.human_review_summary import build_human_review_summary
        from app.services.observation import panel_safe
        evidence = []
        if event is not None:
            evidence.append({"event_id": event.event_id, "kind": event.kind, "db_scope": event.db_scope,
                             "attempt_id": event.attempt_id, "request_id": event.request_id})
        details = {
            "incident_id": identity, "execution_id": scope.execution_id, "project_id": scope.project_id,
            "node_key": node_key, "attempt_id": attempt_id, "type": INCIDENT_TYPE,
            "title": TITLES[INCIDENT_TYPE], "severity": "error", "status": "human_review",
            "blocking": True, "currently_relevant": True, "fact": fact,
            "cause": {"hypothesis": None, "certainty": "unknown"}, "evidence_incomplete": True,
            "event_ids": [e["event_id"] for e in evidence],
            "request_ids": [event.request_id] if event is not None and event.request_id else [],
            "evidence": evidence, "recovery": [], "resolution": None, "stall": stall,
            "node_title": node_key or "Выполнение группы",
            "recommendation": {"action": "human_review", "title": RECOMMENDATIONS["human_review"],
                "reason": fact, "automatic_action": False,
                "safety_conditions": ["Не повторять запрос автоматически: исход предыдущего неизвестен.",
                                      "Повтор и возобновление - только решением оператора."]},
        }
        summary = build_human_review_summary(
            node_key=node_key or "execution", incident_id=identity, incident_type=INCIDENT_TYPE,
            incident_details=details, escalation_reason=fact, model_steps_used=0, max_model_steps=MAX_STEPS)
        details["human_review_summary"] = summary.as_dict()
        details["human_review_summary_text"] = summary.text  # panel_safe hides the key "text"
        session.add(Incident(incident_id=identity, execution_id=scope.execution_id,
                             project_id=scope.project_id, node_key=node_key, attempt_id=attempt_id,
                             type=INCIDENT_TYPE, severity="error", status="human_review",
                             details=panel_safe(details)))
        session.add(ActionRecord(
            action_id=str(uuid.uuid4()), execution_id=scope.execution_id, project_id=scope.project_id,
            action_kind="watchdog_stalled", node_key=node_key, attempt_id=attempt_id, initiator="watchdog",
            rationale=fact[:1000], evidence_ids=[e["event_id"] for e in evidence],
            preconditions={"stall": stall, "incident_id": identity, "auto_retry": False,
                           "summary": summary.text[:600]},
            idempotency_key="watchdog:" + identity, status="human_review",
            verification={"verified": False, "business_action": False}))
        return {"incident_id": identity, "execution_id": scope.execution_id,
                "project_id": scope.project_id, "node_key": node_key, "stall": stall}

    async def _notify(self, created: list[dict]) -> None:
        try:
            from app.services.event_bus import get_bus
            for item in created:
                await get_bus().publish(f"projects.{item['project_id']}", {
                    "type": "observation_updated", "execution_id": item["execution_id"]})
        except Exception as exc:
            logger.warning("watchdog notification failed: {}", type(exc).__name__)
        # C-18: тревога идёт и владельцу через единственную точку доставки (без Telegram строка
        # журнала помечается «не доставлено», как везде), а не только в шину интерфейса.
        try:
            from app.services.supervisor_heartbeat import _send
            for item in created:
                kind = (item.get("stall") or {}).get("kind", "stalled")
                node = item.get("node_key") or "выполнение группы"
                await _send(f"Сторож: зависла работа в проекте #{item['project_id']} "
                            f"({node}, {kind}). Нужно решение человека.", "watchdog_stalled",
                            project_id=item.get("project_id"))
        except Exception as exc:  # noqa: BLE001 - доставка тревоги не должна ронять сканирование
            logger.warning("watchdog telegram alarm failed: {}", type(exc).__name__)


def get_watchdog(observer) -> Watchdog:
    if not hasattr(observer, "watchdog"):
        observer.watchdog = Watchdog(observer)
    return observer.watchdog
