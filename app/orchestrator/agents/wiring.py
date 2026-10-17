"""Ленивые адаптеры прод-рантайма к модулям соседних потоков.

Импорт только через проверку наличия модуля. Чужие пакеты сюда не
вливаются: нет модуля или импорт упал — заглушка и имя в ``runtime_degraded``.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
from typing import Any

from app.orchestrator.agents.planner import NullRetriever
from app.orchestrator.agents.stubs import FallbackRouter
from app.orchestrator.contracts import ClassifyResult, MessageMode

_ROUTING = "app.orchestrator.routing"
_RAG = "app.orchestrator.rag"


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
