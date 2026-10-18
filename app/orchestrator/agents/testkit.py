"""Тестовый runtime приёмки R6 (WS4). Сети нет: роутер скриптовый.

Включение только явное:

* ``ORCH_RUNTIME=r6-fixture`` — ``get_runtime()`` собирает этот runtime.
* ``ORCH_TEST=1`` — обязателен вместе с ним. Без ``ORCH_TEST`` процесс
  падает на старте (lifespan FastAPI и ``build_r6_runtime``) с понятной ошибкой.
* Без ``ORCH_RUNTIME`` прод-сборка не меняется, даже если ``ORCH_TEST=1``.

Живой API приёмки, без исходящей сети:

    ORCH_RUNTIME=r6-fixture ORCH_TEST=1 ORCH_API_TOKEN=... \\
        python -m app.orchestrator.agents.testkit

Слушает ``http://127.0.0.1:8877``.

* ``POST /api/orchestrator/chat/{session}/message``
* ``GET  /api/orchestrator/chat/{session}/stream``

Задержка неплатных шагов: ``ORCH_TEST_STEP_DELAY_S`` (секунды, по умолчанию ``1``),
чтобы steer и interrupt успевали прийти, пока задача идёт.

Фразы и действия. Удалений кадров и сцен в планах нет.

* Обычная задача (``mode=task``, в тексте нет «среднее касание»): три
  неплатных read-only шага ``test.look``.
* Пока они идут, «измени план» или ``mode=steer``: checkpoint перепланирования
  и следующий шаг ``test.look_again``.
* «отмена» / «стой» или ``mode=interrupt``: ``agent_status=cancelled``.
* «kill switch» / «аварийный стоп»: тот же ``request_kill``, что и в проде.
  Хук ``_publish_kill`` пишет стоп-ключ узла ``n_r6`` в память фикстуры.
  Событие ``kill_switch`` несёт ``stop_key_set=true``, ``only_node=true``
  и этот ключ. Если узла в хранилище нет, хук оставляет прод-ответ:
  ``stop_key_set=false``, ``stop_key=pending``.
* «среднее касание» в тексте задачи: один шаг ``test.medium_touch``
  (реестр ``risk=medium``, ничего не меняет и не удаляет).
  С ``project_id`` — ``approval_required`` / ``waiting_approval``, reason ``risk``.
  Без ``project_id`` — reason ``missing_project_id``.
* Текст без детерминированного правила (например «добро пожаловать»):
  ``router.call`` зовёт ``on_delta`` по словам, в SSE идут ``token``.
  Слово «сцен» в тексте само по себе делает сообщение задачей, токены не идут.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
from typing import Any

from app.orchestrator.agents.critic import PlanCritic
from app.orchestrator.agents.planner import RouterPlanner
from app.orchestrator.agents.runtime import Runtime, build_runtime
from app.orchestrator.agents.stubs import UnlinkedValidators
from app.orchestrator.contracts import (
    ActionResult,
    ClassifyResult,
    LLMResponse,
    MessageMode,
    Risk,
    registry_risk,
    require_internal_run_id,
)

R6_HOST = "127.0.0.1"
R6_PORT = 8877
R6_RUNTIME = "r6-fixture"
PHRASE_MEDIUM = "среднее касание"
LOOK_ACTION = "test.look"
LOOK_AGAIN_ACTION = "test.look_again"
MEDIUM_ACTION = "test.medium_touch"
R6_NODE = "n_r6"
_TASK_MARKERS = ("задач", "шаг", "план", "сцен")
_LOOK_STEPS = 3


def enforce_r6_gate() -> None:
    """``r6-fixture`` без ``ORCH_TEST=1`` — ошибка старта, не тихий прод."""
    if os.environ.get("ORCH_RUNTIME", "").strip() != R6_RUNTIME:
        return
    if os.environ.get("ORCH_TEST", "").strip() == "1":
        return
    raise RuntimeError(
        "ORCH_RUNTIME=r6-fixture требует ORCH_TEST=1. "
        "Это тестовый runtime приёмки R6 на 127.0.0.1:8877, в прод его не включают."
    )


def step_delay_s() -> float:
    raw = os.environ.get("ORCH_TEST_STEP_DELAY_S", "1").strip()
    try:
        value = float(raw)
    except ValueError:
        return 1.0
    if value < 0:
        return 0.0
    return value


def _chunks(text: str) -> list[str]:
    raw = text or ""
    words = raw.split(" ")
    if len(words) >= 2:
        return [word if index == len(words) - 1 else word + " " for index, word in enumerate(words)]
    if len(raw) < 2:
        return [raw or " "]
    mid = max(1, len(raw) // 2)
    return [raw[:mid], raw[mid:]]


class R6Router:
    """Скриптовый роутер: классификатор, план и критик без сети.

    ``call`` требует непустой ``run_id`` и для ``internal``, и для ``pipeline``.
    Если передан ``on_delta``, текст отдаётся почанково — очередь публикует ``token``.
    """

    def __init__(self) -> None:
        self.calls = 0

    async def classify(
        self,
        message: str,
        *,
        run_id: str | None = None,
        task_id: str | None = None,
    ) -> ClassifyResult:
        require_internal_run_id(run_id, "internal")
        del task_id
        text = message or ""
        lowered = text.lower()
        if PHRASE_MEDIUM in lowered or any(marker in lowered for marker in _TASK_MARKERS):
            mode = MessageMode.task
        else:
            mode = MessageMode.append
        return ClassifyResult(
            tier="strong",
            mode=mode,
            deterministic=True,
            confidence=1.0,
            kill_switch=False,
        )

    async def call(
        self,
        tier: str,
        messages: list[dict[str, Any]],
        stream: bool = False,
        *,
        run_id: str | None = None,
        task_id: str | None = None,
        agent_role: str | None = None,
        budget_scope: str | None = None,
        reasoning_effort: str | None = None,
        on_delta: Any = None,
    ) -> LLMResponse:
        require_internal_run_id(run_id, budget_scope)
        del stream, task_id, reasoning_effort
        self.calls += 1
        text = _answer(messages, agent_role)
        if on_delta is not None:
            for piece in _chunks(text):
                on_delta(piece)
        return LLMResponse(
            text=text,
            call_id="r6-fixture",
            model="r6-fixture",
            provider="fixture",
            tier=tier or "strong",
            finish_reason="stop",
            cost_usd=0.0,
            latency_ms=1,
            fallback_index=0,
            deterministic=True,
            fixture_key="r6",
        )


class R6Actions:
    """Закрытый реестр приёмки. Других имён нет — неизвестное имя destructive.

    ``test.medium_touch`` имеет risk=medium, ничего не пишет и не удаляет.
    ``test.look`` и ``test.look_again`` — read-only, risk=none.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.risks = {
            LOOK_ACTION: "none",
            LOOK_AGAIN_ACTION: "none",
            MEDIUM_ACTION: "medium",
        }

    def risk_of(self, action_name: str) -> Risk:
        return registry_risk(action_name, self.risks)

    def metadata_of(self, action_name: str) -> dict[str, object]:
        """Карточка приёмки. Нет ключа — ``KeyError``, как закрытый реестр.

        ``read_only`` истинно: шаги ничего не пишут. ``medium`` всё равно
        ждёт approval через риск, это не ``deletion_blocked``.
        """
        if action_name not in self.risks:
            raise KeyError(action_name)
        return {
            "name": action_name,
            "risk": self.risks[action_name],
            "read_only": True,
            "destructive": False,
            "paid": False,
        }

    async def execute(self, name: str, params: dict[str, Any], task_ctx: dict[str, Any]) -> ActionResult:
        del params, task_ctx
        self.calls.append(name)
        delay = step_delay_s()
        if delay:
            await asyncio.sleep(delay)
        return ActionResult(ok=True, data={"name": name}, change_id=None, cost=0.0)


