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
  [ ✏️ Сопр. сообщение ]   ← (только если шаг поддерживает override)
  [ ⬅ Отмена ]

Callback-data:
  prm:<pid>:<step>:sel:<name>            — выбрать существующий
  prm:<pid>:<step>:add                   — начать «новый промт»
  prm:<pid>:<step>:edit:<name>           — выслать файл, ждать ответ
  prm:<pid>:<step>:delask                — показать список для удаления
  prm:<pid>:<step>:del:<name>            — подтвердить удаление
  prm:<pid>:<step>:cancel                — закрыть picker
  prm:<pid>:<step>:menu                  — обновить picker (refresh)
  prm:<pid>:<step>:msgmenu               — открыть подменю «сопр. сообщения»
  prm:<pid>:<step>:msgsend               — отправить файл с текущим текстом
  prm:<pid>:<step>:msgreset              — сбросить override на дефолт
"""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.services import gpt_text_builder as gtb
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
    pid: int,
    step_code: str,
    project_overrides: dict | None = None,
    *,
    has_msg_override: bool = False,
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
    # Кнопка редактирования «сопр. сообщения» — только для шагов, в
    # которых поддерживается override полного текста (см. SUPPORTED_STEPS
    # в gpt_text_builder).
    if gtb.is_supported(step_code):
        marker = "✅ " if has_msg_override else ""
        rows.append([
            InlineKeyboardButton(
                text=f"{marker}✏️ Сопр. сообщение",
                callback_data=f"prm:{pid}:{step_code}:msgmenu",
            ),
        ])
    # Шорткат на picker'е стиля персонажа (`hero_style`) — туда юзер
    # идёт каждый раз, когда настраивает шаг 4. Логично дать ему
    # отсюда же доступ к «сопр. сообщению» самого шага 4 (`hero`).
    if step_code == "hero_style" and gtb.is_supported("hero"):
        rows.append([
            InlineKeyboardButton(
                text="✏️ Сопр. сообщение (Hero)",
                callback_data=f"prm:{pid}:hero:msgmenu",
            ),
        ])
    rows.append([
        InlineKeyboardButton(
            text="⬅ Отмена",
            callback_data=f"prm:{pid}:{step_code}:cancel",
        ),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def msg_menu_text(step_code: str, has_override: bool) -> str:
    human = plib.STEP_HUMAN_NAMES.get(step_code, step_code)
    status = (
        "✅ <b>отредактировано пользователем</b>"
        if has_override
        else "<i>дефолтное (собирается из мастер-промта + контекста проекта)</i>"
    )
    return (
        f"✏️ <b>Сопр. сообщение</b> для шага «{human}»\n\n"
        f"Текущее состояние: {status}\n\n"
        "Это тот текст, который уходит в ChatGPT вместе с прикреплёнными "
        "файлами (project.xlsx и т.п.). Можно отредактировать его под "
        "конкретный проект — изменения сохраняются в БД и применяются "
        "при каждом запуске этого шага.\n\n"
        "<b>Как редактировать:</b>\n"
        "1. Жми «📥 Получить файл» — пришлю .md с текущим текстом.\n"
        "2. Открой файл, отредактируй, сохрани.\n"
        "3. Ответь на моё сообщение этим .md/.txt-файлом — заменю.\n\n"
        "Чтобы вернуться к дефолту — «🔄 Сбросить»."
    )


def msg_menu_kb(pid: int, step_code: str, has_override: bool) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text="📥 Получить файл",
                callback_data=f"prm:{pid}:{step_code}:msgsend",
            )
        ],
    ]
    if has_override:
        rows.append([
            InlineKeyboardButton(
                text="🔄 Сбросить (вернуть дефолт)",
                callback_data=f"prm:{pid}:{step_code}:msgreset",
            )
        ])
    rows.append([
        InlineKeyboardButton(
            text="⬅ Назад к picker'у",
            callback_data=f"prm:{pid}:{step_code}:menu",
        )
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
