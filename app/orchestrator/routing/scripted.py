"""Сценарные ответы для тестов FSM/очереди (WS3). Провайдер не вызывается.

Скрипт: ``{"contains": "...", "text": "..."}`` или ``{"regex": "...", "text": "..."}``.
Нет совпадения — ``RouterError("scripted-miss")``, не живой вызов.
``router_impl`` в логе — ``scripted``. Поле provider остаётся ``52pay|kie``
(по умолчанию 52pay): это не реальный расход, его отличает ``router_impl``.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import replace
from typing import Any

from app.orchestrator.routing.hashing import normalize_messages
from app.orchestrator.routing.model_router import ModelRouter
from app.orchestrator.routing.transport import CompletionResult
from app.orchestrator.routing.types import CallContext, RouterError


class ScriptedRouter(ModelRouter):
    impl_name = "scripted"

    def __init__(
        self,
        config=None,
        *,
        scripts: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(config, **kwargs)
        self.scripts = list(scripts or [])

    def _match(self, messages: list[dict[str, str]]) -> dict[str, Any] | None:
        text = ""
        normalized = normalize_messages(messages)
        for message in reversed(normalized):
            if message["role"] == "user":
                text = message["content"]
                break
        for script in self.scripts:
            if "contains" in script:
                if str(script["contains"]) in text:
                    return script
                continue
            if "regex" in script and re.search(str(script["regex"]), text):
                return script
        return None

    async def _dispatch(self, ctx: CallContext):
        hop = self._hops(ctx)[0]
        if self._blocked(ctx):
            return self._waiting_approval(ctx, hop, 0)
        script = self._match(ctx.messages)
        if script is None:
            raise RouterError("scripted-miss")
        provider = str(script.get("provider") or "52pay")
        if provider not in {"52pay", "kie"}:
            raise ValueError("provider must be 52pay|kie")
        model = str(script.get("model") or hop.model or "gpt-5.6-sol")
        logged = replace(hop, model=model, provider=provider, tier=ctx.effective_tier)
        result = CompletionResult(
            text=str(script.get("text") or ""),
            finish_reason=str(script.get("finish_reason") or "stop"),
            model=model,
            prompt_tokens=int(script.get("prompt_tokens") or 0),
            completion_tokens=int(script.get("completion_tokens") or 0),
            reasoning_tokens=int(script.get("reasoning_tokens") or 0),
        )
        return self._success(
            ctx,
            logged,
            result,
            call_id=str(uuid.uuid4()),
            fallback_index=0,
            started=0.0,
            cost_override=float(script["cost_usd"]) if "cost_usd" in script else 0.0,
            latency_override=0,
        )
