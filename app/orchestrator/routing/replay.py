"""ReplayRouter: ответ только из фикстуры. Промах = ``FixtureMiss``, не живой вызов.

Ключ — sha256 канонического JSON ``{tier, messages, temperature, max_tokens,
reasoning_effort}`` уже после разрешения D1/D6 (tier фактический, effort
primary-хопа). Файл ``{key}.json``.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from app.orchestrator.routing.hashing import fixture_key
from app.orchestrator.routing.model_router import ModelRouter, primary_key_fields
from app.orchestrator.routing.transport import CompletionResult
from app.orchestrator.routing.types import CallContext, FixtureMiss, LLMUsage


def _usage(raw: dict[str, Any] | None) -> LLMUsage:
    data = raw or {}
    return LLMUsage(
        prompt_tokens=int(data.get("prompt_tokens") or 0),
        completion_tokens=int(data.get("completion_tokens") or 0),
        reasoning_tokens=int(data.get("reasoning_tokens") or 0),
    )


class ReplayRouter(ModelRouter):
    impl_name = "replay"

    def __init__(self, config=None, *, fixture_dir: Path | str, **kwargs: Any) -> None:
        super().__init__(config, **kwargs)
        self.fixture_dir = Path(fixture_dir)

    async def _dispatch(self, ctx: CallContext):
        hop = self._hops(ctx)[0]
        if self._blocked(ctx):
            return self._waiting_approval(ctx, hop, 0)
        fields = primary_key_fields(
            self.config,
            tier=ctx.effective_tier,
            messages=ctx.messages,
            temperature=ctx.temperature,
            max_tokens=ctx.max_tokens,
        )
        key = fixture_key(**fields)
        path = self.fixture_dir / f"{key}.json"
        call_id_s = str(uuid.uuid4())
        if not path.is_file():
            self._log(
                ctx,
                hop,
                call_id=call_id_s,
                attempt=1,
                fallback_index=0,
                status="fixture_miss",
                error="fixture-miss",
                latency_ms=0,
                fixture_key=key,
            )
            raise FixtureMiss(key)
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("key") != key:
            self._log(
                ctx,
                hop,
                call_id=call_id_s,
                attempt=1,
                fallback_index=0,
                status="fixture_miss",
                error="fixture-miss",
                latency_ms=0,
                fixture_key=key,
            )
            raise FixtureMiss(key)
        body = payload.get("response") or {}
        usage = _usage(body.get("usage"))
        recorded = CompletionResult(
            text=str(body.get("text") or ""),
            finish_reason=str(body.get("finish_reason") or "stop"),
            model=str(body.get("model") or hop.model),
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            reasoning_tokens=usage.reasoning_tokens,
        )
        # Новый call_id на каждый прогон, чтобы UNIQUE в orch_llm_calls не столкнулся.
        return self._success(
            ctx,
            hop,
            recorded,
            call_id=call_id_s,
            fallback_index=int(body.get("fallback_index") or 0),
            started=0.0,
            fixture_key_value=key,
            cost_override=float(body.get("cost_usd") or 0.0),
            latency_override=int(body.get("latency_ms") or 0),
        )
