"""TG-UI для глобальной библиотеки мастер-промтов (`prompts/*`).

Фаза 8. Зеркало `prompt_picker.py` (per-project) и `mass_prompt_picker.py`
(per-batch), но работает на ГЛОБАЛЬНОМ уровне — без привязки к проекту
или массовой генерации. Здесь юзер редактирует базовый default,
с которого все одиночные проекты и новые батчи начинают.

Точка входа: команда `/prompts` (везде) или кнопка «🧰 Промты
(библиотека)» в потоке МАССОВОЙ генерации (`mass_list_kb`). В
индивидуальное `/menu` (`main_menu_kb`) кнопка НЕ добавляется — UX
индивидуальной генерации остаётся неизменным.

Callback-data:
  gprm:overview                       — список всех шагов
  gprm:<step>:menu                    — picker конкретного шага
  gprm:<step>:sel:<name>              — пометить вариант как «активный»
                                        (это чисто визуально-навигационная
                                        отметка, в БД ничего не пишется —
                                        per-project выбор идёт через
                                        `prm:*`, per-batch через `mprm:*`)
  gprm:<step>:add                     — начать «новый вариант»
  gprm:<step>:editcur                 — получить файл выбранного варианта
  gprm:<step>:edit:<name>             — получить файл конкретного варианта
  gprm:<step>:delask                  — показать список для удаления
  gprm:<step>:del:<name>              — подтвердить удаление
  gprm:<step>:cancel                  — вернуться в `menu:root`
"""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.services import prompt_library as plib


def overview_text() -> str:
    """Текст обзорного экрана: список всех шагов с количеством вариантов."""
    lines: list[str] = [
        "🧰 <b>Глобальная библиотека мастер-промтов</b>",
        "",
        (
            "Здесь хранятся базовые промты, которые видят:\n"
            "• <b>одиночные проекты</b> — это их основа (override через "
            "<code>🧰 Промты</code> в меню проекта),\n"
            "• <b>новые массовые</b> — копируются в snapshot батча "
            "при создании.\n"
        ),
        "",
        "<b>Кол-во вариантов по шагам:</b>",
    ]
    for step_code, human in plib.STEP_HUMAN_NAMES.items():
        try:
            n = len(plib.list_prompts(step_code))
        except Exception:  # noqa: BLE001
            n = 0
        lines.append(f"• <b>{human}</b>: {n} вар.")
    return "\n".join(lines)


def overview_kb() -> InlineKeyboardMarkup:
    """Клавиатура обзора — по кнопке на каждый шаг + «закрыть»."""
    rows: list[list[InlineKeyboardButton]] = []
    for step_code, human in plib.STEP_HUMAN_NAMES.items():
        rows.append([
            InlineKeyboardButton(
                text=f"⚙ {human}",
                callback_data=f"gprm:{step_code}:menu",
            )
        ])
    rows.append([
        InlineKeyboardButton(
            text="⬅ В главное меню",
            callback_data="menu:root",
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def picker_text(step_code: str) -> str:
    """Текст пикера конкретного шага."""
    human = plib.STEP_HUMAN_NAMES.get(step_code, step_code)
    try:
        names = plib.list_prompts(step_code)
    except Exception:  # noqa: BLE001
        names = []
    return (
        f"📚 <b>Глобальная библиотека</b> · шаг «{human}»\n\n"
        f"Найдено вариантов: <b>{len(names)}</b>. "
        f"<code>default</code> — базовый, удалить его нельзя.\n\n"
        "Жми вариант → пришлю файл (можно отредактировать и прислать "
        "обратно). «+ Новый» — создать пустой шаблон. «🗑 Удалить» — "
        "выбрать вариант для удаления."
    )


def picker_kb(step_code: str) -> InlineKeyboardMarkup:
    """Клавиатура пикера: список вариантов + add/edit/delete/cancel."""
    rows: list[list[InlineKeyboardButton]] = []
    try:
        names = plib.list_prompts(step_code)
    except Exception:  # noqa: BLE001
        names = []
    for name in names:
        rows.append([
            InlineKeyboardButton(
                text=name,
                callback_data=f"gprm:{step_code}:edit:{name}",
            )
        ])
    rows.append([
        InlineKeyboardButton(
            text="+ Новый промт",
            callback_data=f"gprm:{step_code}:add",
        ),
        InlineKeyboardButton(
            text="🗑 Удалить",
            callback_data=f"gprm:{step_code}:delask",
        ),
    ])
    rows.append([
        InlineKeyboardButton(
            text="⬅ К списку шагов",
            callback_data="gprm:overview",
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def delete_kb(step_code: str) -> InlineKeyboardMarkup:
    """Клавиатура выбора варианта для удаления (default скрыт)."""
    rows: list[list[InlineKeyboardButton]] = []
    try:
        names = plib.list_prompts(step_code)
    except Exception:  # noqa: BLE001
        names = []
    for name in names:
        if name == plib.DEFAULT_NAME:
            continue
        rows.append([
            InlineKeyboardButton(
                text=f"🗑 {name}",
                callback_data=f"gprm:{step_code}:del:{name}",
            )
        ])
    rows.append([
        InlineKeyboardButton(
            text="⬅ Назад",
            callback_data=f"gprm:{step_code}:menu",
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)
