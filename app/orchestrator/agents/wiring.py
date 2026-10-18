"""Ленивые адаптеры прод-рантайма к модулям соседних потоков.

Импорт только через проверку наличия модуля. Чужие пакеты сюда не
вливаются: нет модуля или импорт упал — заглушка и имя в ``runtime_degraded``.
"""

from __future__ import annotations

import importlib
import importlib.util
import inspect
import re
import sys
from typing import Any

from app.orchestrator.agents.planner import NullRetriever
from app.orchestrator.agents.stubs import FallbackRouter, UnlinkedActions, UnlinkedValidators
from app.orchestrator.contracts import ActionResult, ClassifyResult, MessageMode, Risk, coerce_risk

_ROUTING = "app.orchestrator.routing"
_RAG = "app.orchestrator.rag"
_VALIDATORS = "app.orchestrator.validators"
_ACTIONS = "app.orchestrator.actions"
_APPROVAL_CHANGE_ID = re.compile(r"^orch-appr-\d+-\d+$")
_ENVELOPE_STATUSES = frozenset({"ok", "failed", "approval_required"})


def load_module(name: str) -> Any | None:
    """Импорт по имени. ``None`` — модуля нет или он падает при загрузке."""
    if name in sys.modules:
        loaded = sys.modules[name]
        return loaded if loaded is not None else None
    try:
        spec = importlib.util.find_spec(name)
    except (ImportError, ValueError, AttributeError):
        return None
    if spec is None:
        return None
    try:
        return importlib.import_module(name)
    except Exception:
        return None


def load_router() -> tuple[Any, str | None]:
    """``build_router()`` из WS5 (#359). Нет пакета — ``FallbackRouter``."""
    module = load_module(_ROUTING)
    if module is None:
        return FallbackRouter(), "routing"
    factory = getattr(module, "build_router", None)
    if not callable(factory):
        return FallbackRouter(), "routing"
    try:
        router = factory()
    except Exception:
        return FallbackRouter(), "routing"
    if router is None or not callable(getattr(router, "call", None)):
        return FallbackRouter(), "routing"
    if not callable(getattr(router, "classify", None)):
        return FallbackRouter(), "routing"
    return RoutingAdapter(router), None


def load_rag() -> tuple[Any, str | None]:
    """``retrieve`` и ``rules_system_block`` из WS6 (#363). Профиль не задаём."""
    module = load_module(_RAG)
    retrieve = getattr(module, "retrieve", None) if module is not None else None
    if not callable(retrieve):
        return NullRetriever(), "rag"
    return ModuleRag(module), None


class ModuleRag:
    """Объектный retrieve поверх функции модуля. Сигнатура #363 ``6ea0b058``."""

    def __init__(self, module: Any) -> None:
        self._module = module

    async def retrieve(
        self,
        query: str,
        corpora: list[str] | tuple[str, ...] | None = None,
        top_k: int = 8,
    ) -> Any:
        result = self._module.retrieve(query, corpora, top_k)
        return result

    def rules_system_block(self) -> str:
        fn = getattr(self._module, "rules_system_block", None)
        if not callable(fn):
            return ""
        value = fn()
        return "" if value is None else str(value)

    def reset_engine(self) -> None:
        fn = getattr(self._module, "reset_engine", None)
        if callable(fn):
            fn()


class RoutingAdapter:
    """Провод WS3 → сигнатуры ``call``/``classify`` из #359 ``111f55ee``.

    ``reasoning_effort`` у ``ModelRouter.call`` нет: аргумент принимается
    от ``router_call`` и в роутер не уходит. ``Classification`` — dataclass
    без ``model_copy``; очередь читает ``ClassifyResult``.
    """

    def __init__(self, inner: Any) -> None:
        self.inner = inner

    async def call(
        self,
        tier: str = "auto",
        messages: list | None = None,
        stream: bool = False,
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        run_id: str | None = None,
        task_id: str | None = None,
        agent_role: str | None = None,
        risk: str | None = None,
        mutating: bool | None = None,
        budget_scope: str | None = None,
        on_delta: Any = None,
        reasoning_effort: str | None = None,
    ) -> Any:
        del reasoning_effort
        return await self.inner.call(
            tier,
            messages,
            stream,
            temperature=temperature,
            max_tokens=max_tokens,
            run_id=run_id,
            task_id=task_id,
            agent_role=agent_role,
            risk=risk,
            mutating=mutating,
            budget_scope=budget_scope,
            on_delta=on_delta,
        )

    async def classify(
        self,
        message: str,
        *,
        run_id: str | None = None,
        task_id: str | None = None,
        agent_role: str | None = None,
        risk: str | None = None,
        mutating: bool | None = None,
    ) -> ClassifyResult:
        raw = await self.inner.classify(
            message,
            agent_role=agent_role,
            risk=risk,
            mutating=mutating,
            run_id=run_id,
            task_id=task_id,
        )
        if isinstance(raw, ClassifyResult):
            return raw
        mode = getattr(raw, "mode", MessageMode.append)
        if not isinstance(mode, MessageMode):
            mode = MessageMode(str(getattr(mode, "value", mode)))
        return ClassifyResult(
            tier=str(getattr(raw, "tier", "strong")),
            mode=mode,
            deterministic=bool(getattr(raw, "deterministic", False)),
            confidence=float(getattr(raw, "confidence", 1.0) or 0.0),
            kill_switch=bool(getattr(raw, "kill_switch", False)),
        )


