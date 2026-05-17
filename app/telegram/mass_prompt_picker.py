"""TG-UI для библиотеки мастер-промтов на уровне массового проекта (батча).

Зеркало `prompt_picker.py`, но callback-data всегда привязана к batch_id
вместо project_id, и при сохранении задаётся выбор «локально / глобально».

Callback-data:
  mprm:<bid>:overview                  — список всех шагов
  mprm:<bid>:<step>:menu               — picker конкретного шага
  mprm:<bid>:<step>:sel:<name>         — выбрать существующий вариант
  mprm:<bid>:<step>:add                — начать «новый вариант»
  mprm:<bid>:<step>:editcur            — редактировать выбранный (получить файл)
  mprm:<bid>:<step>:edit:<name>        — редактировать конкретный (получить файл)
  mprm:<bid>:<step>:delask             — показать список для удаления
  mprm:<bid>:<step>:del:<name>         — подтвердить удаление
  mprm:<bid>:<step>:msgmenu            — подменю «сопр. сообщения»
  mprm:<bid>:<step>:msgsend            — отправить файл с текущим сопр.сообщ.
  mprm:<bid>:<step>:msgreset           — сбросить override
  mprm:<bid>:<step>:cancel             — закрыть picker
  mprm:save:<bid>:<step>:<name>:loc    — сохранить локально (только этот батч)
  mprm:save:<bid>:<step>:<name>:glob   — сохранить глобально (для буд. массовых)
  mprm:txtsave:<bid>:<step>:loc        — сохранить сопр. сообщение локально
  mprm:txtsave:<bid>:<step>:glob       — сохранить сопр. сообщение глобально
"""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.models import BatchProject
from app.services import gpt_text_builder as gtb
from app.services import mass_prompts as mp
from app.services import prompt_library as plib


def overview_text(batch: BatchProject) -> str:
    """Сводный обзор: показывает выбранные варианты по всем шагам."""
    snap = batch.settings_snapshot or {}
    overrides = dict(snap.get("prompt_overrides") or {})
    text_overrides = dict(snap.get("gpt_text_overrides") or {})
    lines = [
        f"🧰 <b>Промты + тексты массовой «{batch.name}»</b>",
        "",
        (
            "Здесь ты редактируешь промты и «сопр. сообщения» для всех "
            "подпроектов этого массового. При сохранении можно выбрать:\n"
            "• <b>Локально</b> — только этот массовый.\n"
            "• <b>Глобально</b> — будущие новые массовые получат это "
            "как default (но НЕ одиночные).\n"
        ),
        "",
        "<b>Текущие варианты:</b>",
    ]
    for step_code, human in plib.STEP_HUMAN_NAMES.items():
        chosen = overrides.get(step_code) or plib.DEFAULT_NAME
        msg_mark = " · ✏️ текст" if text_overrides.get(step_code) else ""
        lines.append(f"• <b>{human}</b>: <code>{chosen}</code>{msg_mark}")
    return "\n".join(lines)


