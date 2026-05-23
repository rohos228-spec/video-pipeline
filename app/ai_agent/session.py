"""Runtime state одной AI-сессии.

Сессия живёт от первого запроса до `final_answer` (или cancel / лимит).
Хранит:
- историю messages (для передачи в LLM каждый шаг),
- счётчики токенов / шагов,
- pending HITL (если ждём решения owner'а).

Параллельно в БД хранятся AISession/AIMessage/AIToolCall — для аудита.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from app.models import AISessionMode

# Грубые цены за 1M токенов (₽) для aitunnel.ru — используются для оценки
# стоимости в session_summary, если cost_rub не приходит в usage.
# Реальная стоимость возвращается aitunnel'ом, эти числа — fallback.
_MODEL_PRICES_RUB_PER_M = {
    "gpt-4o-mini": (15.0, 60.0),  # in / out
    "gpt-4o": (250.0, 1000.0),
    "claude-opus-4.1": (1500.0, 7500.0),
}


@dataclass
class RuntimeSession:
    """Состояние одной активной /ai сессии в памяти.

    Параллельно в БД пишется AISession/AIMessage/AIToolCall для аудита.
    Здесь — то что нужно loop'у в runtime.
    """

    db_id: int  # id строки в БД (для логирования и /debug ai)
    chat_id: int
    model: str
    mode: AISessionMode
    initial_query: str

    # OpenAI-формат истории (передаётся в каждый chat.completions).
    history: list[dict[str, Any]] = field(default_factory=list[Any])

    # Счётчики
    step_count: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_rub: float = 0.0

    # Pending state
    cancelled: bool = False
    finished: bool = False
    final_answer: str | None = None
    pending_hitl_future: asyncio.Future[Any] | None = None

    # Branch для auto-mode
    branch: str | None = None

    def add_message(self, role: str, content: str | None, **kwargs: Any) -> None:
        """Добавить сообщение в историю в OpenAI-формате."""
        msg: dict[str, Any] = {"role": role}
        if content is not None:
            msg["content"] = content
        msg.update(kwargs)
        self.history.append(msg)

    def add_tool_result(
        self, tool_call_id: str, content: str, *, tool_name: str | None = None
    ) -> None:
        """Добавить tool-result в историю."""
        msg: dict[str, Any] = {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": content,
        }
        if tool_name:
            msg["name"] = tool_name
        self.history.append(msg)

    def estimate_cost(self) -> float:
        """Грубая оценка стоимости (₽) — если cost_rub из usage не пришёл."""
        if self.cost_rub:
            return self.cost_rub
        in_price, out_price = _MODEL_PRICES_RUB_PER_M.get(
            self.model, (50.0, 200.0)  # консервативный дефолт
        )
        return (
            self.tokens_in / 1_000_000 * in_price
            + self.tokens_out / 1_000_000 * out_price
        )

    def update_cost_from_usage(
        self,
        prompt_tokens: int,
        completion_tokens: int,
        cost_rub: float | None,
    ) -> None:
        self.tokens_in += prompt_tokens
        self.tokens_out += completion_tokens
        if cost_rub is not None:
            self.cost_rub += cost_rub

    def cancel(self) -> None:
        self.cancelled = True
        if self.pending_hitl_future and not self.pending_hitl_future.done():
            self.pending_hitl_future.cancel()

    def is_done(self) -> bool:
        return self.finished or self.cancelled

    def summary_text(self) -> str:
        parts = [
            f"🤖 Сессия #{self.db_id}",
            f"Модель: {self.model}",
            f"Режим: {self.mode.value}",
            f"Шагов: {self.step_count}",
            f"Токены: {self.tokens_in}+{self.tokens_out} (≈ {self.estimate_cost():.2f}₽)",
        ]
        if self.branch:
            parts.append(f"Ветка: {self.branch}")
        if self.final_answer:
            snippet = self.final_answer[:300]
            if len(self.final_answer) > 300:
                snippet += "..."
            parts.append(f"\nИтог:\n{snippet}")
        elif self.cancelled:
            parts.append("\n⏹ Отменена")
        return "\n".join(parts)
