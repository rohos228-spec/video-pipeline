"""TG-UI для библиотеки мастер-промтов.

Перед запуском шага, у которого есть мастер-промт, бот проверяет, выбран
ли в проекте конкретный вариант промта. Если нет — присылает picker:

  📚 Промт для шага «5. Промты картинок»
  Выбери вариант или добавь свой:

  [ default ]
  [ horror_v2 ]
  [ ✏ Редактировать выбранный ]
  [ + Новый промт ]
  [ 🗑 Удалить ]
  [ ⬅ Отмена ]

Callback-data:
  prm:<pid>:<step>:sel:<name>            — выбрать существующий
  prm:<pid>:<step>:add                   — начать «новый промт»
  prm:<pid>:<step>:edit:<name>           — выслать файл, ждать ответ
  prm:<pid>:<step>:delask                — показать список для удаления
  prm:<pid>:<step>:del:<name>            — подтвердить удаление
  prm:<pid>:<step>:cancel                — закрыть picker
  prm:<pid>:<step>:menu                  — обновить picker (refresh)
"""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.services import prompt_library as plib


def picker_text(step_code: str, project_overrides: dict | None) -> str:
    name = (project_overrides or {}).get(step_code)
    chosen_line = (
        f"\nТекущий выбор: <b>{name}</b>"
        if name and plib.prompt_path(step_code, name).exists()
        else "\nТекущий выбор: <i>не задан</i> (будет использован <code>default</code>)"
    )
    human = plib.STEP_HUMAN_NAMES.get(step_code, step_code)
    return (
        f"📚 Мастер-промт для шага «{human}»."
        f"{chosen_line}\n\n"
        "Выбери существующий вариант или добавь свой "
        "(<code>+ Новый</code>) / отредактируй (<code>✏ Редактировать</code>)."
    )


def picker_kb(
    pid: int, step_code: str, project_overrides: dict | None = None
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    chosen = (project_overrides or {}).get(step_code)
    for name in plib.list_prompts(step_code):
        marker = "● " if name == chosen else ""
        rows.append([
            InlineKeyboardButton(
                text=f"{marker}{name}",
                callback_data=f"prm:{pid}:{step_code}:sel:{name}",
            )
        ])
    rows.append([
        InlineKeyboardButton(
            text="✏ Редактировать выбранный",
            callback_data=f"prm:{pid}:{step_code}:editcur",
        ),
    ])
    rows.append([
        InlineKeyboardButton(
            text="+ Новый промт",
            callback_data=f"prm:{pid}:{step_code}:add",
        ),
        InlineKeyboardButton(
            text="🗑 Удалить",
            callback_data=f"prm:{pid}:{step_code}:delask",
        ),
    ])
    rows.append([
        InlineKeyboardButton(
            text="⬅ Отмена",
            callback_data=f"prm:{pid}:{step_code}:cancel",
        ),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def delete_kb(pid: int, step_code: str) -> InlineKeyboardMarkup:
    """Список вариантов для удаления (default нельзя)."""
    rows: list[list[InlineKeyboardButton]] = []
    for name in plib.list_prompts(step_code):
        if name == plib.DEFAULT_NAME:
            continue
        rows.append([
            InlineKeyboardButton(
                text=f"🗑 {name}",
                callback_data=f"prm:{pid}:{step_code}:del:{name}",
            )
        ])
    rows.append([
        InlineKeyboardButton(
            text="⬅ Назад",
            callback_data=f"prm:{pid}:{step_code}:menu",
        ),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def overview_text(project) -> str:
    """Текст для меню `🧰 Промты` — список выбранных вариантов по всем шагам."""
    overrides = dict(getattr(project, "prompt_overrides", None) or {})
    lines = ["📚 <b>Мастер-промты проекта</b>\n"]
    for step_code, human in plib.STEP_HUMAN_NAMES.items():
        chosen = overrides.get(step_code)
        if not chosen or not plib.prompt_path(step_code, chosen).exists():
            chosen = plib.DEFAULT_NAME
        lines.append(f"• <b>{human}</b>: <code>{chosen}</code>")
    return "\n".join(lines)


def overview_kb(pid: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for step_code, human in plib.STEP_HUMAN_NAMES.items():
        rows.append([
            InlineKeyboardButton(
                text=f"⚙ {human}",
                callback_data=f"prm:{pid}:{step_code}:menu",
            )
        ])
    rows.append([
        InlineKeyboardButton(
            text="⬅ В меню проекта",
            callback_data=f"proj:{pid}:menu",
        ),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)