def load_validators() -> tuple[Any, str | None]:
    """``run(name, project_id, context=None)`` из WS1 #351 ``b0c5e0c3``."""
    module = load_module(_VALIDATORS)
    fn = getattr(module, "run", None) if module is not None else None
    if not callable(fn):
        return UnlinkedValidators(), "validators"
    return FunctionValidators(fn), None


class FunctionValidators:
    def __init__(self, fn: Any) -> None:
        self._fn = fn

    def run(self, name: str, project_id: int, context: dict[str, Any] | None = None) -> Any:
        return self._fn(name, project_id, context)


def load_actions() -> tuple[Any, str | None]:
    """Мост к ``actions.execute``. Модуля ещё нет — заглушка и ``actions`` в degraded."""
    module = load_module(_ACTIONS)
    execute = getattr(module, "execute", None) if module is not None else None
    if not callable(execute):
        return ActionsBridge(UnlinkedActions()), "actions"
    return ActionsBridge(ModuleActions(module)), None


class ModuleActions:
    def __init__(self, module: Any) -> None:
        self._module = module

    async def execute(self, name: str, params: dict[str, Any], task_ctx: dict[str, Any]) -> Any:
        result = self._module.execute(name, params, task_ctx)
        if inspect.isawaitable(result):
            result = await result
        return result

    def metadata_of(self, name: str) -> Any:
        fn = getattr(self._module, "metadata_of", None)
        if not callable(fn):
            raise KeyError(name)
        return fn(name)


class ActionsBridge:
    """Будущий ``metadata_of``: dict или ``KeyError``. Approval-id действиям не отдаём."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner

    def metadata_of(self, name: str) -> Any:
        fn = getattr(self.inner, "metadata_of", None)
        if not callable(fn):
            raise KeyError(name)
        return fn(name)

    def risk_of(self, name: str) -> Risk:
        try:
            meta = self.metadata_of(name)
        except KeyError:
            return Risk.high
        if meta is None:
            return Risk.high
        raw = meta.get("risk") if isinstance(meta, dict) else getattr(meta, "risk", Risk.high)
        return coerce_risk(raw)

    async def execute(self, name: str, params: dict[str, Any], task_ctx: dict[str, Any]) -> Any:
        result = self.inner.execute(name, params, task_ctx)
        if inspect.isawaitable(result):
            result = await result
        if isinstance(result, dict) and result.get("status") in _ENVELOPE_STATUSES:
            return _strip_envelope(result)
        if not isinstance(result, ActionResult):
            result = ActionResult.model_validate(result)
        change_id = result.change_id
        if isinstance(change_id, str) and _APPROVAL_CHANGE_ID.match(change_id):
            return result.model_copy(update={"change_id": None})
        return result


def _strip_approval_id(value: Any) -> Any:
    if isinstance(value, str) and _APPROVAL_CHANGE_ID.match(value):
        return None
    return value


def _strip_envelope(payload: dict[str, Any]) -> dict[str, Any]:
    """Конверт r2.2 проходит как есть. ``orch-appr-*`` срезается и в теле ``result``."""
    body = dict(payload)
    if "change_id" in body:
        body["change_id"] = _strip_approval_id(body.get("change_id"))
    inner = body.get("result")
    if isinstance(inner, dict):
        inner = dict(inner)
        if "change_id" in inner:
            inner["change_id"] = _strip_approval_id(inner.get("change_id"))
        body["result"] = inner
    return body
