"""Terminal tool: final_answer.

Когда LLM вызывает final_answer — loop завершается, текст идёт owner'у.
"""

from __future__ import annotations

from typing import Any


async def _run(args: dict, ctx: Any) -> dict:
    """Просто эхо обратно. loop в loop.py знает: это terminal."""
    return {
        "ok": True,
        "answer": args.get("answer", ""),
    }


# Импорт здесь чтобы избежать циклической зависимости с tools/__init__.py.
from app.ai_agent.tools._spec import ToolSpec  # noqa: E402

TOOL_FINAL_ANSWER = ToolSpec(
    name="final_answer",
    spec={
        "type": "function",
        "function": {
            "name": "final_answer",
            "description": (
                "ОБЯЗАТЕЛЬНО вызови этот tool в самом конце, когда задача решена. "
                "Передай в `answer` финальный ответ для пользователя (что было сделано, "
                "какие правки внесены, на что обратить внимание). Это терминальный tool — "
                "после него loop завершается."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "answer": {
                        "type": "string",
                        "description": "Финальный текст для пользователя (markdown ок).",
                    }
                },
                "required": ["answer"],
            },
        },
    },
    run=_run,
    is_hitl=False,
    is_terminal=True,
    description_short="Завершить сессию и вернуть ответ owner'у",
)
