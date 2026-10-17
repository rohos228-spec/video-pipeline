"""Очередь входящих сообщений.

Ack («принял, разбираю») уходит сразу, до классификации. Классификация —
сначала детерминированные правила, спорное отдаётся ``router.classify``.
Задача супервизора это не ждёт: разбор идёт отдельной asyncio-задачей.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from loguru import logger

from app.orchestrator.contracts import (
    ORCH_DEFAULT_TIER,
    ORCH_REASONING_EFFORT,
    AgentRole,
    BudgetScope,
    ClassifyResult,
    MessageMode,
    budget_exhausted_response,
    orch_call_tier,
    orch_run_id,
)
from app.orchestrator.db.migrations import connect, utc_now
from app.orchestrator.events import EventBus

_TOKEN_RE = re.compile(r"[a-zа-яё0-9]+", re.IGNORECASE)
_STOP_TOKENS = frozenset(
    {
        "стой",
        "отмена",
        "останови",
        "остановись",
        "стоп",
        "прерви",
        "прекрати",
        "stop",
        "cancel",
    }
)
_KILL_PHRASES = (
    "аварийный стоп",
    "kill switch",
    "kill-switch",
)
_STEER_PHRASES = (
    "измени план",
    "поменяй план",
    "перепланируй",
    "скорректируй план",
    "вместо этого",
    "учти что",
    "steer:",
)
_QUESTION_STARTS = frozenset(
    {
        "что",
        "как",
        "почему",
        "зачем",
        "когда",
        "где",
        "кто",
        "какой",
        "какая",
        "какие",
        "какое",
        "сколько",
        "можно",
        "нужно",
    }
)
_TASK_MARKERS = ("задач", "план", "шаг", "нод", "кадр", "сцен")


def classify_deterministic(text: str) -> ClassifyResult | None:
    """Правила до LLM. None — спорный случай, нужен fast-классификатор."""
    norm = " ".join((text or "").lower().split())
    tokens = _TOKEN_RE.findall(norm)
    if any(phrase in norm for phrase in _KILL_PHRASES):
        return ClassifyResult(
            tier="fast",
            mode=MessageMode.interrupt,
            deterministic=True,
            confidence=1.0,
            kill_switch=True,
        )
    if any(token in _STOP_TOKENS for token in tokens):
        return ClassifyResult(
            tier="fast",
            mode=MessageMode.interrupt,
            deterministic=True,
            confidence=1.0,
        )
    if any(phrase in norm for phrase in _STEER_PHRASES):
        return ClassifyResult(
            tier="strong",
            mode=MessageMode.steer,
            deterministic=True,
            confidence=1.0,
        )
    mentions_task = any(marker in norm for marker in _TASK_MARKERS)
    is_question = norm.endswith("?") or (bool(tokens) and tokens[0] in _QUESTION_STARTS)
    if is_question and not mentions_task:
        return ClassifyResult(
            tier="fast",
            mode=MessageMode.append,
            deterministic=True,
            confidence=1.0,
        )
    return None


class MessageQueue:
    def __init__(self, bus: EventBus, router: Any, supervisor: Any) -> None:
        self.bus = bus
        self.router = router
        self.supervisor = supervisor
        self._pending: set[asyncio.Task[Any]] = set()

    async def accept(
        self,
        session_id: str,
        text: str,
        mode: MessageMode | None = None,
        project_id: int | None = None,
    ) -> int:
        """Записать реплику, опубликовать ack и поставить разбор в фон.

        ``run_id`` резервируется здесь, до ``classify`` и ``call``.
        Явный ``mode`` не классифицируется, кроме фразы kill switch:
        она перебивает mode. Неизвестное значение mode отсекает HTTP.
        Возвращает seq события ack. Не ждёт разбор.
        """
        task_id = self.supervisor.active_task(session_id)
        run_id = orch_run_id(task_id=task_id, session_id=session_id)
        message_id = _insert_message(session_id, "user", text, task_id=task_id, mode=None)
        ack = await self.bus.publish(
            session_id,
            "ack",
            {"text": text, "accepted": True},
            task_id=task_id,
        )
        job = asyncio.create_task(
            self._classify(session_id, text, run_id, mode, project_id, message_id),
            name=f"orch-classify-{session_id}",
        )
        self._pending.add(job)
        job.add_done_callback(self._pending.discard)
        return ack.seq

    async def drain(self, timeout: float = 2.0) -> None:
        """Дождаться разборов, которые уже стоят в фоне."""
        pending = [job for job in list(self._pending) if not job.done()]
        if not pending:
            return
        await asyncio.wait_for(asyncio.gather(*pending), timeout)

    async def _classify(
        self,
        session_id: str,
        text: str,
        run_id: str,
        mode: MessageMode | None,
        project_id: int | None = None,
        message_id: int | None = None,
    ) -> None:
        published = False
        try:
            forced = classify_deterministic(text)
            if forced is not None and forced.kill_switch:
                decided = forced
            elif mode is not None:
                decided = ClassifyResult(
                    tier=ORCH_DEFAULT_TIER,
                    mode=mode,
                    deterministic=True,
                    confidence=1.0,
                )
            else:
                decided = forced
                if decided is None:
                    task_id = self.supervisor.active_task(session_id)
                    decided = await self.router.classify(
                        text,
                        run_id=run_id,
                        task_id=None if task_id is None else str(task_id),
                    )
                    decided = decided.model_copy(update={"deterministic": False})
            await self.bus.publish(
                session_id,
                "classification",
                {
                    "mode": decided.mode.value,
                    "tier": decided.tier,
                    "deterministic": decided.deterministic,
                    "confidence": decided.confidence,
                    "kill_switch": decided.kill_switch,
                },
            )
            published = True
            if message_id is not None:
                _set_message_mode(message_id, decided.mode.value)
            await self._apply(session_id, text, decided, project_id)
        except Exception:
            logger.exception("orch classification failed for session {}", session_id)
            if published:
                return
            if message_id is not None:
                _set_message_mode(message_id, MessageMode.append.value)
            await self.bus.publish(
                session_id,
                "classification",
                {"mode": MessageMode.append.value, "deterministic": False, "error": "classifier_failed"},
            )

    async def _apply(
        self,
        session_id: str,
        text: str,
        decided: ClassifyResult,
        project_id: int | None = None,
    ) -> None:
        if decided.kill_switch:
            self.supervisor.request_kill(session_id)
            return
        if decided.mode is MessageMode.interrupt:
            self.supervisor.request_cancel(session_id)
            return
        if decided.mode is MessageMode.steer:
            self.supervisor.request_steer(session_id, text)
            return
        if decided.mode is MessageMode.task:
            await self.supervisor.submit_task(session_id, text, project_id=project_id)
            return
        await self._stream_fast(session_id, text, decided.tier or "fast")

    async def _stream_fast(self, session_id: str, text: str, tier: str) -> None:
        """Заглушка стрима: слова из готового ``response.text``.

        Это не контракт UI. Боевой путь — keyword-only ``on_delta`` у
        ``router.call`` (WS5): каждый delta публикуется как token.
        Если роутер не зовёт ``on_delta``, остаётся нарезка готового текста.
        """
        task_id = self.supervisor.active_task(session_id)
        deltas: list[str] = []

        def on_delta(piece: str) -> None:
            deltas.append(str(piece))

        response = await self.router.call(
            orch_call_tier(tier),
            [{"role": "user", "content": text}],
            True,
            run_id=orch_run_id(task_id=task_id, session_id=session_id),
            task_id=None if task_id is None else str(task_id),
            agent_role=AgentRole.classifier.value,
            budget_scope=BudgetScope.internal.value,
            reasoning_effort=ORCH_REASONING_EFFORT,
            on_delta=on_delta,
        )
        parts = deltas or _token_pieces(response.text)
        for part in parts:
            await self.bus.publish(session_id, "token", {"text": part})
        _insert_message(session_id, "assistant", response.text, task_id=task_id, mode=MessageMode.append.value)
        if budget_exhausted_response(response) and task_id is not None:
            await self.supervisor.absorb_budget(task_id, session_id, response)
        elif budget_exhausted_response(response):
            await self.bus.publish(
                session_id,
                "approval_required",
                {"reason": "budget_exhausted", "task_id": None},
            )
        await self.bus.publish(
            session_id,
            "message",
            {"role": "assistant", "text": response.text},
            task_id=task_id,
        )


def _token_pieces(text: str) -> list[str]:
    if not text:
        return []
    bits = text.split(" ")
    pieces: list[str] = []
    for index, bit in enumerate(bits):
        pieces.append(bit if index == len(bits) - 1 else bit + " ")
    return pieces


def _insert_message(
    session_id: str,
    role: str,
    content: str,
    *,
    task_id: int | None,
    mode: str | None,
) -> int:
    conn = connect()
    try:
        cursor = conn.execute(
            """
            INSERT INTO orch_messages (session_id, task_id, role, content, mode, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (session_id, task_id, role, content, mode, utc_now()),
        )
        conn.commit()
        return int(cursor.lastrowid)
    finally:
        conn.close()


def _set_message_mode(message_id: int, mode: str) -> None:
    conn = connect()
    try:
        conn.execute(
            "UPDATE orch_messages SET mode = ? WHERE id = ? AND mode IS NULL",
            (mode, message_id),
        )
        conn.commit()
    finally:
        conn.close()
