"""UI-tools агента: интерактивные карточки (кнопки/выбор/варианты).

`present_choice` ставит ход на паузу: сессия → waiting_choice, в
pending_choice — вопрос и опции. UI рисует карточку; ответ пользователя
приходит в POST /agent/sessions/{uuid}/answer → ход продолжается.
"""

from __future__ import annotations

import json
from typing import Any

from app.services.agent.registry import tool

CHOICE_KINDS = ("confirm", "choice", "variant_picker")


class PresentChoiceRequested(Exception):
    """Сигнал loop: tool просит паузу ради ответа пользователя."""

    def __init__(self, card: dict[str, Any]) -> None:
        super().__init__("present_choice")
        self.card = card


@tool(
    name="present_choice",
    description=(
        "Показать пользователю интерактивную карточку выбора и ЖДАТЬ ответа. "
        "kind=confirm — подтверждение (Да/Нет) перед опасным/важным действием; "
        "kind=choice — выбор одной опции; kind=variant_picker — выбор варианта "
        "промпта/стиля. После ответа получишь {selected_id, selected_label}."
    ),
    parameters={
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": list(CHOICE_KINDS)},
            "question": {"type": "string"},
            "options": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "label": {"type": "string"},
                        "description": {"type": "string"},
                    },
                    "required": ["id", "label"],
                },
            },
        },
        "required": ["kind", "question", "options"],
    },
    risk="read",
    tags=["ui"],
)
async def _present_choice(args: dict[str, Any]) -> str:
    kind = str(args.get("kind") or "choice")
    if kind not in CHOICE_KINDS:
        return json.dumps({"error": f"bad kind {kind!r}"}, ensure_ascii=False)
    options = args.get("options") or []
    if not isinstance(options, list) or not (2 <= len(options) <= 8):
        return json.dumps(
            {"error": "options: нужно 2-8 вариантов"}, ensure_ascii=False
        )
    card = {
        "kind": kind,
        "question": str(args.get("question") or "")[:500],
        "options": [
            {
                "id": str(o.get("id") or f"opt{i}"),
                "label": str(o.get("label") or "")[:200],
                "description": str(o.get("description") or "")[:300],
            }
            for i, o in enumerate(options)
            if isinstance(o, dict)
        ],
    }
    if len(card["options"]) < 2:
        return json.dumps({"error": "options: минимум 2 валидных"}, ensure_ascii=False)
    # Исполнение прерывается loop'ом: карточка → pending_choice, ответ позже.
    raise PresentChoiceRequested(card)