def _answer(messages: list[dict[str, Any]], agent_role: str | None) -> str:
    blob = "\n".join(str(item.get("content") or "") for item in messages)
    role = agent_role or ""
    if role == "critic":
        return "approve"
    if role == "planner":
        return json.dumps({"steps": _plan_steps(blob)}, ensure_ascii=False)
    return "фиксация принята и разобрана"


def _plan_steps(blob: str) -> list[dict[str, Any]]:
    if PHRASE_MEDIUM in blob.lower():
        return [_step(MEDIUM_ACTION, "medium")]
    if "измени" in blob.lower():
        return [_step(LOOK_AGAIN_ACTION, "none")]
    return [_step(LOOK_ACTION, "none", {"n": index}) for index in range(_LOOK_STEPS)]


def _step(action: str, risk: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "action": action,
        "params": dict(params or {}),
        "expected": "ok",
        "paid": False,
        "mutating": False,
        "risk": risk,
    }


class NodeStopStore:
    """Память стоп-ключа узла. Прод её не видит: без узла ключ не ставится."""

    def __init__(self) -> None:
        self._nodes: set[str] = set()
        self._armed: dict[str, str] = {}

    def add_node(self, node_key: str) -> None:
        self._nodes.add(node_key)

    def arm(self, session_id: str) -> str | None:
        if not self._nodes:
            return None
        key = secrets.token_hex(16)
        self._armed[session_id] = key
        return key

    def same(self, session_id: str, key: object) -> bool:
        stored = self._armed.get(session_id)
        return isinstance(key, str) and bool(stored) and stored == key and key != "pending"