def overview_kb(batch: BatchProject) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for step_code, human in plib.STEP_HUMAN_NAMES.items():
        rows.append([
            InlineKeyboardButton(
                text=f"⚙ {human}",
                callback_data=f"mprm:{batch.id}:{step_code}:menu",
            )
        ])
    rows.append([
        InlineKeyboardButton(
            text="⬅ К меню массового",
            callback_data=f"mass:open:{batch.id}",
        ),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def picker_text(batch: BatchProject, step_code: str) -> str:
    snap = batch.settings_snapshot or {}
    overrides = snap.get("prompt_overrides") or {}
    chosen = overrides.get(step_code)
    human = plib.STEP_HUMAN_NAMES.get(step_code, step_code)
    chosen_line = (
        f"\nТекущий выбор: <b>{chosen}</b>"
        if chosen
        else "\nТекущий выбор: <i>не задан</i> (используется <code>default</code>)"
    )
    return (
        f"📚 Мастер-промт массовой для шага «{human}»."
        f"{chosen_line}\n\n"
        "Выбери существующий вариант или добавь/отредактируй.\n"
        "При сохранении бот спросит: <b>локально</b> или <b>глобально</b>."
    )


def picker_kb(batch: BatchProject, step_code: str) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    snap = batch.settings_snapshot or {}
    overrides = snap.get("prompt_overrides") or {}
    chosen = overrides.get(step_code)
    text_overrides = snap.get("gpt_text_overrides") or {}

    # Список вариантов с учётом snapshot + mass-global + global.
    for name in mp.list_variants_for_batch(batch.slug, step_code):
        marker = "● " if name == chosen else ""
        rows.append([
            InlineKeyboardButton(
                text=f"{marker}{name}",
                callback_data=f"mprm:{batch.id}:{step_code}:sel:{name}",
            ),
        ])
    rows.append([
        InlineKeyboardButton(
            text="✏ Редактировать выбранный",
            callback_data=f"mprm:{batch.id}:{step_code}:editcur",
        ),
    ])
    rows.append([
        InlineKeyboardButton(
            text="+ Новый промт",
            callback_data=f"mprm:{batch.id}:{step_code}:add",
        ),
        InlineKeyboardButton(
            text="🗑 Удалить",
            callback_data=f"mprm:{batch.id}:{step_code}:delask",
        ),
    ])
    if gtb.is_supported(step_code):
        marker = "✅ " if text_overrides.get(step_code) else ""
        rows.append([
            InlineKeyboardButton(
                text=f"{marker}✏️ Сопр. сообщение",
                callback_data=f"mprm:{batch.id}:{step_code}:msgmenu",
            ),
        ])
    rows.append([
        InlineKeyboardButton(
            text="⬅ К списку шагов",
            callback_data=f"mprm:{batch.id}:overview",
        ),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def msg_menu_text(batch: BatchProject, step_code: str) -> str:
    human = plib.STEP_HUMAN_NAMES.get(step_code, step_code)
    text_overrides = (batch.settings_snapshot or {}).get("gpt_text_overrides") or {}
    has_override = bool(text_overrides.get(step_code))
    status = (
        "✅ <b>отредактировано (на уровне массовой)</b>"
        if has_override
        else "<i>дефолтное (мастер-промт + контекст проекта)</i>"
    )
    return (
        f"✏️ <b>Сопр. сообщение</b> массовой для шага «{human}»\n\n"
        f"Текущее состояние: {status}\n\n"
        "Это текст, который уходит в ChatGPT вместе с прикреплёнными "
        "файлами. Можно отредактировать на уровне массовой — изменения "
        "наследуются всеми будущими подпроектами этого массового.\n\n"
        "<b>Как редактировать:</b>\n"
        "1. «📥 Получить файл» — пришлю .md с текущим текстом.\n"
        "2. Открой/отредактируй/сохрани.\n"
        "3. Ответь моим сообщением .md/.txt-файлом — заменю.\n"
        "4. На последнем шаге выбираешь — локально (этот массовый) "
        "или глобально (для всех новых).\n\n"
        "Чтобы вернуться к дефолту — «🔄 Сбросить»."
    )


def msg_menu_kb(batch: BatchProject, step_code: str) -> InlineKeyboardMarkup:
    text_overrides = (batch.settings_snapshot or {}).get("gpt_text_overrides") or {}
    has_override = bool(text_overrides.get(step_code))
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(
            text="📥 Получить файл",
            callback_data=f"mprm:{batch.id}:{step_code}:msgsend",
        )],
    ]
    if has_override:
        rows.append([InlineKeyboardButton(
            text="🔄 Сбросить (вернуть дефолт)",
            callback_data=f"mprm:{batch.id}:{step_code}:msgreset",
        )])
    rows.append([
        InlineKeyboardButton(
            text="⬅ Назад к picker'у",
            callback_data=f"mprm:{batch.id}:{step_code}:menu",
        ),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def delete_kb(batch: BatchProject, step_code: str) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for name in mp.list_variants_for_batch(batch.slug, step_code):
        if name == plib.DEFAULT_NAME:
            continue
        rows.append([InlineKeyboardButton(
            text=f"🗑 {name}",
            callback_data=f"mprm:{batch.id}:{step_code}:del:{name}",
        )])
    rows.append([InlineKeyboardButton(
        text="⬅ Назад",
        callback_data=f"mprm:{batch.id}:{step_code}:menu",
    )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def save_choice_kb(
    batch_id: int, step_code: str, variant_name: str
) -> InlineKeyboardMarkup:
    """Клавиатура выбора «локально / глобально» при сохранении файла."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="🏷 Локально (только этот массовый)",
            callback_data=f"mprm:save:{batch_id}:{step_code}:{variant_name}:loc",
        )],
        [InlineKeyboardButton(
            text="🌐 Глобально (для всех будущих массовых)",
            callback_data=f"mprm:save:{batch_id}:{step_code}:{variant_name}:glob",
        )],
        [InlineKeyboardButton(
            text="⬅ Отмена",
            callback_data=f"mprm:{batch_id}:{step_code}:menu",
        )],
    ])


def text_save_choice_kb(batch_id: int, step_code: str) -> InlineKeyboardMarkup:
    """Клавиатура «локально / глобально» для сопр. сообщения."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="🏷 Локально (только этот массовый)",
            callback_data=f"mprm:txtsave:{batch_id}:{step_code}:loc",
        )],
        [InlineKeyboardButton(
            text="🌐 Глобально (для всех будущих массовых)",
            callback_data=f"mprm:txtsave:{batch_id}:{step_code}:glob",
        )],
        [InlineKeyboardButton(
            text="⬅ Отмена",
            callback_data=f"mprm:{batch_id}:{step_code}:msgmenu",
        )],
    ])