def _install_node_stops(supervisor: Any) -> NodeStopStore:
    """Тот же хук, что зовёт ``request_kill``: ``_publish_kill``.

    Класс супервизора не меняется. У экземпляра фикстуры хук сначала
    пишет ключ в память узла и публикует ``stop_key_set=true``.
    Пустое хранилище оставляет исходный ответ ``pending``.
    """
    store = NodeStopStore()
    store.add_node(R6_NODE)
    original = supervisor._publish_kill

    async def _publish_kill(session_id: str, task_id: int | None) -> None:
        key = store.arm(session_id)
        if not key:
            await original(session_id, task_id)
            return
        await supervisor.bus.publish(
            session_id,
            "kill_switch",
            {"stop_key_set": True, "stop_key": key, "only_node": True, "task_id": task_id},
            task_id=task_id,
        )

    supervisor._publish_kill = _publish_kill
    supervisor.node_stops = store
    return store


def build_r6_runtime() -> Runtime:
    """Сборка R6. Без ``ORCH_TEST=1`` бросает ``RuntimeError`` и ничего не поднимает."""
    enforce_r6_gate()
    router = R6Router()
    runtime = build_runtime(
        actions=R6Actions(),
        validators=UnlinkedValidators(),
        router=router,
        planner=RouterPlanner(router),
        critic=PlanCritic(router),
    )
    _install_node_stops(runtime.supervisor)
    return runtime


def build_r6_app() -> Any:
    """Тот же чат, что у студии: HTTP + SSE, без остального API и без сети."""
    from fastapi import FastAPI

    from app.orchestrator.auth import OrchestratorAuthMiddleware
    from app.web.routers.orchestrator_chat import router as chat_router

    app = FastAPI()
    app.add_middleware(OrchestratorAuthMiddleware)
    app.include_router(chat_router, prefix="/api")
    return app


def main() -> None:
    """Поднять приёмку на ``127.0.0.1:8877``. Гейт проверяется до bind."""
    enforce_r6_gate()
    from app.orchestrator.agents.runtime import get_runtime

    get_runtime()
    import uvicorn

    uvicorn.run(build_r6_app(), host=R6_HOST, port=R6_PORT, log_level="info", access_log=False)


if __name__ == "__main__":
    main()
