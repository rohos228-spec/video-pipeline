"""Telegram-бот: ручное управление пайплайном через /menu.

Главные сценарии:
  /menu                              — главное меню (новый/существующие проекты)
  cb=menu:new  → ввод темы → создание проекта → меню проекта
  cb=menu:list                       → список проектов
  cb=proj:<id>:menu                  → меню одного проекта (10 шагов + xlsx)
  cb=proj:<id>:step:<code>           → запустить шаг
  cb=proj:<id>:dl_xlsx               → прислать project.xlsx файлом
  cb=proj:<id>:reload_xlsx           → перечитать xlsx → БД
  cb=proj:<id>:delete                → удалить проект (после подтверждения)

Все «активные» статусы пайплайна — running-статусы. Их выставляет ЭТОТ файл,
когда пользователь жмёт кнопку шага. Воркер видит running-статус и запускает
шаг. После шага статус становится «*_ready», воркер его игнорит, пока юзер
снова не тыкнет кнопку.

Также сохранён старый HITL-callback (hitl:<id>:<action>) — карточки картинок
(шаг 6) шлются как раньше, с кнопками ✅/🔁/❌/✏ для approve/regen/reject/edit.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Coroutine
from typing import Any

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from loguru import logger
from sqlalchemy import select

from app.bots.browser import browser_session
from app.bots.chatgpt import ChatGPTBot
from app.db import session_scope
from app.models import (
    Frame,
    HITLDecision,
    HITLKind,
    HITLRequest,
    Project,
    ProjectStatus,
)
from app.services import gpt_text_builder as gtb
from app.services import prompt_library as plib
from app.settings import settings
from app.storage import ProjectSheet
from app.storage import for_project as _sheet_for_project
from app.telegram.menu import (
    PERSISTENT_BACK_TEXT,
    PERSISTENT_HOME_TEXT,
    PERSISTENT_LAST_TEXT,
    is_step_runnable,
    main_menu_kb,
    persistent_reply_kb,
    project_header,
    project_menu_kb,
    script_step_kb,
    step_by_code,
)
from app.telegram.prompt_picker import (
    delete_kb as _prompt_delete_kb,
)
from app.telegram.prompt_picker import (
    msg_menu_kb as _gpt_msg_menu_kb,
)
from app.telegram.prompt_picker import (
    msg_menu_text as _gpt_msg_menu_text,
)
from app.telegram.prompt_picker import (
    overview_kb as _prompt_overview_kb,
)
from app.telegram.prompt_picker import (
    overview_text as _prompt_overview_text,
)
from app.telegram.prompt_picker import (
    picker_kb as _prompt_picker_kb,
)
from app.telegram.prompt_picker import (
    picker_text as _prompt_picker_text,
)
from app.telegram.wizard import (
    handle_wizard_callback,
    send_wizard_question,
)

dp = Dispatcher()

# Ожидание текстового ответа (тема нового проекта). user_id → True.
_pending_topic_input: dict[int, bool] = {}

# Ожидание описания героя для конкретного проекта.
# user_id → (project_id, hero_index 1..N) — какого по счёту героя сейчас
# описывает пользователь. После N описаний словарь чистится и проект
# уходит в generating_hero.
_pending_hero_brief: dict[int, tuple[int, int]] = {}

# Ожидание выбора кол-ва вариаций для конкретного героя.
# Появляется после того, как юзер описал героя (текстом). Бот шлёт
# инлайн-кнопки 1..5; на клик пишем в project.hero_variations[idx-1] = N
# и переходим к описанию следующего героя или, если все собраны, к
# generating_hero. user_id → (project_id, hero_index 1..N).
_pending_hero_variation: dict[int, tuple[int, int]] = {}

# Ожидание ввода ОТЛИЧИЙ для конкретной вариации героя (вариации 2..N).
# Появляется после выбора кол-ва вариаций (если count >= 2). Юзер
# по очереди пишет «что меняется в вариации 2», «в вариации 3», и т.д.
# user_id → (project_id, hero_index 1..N, variation_index 2..N).
_pending_hero_var_modifier: dict[int, tuple[int, int, int]] = {}

# Ожидание выбора пресета «стиль персонажа» (prompts/04_hero_style/) — picker
# открывается при клике «4. Hero», если в проекте ещё не выбран стиль.
# user_id → project_id. На on_prompt_picker_cb с step_code='hero_style' и
# совпадающим pid считаем, что после сохранения в overrides['hero_style']
# нужно сразу продолжить hero-flow (запросить кол-во героев).
_pending_hero_style: dict[int, int] = {}

# Ожидание имени нового мастер-промта. После того как юзер кликнул
# «+ Новый промт», бот спрашивает имя текстом. Здесь храним к какому
# (проекту, шагу) относится ожидание.
# user_id → (project_id, step_code)
_pending_prompt_name: dict[int, tuple[int, str]] = {}

# Ожидание возврата `.md`-файла после редактирования / создания.
# user_id → (project_id, step_code, prompt_name).
_pending_prompt_upload: dict[int, tuple[int, str, str]] = {}

# Ожидание темы для xlsx-плана (после клика «1. План»).
# user_id → project_id.
_pending_plan_topic: dict[int, int] = {}

# Ожидание выбора файла-промта для xlsx-плана (после ввода темы).
# user_id → (project_id, topic). Когда юзер кликает в picker'е sel:<name>,
# мы видим что для него есть pending запись и запускаем xlsx-flow вместо
# обычного запуска шага.
_pending_plan_prompt: dict[int, tuple[int, str]] = {}

# Ожидание выбора файла-промта для xlsx-сценария (Step 2 «Закадровый текст»).
# Когда юзер кликнул «2. Закадровый текст» на v8-xlsx-проекте — сразу
# показываем picker промтов и сохраняем здесь pid; потом в picker'е
# на sel:<name> запускаем _run_script_xlsx.
# user_id → project_id.
_pending_script_prompt: dict[int, int] = {}

# Ожидание выбора файла-промта для xlsx-разбивки (Step 3 «Разбивка на блоки»).
# Аналогично script. user_id → project_id.
_pending_split_prompt: dict[int, int] = {}

# Ожидание замены voiceover.txt (Step 2 подменю «✏️ Заменить»).
# Юзер жмёт кнопку → бот просит прислать текст или .txt-файл.
# user_id → project_id.
_pending_voiceover_replace: dict[int, int] = {}

# Ожидание ответа на «✏️ Сопр. сообщение» (для gpt_text_overrides).
# Поддерживаем два варианта матчинга — оба индексируют один и тот же
# «активный» edit-сеанс юзера:
#   1. По message_id — если юзер ответил (reply) именно на сообщение
#      бота с .md-файлом. Ключ — message_id отправленного ботом
#      сообщения, значение — (user_id, project_id, step_code).
#   2. По user_id — если юзер просто прислал .md-файл / текст без
#      reply. Ключ — user_id, значение — (project_id, step_code).
# По любому совпадению override сохраняется. Запись чистится после.
_pending_gpt_text_edit: dict[int, tuple[int, int, str]] = {}
_pending_gpt_text_edit_by_user: dict[int, tuple[int, str]] = {}

# Последний открытый юзером проект — для кнопки «📁 Последний проект»
# в постоянной reply-клавиатуре. Обновляется при открытии меню проекта,
# нажатии шага, выборе промта и т.п.  user_id → project_id.
_last_project_by_user: dict[int, int] = {}

# Активные xlsx-flow-операции — чтобы юзер случайным двойным/тройным
# нажатием не запустил параллельные прогоны одного и того же шага
# (ChatGPT в одном чате не справляется с 2-3 параллельными upload'ами,
# в итоге все три кладут в project.xlsx «пустышки»). Ключ —
# (project_id, step_code), где step_code ∈ {'plan', 'script', 'split'}.
_xlsx_flow_active: set[tuple[int, str]] = set()

# Ожидание загрузки отредактированного xlsx обратно в проект.
# Когда юзер скачивает xlsx кнопкой «📥 Скачать xlsx», запоминаем
# project_id. Если после этого юзер пришлёт .xlsx-документ —
# подменим project.xlsx (с бэкапом + валидацией + reload в БД).
# user_id → project_id.
_pending_xlsx_replace: dict[int, int] = {}


def _project_display_topic(project: Project) -> str:
    """Тема или slug для отображения в сообщениях."""
    return (project.topic or "").strip() or project.slug


def _is_enrich_slot(code: str) -> bool:
    """`True` для enrich_1..enrich_5 — sub-step'ов шага 5 «Доп работа с EXCEL».

    Для этих шагов:
      - выбор шаблона в picker'е НЕ запускает шаг автоматом
      - picker остаётся виден после выбора, чтобы можно было ткнуть
        «✏ Редактировать выбранный» или сменить шаблон
      - запуск — только через явную кнопку «▶ Запустить шаг» в picker'е
    """
    if not code.startswith("enrich_"):
        return False
    tail = code[len("enrich_"):]
    return tail.isdigit() and 1 <= int(tail) <= 5


def _can_run_enrich_slot_now(project: Project, step_code: str) -> bool:
    """`True` если этот enrich-слот можно запустить ПРЯМО СЕЙЧАС
    (его prerequisite уже достигнут). Используется, чтобы решить,
    рисовать ли кнопку «▶ Запустить шаг» в picker'е.

    Для слотов, у которых ещё не выполнен предыдущий слот, кнопка
    «▶ Запустить шаг» не нужна (она бы только показала alert при
    клике). Юзер может настроить шаблон и сопр. сообщение, а потом
    стартовать всю цепочку через «▶▶ Запустить все слоты подряд».
    """
    if not _is_enrich_slot(step_code):
        return False
    from app.telegram.menu import status_order, step_by_code
    step = step_by_code(step_code)
    if step is None:
        return False
    if step.requires is None:
        return True
    return status_order(project.status) >= status_order(step.requires)


def _remember_project(user_id: int, project_id: int) -> None:
    """Запоминает что юзер сейчас работает с этим проектом — для кнопки
    «📁 Последний проект» в постоянной клавиатуре."""
    _last_project_by_user[user_id] = project_id


# Текущий «экран» юзера — используется кнопкой «⬅ Назад» (postоянная
# reply-клавиатура), чтобы возвращать на ОДИН шаг назад, а не сразу
# в меню проекта/главное меню.
#
# Возможные значения:
#   ("project_menu", pid, None)        — карточка проекта (proj_menu_kb)
#   ("enrich_submenu", pid, None)      — подменю шага 5 со слотами
#   ("picker", pid, step_code)         — picker промта для шага step_code
#   ("step_submenu", pid, step_code)   — подменю шага (script/split/hero/items/…)
#   ("main", None, None)               — главное меню
#
# Если значения для юзера нет — fallback на старое поведение (project_menu).
_user_screen: dict[int, tuple[str, int | None, str | None]] = {}


def _set_user_screen(
    user_id: int,
    screen_type: str,
    pid: int | None = None,
    extra: str | None = None,
) -> None:
    _user_screen[user_id] = (screen_type, pid, extra)


async def _run_xlsx_with_lock(
    coro: Coroutine[Any, Any, None], project_id: int, step: str
) -> None:
    """Запускает xlsx-flow корутину под глобальным per-(project, step) локом.

    Лок защищает от тройного нажатия одной кнопки, когда пользователь видит
    что «ничего не происходит» и тыкает повторно — в результате 2-3 параллельных
    upload'а в один чат ChatGPT и испорченный project.xlsx.
    """
    key = (project_id, step)
    _xlsx_flow_active.add(key)
    try:
        await coro
    finally:
        _xlsx_flow_active.discard(key)


def _clear_pending_state(user_id: int) -> None:
    """Сбрасывает все pending-состояния юзера (используется при кликах на
    кнопки постоянной клавиатуры — Главное меню / Назад)."""
    _pending_topic_input.pop(user_id, None)
    _pending_hero_brief.pop(user_id, None)
    _pending_hero_variation.pop(user_id, None)
    _pending_hero_var_modifier.pop(user_id, None)
    _pending_hero_style.pop(user_id, None)
    _pending_prompt_name.pop(user_id, None)
    _pending_prompt_upload.pop(user_id, None)
    _pending_plan_topic.pop(user_id, None)
    _pending_plan_prompt.pop(user_id, None)
    _pending_script_prompt.pop(user_id, None)
    _pending_split_prompt.pop(user_id, None)
    _pending_voiceover_replace.pop(user_id, None)


async def _last_project_id_fallback() -> int | None:
    """Если для юзера нет запомненного last-project — возвращает id самого
    свежесозданного проекта (по убыванию id) или None если проектов нет."""
    async with session_scope() as s:
        proj = (
            await s.execute(
                select(Project).order_by(Project.id.desc()).limit(1)
            )
        ).scalar_one_or_none()
    return proj.id if proj is not None else None


def is_owner(msg: Message) -> bool:
    return msg.from_user is not None and msg.from_user.id == settings.telegram_owner_chat_id


# ---------------------------------------------------------------------------
# /start, /menu — главные команды

@dp.message(CommandStart())
async def cmd_start(msg: Message) -> None:
    if not is_owner(msg):
        return
    # Включаем постоянную reply-клавиатуру внизу TG. Она остаётся видна
    # на всех последующих экранах (Telegram её хранит, пока бот её не
    # снимет/не пришлёт другую).
    await msg.answer(
        "Готов. Команды:\n"
        "  /menu — главное меню (создание/просмотр проектов)\n"
        "  /status — список проектов\n"
        "  /status <id> — детали проекта\n\n"
        "Внизу есть постоянные кнопки:\n"
        f"  • <b>{PERSISTENT_HOME_TEXT}</b> — главное меню\n"
        f"  • <b>{PERSISTENT_LAST_TEXT}</b> — последний проект\n"
        f"  • <b>{PERSISTENT_BACK_TEXT}</b> — назад",
        reply_markup=persistent_reply_kb(),
        parse_mode="HTML",
    )


@dp.message(Command("menu"))
async def cmd_menu(msg: Message) -> None:
    if not is_owner(msg):
        return
    _clear_pending_state(msg.from_user.id if msg.from_user else 0)
    # Отдельным сообщением «активируем» постоянную клавиатуру (на случай
    # если юзер пришёл /menu без /start — Telegram запомнит её).
    await msg.answer(
        "Главное меню:",
        reply_markup=persistent_reply_kb(),
    )
    await msg.answer(
        "Что делаем?",
        reply_markup=main_menu_kb(),
    )


@dp.message(Command("test_xlsx"))
async def cmd_test_xlsx(msg: Message) -> None:
    """Дебаг-команда: проверяет, что бот умеет загрузить xlsx в ChatGPT,
    отправить промт и скачать обратно изменённый файл.

    Шаги:
      1. Берём templates/project_template_v8.xlsx.
      2. В Chrome (с remote-debugging-port=29229) открываем новый чат ChatGPT.
      3. Прикрепляем файл, шлём короткий промт "впиши тест и верни файл".
      4. Скачиваем сгенерированный файл, шлём пользователю в Telegram.
    """
    if not is_owner(msg):
        return
    from datetime import datetime
    from pathlib import Path as _Path

    template = _Path("templates/project_template_v8.xlsx")
    if not template.exists():
        await msg.answer(
            f"Шаблон не найден: {template}\nУбедись, что репо обновлён "
            "(git pull origin devin/windows-installer)."
        )
        return

    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    out_path = _Path("data") / f"test_xlsx_out_{ts}.xlsx"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    await msg.answer(
        "Запускаю тест xlsx-загрузки в ChatGPT.\n"
        "Шаги: open new chat → upload xlsx → ask GPT → download response.\n"
        "Не закрывай Chrome. Жди до 5 минут."
    )

    prompt = (
        "Открой прикреплённый Excel-файл. На листе 'Общий план' в ячейку D1 "
        "впиши слово 'тест'. Затем сохрани файл и пришли его мне обратно "
        "как .xlsx, без обрезок и компрессии. Только кратко ответь — никаких "
        "длинных пояснений."
    )

    html_dump_path = _Path("data") / f"test_xlsx_dump_{ts}.html"
    try:
        async with browser_session() as bs:
            gpt = ChatGPTBot(bs)
            await gpt.new_conversation()
            reply = await gpt.ask_with_file(prompt, template, timeout=600)
            logger.info("test_xlsx: GPT reply len={}", len(reply))
            try:
                await gpt.download_attachment_from_last_reply(out_path, timeout=120)
            except Exception:
                # Фолбэк: дампим HTML последнего сообщения, чтобы Devin
                # увидел нужные селекторы скачивания.
                try:
                    full_html = await gpt._dump_last_assistant_html(max_chars=200_000)
                    html_dump_path.write_text(full_html or "", encoding="utf-8")
                except Exception:  # noqa: BLE001
                    pass
                raise
    except Exception as e:  # noqa: BLE001
        logger.exception("test_xlsx failed: {}", e)
        await msg.answer(f"Ошибка: {e}")
        if html_dump_path.exists() and html_dump_path.stat().st_size > 0:
            try:
                await msg.answer_document(
                    FSInputFile(str(html_dump_path)),
                    caption=(
                        "HTML последнего ответа GPT (для подбора селектора скачивания). "
                        "Перешли мне файл — добавлю нужный селектор и починю."
                    ),
                )
            except Exception as ex:  # noqa: BLE001
                logger.exception("dump send failed: {}", ex)
        return

    if not out_path.exists() or out_path.stat().st_size < 100:
        await msg.answer(
            f"Файл не скачался или слишком мал: {out_path} "
            f"(size={out_path.stat().st_size if out_path.exists() else 0})"
        )
        return

    await msg.answer_document(
        FSInputFile(str(out_path)),
        caption=(
            f"Скачанный файл от ChatGPT ({out_path.stat().st_size} байт).\n"
            "Открой и проверь — должна быть 'тест' в ячейке D1 листа 'Общий план'."
        ),
    )


@dp.message(Command("status"))
async def cmd_status(msg: Message) -> None:
    if not is_owner(msg):
        return
    parts = (msg.text or "").split()
    async with session_scope() as s:
        if len(parts) >= 2 and parts[1].isdigit():
            pid = int(parts[1])
            project = (
                await s.execute(select(Project).where(Project.id == pid))
            ).scalar_one_or_none()
            if project is None:
                await msg.answer(f"Проект #{pid} не найден")
                return
            await msg.answer(
                project_header(project),
                parse_mode="HTML",
                reply_markup=project_menu_kb(project),
            )
        else:
            rows = (
                await s.execute(
                    select(Project).order_by(Project.id.desc()).limit(20)
                )
            ).scalars().all()
            if not rows:
                await msg.answer("Пока нет проектов. /menu → 📁 Новый проект")
                return
            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=f"#{p.id} {p.topic[:40]} · {p.status.value}",
                            callback_data=f"proj:{p.id}:menu",
                        )
                    ]
                    for p in rows
                ]
            )
            await msg.answer("Последние проекты:", reply_markup=kb)


# ---------------------------------------------------------------------------
# Главное меню (callback menu:*)

@dp.callback_query(F.data == "menu:root")
async def on_menu_root(cb: CallbackQuery) -> None:
    if cb.from_user.id != settings.telegram_owner_chat_id:
        await cb.answer("Нет доступа", show_alert=True)
        return
    _clear_pending_state(cb.from_user.id)
    await cb.answer()
    # Шлём reply-клавиатуру отдельным сообщением — у callback'а нет
    # возможности вернуть reply-клавиатуру вместе с inline-кнопками.
    await cb.message.answer(
        "Главное меню:",
        reply_markup=persistent_reply_kb(),
    )
    await cb.message.answer("Что делаем?", reply_markup=main_menu_kb())


@dp.callback_query(F.data == "menu:new")
async def on_menu_new(cb: CallbackQuery) -> None:
    if cb.from_user.id != settings.telegram_owner_chat_id:
        await cb.answer("Нет доступа", show_alert=True)
        return
    await cb.answer()
    _pending_topic_input[cb.from_user.id] = True
    await cb.message.answer(
        "Напишите название вашего проекта",
        reply_markup=persistent_reply_kb(),
    )


@dp.callback_query(F.data == "menu:list")
async def on_menu_list(cb: CallbackQuery) -> None:
    if cb.from_user.id != settings.telegram_owner_chat_id:
        await cb.answer("Нет доступа", show_alert=True)
        return
    async with session_scope() as s:
        rows = (
            await s.execute(
                select(Project).order_by(Project.id.desc()).limit(30)
            )
        ).scalars().all()
    await cb.answer()
    if not rows:
        await cb.message.answer("Пока нет проектов. ⬅ Меню → 📁 Новый проект")
        return
    kb = InlineKeyboardMarkup(
        inline_keyboard=(
            [
                [
                    InlineKeyboardButton(
                        text=f"#{p.id} {p.topic[:40]} · {p.status.value}",
                        callback_data=f"proj:{p.id}:menu",
                    )
                ]
                for p in rows
            ]
            + [[InlineKeyboardButton(text="⬅ Меню", callback_data="menu:root")]]
        )
    )
    await cb.message.answer("Существующие проекты:", reply_markup=kb)


@dp.callback_query(F.data == "noop")
async def on_noop(cb: CallbackQuery) -> None:
    await cb.answer("Эта кнопка пока недоступна")


# ---------------------------------------------------------------------------
# Мастер настроек проекта (cb=wiz:<pid>:*)

@dp.callback_query(F.data.startswith("wiz:"))
async def on_wizard_cb(cb: CallbackQuery) -> None:
    if cb.from_user.id != settings.telegram_owner_chat_id:
        await cb.answer("Нет доступа", show_alert=True)
        return
    await handle_wizard_callback(cb)


# ---------------------------------------------------------------------------
# Шаг 4 — выбор количества героев (cb=hero_cnt:<pid>:<N>)

@dp.callback_query(F.data.regexp(r"^hero_cnt:\d+:\d$"))
async def on_hero_count_cb(cb: CallbackQuery) -> None:
    if cb.from_user.id != settings.telegram_owner_chat_id:
        await cb.answer("Нет доступа", show_alert=True)
        return
    parts = (cb.data or "").split(":")
    pid = int(parts[1])
    n = int(parts[2])
    async with session_scope() as s:
        project = (
            await s.execute(select(Project).where(Project.id == pid))
        ).scalar_one_or_none()
        if project is None:
            await cb.answer("Проект не найден", show_alert=True)
            return
        project.hero_count = n
        project.hero_descriptions = []
        project.hero_variations = []
        project.hero_variation_modifiers = []
        if n == 0:
            # Шаг сразу готов — без героев.
            project.hero_description = None
            project.status = ProjectStatus.hero_ready
            try:
                _sheet_for_project(project).write_general(
                    status=project.status.value,
                    hero_description="",
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("hero_count=0 xlsx write failed: {}", e)
            await cb.answer("0 героев — шаг пропущен")
            await _hide_buttons_with_badge(
                cb.message,
                "✅ Без героев. Шаг 4 закрыт.",
            )
            return
    # N >= 1 — просим описание первого.
    user_id = cb.from_user.id
    _pending_hero_brief[user_id] = (pid, 1)
    await cb.answer(f"Будет {n} героев — жду описания первого")
    await _hide_buttons_with_badge(
        cb.message,
        f"Выбрано героев: {n}. Жду описания первого.",
    )
    await cb.message.answer(
        _hero_brief_question_text(1, n),
        parse_mode="HTML",
    )


# ---------------------------------------------------------------------------
# Шаг 4 — выбор кол-ва вариаций для конкретного героя
# (cb=hero_var:<pid>:<hero_idx>:<count>)

@dp.callback_query(F.data.regexp(r"^hero_var:\d+:\d+:\d+$"))
async def on_hero_variation_cb(cb: CallbackQuery) -> None:
    if cb.from_user.id != settings.telegram_owner_chat_id:
        await cb.answer("Нет доступа", show_alert=True)
        return
    parts = (cb.data or "").split(":")
    pid = int(parts[1])
    hero_idx = int(parts[2])
    count = int(parts[3])
    if count < 1 or count > 10 or hero_idx < 1:
        await cb.answer("Плохое значение", show_alert=True)
        return
    user_id = cb.from_user.id
    _pending_hero_variation.pop(user_id, None)
    async with session_scope() as s:
        project = (
            await s.execute(select(Project).where(Project.id == pid))
        ).scalar_one_or_none()
        if project is None:
            await cb.answer("Проект не найден", show_alert=True)
            return
        n_total = project.hero_count or 1
        if hero_idx > n_total:
            await cb.answer("Индекс героя вне диапазона", show_alert=True)
            return
        variations = list(project.hero_variations or [])
        idx0 = hero_idx - 1
        while len(variations) <= idx0:
            variations.append(0)
        variations[idx0] = count
        project.hero_variations = variations
        # Также аккуратно ужимаем list модификаторов под новое значение
        # вариаций (если юзер ранее ввёл 4, потом передумал на 2).
        modifiers_all = list(project.hero_variation_modifiers or [])
        while len(modifiers_all) <= idx0:
            modifiers_all.append([])
        # Для героя hero_idx нужно (count - 1) модификаторов (вариации 2..N).
        cur = list(modifiers_all[idx0] or [])
        need = max(count - 1, 0)
        if len(cur) > need:
            cur = cur[:need]
        modifiers_all[idx0] = cur
        project.hero_variation_modifiers = modifiers_all
    await cb.answer(f"Героя {hero_idx}: {count} вариаций")
    await _hide_buttons_with_badge(
        cb.message,
        f"✅ Герой {hero_idx}: {count} вариаций сохранено.",
    )
    # Если у героя > 1 вариации и модификаторы для них ещё не собраны —
    # просим юзера описать отличия вариации 2.
    if count >= 2:
        # Найдём первую недозаписанную вариацию (2..count).
        cur = list(modifiers_all[idx0] or [])
        next_var = 2 + len(cur)
        if next_var <= count:
            _pending_hero_var_modifier[user_id] = (pid, hero_idx, next_var)
            await cb.message.answer(
                _hero_var_modifier_question_text(hero_idx, n_total, next_var, count),
                parse_mode="HTML",
            )
            return
    # Иначе (count==1 или модификаторы уже все собраны) → пробуем
    # пройти дальше: либо описание следующего героя, либо запуск.
    await _continue_hero_flow_after_step(
        cb.message, user_id, pid, hero_idx, n_total
    )


def _hero_var_modifier_question_text(
    hero_idx: int, n_total: int, var_idx: int, count: int
) -> str:
    return (
        f"Опиши <b>отличия вариации {var_idx}/{count}</b> для героя "
        f"<b>{hero_idx}/{n_total}</b> одним сообщением: что должно "
        "быть иначе по сравнению с вариацией 1 — например другой "
        "ракурс, поза, эмоция, одежда, окружение.\n\n"
        "Пример: «три-четверти слева, лёгкая улыбка, плащ на плечах, "
        "вечерний свет»."
    )


async def _continue_hero_flow_after_step(
    msg: Message,
    user_id: int,
    pid: int,
    hero_idx: int,
    n_total: int,
) -> None:
    """Вызывается когда у героя hero_idx закончен сбор модификаторов
    (или вариаций=1 — модификаторы не нужны). Решает:
      — все герои готовы → запускаем generating_hero
      — есть ещё герои → просим описание следующего
    """
    async with session_scope() as s:
        project = (
            await s.execute(select(Project).where(Project.id == pid))
        ).scalar_one_or_none()
        if project is None:
            await msg.answer("Проект не найден.")
            return
        descriptions = list(project.hero_descriptions or [])
        variations = list(project.hero_variations or [])
        modifiers_all = list(project.hero_variation_modifiers or [])
        all_described = (
            len(descriptions) >= n_total
            and all(d.strip() for d in descriptions[:n_total])
        )
        all_var_set = (
            len(variations) >= n_total
            and all(int(v or 0) >= 1 for v in variations[:n_total])
        )
        # Все ли модификаторы собраны (для каждого героя — variations[i-1]-1 шт.)
        all_modifiers_set = True
        for i in range(n_total):
            need = max(int(variations[i] or 1) - 1, 0)
            cur = list(modifiers_all[i] or []) if i < len(modifiers_all) else []
            if len(cur) < need:
                all_modifiers_set = False
                break
        all_done = all_described and all_var_set and all_modifiers_set
        if all_done:
            project.status = ProjectStatus.generating_hero
            try:
                _sheet_for_project(project).write_general(
                    status=project.status.value,
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("hero_variations xlsx write failed: {}", e)
        slug = project.slug
    if all_done:
        total_imgs = sum(int(v or 1) for v in variations[:n_total])
        await msg.answer(
            f"✅ Все описания, вариации и отличия собраны "
            f"(героев: {n_total}, всего изображений: {total_imgs}).\n"
            f"Запускаю генерацию — воркер подхватит за ~15 сек.\n"
            f"Slug: <code>{slug}</code>.",
            parse_mode="HTML",
        )
        return
    # Не все — переходим к следующему герою (описание).
    next_idx = hero_idx + 1
    if next_idx <= n_total:
        _pending_hero_brief[user_id] = (pid, next_idx)
        await msg.answer(
            _hero_brief_question_text(next_idx, n_total),
            parse_mode="HTML",
        )


# ---------------------------------------------------------------------------
# Меню проекта (cb=proj:<id>:menu и cb=proj:<id>:step:<code>)

@dp.callback_query(F.data.regexp(r"^proj:\d+:menu$"))
async def on_project_menu(cb: CallbackQuery) -> None:
    if cb.from_user.id != settings.telegram_owner_chat_id:
        await cb.answer("Нет доступа", show_alert=True)
        return
    pid = int((cb.data or "").split(":")[1])
    async with session_scope() as s:
        project = (
            await s.execute(select(Project).where(Project.id == pid))
        ).scalar_one_or_none()
        if project is None:
            await cb.answer("Проект не найден", show_alert=True)
            return
        _remember_project(cb.from_user.id, pid)
        _set_user_screen(cb.from_user.id, "project_menu", pid)
        await cb.answer()
        await cb.message.answer(
            project_header(project),
            parse_mode="HTML",
            reply_markup=project_menu_kb(project),
        )


# ----------------------------------------------------------------------
# Шаг 4 (Hero) — подменю «продолжить / перезадать», cb=hero_menu:<pid>:<action>
#
# Действия:
#   - continue     — продолжить как обычно (запросить недостающее
#                    описание/вариацию или запустить генерацию).
#   - reset_briefs — оставить стиль и кол-во героев, обнулить
#                    описания+вариации и запросить заново начиная с героя 1.
#   - reset_all    — обнулить вообще всё (стиль, кол-во, описания,
#                    вариации) и начать с выбора стиля.

@dp.callback_query(F.data.regexp(r"^hero_menu:\d+:(continue|reset_briefs|reset_all)$"))
async def on_hero_menu_cb(cb: CallbackQuery) -> None:
    if cb.from_user.id != settings.telegram_owner_chat_id:
        await cb.answer("Нет доступа", show_alert=True)
        return
    parts = (cb.data or "").split(":")
    pid = int(parts[1])
    action = parts[2]
    user_id = cb.from_user.id
    async with session_scope() as s:
        project = (
            await s.execute(select(Project).where(Project.id == pid))
        ).scalar_one_or_none()
        if project is None:
            await cb.answer("Проект не найден", show_alert=True)
            return

        if action == "reset_briefs":
            # Сохраняем стиль и hero_count, чистим описания+вариации.
            project.hero_descriptions = []
            project.hero_variations = []
            project.hero_variation_modifiers = []
            # Старые approve_hero-одобрения помечаем как rejected, иначе
            # generate_hero увидит «N из N уже одобрено» и сразу выйдет
            # с status=hero_ready (баг при повторной генерации).
            old_hitls = (
                await s.execute(
                    select(HITLRequest).where(
                        HITLRequest.project_id == project.id,
                        HITLRequest.kind == HITLKind.approve_hero,
                        HITLRequest.decision == HITLDecision.approved,
                    )
                )
            ).scalars().all()
            for h in old_hitls:
                h.decision = HITLDecision.rejected
            # Возвращаем статус в frames_ready чтобы юзер мог снова
            # запустить шаг 4 — хоть из hero_ready, хоть из
            # generating_hero (зомби-статус после Ctrl+C).
            if project.status in (
                ProjectStatus.hero_ready,
                ProjectStatus.generating_hero,
            ):
                project.status = ProjectStatus.frames_ready
            # Pending стейты тоже на всякий случай чистим.
            _pending_hero_brief.pop(user_id, None)
            _pending_hero_variation.pop(user_id, None)
            _pending_hero_var_modifier.pop(user_id, None)
            await s.flush()
            await cb.answer("Описания/вариации сброшены")
            n = project.hero_count or 0
            if n <= 0:
                await cb.message.answer(
                    "Сбросил, но кол-во героев = 0. Чтобы задать заново — "
                    "тыкни «🎨 Сменить стиль (всё с начала)» или жми «4. Hero» "
                    "ещё раз и выбирай ❌-вариант."
                )
                return
            # Сразу спрашиваем описание героя 1.
            _pending_hero_brief[user_id] = (pid, 1)
            await cb.message.answer(
                _hero_brief_question_text(1, n),
                parse_mode="HTML",
            )
            return

        if action == "reset_all":
            # Полный сброс: стиль, кол-во, описания, вариации.
            overrides = dict(project.prompt_overrides or {})
            overrides.pop("hero_style", None)
            project.prompt_overrides = overrides
            project.hero_count = None
            project.hero_descriptions = []
            project.hero_variations = []
            project.hero_variation_modifiers = []
            project.hero_description = None
            # Старые approve_hero-одобрения помечаем как rejected — см.
            # коммент выше в ветке reset_briefs.
            old_hitls = (
                await s.execute(
                    select(HITLRequest).where(
                        HITLRequest.project_id == project.id,
                        HITLRequest.kind == HITLKind.approve_hero,
                        HITLRequest.decision == HITLDecision.approved,
                    )
                )
            ).scalars().all()
            for h in old_hitls:
                h.decision = HITLDecision.rejected
            if project.status in (
                ProjectStatus.hero_ready,
                ProjectStatus.generating_hero,
            ):
                project.status = ProjectStatus.frames_ready
            _pending_hero_brief.pop(user_id, None)
            _pending_hero_variation.pop(user_id, None)
            _pending_hero_var_modifier.pop(user_id, None)
            _pending_hero_style.pop(user_id, None)
            await s.flush()
            # Сразу запускаем выбор стиля (как при первом заходе в шаг 4).
            _pending_hero_style[user_id] = pid
            await cb.answer("Все параметры героев сброшены")
            await cb.message.answer(
                "🎨 <b>Шаг 4 (Hero) — стиль персонажа</b>\n\n"
                "Выбери стиль из списка или добавь свой "
                "(<code>+ Новый</code>):",
                parse_mode="HTML",
            )
            await cb.message.answer(
                _prompt_picker_text("hero_style", overrides),
                reply_markup=_prompt_picker_kb(pid, "hero_style", overrides),
                parse_mode="HTML",
            )
            return

        # action == "continue" — обычный flow: дозапрашиваем недостающее
        # или запускаем генерацию. Просто обрабатываем как обычный
        # `proj:<pid>:step:hero` — но пропустив только что показанное
        # подменю. Для этого временно ставим флаг pending_hero_style,
        # что заставляет on_project_step пройти мимо «короткого
        # подменю» (`if not _pending_hero_style.get(...)`).
        await cb.answer("Продолжаю")
        # Вычисляем что показать дальше (тот же набор условий что в
        # on_project_step, но без подменю-проверки).
        n = project.hero_count or 0
        if n <= 0:
            await cb.message.answer(
                "0 героев — шаг пропускаем. Если хочешь героев — "
                "жми «🎨 Сменить стиль (всё с начала)»."
            )
            return
        descriptions = list(project.hero_descriptions or [])
        variations = list(project.hero_variations or [])
        if len(descriptions) < n:
            next_idx = len(descriptions) + 1
            _pending_hero_brief[user_id] = (pid, next_idx)
            await cb.message.answer(
                _hero_brief_question_text(next_idx, n),
                parse_mode="HTML",
            )
            return
        if len(variations) < n:
            next_idx = len(variations) + 1
            _pending_hero_variation[user_id] = (pid, next_idx)
            await cb.message.answer(
                _hero_variation_question_text(next_idx, n),
                reply_markup=_hero_variation_kb(pid, next_idx),
                parse_mode="HTML",
            )
            return
        # Всё собрано → запускаем генерацию.
        step = step_by_code("hero")
        if step is not None:
            project.status = step.running_status
            total_variations = sum(int(v or 1) for v in variations[:n])
            style_chosen = (
                dict(project.prompt_overrides or {}).get("hero_style") or "default"
            )
            await cb.message.answer(
                f"▶ Шаг {step.n}: <b>{step.title}</b>\n"
                f"Героев: {n}, всего изображений с вариациями: "
                f"{total_variations}.\n"
                f"Стиль: <code>{style_chosen}</code>\n"
                f"Воркер подхватит за ~15 сек.",
                parse_mode="HTML",
            )


@dp.callback_query(F.data.regexp(r"^proj:\d+:step:[a-z_0-9]+$"))
async def on_project_step(cb: CallbackQuery) -> None:
    if cb.from_user.id != settings.telegram_owner_chat_id:
        await cb.answer("Нет доступа", show_alert=True)
        return
    parts = (cb.data or "").split(":")
    pid = int(parts[1])
    step_code = parts[3]
    step = step_by_code(step_code)
    if step is None:
        await cb.answer("Неизвестный шаг", show_alert=True)
        return

    async with session_scope() as s:
        project = (
            await s.execute(select(Project).where(Project.id == pid))
        ).scalar_one_or_none()
        if project is None:
            await cb.answer("Проект не найден", show_alert=True)
            return
        _remember_project(cb.from_user.id, pid)
        # Зомби-статус: проект застрял в `generating_*` (например, воркер
        # упал на playwright TargetClosedError, прибили Ctrl+C, chrome
        # перезапустили и т.п.). Для шага 4 (hero) показываем спец-меню
        # «продолжить / сбросить описания / сбросить всё»; для остальных
        # шагов просто разрешаем юзеру повторно ткнуть кнопку — не надо
        # «Этот шаг уже выполняется» (это блокировало возврат к шагу 5,
        # пока проект висит в generating_image_prompts).
        is_hero_zombie = (
            step.code == "hero"
            and project.status is ProjectStatus.generating_hero
        )
        is_other_zombie = (
            step.code != "hero"
            and project.status is step.running_status
        )

        if is_other_zombie:
            logger.info(
                "[#{}] step={} клик при зомби-статусе {}: "
                "разрешаю перезапуск шага.",
                pid, step.code, project.status.value,
            )

        # `failed` больше не используется — воркер вместо этого откатывает
        # статус на prerequisite упавшего шага (см. _run_worker_loop в
        # app/main.py). Старая логика «клик из failed → reset до
        # step.requires» убрана: она позволяла молча проскочить
        # невыполненные prerequisite (тыкаешь шаг 5 из failed → статус
        # ставится в hero_ready, хотя ни шаг 3, ни шаг 4 не отрабатывали;
        # шаг 5 потом падает на «нет кадров», цикл повторяется).
        # Для enrich-слотов (шаг 5) НЕ блокируем клик, даже если
        # prerequisite не достигнут — юзер хочет «настроить заранее»
        # шаблон + сопр. сообщение для слотов 2..N, а потом запустить
        # цепочку через «▶▶ Запустить все слоты подряд». Реальная
        # проверка runnable выполнится при попытке нажать
        # «▶ Запустить шаг» в picker'е (action="run" в
        # on_prompt_picker_cb).
        if (
            not is_hero_zombie
            and not is_other_zombie
            and not _is_enrich_slot(step.code)
            and not is_step_runnable(step, project.status)
        ):
            await cb.answer(
                f"Сначала пройди шаг до {step.requires.value if step.requires else '?'}",
                show_alert=True,
            )
            return

        # Шаг 1 (План) — picker + «✏️ Сопр. сообщение» + «▶ Запустить шаг».
        # Flow аналогичен шагу 5 (enrich): выбрать шаблон, при желании
        # отредактировать сопр. сообщение, явно нажать «▶ Запустить».
        # Если тема ролика ещё не задана — сначала спрашиваем тему.
        if step.code == "plan":
            from pathlib import Path as _Path
            proj_xlsx = (
                _Path(settings.data_dir) / "videos" / project.slug / "project.xlsx"
            )
            if proj_xlsx.exists():
                # Если тема не задана — просим ввести.
                if not (project.topic or "").strip():
                    _pending_plan_topic[cb.from_user.id] = pid
                    _set_user_screen(cb.from_user.id, "picker", pid, "plan")
                    await cb.answer()
                    await cb.message.answer(
                        f"Проект #{pid} «{project.slug}»\n\n"
                        "Напишите тему ролика (может быть длинным описанием):",
                        parse_mode="HTML",
                    )
                    return
                overrides = dict(project.prompt_overrides or {})
                has_msg_override = gtb.has_override(project, "plan")
                chosen = overrides.get("plan")
                show_run = bool(
                    chosen
                    and plib.is_valid_prompt_name(chosen)
                    and plib.prompt_path("plan", chosen).exists()
                )
                _set_user_screen(cb.from_user.id, "picker", pid, "plan")
                await cb.answer()
                await cb.message.answer(
                    f"Тема: <b>{project.topic}</b>\n\n"
                    + _prompt_picker_text("plan", overrides),
                    reply_markup=_prompt_picker_kb(
                        pid, "plan", overrides,
                        has_msg_override=has_msg_override,
                        show_run_button=show_run,
                        show_topic_button=True,
                    ),
                    parse_mode="HTML",
                )
                return
            # xlsx-файла нет — упадём в старую логику ниже.

        # Шаг 2 (Закадровый текст) — новый xlsx-flow.
        #   Подменю шага 2: если voiceover.txt уже есть, показываем
        #   кнопки [📄 Посмотреть voiceover.txt] [▶ Сгенерировать заново]
        #   [⬅ Назад]. Если ещё нет — сразу picker промтов из
        #   prompts/02_script/ (как было).
        if step.code == "script":
            from pathlib import Path as _Path
            proj_xlsx = (
                _Path(settings.data_dir) / "videos" / project.slug / "project.xlsx"
            )
            if proj_xlsx.exists():
                voiceover_path = proj_xlsx.parent / "voiceover.txt"
                voiceover_exists = voiceover_path.exists()
                await cb.answer()
                header = (
                    "<b>Шаг 2. Закадровый текст</b>\n"
                    f"Проект #{pid} «{project.topic}»\n"
                )
                if voiceover_exists:
                    size = voiceover_path.stat().st_size
                    body = (
                        f"Текущий voiceover.txt — <code>{size}</code> байт.\n"
                        "Можно посмотреть его или перегенерировать с другим "
                        "промтом."
                    )
                else:
                    body = (
                        "voiceover.txt ещё не сгенерирован. Жми "
                        "«▶ Сгенерировать», выбери промт и подожди ответ от "
                        "ChatGPT."
                    )
                await cb.message.answer(
                    header + body,
                    parse_mode="HTML",
                    reply_markup=script_step_kb(
                        pid, voiceover_exists=voiceover_exists
                    ),
                )
                return
            # xlsx-файла нет — упадём в старую логику ниже.

        # Шаг 3 (Разбивка на блоки) — новый xlsx-flow.
        #   1) сразу показываем picker промтов из prompts/03_razbivka/
        #   2) после выбора — uploadим project.xlsx + voiceover.txt + промт,
        #      ждём ответ, скачиваем txt, бэкапим старый voiceover.txt в old/,
        #      сохраняем новый как voiceover.txt, статус → frames_ready.
        if step.code == "split":
            from pathlib import Path as _Path
            proj_xlsx = (
                _Path(settings.data_dir) / "videos" / project.slug / "project.xlsx"
            )
            if proj_xlsx.exists():
                voiceover_path = proj_xlsx.parent / "voiceover.txt"
                if not voiceover_path.exists():
                    await cb.answer(
                        "Сначала Шаг 2 — нет voiceover.txt", show_alert=True
                    )
                    return
                overrides = dict(project.prompt_overrides or {})
                _pending_split_prompt[cb.from_user.id] = pid
                await cb.answer()
                await cb.message.answer(
                    _prompt_picker_text("split", overrides),
                    reply_markup=_prompt_picker_kb(pid, "split", overrides),
                    parse_mode="HTML",
                )
                return
            # xlsx-файла нет — упадём в старую логику ниже.

        # Шаг 4 «Объекты» — wrapper, показывает подменю с двумя
        # суб-шагами «Персонажи» (старая hero-логика) и «Предметы»
        # (новый generate_items). Сам по себе step.code=="objects"
        # никогда не запускает воркер — он только показывает submenu.
        if step.code == "objects":
            from app.telegram.menu import objects_submenu_kb

            await cb.answer()
            await cb.message.answer(
                f"<b>Шаг 4. Объекты</b>\n"
                f"Проект #{pid} «{project.topic}»\n\n"
                "Выбери, что генерировать. «Персонажи» — старая Hero-логика "
                "(c01..c05 из листа «Персонажи»). «Предметы» — реф-картинки "
                "по item_descriptions, кладутся в "
                "<code>data/videos/&lt;slug&gt;/items/</code> как "
                "<code>predmet&lt;N&gt;_&lt;uuid&gt;.png</code>.",
                reply_markup=objects_submenu_kb(project),
                parse_mode="HTML",
            )
            return

        # Шаг 5 «Доп работа с EXCEL» (wrapper) — показывает подменю
        # с N кнопками «Доп работа с EXCEL #i» и «➕ Добавить слот».
        # Сам по себе step.code=="enrich" воркера НЕ запускает —
        # юзер выбирает конкретный слот через subменю.
        if step.code == "enrich":
            from app.telegram.menu import enabled_enrich_slots, enrich_submenu_kb

            await cb.answer()
            n_slots = enabled_enrich_slots(project)
            _set_user_screen(cb.from_user.id, "enrich_submenu", pid)
            await cb.message.answer(
                f"<b>Шаг 5. Доп работа с EXCEL</b>\n"
                f"Проект #{pid} «{project.topic}»\n"
                f"Активных слотов: <b>{n_slots}</b> (макс {5}).\n\n"
                "Каждый слот — один round-trip xlsx ↔ ChatGPT: бот шлёт "
                "<code>project.xlsx</code> + твой мастер-промт, ChatGPT "
                "редактирует и возвращает обновлённый xlsx, бот импортит "
                "его обратно в БД. Слоты выполняются по порядку: #1 → "
                "#2 → ... → #N.",
                reply_markup=enrich_submenu_kb(project),
                parse_mode="HTML",
            )
            return

        # Шаг 4 (Hero) — многоэтапный:
        #   0) ОБЯЗАТЕЛЬНО: выбор пресета «стиль персонажа» из
        #      prompts/04_hero_style/. Сохраняется в overrides['hero_style'].
        #   1) если hero_count ещё не задан → кнопки 0-9 «сколько героев?»
        #   2) если задан и описаний недостаточно → запрашиваем описание
        #      следующего героя у юзера текстом
        #   3) после описания → меню «кол-во вариаций» (1..5) для этого
        #      героя; вариация 1 — без референса, варианты 2..N — с
        #      первой как референс-картинкой в outsee.
        #   4) если все описания+вариации собраны → выставляем
        #      generating_hero и воркер запускает генерацию по очереди.
        if step.code == "hero":
            overrides = dict(project.prompt_overrides or {})
            style_chosen = overrides.get("hero_style")
            style_ok = (
                style_chosen
                and plib.is_valid_prompt_name(style_chosen)
                and plib.prompt_path("hero_style", style_chosen).exists()
            )
            # Если у проекта УЖЕ задано кол-во героев (даже частично с
            # описаниями) — сначала показываем подменю «продолжить /
            # перезадать». Это даёт юзеру возможность поменять описания
            # или начать заново со стилем. На первом входе (hero_count
            # is None) подменю не нужно — сразу обычный flow.
            if (
                project.hero_count is not None
                and project.hero_count > 0
                and not _pending_hero_brief.get(cb.from_user.id)
                and not _pending_hero_variation.get(cb.from_user.id)
                and not _pending_hero_style.get(cb.from_user.id)
            ):
                await cb.answer()
                await cb.message.answer(
                    _hero_reset_menu_text(project),
                    reply_markup=_hero_reset_menu_kb(pid),
                    parse_mode="HTML",
                )
                return
            if not style_ok:
                # Просим выбрать стиль; после выбора сразу продолжим
                # hero-flow в on_prompt_picker_cb (см. _pending_hero_style).
                _pending_hero_style[cb.from_user.id] = pid
                await cb.answer()
                await cb.message.answer(
                    "🎨 <b>Шаг 4 (Hero) — стиль персонажа</b>\n\n"
                    "Этот пресет (визуал, освещение, lens, стилистика) "
                    "будет приклеен к КАЖДОМУ персонажу в шаге Hero.\n\n"
                    "Выбери стиль из списка или добавь свой "
                    "(<code>+ Новый</code>):",
                    parse_mode="HTML",
                )
                await cb.message.answer(
                    _prompt_picker_text("hero_style", overrides),
                    reply_markup=_prompt_picker_kb(
                        pid, "hero_style", overrides
                    ),
                    parse_mode="HTML",
                )
                return
            if project.hero_count is None:
                await cb.answer()
                await cb.message.answer(
                    f"✅ Стиль персонажа: <code>{style_chosen}</code>\n\n"
                    "Сколько персонажей-героев сгенерировать? "
                    "Выбери число (0 — без героев, шаг будет пропущен).",
                    reply_markup=_hero_count_kb(pid),
                    parse_mode="HTML",
                )
                return
            n = project.hero_count
            descriptions = list(project.hero_descriptions or [])
            variations = list(project.hero_variations or [])
            if n == 0:
                # Пользователь раньше выбрал «0 героев» — шаг сразу готов.
                project.status = ProjectStatus.hero_ready
                await cb.answer("0 героев — шаг пропущен")
                await cb.message.answer(
                    "✅ Шаг 4 пропущен (0 героев). Можно идти к шагу 5."
                )
                return
            # Описания и вариации заполняем параллельно: сначала
            # описание героя i, потом сразу кол-во его вариаций, потом
            # описание героя i+1 и т.д.
            if len(descriptions) < n:
                # Нужно описать ещё одного.
                next_idx = len(descriptions) + 1
                _pending_hero_brief[cb.from_user.id] = (pid, next_idx)
                await cb.answer()
                await cb.message.answer(
                    _hero_brief_question_text(next_idx, n),
                    parse_mode="HTML",
                )
                return
            if len(variations) < n:
                # Описания все есть, но для последнего героя ещё не
                # выбрано кол-во вариаций.
                next_idx = len(variations) + 1
                _pending_hero_variation[cb.from_user.id] = (pid, next_idx)
                await cb.answer()
                await cb.message.answer(
                    _hero_variation_question_text(next_idx, n),
                    reply_markup=_hero_variation_kb(pid, next_idx),
                    parse_mode="HTML",
                )
                return
            # Всё собрано → запускаем генерацию первого/следующего.
            project.status = step.running_status
            slug = project.slug
            topic = project.topic
            total_variations = sum(int(v or 1) for v in variations[:n])
            await cb.answer(f"Запускаю: {step.title}")
            await cb.message.answer(
                f"▶ Шаг {step.n}: <b>{step.title}</b>\n"
                f"Героев: {n}, всего изображений с вариациями: "
                f"{total_variations}.\n"
                f"Стиль: <code>{style_chosen}</code>\n"
                f"Проект #{pid} «{topic}» (slug: <code>{slug}</code>)\n"
                f"Воркер подхватит за ~15 сек.",
                parse_mode="HTML",
            )
            return

        # Если у шага есть мастер-промт и в проекте ещё не выбран
        # вариант (или указанный файл пропал) — показываем picker и НЕ
        # запускаем шаг до выбора. Это ключевая часть Push C.
        #
        # Для enrich_<N> (шаг 5) picker показываем ВСЕГДА — даже если
        # шаблон уже выбран. Юзер просил не запускать шаг автоматом,
        # чтобы успеть отредактировать выбранный шаблон. Запуск делает
        # отдельная кнопка «▶ Запустить шаг» в picker'е (action="run").
        if step.code in plib.STEP_FOLDERS:
            overrides = dict(project.prompt_overrides or {})
            chosen = overrides.get(step.code)
            chosen_ok = (
                chosen
                and plib.is_valid_prompt_name(chosen)
                and plib.prompt_path(step.code, chosen).exists()
            )
            need_picker = not chosen_ok
            # Для enrich-слотов и xlsx-flow шагов (script, split):
            # picker всегда видим, авто-запуска нет — только по кнопке
            # «▶ Запустить шаг».
            always_picker = (
                _is_enrich_slot(step.code)
                or step.code in ("script", "split")
            )
            if always_picker:
                need_picker = True
            if need_picker:
                has_msg_override = gtb.has_override(project, step.code)
                if always_picker:
                    show_run = bool(chosen_ok)
                else:
                    show_run = _can_run_enrich_slot_now(project, step.code)
                _set_user_screen(
                    cb.from_user.id, "picker", pid, step.code
                )
                await cb.answer()
                await cb.message.answer(
                    _prompt_picker_text(step.code, overrides),
                    reply_markup=_prompt_picker_kb(
                        pid, step.code, overrides,
                        has_msg_override=has_msg_override,
                        show_run_button=show_run,
                    ),
                    parse_mode="HTML",
                )
                return

        # выставляем running-статус — воркер увидит и запустит шаг
        project.status = step.running_status
        slug = project.slug
        topic = project.topic

    await cb.answer(f"Запускаю: {step.title}")
    await cb.message.answer(
        f"▶ Шаг {step.n}: <b>{step.title}</b>\n"
        f"Проект #{pid} «{topic}» (slug: <code>{slug}</code>)\n"
        f"Воркер подхватит за ~15 сек, по завершении пришлю результат.",
        parse_mode="HTML",
    )


# ---------------------------------------------------------------------------
# Подменю шага 2 «Закадровый текст»: посмотреть текущий voiceover.txt /
# сгенерировать заново.

@dp.callback_query(F.data.regexp(r"^proj:\d+:script_view$"))
async def on_script_view(cb: CallbackQuery) -> None:
    """Прислать пользователю текущий voiceover.txt файлом."""
    if cb.from_user.id != settings.telegram_owner_chat_id:
        await cb.answer("Нет доступа", show_alert=True)
        return
    pid = int((cb.data or "").split(":")[1])
    async with session_scope() as s:
        project = (
            await s.execute(select(Project).where(Project.id == pid))
        ).scalar_one_or_none()
        if project is None:
            await cb.answer("Проект не найден", show_alert=True)
            return
        slug = project.slug
        topic = project.topic
    voiceover_path = settings.data_dir / "videos" / slug / "voiceover.txt"
    if not voiceover_path.exists():
        await cb.answer(
            "voiceover.txt ещё не сгенерирован — нечего показывать.",
            show_alert=True,
        )
        return
    await cb.answer("Шлю файл…")
    await cb.message.answer_document(
        FSInputFile(str(voiceover_path)),
        caption=(
            f"📄 voiceover.txt — текущий закадровый текст\n"
            f"Проект #{pid} «{topic}»\n"
            f"({voiceover_path.stat().st_size} байт)"
        ),
    )


@dp.callback_query(F.data.regexp(r"^proj:\d+:script_regen$"))
async def on_script_regen(cb: CallbackQuery) -> None:
    """Открыть picker промтов для шага 2 «Закадровый текст»."""
    if cb.from_user.id != settings.telegram_owner_chat_id:
        await cb.answer("Нет доступа", show_alert=True)
        return
    pid = int((cb.data or "").split(":")[1])
    async with session_scope() as s:
        project = (
            await s.execute(select(Project).where(Project.id == pid))
        ).scalar_one_or_none()
        if project is None:
            await cb.answer("Проект не найден", show_alert=True)
            return
        proj_xlsx = (
            settings.data_dir / "videos" / project.slug / "project.xlsx"
        )
        if not proj_xlsx.exists():
            await cb.answer("Сначала Шаг 1 — нет project.xlsx", show_alert=True)
            return
        overrides = dict(project.prompt_overrides or {})
    _pending_script_prompt[cb.from_user.id] = pid
    _remember_project(cb.from_user.id, pid)
    await cb.answer()
    await cb.message.answer(
        _prompt_picker_text("script", overrides),
        reply_markup=_prompt_picker_kb(pid, "script", overrides),
        parse_mode="HTML",
    )


@dp.callback_query(F.data.regexp(r"^proj:\d+:script_replace$"))
async def on_script_replace(cb: CallbackQuery) -> None:
    """Запросить у юзера новый voiceover.txt (текстом или файлом)."""
    if cb.from_user.id != settings.telegram_owner_chat_id:
        await cb.answer("Нет доступа", show_alert=True)
        return
    pid = int((cb.data or "").split(":")[1])
    async with session_scope() as s:
        project = (
            await s.execute(select(Project).where(Project.id == pid))
        ).scalar_one_or_none()
        if project is None:
            await cb.answer("Проект не найден", show_alert=True)
            return
    _pending_voiceover_replace[cb.from_user.id] = pid
    _remember_project(cb.from_user.id, pid)
    await cb.answer()
    await cb.message.answer(
        "✏️ Пришли новый текст сообщением или прикрепи <b>.txt</b>-файл.\n"
        "Старый voiceover.txt будет сохранён в <code>old/</code>.",
        parse_mode="HTML",
    )


# ---------------------------------------------------------------------------
# Библиотека мастер-промтов (Push C):
#   prm:<pid>:<step>:sel:<name>     — выбрать существующий вариант + запустить шаг
#   prm:<pid>:<step>:menu           — обновить picker (refresh / возврат назад)
#   prm:<pid>:<step>:add            — спросить имя нового варианта
#   prm:<pid>:<step>:editcur        — выслать текущий выбранный файлом, ждать ответ
#   prm:<pid>:<step>:delask         — список вариантов для удаления
#   prm:<pid>:<step>:del:<name>     — удалить файл варианта
#   prm:<pid>:<step>:cancel         — закрыть picker (вернуться в меню проекта)
#
#   pov:<pid>                       — открыть «🧰 Промты» (overview по проекту)


def _parse_prm(data: str) -> tuple[int, str, str, str | None]:
    """Распарсить prm:<pid>:<step>:<action>[:<name>]. Возвращает кортеж."""
    parts = data.split(":", 4)
    if len(parts) < 4 or parts[0] != "prm":
        raise ValueError(f"bad prm callback: {data}")
    pid = int(parts[1])
    step_code = parts[2]
    action = parts[3]
    name = parts[4] if len(parts) >= 5 else None
    return pid, step_code, action, name


@dp.callback_query(F.data.regexp(r"^pov:\d+$"))
async def on_prompt_overview(cb: CallbackQuery) -> None:
    if cb.from_user.id != settings.telegram_owner_chat_id:
        await cb.answer("Нет доступа", show_alert=True)
        return
    pid = int((cb.data or "").split(":")[1])
    async with session_scope() as s:
        project = (
            await s.execute(select(Project).where(Project.id == pid))
        ).scalar_one_or_none()
        if project is None:
            await cb.answer("Проект не найден", show_alert=True)
            return
        text = _prompt_overview_text(project)
    await cb.answer()
    await cb.message.answer(
        text, reply_markup=_prompt_overview_kb(pid), parse_mode="HTML"
    )


@dp.callback_query(F.data.startswith("prm:"))
async def on_prompt_picker_cb(cb: CallbackQuery) -> None:
    if cb.from_user.id != settings.telegram_owner_chat_id:
        await cb.answer("Нет доступа", show_alert=True)
        return
    try:
        pid, step_code, action, name = _parse_prm(cb.data or "")
    except Exception:
        await cb.answer("Плохой callback", show_alert=True)
        return
    if step_code not in plib.STEP_FOLDERS:
        await cb.answer("Неизвестный шаг для промтов", show_alert=True)
        return

    if action == "cancel":
        await cb.answer("Отменено")
        await cb.message.answer("Отменено. Открой меню проекта /menu.")
        return

    if action == "menu":
        # Перерисовать picker.
        async with session_scope() as s:
            project = (
                await s.execute(select(Project).where(Project.id == pid))
            ).scalar_one_or_none()
            overrides = dict(project.prompt_overrides or {}) if project else {}
            has_msg_override = (
                gtb.has_override(project, step_code) if project else False
            )
            if step_code == "plan" and project is not None:
                chosen = overrides.get("plan")
                show_run = bool(
                    chosen
                    and plib.is_valid_prompt_name(chosen)
                    and plib.prompt_path("plan", chosen).exists()
                )
            else:
                show_run = (
                    _can_run_enrich_slot_now(project, step_code)
                    if project else False
                )
        is_plan = step_code == "plan"
        topic_prefix = ""
        if is_plan and project is not None and (project.topic or "").strip():
            topic_prefix = f"Тема: <b>{project.topic}</b>\n\n"
        _set_user_screen(cb.from_user.id, "picker", pid, step_code)
        await cb.answer()
        await cb.message.answer(
            topic_prefix + _prompt_picker_text(step_code, overrides),
            reply_markup=_prompt_picker_kb(
                pid, step_code, overrides,
                has_msg_override=has_msg_override,
                show_run_button=show_run,
                show_topic_button=is_plan,
            ),
            parse_mode="HTML",
        )
        return

    if action == "topic":
        # Изменить тему ролика (для шага plan).
        _pending_plan_topic[cb.from_user.id] = pid
        await cb.answer()
        async with session_scope() as s:
            project = (
                await s.execute(select(Project).where(Project.id == pid))
            ).scalar_one_or_none()
            cur_topic = (project.topic or "") if project else ""
        text = f"Текущая тема: <b>{cur_topic}</b>\n\n" if cur_topic else ""
        await cb.message.answer(
            f"{text}Напишите новую тему ролика (может быть длинным описанием):",
            parse_mode="HTML",
        )
        return

    if action == "run":
        # Явный запуск шага (для enrich_<N> и plan). Picker не запускает
        # шаг автоматом, юзер должен сам ткнуть «▶ Запустить шаг».

        # Шаг 1 (План) — запуск xlsx-flow напрямую из picker'а.
        if step_code == "plan":
            async with session_scope() as s:
                project = (
                    await s.execute(select(Project).where(Project.id == pid))
                ).scalar_one_or_none()
                if project is None:
                    await cb.answer("Проект не найден", show_alert=True)
                    return
                overrides = dict(project.prompt_overrides or {})
                chosen = overrides.get("plan")
                chosen_ok = (
                    chosen
                    and plib.is_valid_prompt_name(chosen)
                    and plib.prompt_path("plan", chosen).exists()
                )
                if not chosen_ok:
                    await cb.answer(
                        "Сначала выбери шаблон в списке.",
                        show_alert=True,
                    )
                    return
                topic = project.topic or ""
                if not topic.strip():
                    await cb.answer(
                        "Сначала задайте тему ролика (📝 Изменить тему).",
                        show_alert=True,
                    )
                    return
            if (pid, "plan") in _xlsx_flow_active:
                await cb.answer(
                    "⏳ Уже идёт обработка «Плана» по этому проекту, подожди.",
                    show_alert=True,
                )
                return
            await cb.answer(f"Запускаю план: {chosen}")
            asyncio.create_task(
                _run_xlsx_with_lock(
                    _run_plan_xlsx(cb.message, pid, chosen, topic),
                    pid,
                    "plan",
                )
            )
            return

        # Шаг 2 (script) — запуск xlsx-flow из picker'а.
        if step_code == "script":
            async with session_scope() as s:
                project = (
                    await s.execute(select(Project).where(Project.id == pid))
                ).scalar_one_or_none()
                if project is None:
                    await cb.answer("Проект не найден", show_alert=True)
                    return
                overrides = dict(project.prompt_overrides or {})
                chosen = overrides.get("script")
                chosen_ok = (
                    chosen
                    and plib.is_valid_prompt_name(chosen)
                    and plib.prompt_path("script", chosen).exists()
                )
                if not chosen_ok:
                    await cb.answer(
                        "Сначала выбери шаблон в списке.",
                        show_alert=True,
                    )
                    return
            if (pid, "script") in _xlsx_flow_active:
                await cb.answer(
                    "⏳ Уже идёт обработка «Закадрового текста», подожди.",
                    show_alert=True,
                )
                return
            await cb.answer(f"Запускаю закадровый текст: {chosen}")
            asyncio.create_task(
                _run_xlsx_with_lock(
                    _run_script_xlsx(cb.message, pid, chosen),
                    pid,
                    "script",
                )
            )
            return

        # Шаг 3 (split) — запуск xlsx-flow из picker'а.
        if step_code == "split":
            async with session_scope() as s:
                project = (
                    await s.execute(select(Project).where(Project.id == pid))
                ).scalar_one_or_none()
                if project is None:
                    await cb.answer("Проект не найден", show_alert=True)
                    return
                overrides = dict(project.prompt_overrides or {})
                chosen = overrides.get("split")
                chosen_ok = (
                    chosen
                    and plib.is_valid_prompt_name(chosen)
                    and plib.prompt_path("split", chosen).exists()
                )
                if not chosen_ok:
                    await cb.answer(
                        "Сначала выбери шаблон в списке.",
                        show_alert=True,
                    )
                    return
            if (pid, "split") in _xlsx_flow_active:
                await cb.answer(
                    "⏳ Уже идёт обработка «Разбивки», подожди.",
                    show_alert=True,
                )
                return
            await cb.answer(f"Запускаю разбивку: {chosen}")
            asyncio.create_task(
                _run_xlsx_with_lock(
                    _run_split_xlsx(cb.message, pid, chosen),
                    pid,
                    "split",
                )
            )
            return

        if not _is_enrich_slot(step_code):
            await cb.answer(
                "Кнопка «Запустить» доступна только для шагов 1–3 и "
                "слотов «Доп работа с EXCEL» (шаг 5).",
                show_alert=True,
            )
            return
        from app.telegram.menu import (
            is_step_runnable,
            step_by_code as _step_by_code,
        )
        step = _step_by_code(step_code)
        if step is None:
            await cb.answer("Неизвестный шаг", show_alert=True)
            return
        async with session_scope() as s:
            project = (
                await s.execute(select(Project).where(Project.id == pid))
            ).scalar_one_or_none()
            if project is None:
                await cb.answer("Проект не найден", show_alert=True)
                return
            overrides = dict(project.prompt_overrides or {})
            chosen = overrides.get(step_code)
            chosen_ok = (
                chosen
                and plib.is_valid_prompt_name(chosen)
                and plib.prompt_path(step_code, chosen).exists()
            )
            if not chosen_ok:
                await cb.answer(
                    "Сначала выбери шаблон в списке.",
                    show_alert=True,
                )
                return
            # Зомби-статус: уже идёт — просто говорим «работает».
            if project.status is step.running_status:
                await cb.answer(
                    f"⏳ Шаг уже выполняется ({step.title}). Подожди.",
                    show_alert=True,
                )
                return
            # Перезапуск из ready — разрешаем (юзер мог сменить шаблон).
            if not is_step_runnable(step, project.status):
                from app.telegram.menu import status_order
                is_already_done = (
                    status_order(project.status) >= status_order(step.ready_status)
                )
                if not is_already_done:
                    await cb.answer(
                        f"Сначала пройди шаг до "
                        f"{step.requires.value if step.requires else '?'}",
                        show_alert=True,
                    )
                    return
            project.status = step.running_status
            slug = project.slug
            topic = project.topic
        await cb.answer(f"Запускаю: {step.title}")
        await cb.message.answer(
            f"▶ <b>{step.title}</b>\n"
            f"Шаблон: <code>{chosen}</code>\n"
            f"Проект #{pid} «{topic}» (slug: <code>{slug}</code>)\n"
            f"Воркер подхватит за ~15 сек, по завершении пришлю результат.",
            parse_mode="HTML",
        )
        return

    if action == "msgmenu":
        # Открыть подменю «сопр. сообщения»: статус (default/override) +
        # кнопки «📥 Получить файл», «🔄 Сбросить», «⬅ Назад».
        if not gtb.is_supported(step_code):
            await cb.answer("Шаг не поддерживает редактирование сопр. сообщения",
                            show_alert=True)
            return
        async with session_scope() as s:
            project = (
                await s.execute(select(Project).where(Project.id == pid))
            ).scalar_one_or_none()
            has_ovr = gtb.has_override(project, step_code) if project else False
        await cb.answer()
        await cb.message.answer(
            _gpt_msg_menu_text(step_code, has_ovr),
            reply_markup=_gpt_msg_menu_kb(pid, step_code, has_ovr),
            parse_mode="HTML",
        )
        return

    if action == "msgsend":
        # Сборка дефолтного «сопр. сообщения» для этого шага и отправка
        # юзеру файлом .md. Регистрируем ожидание ответа.
        if not gtb.is_supported(step_code):
            await cb.answer("Шаг не поддерживает редактирование сопр. сообщения",
                            show_alert=True)
            return
        try:
            text = await _build_gpt_text_for_edit(pid, step_code)
        except Exception as e:  # noqa: BLE001
            logger.exception("msgsend build failed: {}", e)
            await cb.answer("Ошибка сборки текста", show_alert=True)
            await cb.message.answer(f"❌ Ошибка сборки текста: {e}")
            return

        # Сохраняем .md-файл и шлём его.
        from datetime import datetime as _dt
        from pathlib import Path as _Path
        ts = _dt.utcnow().strftime("%Y%m%d_%H%M%S")
        out_dir = _Path(settings.data_dir) / "tmp_gpt_text_edits"
        out_dir.mkdir(parents=True, exist_ok=True)
        f_path = out_dir / f"gpt_text_{step_code}_p{pid}_{ts}.md"
        f_path.write_text(text, encoding="utf-8")

        await cb.answer()
        sent = await cb.message.answer_document(
            FSInputFile(str(f_path)),
            caption=(
                f"✏️ Сопр. сообщение для шага «"
                f"{plib.STEP_HUMAN_NAMES.get(step_code, step_code)}» "
                f"(проект #{pid}).\n\n"
                "Отредактируй и пришли обратно — можно ОТВЕТОМ на это "
                "сообщение или просто отдельным сообщением "
                "(.md / .txt-файл, или текстом).\n"
                "Чтобы отменить — пришли «отмена»."
            ),
        )
        # Регистрируем ожидание сразу по двум ключам: message_id (если юзер
        # сделает reply) и user_id (если юзер просто пришлёт следующим
        # сообщением). Любой матч сработает.
        _pending_gpt_text_edit[sent.message_id] = (cb.from_user.id, pid, step_code)
        _pending_gpt_text_edit_by_user[cb.from_user.id] = (pid, step_code)
        logger.info(
            "msgsend: registered pending gpt-text-edit for user={} pid={} step={} "
            "(reply_to msg_id={})",
            cb.from_user.id, pid, step_code, sent.message_id,
        )
        return

    if action == "msgreset":
        # Удалить override. Перерисовать подменю.
        if not gtb.is_supported(step_code):
            await cb.answer("Шаг не поддерживает", show_alert=True)
            return
        async with session_scope() as s:
            project = (
                await s.execute(select(Project).where(Project.id == pid))
            ).scalar_one_or_none()
            if project is None:
                await cb.answer("Проект не найден", show_alert=True)
                return
            await gtb.clear_override(s, project, step_code)
        await cb.answer("Сброшено")
        await cb.message.answer(
            _gpt_msg_menu_text(step_code, has_override=False),
            reply_markup=_gpt_msg_menu_kb(pid, step_code, has_override=False),
            parse_mode="HTML",
        )
        return

    if action == "sel" and name is not None:
        if not plib.is_valid_prompt_name(name):
            await cb.answer("Некорректное имя", show_alert=True)
            return
        if not plib.prompt_path(step_code, name).exists():
            await cb.answer("Файл не найден", show_alert=True)
            return
        async with session_scope() as s:
            project = (
                await s.execute(select(Project).where(Project.id == pid))
            ).scalar_one_or_none()
            if project is None:
                await cb.answer("Проект не найден", show_alert=True)
                return
            overrides = dict(project.prompt_overrides or {})
            overrides[step_code] = name
            project.prompt_overrides = overrides

        # Шаг 1 (План) — picker остаётся видимым (как в enrich), авто-запуска нет.
        if step_code == "plan":
            async with session_scope() as s:
                project = (
                    await s.execute(select(Project).where(Project.id == pid))
                ).scalar_one_or_none()
                overrides_after = (
                    dict(project.prompt_overrides or {}) if project else {}
                )
                has_msg_override = (
                    gtb.has_override(project, step_code) if project else False
                )
                show_run = bool(
                    overrides_after.get(step_code)
                    and plib.is_valid_prompt_name(overrides_after.get(step_code, ""))
                    and plib.prompt_path(step_code, overrides_after.get(step_code, "")).exists()
                )
                topic_prefix = ""
                if project is not None and (project.topic or "").strip():
                    topic_prefix = f"Тема: <b>{project.topic}</b>\n\n"
            _set_user_screen(cb.from_user.id, "picker", pid, step_code)
            await cb.answer(f"Выбрано: {name}")
            await cb.message.answer(
                f"✅ Для шага «План» теперь выбран шаблон "
                f"<code>{name}</code>.\n\n"
                "Можешь:\n"
                "  • <b>▶ Запустить шаг</b> — стартовать ChatGPT.\n"
                "  • <b>📝 Изменить тему</b> — переписать тему ролика.\n"
                "  • <b>✏ Редактировать выбранный</b> — поправить шаблон.\n"
                "  • <b>✏️ Сопр. сообщение</b> — отредактировать текст, "
                "который уходит в ChatGPT вместе с xlsx.\n"
                "  • выбрать другой шаблон из списка.\n\n"
                + topic_prefix
                + _prompt_picker_text(step_code, overrides_after),
                reply_markup=_prompt_picker_kb(
                    pid, step_code, overrides_after,
                    has_msg_override=has_msg_override,
                    show_run_button=show_run,
                    show_topic_button=True,
                ),
                parse_mode="HTML",
            )
            return

        # Шаг 2 (script) и Шаг 3 (split) — picker остаётся видимым
        # (как plan/enrich), авто-запуска нет — только по «▶ Запустить шаг».
        if step_code in ("script", "split"):
            async with session_scope() as s:
                project = (
                    await s.execute(select(Project).where(Project.id == pid))
                ).scalar_one_or_none()
                overrides_after = (
                    dict(project.prompt_overrides or {}) if project else {}
                )
                has_msg_override = (
                    gtb.has_override(project, step_code) if project else False
                )
                show_run = bool(
                    overrides_after.get(step_code)
                    and plib.is_valid_prompt_name(overrides_after.get(step_code, ""))
                    and plib.prompt_path(step_code, overrides_after.get(step_code, "")).exists()
                )
            _set_user_screen(cb.from_user.id, "picker", pid, step_code)
            await cb.answer(f"Выбрано: {name}")
            await cb.message.answer(
                f"✅ Для шага «{step_code}» выбран шаблон "
                f"<code>{name}</code>.\n\n"
                "Можешь:\n"
                "  • <b>▶ Запустить шаг</b> — стартовать ChatGPT.\n"
                "  • <b>✏ Редактировать выбранный</b> — поправить шаблон.\n"
                "  • <b>✏️ Сопр. сообщение</b> — отредактировать текст, "
                "который уходит в ChatGPT вместе с xlsx.\n"
                "  • выбрать другой шаблон из списка.\n\n"
                + _prompt_picker_text(step_code, overrides_after),
                reply_markup=_prompt_picker_kb(
                    pid, step_code, overrides_after,
                    has_msg_override=has_msg_override,
                    show_run_button=show_run,
                ),
                parse_mode="HTML",
            )
            return

        # Особый случай: «Стиль персонажа» (sub-step Hero).
        # Юзер кликнул «4. Hero», бот показал picker со стилями. Сейчас
        # он выбрал стиль — продолжаем hero-flow (запрашиваем кол-во героев).
        if step_code == "hero_style":
            uid = cb.from_user.id
            pending_hero_pid = _pending_hero_style.get(uid)
            if pending_hero_pid is not None and pending_hero_pid == pid:
                _pending_hero_style.pop(uid, None)
                await cb.answer(f"Стиль: {name}")
                await cb.message.answer(
                    f"✅ Стиль персонажа: <code>{name}</code>\n\n"
                    "Сколько персонажей-героев сгенерировать? "
                    "Выбери число (0 — без героев, шаг будет пропущен).",
                    reply_markup=_hero_count_kb(pid),
                    parse_mode="HTML",
                )
                return

        human = plib.STEP_HUMAN_NAMES.get(step_code, step_code)

        # Для enrich_<N> (шаг 5) — не показываем терминальное «тыкни шаг»,
        # а перерисовываем picker. Юзер останется на нём, сможет ткнуть
        # «✏ Редактировать выбранный» / сменить шаблон / ткнуть
        # «▶ Запустить шаг» для явного запуска.
        if _is_enrich_slot(step_code):
            async with session_scope() as s:
                project = (
                    await s.execute(select(Project).where(Project.id == pid))
                ).scalar_one_or_none()
                overrides_after = (
                    dict(project.prompt_overrides or {}) if project else {}
                )
                has_msg_override = (
                    gtb.has_override(project, step_code) if project else False
                )
                show_run = (
                    _can_run_enrich_slot_now(project, step_code)
                    if project else False
                )
            _set_user_screen(cb.from_user.id, "picker", pid, step_code)
            hint_run = (
                "  • <b>▶ Запустить шаг</b> — стартовать ChatGPT.\n"
                if show_run else
                "  • <i>Запустить этот слот можно только после того, "
                "как предыдущий слот будет готов.</i>\n"
                "    Чтобы запустить всё подряд — кнопка "
                "<b>▶▶ Запустить все слоты подряд</b> в меню шага 5.\n"
            )
            await cb.answer(f"Выбрано: {name}")
            await cb.message.answer(
                f"✅ Для шага «{human}» теперь выбран шаблон "
                f"<code>{name}</code>.\n\n"
                "Можешь:\n"
                + hint_run
                + "  • <b>✏ Редактировать выбранный</b> — поправить шаблон.\n"
                "  • <b>✏️ Сопр. сообщение</b> — отредактировать текст, "
                "который уходит в ChatGPT вместе с xlsx.\n"
                "  • выбрать другой шаблон из списка.\n\n"
                + _prompt_picker_text(step_code, overrides_after),
                reply_markup=_prompt_picker_kb(
                    pid, step_code, overrides_after,
                    has_msg_override=has_msg_override,
                    show_run_button=show_run,
                ),
                parse_mode="HTML",
            )
            return

        await cb.answer(f"Выбрано: {name}")
        await cb.message.answer(
            f"✅ Для шага «{human}» теперь используется промт "
            f"<code>{name}</code>.\n\n"
            f"Тыкни шаг в меню /menu чтобы запустить.",
            parse_mode="HTML",
        )
        return

    if action == "add":
        user_id = cb.from_user.id
        _pending_prompt_name[user_id] = (pid, step_code)
        # Чистим upload-pending на всякий случай.
        _pending_prompt_upload.pop(user_id, None)
        human = plib.STEP_HUMAN_NAMES.get(step_code, step_code)
        await cb.answer()
        await cb.message.answer(
            f"<b>Шаг 1 из 2.</b> Назови новый вариант мастер-промта для "
            f"шага «{human}» — отправь имя <b>текстовым сообщением</b>.\n\n"
            f"Имя — это просто название для бота, чтобы потом выбирать его "
            f"в списке вариантов. Можно по-русски, с пробелами и почти "
            f"любыми символами (до ~20 кириллических или ~40 латинских "
            f"симв). Файл сам переименовывать не нужно — на следующем "
            f"шаге я пришлю шаблон <code>.md</code>, ты заменишь его "
            f"содержимое и пришлёшь обратно документом.\n\n"
            f"Например: <code>хоррор тёмный</code> или "
            f"<code>horror_v1</code>.",
            parse_mode="HTML",
        )
        return

    if action == "editcur":
        async with session_scope() as s:
            project = (
                await s.execute(select(Project).where(Project.id == pid))
            ).scalar_one_or_none()
            if project is None:
                await cb.answer("Проект не найден", show_alert=True)
                return
            overrides = dict(project.prompt_overrides or {})
        chosen = overrides.get(step_code) or plib.DEFAULT_NAME
        if not plib.prompt_path(step_code, chosen).exists():
            chosen = plib.DEFAULT_NAME
        await _send_prompt_for_edit(cb, pid, step_code, chosen)
        return

    if action == "delask":
        await cb.answer()
        await cb.message.answer(
            "Выбери вариант для удаления (<code>default</code> "
            "удалить нельзя):",
            reply_markup=_prompt_delete_kb(pid, step_code),
            parse_mode="HTML",
        )
        return

    if action == "del" and name is not None:
        if name == plib.DEFAULT_NAME:
            await cb.answer("default удалять нельзя", show_alert=True)
            return
        try:
            removed = plib.delete_prompt(step_code, name)
        except Exception as e:  # noqa: BLE001
            await cb.answer(f"Не удалось удалить: {e}", show_alert=True)
            return
        # Если этот вариант был выбран в проекте — сбрасываем override.
        async with session_scope() as s:
            project = (
                await s.execute(select(Project).where(Project.id == pid))
            ).scalar_one_or_none()
            if project is not None:
                overrides = dict(project.prompt_overrides or {})
                if overrides.get(step_code) == name:
                    overrides.pop(step_code, None)
                    project.prompt_overrides = overrides
        await cb.answer("Удалено" if removed else "Файла не было")
        async with session_scope() as s:
            project = (
                await s.execute(select(Project).where(Project.id == pid))
            ).scalar_one_or_none()
            overrides = dict(project.prompt_overrides or {}) if project else {}
            has_msg_override = (
                gtb.has_override(project, step_code) if project else False
            )
            show_run = (
                _can_run_enrich_slot_now(project, step_code) if project else False
            )
        await cb.message.answer(
            _prompt_picker_text(step_code, overrides),
            reply_markup=_prompt_picker_kb(
                pid, step_code, overrides,
                has_msg_override=has_msg_override,
                show_run_button=show_run,
            ),
            parse_mode="HTML",
        )
        return

    await cb.answer("Неизвестное действие picker", show_alert=True)


async def _send_prompt_for_edit(
    cb: CallbackQuery, pid: int, step_code: str, name: str
) -> None:
    """Отправляет файл `<step>/<name>.md` юзеру и переводит его в режим
    ожидания возврата отредактированного файла."""
    path = plib.prompt_path(step_code, name)
    if not path.exists():
        await cb.answer("Файл не найден", show_alert=True)
        return
    user_id = cb.from_user.id
    _pending_prompt_upload[user_id] = (pid, step_code, name)
    _pending_prompt_name.pop(user_id, None)
    await cb.answer()
    human = plib.STEP_HUMAN_NAMES.get(step_code, step_code)
    await cb.message.answer_document(
        FSInputFile(str(path)),
        caption=(
            f"📝 Шаг «{human}» · вариант <b>{name}</b>.\n\n"
            f"Поправь файл локально и пришли его <b>обратно как документ</b> "
            f"в этот чат — я сохраню и сразу выберу его для проекта.\n"
            f"Имя файла менять не обязательно."
        ),
        parse_mode="HTML",
    )


async def _handle_prompt_name_input(msg: Message, pid: int, step_code: str) -> None:
    """Юзер прислал имя нового мастер-промта. Создаём шаблон и шлём файл."""
    name = (msg.text or "").strip()
    if not plib.is_valid_prompt_name(name):
        await msg.answer(
            "Имя пустое или слишком длинное (лимит ~20 кириллических "
            "/ ~40 латинских симв) или содержит запрещённые символы "
            "(<code>/ \\ : * ? \" &lt; &gt; |</code>). Попробуй ещё раз или нажми "
            "«⬅ Отмена» в picker'е.",
            parse_mode="HTML",
        )
        # Возвращаем юзера в режим ввода имени.
        user_id = msg.from_user.id if msg.from_user else 0
        if user_id:
            _pending_prompt_name[user_id] = (pid, step_code)
        return
    # Если файл уже есть — не перезаписываем шаблоном; просто шлём как
    # есть на редактирование.
    path = plib.prompt_path(step_code, name)
    if not path.exists():
        plib.write_prompt(step_code, name, plib.make_template_for_new(step_code, name))
    user_id = msg.from_user.id if msg.from_user else 0
    if user_id:
        _pending_prompt_upload[user_id] = (pid, step_code, name)
    human = plib.STEP_HUMAN_NAMES.get(step_code, step_code)
    await msg.answer_document(
        FSInputFile(str(path)),
        caption=(
            f"<b>Шаг 2 из 2.</b> Создан шаблон для варианта "
            f"<b>{name}</b> (шаг «{human}»).\n\n"
            f"📥 Скачай файл, замени содержимое на свой мастер-промт и "
            f"пришли <b>обратно как документ</b> в этот чат "
            f"(.md или .txt — без разницы). Имя файла менять не надо.\n\n"
            f"После возврата я сохраню вариант и сразу выберу его для проекта."
        ),
        parse_mode="HTML",
    )


async def _handle_prompt_upload(msg: Message) -> None:
    """Юзер прислал .md-файл — сохраняем по адресу из _pending_prompt_upload."""
    user_id = msg.from_user.id if msg.from_user else 0
    pending = _pending_prompt_upload.get(user_id)
    if pending is None:
        return
    pid, step_code, name = pending
    doc = msg.document
    if doc is None:
        await msg.answer("Жду документ (.md), не текст.")
        return
    # Читаем содержимое файла через aiogram bot.download.
    try:
        buf = await msg.bot.download(doc)
        raw = buf.read() if hasattr(buf, "read") else bytes(buf)
        content = raw.decode("utf-8")
    except Exception as e:  # noqa: BLE001
        await msg.answer(f"Не смог прочитать файл: {e}")
        return
    if len(content.strip()) < 5:
        await msg.answer(
            "Файл практически пустой. Пришли заново с реальным мастер-"
            "промтом. (Жду тот же файл, режим ожидания не сброшен.)"
        )
        return
    plib.write_prompt(step_code, name, content)
    _pending_prompt_upload.pop(user_id, None)
    # Сохраняем выбор в проекте.
    async with session_scope() as s:
        project = (
            await s.execute(select(Project).where(Project.id == pid))
        ).scalar_one_or_none()
        if project is None:
            await msg.answer(f"Проект #{pid} не найден")
            return
        overrides = dict(project.prompt_overrides or {})
        overrides[step_code] = name
        project.prompt_overrides = overrides
    human = plib.STEP_HUMAN_NAMES.get(step_code, step_code)
    await msg.answer(
        f"✅ Сохранено: <code>prompts/{plib.STEP_FOLDERS[step_code]}/{name}.md</code> "
        f"({len(content)} симв).\n"
        f"Выбран как мастер-промт для шага «{human}». Тыкни шаг в "
        f"/menu чтобы запустить.",
        parse_mode="HTML",
    )


async def _replace_voiceover(pid: int, new_text: str, msg: Message) -> None:
    """Бэкапит старый voiceover.txt в old/ и записывает новый."""
    from datetime import datetime as _dt
    from pathlib import Path as _Path

    async with session_scope() as s:
        project = (
            await s.execute(select(Project).where(Project.id == pid))
        ).scalar_one_or_none()
        if project is None:
            await msg.answer("Проект не найден.")
            return
        slug = project.slug
        topic = project.topic

    proj_dir = _Path(settings.data_dir) / "videos" / slug
    voiceover_path = proj_dir / "voiceover.txt"

    if voiceover_path.exists():
        old_dir = proj_dir / "old"
        old_dir.mkdir(parents=True, exist_ok=True)
        ts = _dt.now().strftime("%Y%m%d_%H%M%S")
        backup = old_dir / f"{ts}_voiceover.txt"
        backup.write_bytes(voiceover_path.read_bytes())

    proj_dir.mkdir(parents=True, exist_ok=True)
    voiceover_path.write_text(new_text, encoding="utf-8")
    size = voiceover_path.stat().st_size
    await msg.answer(
        f"✅ voiceover.txt заменён ({size} байт).\n"
        f"Проект #{pid} «{topic}»\n"
        "Старый файл сохранён в <code>old/</code>.",
        parse_mode="HTML",
    )


@dp.message(F.document)
async def on_document_message(msg: Message) -> None:
    """Принимаем `.md`-файл (промт) или .txt (замена voiceover)."""
    if not is_owner(msg):
        return
    user_id = msg.from_user.id if msg.from_user else 0

    # Документ как ответ на «✏️ Сопр. сообщение». Пытаемся определить
    # активный edit-сеанс: 1) по reply_to_message_id, 2) по user_id.
    ed_pid_step: tuple[int, str] | None = None
    if msg.reply_to_message is not None:
        ed = _pending_gpt_text_edit.get(msg.reply_to_message.message_id)
        if ed is not None and ed[0] == user_id:
            ed_pid_step = (ed[1], ed[2])
    if ed_pid_step is None:
        ed_pid_step = _pending_gpt_text_edit_by_user.get(user_id)
    if ed_pid_step is not None:
        pid_e, step_e = ed_pid_step
        doc = msg.document
        if doc is None:
            await msg.answer("Не вижу файл — пришли заново.")
            return
        logger.info(
            "on_document_message: gpt-text-edit reply detected user={} pid={} "
            "step={} doc={}",
            user_id, pid_e, step_e, doc.file_name,
        )
        import tempfile
        from pathlib import Path as _Path

        with tempfile.NamedTemporaryFile(
            suffix=".md", delete=False
        ) as tmp:
            tmp_path = _Path(tmp.name)
        await msg.bot.download(doc, destination=str(tmp_path))
        new_text = tmp_path.read_text(encoding="utf-8", errors="replace")
        tmp_path.unlink(missing_ok=True)
        await _on_gpt_text_edit_reply(msg, pid_e, step_e, from_text=new_text)
        return

    # Замена voiceover.txt файлом
    pending_vo = _pending_voiceover_replace.get(user_id)
    if pending_vo is not None:
        _pending_voiceover_replace.pop(user_id, None)
        doc = msg.document
        if doc is None:
            await msg.answer("Не вижу файл. Пришли .txt ещё раз.")
            return
        import tempfile
        from pathlib import Path as _Path

        with tempfile.NamedTemporaryFile(
            suffix=".txt", delete=False
        ) as tmp:
            tmp_path = _Path(tmp.name)
        await msg.bot.download(doc, destination=str(tmp_path))
        text = tmp_path.read_text(encoding="utf-8", errors="replace")
        tmp_path.unlink(missing_ok=True)
        await _replace_voiceover(pending_vo, text, msg)
        return

    # Замена project.xlsx — юзер скачал xlsx кнопкой «📥», отредактировал
    # и прислал обратно .xlsx-документом.
    pending_xlsx_pid = _pending_xlsx_replace.get(user_id)
    doc = msg.document
    if (
        pending_xlsx_pid is not None
        and doc is not None
        and (doc.file_name or "").lower().endswith(".xlsx")
    ):
        _pending_xlsx_replace.pop(user_id, None)
        await _handle_xlsx_replace(msg, pending_xlsx_pid, doc)
        return

    # Если юзер кликнул «+ Новый промт» и сразу прислал файл (не введя
    # имя текстом) — подсказываем что сначала нужно имя.
    if user_id in _pending_prompt_name:
        await msg.answer(
            "Сначала пришли <b>имя</b> нового варианта <b>текстовым "
            "сообщением</b> (например: <code>хоррор тёмная версия</code>). "
            "Потом я попрошу прислать <code>.md</code>-файл с промтом.",
            parse_mode="HTML",
        )
        return

    if user_id not in _pending_prompt_upload:
        return  # не ждём ничего — игнорим (это может быть просто файл)
    await _handle_prompt_upload(msg)


# ---------------------------------------------------------------------------
# Замена project.xlsx загруженным от юзера документом

async def _handle_xlsx_replace(msg: Message, project_id: int, doc) -> None:
    """Принимает .xlsx-документ от юзера и подменяет project.xlsx.

    1) Скачиваем файл во временную папку.
    2) Валидируем (openpyxl + zip-magic).
    3) Бэкапим текущий project.xlsx в old/.
    4) Подменяем.
    5) Reload xlsx → БД.
    """
    import tempfile
    from pathlib import Path as _Path

    from app.services.xlsx_versioning import (
        backup_to_old,
        replace_with,
        validate_xlsx,
    )

    async with session_scope() as s:
        project = (
            await s.execute(select(Project).where(Project.id == project_id))
        ).scalar_one_or_none()
        if project is None:
            await msg.answer(f"Проект #{project_id} не найден.")
            return
        slug = project.slug
        topic = project.topic

    proj_xlsx = _Path(settings.data_dir) / "videos" / slug / "project.xlsx"
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        tmp_path = _Path(tmp.name)
    await msg.bot.download(doc, destination=str(tmp_path))

    validation_err = validate_xlsx(tmp_path)
    if validation_err is not None:
        tmp_path.unlink(missing_ok=True)
        await msg.answer(
            f"❌ Загруженный файл не валиден: {validation_err}\n"
            f"project.xlsx не подменён. Пришли корректный .xlsx.",
            parse_mode="HTML",
        )
        return

    backup = backup_to_old(proj_xlsx)
    proj_xlsx.parent.mkdir(parents=True, exist_ok=True)
    replace_with(proj_xlsx, tmp_path)
    tmp_path.unlink(missing_ok=True)

    # Reload xlsx → БД.
    try:
        from app.services.xlsx_sync import reload_from_xlsx

        async with session_scope() as s:
            project = (
                await s.execute(
                    select(Project).where(Project.id == project_id)
                )
            ).scalar_one_or_none()
            if project is not None:
                summary = await reload_from_xlsx(s, project, proj_xlsx)
                logger.info(
                    "xlsx_replace: reload_from_xlsx → {}", summary
                )
    except Exception as e:  # noqa: BLE001
        logger.exception("xlsx_replace reload failed: {}", e)

    backup_note = (
        f"\nСтарая версия: <code>old/{backup.name}</code>"
        if backup is not None
        else ""
    )
    await msg.answer(
        f"✅ project.xlsx заменён ({proj_xlsx.stat().st_size} байт).\n"
        f"Проект #{project_id} «{topic}»{backup_note}\n"
        f"Данные перечитаны в БД.",
        parse_mode="HTML",
    )


# ---------------------------------------------------------------------------
# Скачать xlsx / перечитать xlsx / удалить проект

@dp.callback_query(F.data.regexp(r"^proj:\d+:dl_xlsx$"))
async def on_project_download_xlsx(cb: CallbackQuery) -> None:
    if cb.from_user.id != settings.telegram_owner_chat_id:
        await cb.answer("Нет доступа", show_alert=True)
        return
    pid = int((cb.data or "").split(":")[1])
    async with session_scope() as s:
        project = (
            await s.execute(select(Project).where(Project.id == pid))
        ).scalar_one_or_none()
        if project is None:
            await cb.answer("Проект не найден", show_alert=True)
            return
        slug = project.slug
    xlsx_path = settings.data_dir / "videos" / slug / "project.xlsx"
    if not xlsx_path.exists():
        await cb.answer("xlsx-файл ещё не создан", show_alert=True)
        return
    await cb.answer("Шлю файл…")
    _pending_xlsx_replace[cb.from_user.id] = pid
    await cb.message.answer_document(
        FSInputFile(str(xlsx_path)),
        caption=(
            f"📥 project.xlsx (#{pid})\n"
            f"Открой в Excel, поправь нужные ячейки, сохрани.\n"
            f"Пришли отредактированный .xlsx сюда в чат — я заменю им "
            f"текущий project.xlsx (старая версия сохранится в old/)."
        ),
    )


@dp.callback_query(F.data.regexp(r"^proj:\d+:stop_running$"))
async def on_project_stop_running(cb: CallbackQuery) -> None:
    """⏹ Остановить — сбрасывает running-статус и/или снимает xlsx-flow лок.

    Работает в двух случаях:
      1) Проект в running-статусе (воркер) → откат на prerequisite.
      2) Активен xlsx-flow (plan/script/split) → снимаем лок.
    """
    if cb.from_user.id != settings.telegram_owner_chat_id:
        await cb.answer("Нет доступа", show_alert=True)
        return
    pid = int((cb.data or "").split(":")[1])
    from app.services.project_state import is_running_status
    from app.telegram.menu import step_by_running_status

    # Снимаем xlsx-flow локи для этого проекта.
    xlsx_stopped: list[str] = []
    for code in ("plan", "script", "split"):
        key = (pid, code)
        if key in _xlsx_flow_active:
            _xlsx_flow_active.discard(key)
            xlsx_stopped.append(code)

    async with session_scope() as s:
        project = (
            await s.execute(select(Project).where(Project.id == pid))
        ).scalar_one_or_none()
        if project is None:
            await cb.answer("Проект не найден", show_alert=True)
            return
        slug = project.slug

        if is_running_status(project.status):
            cur_running = project.status
            step = step_by_running_status(cur_running)
            rollback_to = (
                step.requires if (step is not None and step.requires is not None)
                else ProjectStatus.new
            )
            project.status = rollback_to
            meta = dict(project.meta or {})
            chain_to = meta.pop("enrich_auto_chain_to", None)
            if chain_to is not None:
                project.meta = meta
                logger.info(
                    "[#{}] STOP: cleared enrich_auto_chain_to=#{}",
                    pid, chain_to,
                )
            step_title = step.title if step is not None else cur_running.value
            logger.info(
                "[#{}] STOP: rolled back {} -> {} (user-requested via ⏹)",
                pid, cur_running.value, rollback_to.value,
            )
            status_msg = (
                f"⏹ <b>Остановил шаг</b> «{step_title}»\n"
                f"Проект #{pid} «{_project_display_topic(project)}» "
                f"(slug: <code>{slug}</code>)\n"
                f"Статус: <code>{cur_running.value}</code> → "
                f"<code>{rollback_to.value}</code>."
            )
        elif xlsx_stopped:
            status_msg = (
                f"⏹ <b>Остановлено</b>: xlsx-flow ({', '.join(xlsx_stopped)})\n"
                f"Проект #{pid} «{_project_display_topic(project)}» "
                f"(slug: <code>{slug}</code>)\n"
                "Лок снят — можно запустить шаг заново."
            )
        else:
            await cb.answer(
                f"Нет активных шагов (статус: {project.status.value}).",
                show_alert=True,
            )
            return

    await cb.answer("Остановлено")
    await cb.message.answer(status_msg, parse_mode="HTML")


@dp.callback_query(F.data.regexp(r"^proj:\d+:reload_xlsx$"))
async def on_project_reload_xlsx(cb: CallbackQuery) -> None:
    if cb.from_user.id != settings.telegram_owner_chat_id:
        await cb.answer("Нет доступа", show_alert=True)
        return
    pid = int((cb.data or "").split(":")[1])
    from app.services.xlsx_sync import reload_from_xlsx

    async with session_scope() as s:
        project = (
            await s.execute(select(Project).where(Project.id == pid))
        ).scalar_one_or_none()
        if project is None:
            await cb.answer("Проект не найден", show_alert=True)
            return
        xlsx_path = settings.data_dir / "videos" / project.slug / "project.xlsx"
        if not xlsx_path.exists():
            await cb.answer("xlsx-файла нет", show_alert=True)
            return
        try:
            summary = await reload_from_xlsx(s, project, xlsx_path)
        except Exception as e:  # noqa: BLE001
            logger.exception("reload_from_xlsx failed")
            await cb.answer(f"Ошибка: {type(e).__name__}", show_alert=True)
            return
        proj_fields = summary.get("project_fields_changed") or []
        frames_changed = summary.get("frames_changed") or []
    await cb.answer("Перечитал")
    parts = []
    if proj_fields:
        parts.append("project: " + ", ".join(proj_fields))
    if frames_changed:
        parts.append(f"кадры: {len(frames_changed)} ({frames_changed})")
    if not parts:
        parts.append("ничего нового — ваши правки уже в БД")
    await cb.message.answer("🔄 Перечитал xlsx.\n" + "\n".join(parts))


# ---------------------------------------------------------------------------
# Подменю шага 4 «Объекты» — Персонажи / Предметы / + слот к Доп работа.

@dp.callback_query(F.data.regexp(r"^proj:\d+:objects:persons$"))
async def on_objects_persons(cb: CallbackQuery) -> None:
    """Клик «Персонажи» в submenu «Объекты» — перенаправляем на старую
    Hero-логику. Просто эмулируем proj:<pid>:step:hero, чтобы переиспользовать
    весь существующий многоэтапный flow (выбор стиля, hero_count, описания,
    вариации и т.д.)."""
    if cb.from_user.id != settings.telegram_owner_chat_id:
        await cb.answer("Нет доступа", show_alert=True)
        return
    pid = int((cb.data or "").split(":")[1])
    # Поджимаем cb.data, чтобы on_project_step увидел старый формат.
    cb_clone = cb.model_copy(update={"data": f"proj:{pid}:step:hero"})
    await on_project_step(cb_clone)


@dp.callback_query(F.data.regexp(r"^proj:\d+:objects:items$"))
async def on_objects_items(cb: CallbackQuery) -> None:
    """Клик «Предметы» в submenu «Объекты» — запускаем generate_items.
    Запускать можно только из hero_ready (или выше). Если ещё не достигли
    hero_ready — refuse (требование сначала сделать Персонажей)."""
    if cb.from_user.id != settings.telegram_owner_chat_id:
        await cb.answer("Нет доступа", show_alert=True)
        return
    pid = int((cb.data or "").split(":")[1])
    async with session_scope() as s:
        project = (
            await s.execute(select(Project).where(Project.id == pid))
        ).scalar_one_or_none()
        if project is None:
            await cb.answer("Проект не найден", show_alert=True)
            return
        # Требование: hero_ready достигнут или превзойдён.
        from app.telegram.menu import status_order as _ord

        if _ord(project.status) < _ord(ProjectStatus.hero_ready):
            await cb.answer(
                "Сначала сделай «Персонажи» — нужен hero_ready",
                show_alert=True,
            )
            return
        # Если item_descriptions пуст — спросим юзера в чате (на будущее),
        # пока просто разрешим — generate_items сам отработает корректно
        # (поставит items_ready, если описаний нет).
        project.status = ProjectStatus.generating_items
        slug = project.slug
        topic = project.topic
        n_items = len([
            d for d in (project.item_descriptions or [])
            if isinstance(d, str) and d.strip()
        ])
    await cb.answer(f"Запускаю: Предметы ({n_items} шт)")
    await cb.message.answer(
        f"▶ Шаг 4b: <b>Предметы</b>\n"
        f"Проект #{pid} «{topic}» (slug: <code>{slug}</code>)\n"
        f"К генерации: <b>{n_items}</b> предмет(ов).\n"
        f"Если нужно описать предметы — отредактируй "
        f"<code>item_descriptions</code> в БД или через xlsx-round-trip "
        f"в шагах «Доп работа с EXCEL».\n"
        f"Воркер подхватит за ~15 сек.",
        parse_mode="HTML",
    )


@dp.callback_query(F.data.regexp(r"^proj:\d+:enrich_add_slot$"))
async def on_enrich_add_slot(cb: CallbackQuery) -> None:
    """Клик «➕ Добавить слот» внутри подменю «Доп работа с EXCEL» —
    инкрементит project.enrich_slots_count на 1 (до лимита
    MAX_ENRICH_SLOTS=5). Перерисовывает подменю enrich (а НЕ основное
    меню проекта, т.к. кнопка теперь живёт в подменю)."""
    if cb.from_user.id != settings.telegram_owner_chat_id:
        await cb.answer("Нет доступа", show_alert=True)
        return
    pid = int((cb.data or "").split(":")[1])
    from app.telegram.menu import MAX_ENRICH_SLOTS, enrich_submenu_kb

    async with session_scope() as s:
        project = (
            await s.execute(select(Project).where(Project.id == pid))
        ).scalar_one_or_none()
        if project is None:
            await cb.answer("Проект не найден", show_alert=True)
            return
        cur = project.enrich_slots_count or 3
        if cur >= MAX_ENRICH_SLOTS:
            await cb.answer(
                f"Уже максимум: {MAX_ENRICH_SLOTS} слотов",
                show_alert=True,
            )
            return
        project.enrich_slots_count = cur + 1
        await s.flush()
        await s.refresh(project)
        kb = enrich_submenu_kb(project)
    await cb.answer(f"Слот #{cur + 1} добавлен")
    try:
        await cb.message.edit_reply_markup(reply_markup=kb)
    except Exception:  # noqa: BLE001
        await cb.message.answer(
            f"➕ Добавлен слот «Доп работа с EXCEL #{cur + 1}»",
            reply_markup=kb,
        )


@dp.callback_query(F.data.regexp(r"^proj:\d+:enrich_run_all$"))
async def on_enrich_run_all(cb: CallbackQuery) -> None:
    """▶▶ Запустить все слоты подряд — кнопка из подменю шага 5.

    Поведение:
      1) Проверяем, что слот #1 готов к запуску (prereq hero_ready/
         items_ready достигнут).
      2) Запоминаем в `project.meta['enrich_auto_chain_to'] = N`
         (где N = enrich_slots_count). После завершения каждого
         слота воркер увидит этот флаг и автоматически переведёт
         статус на следующий enriching_<i+1>.
      3) Выставляем статус `enriching_1` — воркер подхватит и
         запустит первый слот.
      4) Юзер может в любой момент нажать «⏹ Остановить текущий
         шаг» — это уберёт running-статус и очистит auto_chain_to.
    """
    if cb.from_user.id != settings.telegram_owner_chat_id:
        await cb.answer("Нет доступа", show_alert=True)
        return
    pid = int((cb.data or "").split(":")[1])
    from app.telegram.menu import (
        ENRICH_RUNNING,
        _objects_requires_for_step5,
        enabled_enrich_slots,
        is_running_status,
        status_order,
    )

    async with session_scope() as s:
        project = (
            await s.execute(select(Project).where(Project.id == pid))
        ).scalar_one_or_none()
        if project is None:
            await cb.answer("Проект не найден", show_alert=True)
            return
        if is_running_status(project.status):
            await cb.answer(
                f"Сейчас выполняется шаг (статус: {project.status.value}). "
                "Сначала останови его кнопкой ⏹.",
                show_alert=True,
            )
            return
        n_slots = enabled_enrich_slots(project)
        prereq = _objects_requires_for_step5()
        if status_order(project.status) < status_order(prereq):
            await cb.answer(
                f"Сначала пройди шаг до {prereq.value} (готовы объекты).",
                show_alert=True,
            )
            return
        # Метаданные хранят целевой номер слота, до которого автоматически
        # цепочка дойдёт. Worker (см. app/main.py / pipeline.advance_project)
        # после каждого enrich_<i>_ready проверяет meta['enrich_auto_chain_to']
        # и если i<target — выставляет следующий running-статус сам.
        meta = dict(project.meta or {})
        meta["enrich_auto_chain_to"] = n_slots
        project.meta = meta
        project.status = ENRICH_RUNNING[0]  # enriching_1
        slug = project.slug
        topic = project.topic

    logger.info(
        "[#{}] enrich_run_all: chain enriching_1..{} (slug={}, topic={!r})",
        pid, n_slots, slug, topic,
    )
    await cb.answer(f"Стартую слот #1 → #{n_slots}")
    await cb.message.answer(
        f"▶▶ <b>Запустил все слоты «Доп работа с EXCEL» подряд</b>\n"
        f"Проект #{pid} «{topic}» (slug: <code>{slug}</code>)\n"
        f"Цепочка: #1 → #{n_slots} (статусы enriching_1..enriching_{n_slots}).\n\n"
        "Воркер автоматически переведёт статус на следующий слот после "
        "каждого <code>enrich_<i>_ready</code>. Чтобы остановить — "
        "ткни <b>⏹ Остановить текущий шаг</b> в меню проекта.",
        parse_mode="HTML",
    )


@dp.callback_query(F.data.regexp(r"^proj:\d+:delete$"))
async def on_project_delete(cb: CallbackQuery) -> None:
    if cb.from_user.id != settings.telegram_owner_chat_id:
        await cb.answer("Нет доступа", show_alert=True)
        return
    pid = int((cb.data or "").split(":")[1])
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ Удалить безвозвратно",
                    callback_data=f"proj:{pid}:delete_yes",
                ),
                InlineKeyboardButton(
                    text="↩ Отмена", callback_data=f"proj:{pid}:menu"
                ),
            ]
        ]
    )
    await cb.answer()
    await cb.message.answer(
        f"⚠️ Удалить проект #{pid}? "
        "Удалятся все записи из БД. xlsx и файлы на диске останутся.",
        reply_markup=kb,
    )


@dp.callback_query(F.data.regexp(r"^proj:\d+:delete_yes$"))
async def on_project_delete_yes(cb: CallbackQuery) -> None:
    if cb.from_user.id != settings.telegram_owner_chat_id:
        await cb.answer("Нет доступа", show_alert=True)
        return
    pid = int((cb.data or "").split(":")[1])
    async with session_scope() as s:
        project = (
            await s.execute(select(Project).where(Project.id == pid))
        ).scalar_one_or_none()
        if project is None:
            await cb.answer("Уже удалён", show_alert=True)
            return
        await s.delete(project)
    await cb.answer("Удалил")
    await cb.message.answer(f"🗑 Проект #{pid} удалён.", reply_markup=main_menu_kb())


# ---------------------------------------------------------------------------
# Создание проекта: пользователь прислал тему текстом

@dp.message(F.text & ~F.text.startswith("/"))
async def on_text_message(msg: Message) -> None:
    """Обрабатывает текстовый ввод. Используется для:
      0) кнопки постоянной reply-клавиатуры (Главное меню / Последний
         проект / Назад) — ловятся по точному тексту до любых других
         pending-стейтов, иначе нажатие могло бы попасть в «тему
         проекта» или «имя промта».
      1) ввод темы нового проекта (после клика на «📁 Новый проект»)
      2) ответ на сообщение-запрос нового промта (HITL edit)
    """
    if not is_owner(msg):
        return

    user_id = msg.from_user.id if msg.from_user else 0
    text = (msg.text or "").strip()

    # 0a) «🏠 Главное меню» — сбрасываем все pending-стейты и возвращаем
    #     юзера в главное меню. Это нужно даже если бот ждал ввод темы
    #     или имя промта — иначе из мастер-флоу не выйти.
    if text == PERSISTENT_HOME_TEXT:
        _clear_pending_state(user_id)
        await msg.answer(
            "Главное меню:",
            reply_markup=persistent_reply_kb(),
        )
        await msg.answer("Что делаем?", reply_markup=main_menu_kb())
        return

    # 0b) «📁 Последний проект» — открываем меню последнего открытого
    #     проекта (или последнего созданного, если в этой сессии юзер
    #     ничего не открывал).
    if text == PERSISTENT_LAST_TEXT:
        _clear_pending_state(user_id)
        pid = _last_project_by_user.get(user_id)
        if pid is None:
            pid = await _last_project_id_fallback()
        if pid is None:
            await msg.answer(
                "Пока нет ни одного проекта. Жми «🏠 Главное меню» → "
                "«📁 Новый проект».",
                reply_markup=persistent_reply_kb(),
            )
            return
        async with session_scope() as s:
            project = (
                await s.execute(select(Project).where(Project.id == pid))
            ).scalar_one_or_none()
        if project is None:
            await msg.answer(
                f"Проект #{pid} больше не существует. Жми «🏠 Главное "
                "меню».",
                reply_markup=persistent_reply_kb(),
            )
            return
        _remember_project(user_id, pid)
        await msg.answer(
            project_header(project),
            parse_mode="HTML",
            reply_markup=project_menu_kb(project),
        )
        return

    # 0c) «⬅ Назад» — навигация на ОДИН шаг назад по «дереву» экранов.
    #
    # Иерархия экранов:
    #     main
    #      └── project_menu
    #           ├── enrich_submenu (шаг 5)
    #           │    └── picker (enrich_<N>)
    #           ├── picker (любой не-enrich шаг)
    #           └── step_submenu (script/split/hero/items)
    #
    # Поведение:
    #   - picker (enrich_<N>) → enrich_submenu
    #   - picker (не-enrich)  → project_menu
    #   - enrich_submenu      → project_menu
    #   - step_submenu        → project_menu
    #   - project_menu        → main
    #   - main / нет данных   → main (current fallback)
    #
    # Если нужно явно попасть в главное меню — есть отдельная кнопка
    # «🏠 Главное меню».
    if text == PERSISTENT_BACK_TEXT:
        _clear_pending_state(user_id)
        screen = _user_screen.get(user_id, ("main", None, None))
        st, scr_pid, extra = screen
        # Определяем «родительский» экран и рендерим его.
        # На каждом успешном рендере родителя мы обновляем _user_screen,
        # чтобы повторный клик «⬅ Назад» вёл ещё на уровень выше.
        if st == "picker" and scr_pid is not None:
            # Picker — определяем parent по step_code.
            parent_is_enrich = (
                extra is not None and _is_enrich_slot(extra)
            )
            if parent_is_enrich:
                async with session_scope() as s:
                    project = (
                        await s.execute(
                            select(Project).where(Project.id == scr_pid)
                        )
                    ).scalar_one_or_none()
                if project is not None:
                    from app.telegram.menu import (
                        enabled_enrich_slots,
                        enrich_submenu_kb,
                    )
                    _set_user_screen(user_id, "enrich_submenu", scr_pid)
                    _remember_project(user_id, scr_pid)
                    n_slots = enabled_enrich_slots(project)
                    await msg.answer(
                        f"<b>Шаг 5. Доп работа с EXCEL</b>\n"
                        f"Проект #{scr_pid} «{project.topic}»\n"
                        f"Активных слотов: <b>{n_slots}</b>",
                        reply_markup=enrich_submenu_kb(project),
                        parse_mode="HTML",
                    )
                    return
            # Не-enrich picker → в меню проекта.
            async with session_scope() as s:
                project = (
                    await s.execute(
                        select(Project).where(Project.id == scr_pid)
                    )
                ).scalar_one_or_none()
            if project is not None:
                _set_user_screen(user_id, "project_menu", scr_pid)
                _remember_project(user_id, scr_pid)
                await msg.answer(
                    project_header(project),
                    parse_mode="HTML",
                    reply_markup=project_menu_kb(project),
                )
                return

        if st in ("enrich_submenu", "step_submenu") and scr_pid is not None:
            async with session_scope() as s:
                project = (
                    await s.execute(
                        select(Project).where(Project.id == scr_pid)
                    )
                ).scalar_one_or_none()
            if project is not None:
                _set_user_screen(user_id, "project_menu", scr_pid)
                _remember_project(user_id, scr_pid)
                await msg.answer(
                    project_header(project),
                    parse_mode="HTML",
                    reply_markup=project_menu_kb(project),
                )
                return

        if st == "project_menu":
            # Один шаг назад из меню проекта → главное меню.
            _set_user_screen(user_id, "main")
            await msg.answer(
                "Главное меню:",
                reply_markup=persistent_reply_kb(),
            )
            await msg.answer("Что делаем?", reply_markup=main_menu_kb())
            return

        # Fallback: если _user_screen ничего не знает — старое поведение
        # (вернуть в меню последнего проекта).
        pid = _last_project_by_user.get(user_id)
        if pid is None:
            pid = await _last_project_id_fallback()
        if pid is not None:
            async with session_scope() as s:
                project = (
                    await s.execute(select(Project).where(Project.id == pid))
                ).scalar_one_or_none()
            if project is not None:
                _set_user_screen(user_id, "project_menu", pid)
                _remember_project(user_id, pid)
                await msg.answer(
                    project_header(project),
                    parse_mode="HTML",
                    reply_markup=project_menu_kb(project),
                )
                return
        # Уже совсем нет контекста — главное меню.
        _set_user_screen(user_id, "main")
        await msg.answer(
            "Главное меню:",
            reply_markup=persistent_reply_kb(),
        )
        await msg.answer("Что делаем?", reply_markup=main_menu_kb())
        return

    # 1) Если ждём тему нового проекта
    if _pending_topic_input.get(user_id):
        _pending_topic_input.pop(user_id, None)
        await _create_new_project(msg)
        return

    # 2) Если ждём описание героя N для конкретного проекта
    pending = _pending_hero_brief.get(user_id)
    if pending is not None:
        pid, hero_idx = pending
        _pending_hero_brief.pop(user_id, None)
        await _save_hero_brief_and_run(msg, pid, hero_idx)
        return

    # 2.1) Если ждём текст-отличия для конкретной вариации героя.
    pending_var_mod = _pending_hero_var_modifier.get(user_id)
    if pending_var_mod is not None:
        pid, hero_idx, var_idx = pending_var_mod
        _pending_hero_var_modifier.pop(user_id, None)
        await _save_hero_var_modifier_and_continue(msg, pid, hero_idx, var_idx)
        return

    # 2.5) Если ждём тему ролика для xlsx-плана.
    pending_plan_pid = _pending_plan_topic.get(user_id)
    if pending_plan_pid is not None:
        _pending_plan_topic.pop(user_id, None)
        topic = (msg.text or "").strip()
        if not topic:
            await msg.answer(
                "Пустая тема. Нажми «1. План» в меню проекта ещё раз."
            )
            return
        # Сохраняем тему в project.topic в БД.
        async with session_scope() as s:
            project = (
                await s.execute(
                    select(Project).where(Project.id == pending_plan_pid)
                )
            ).scalar_one_or_none()
            if project is None:
                await msg.answer("Проект не найден.")
                return
            project.topic = topic
            slug = project.slug
            overrides = dict(project.prompt_overrides or {})
            has_msg_override = gtb.has_override(project, "plan")
        # Обновляем тему и в xlsx (лист «Общий план ролика»).
        try:
            from pathlib import Path as _Path
            proj_xlsx = _Path(settings.data_dir) / "videos" / slug / "project.xlsx"
            if proj_xlsx.exists():
                sheet = ProjectSheet(file_path=proj_xlsx)
                sheet.write_general(topic=topic)
        except Exception as e:  # noqa: BLE001
            logger.warning("write_general(topic) failed: {}", e)
        chosen = overrides.get("plan")
        show_run = bool(
            chosen
            and plib.is_valid_prompt_name(chosen)
            and plib.prompt_path("plan", chosen).exists()
        )
        await msg.answer(
            f"Тема сохранена: <b>{topic}</b>\n\n"
            + _prompt_picker_text("plan", overrides),
            reply_markup=_prompt_picker_kb(
                pending_plan_pid, "plan", overrides,
                has_msg_override=has_msg_override,
                show_run_button=show_run,
                show_topic_button=True,
            ),
            parse_mode="HTML",
        )
        return

    # 2.6) Если ждём замену voiceover.txt текстом
    pending_vo_pid = _pending_voiceover_replace.get(user_id)
    if pending_vo_pid is not None:
        _pending_voiceover_replace.pop(user_id, None)
        if not text:
            await msg.answer(
                "Пустой текст. Нажми «✏️ Заменить voiceover.txt» ещё раз."
            )
            return
        await _replace_voiceover(pending_vo_pid, text, msg)
        return

    # 3) Если ждём имя нового мастер-промта (после клика «+ Новый» в picker'е)
    pending_name = _pending_prompt_name.get(user_id)
    if pending_name is not None:
        pid_p, step_p = pending_name
        _pending_prompt_name.pop(user_id, None)
        await _handle_prompt_name_input(msg, pid_p, step_p)
        return

    # 4) Текст как ответ на «✏️ Сопр. сообщение». Сначала reply, потом
    #    fallback на user-level pending (если юзер прислал просто текстом
    #    без reply).
    ed_pid_step: tuple[int, str] | None = None
    if msg.reply_to_message is not None:
        ed = _pending_gpt_text_edit.get(msg.reply_to_message.message_id)
        if ed is not None and ed[0] == user_id:
            ed_pid_step = (ed[1], ed[2])
    if ed_pid_step is None:
        # Принимаем только если текст «отмена» либо длинный — иначе любая
        # короткая фраза могла бы случайно сработать как override.
        ed_user = _pending_gpt_text_edit_by_user.get(user_id)
        if ed_user is not None and (
            text.lower() == "отмена" or len(text) >= 80
        ):
            ed_pid_step = ed_user
    if ed_pid_step is not None:
        pid_e, step_e = ed_pid_step
        logger.info(
            "on_text_message: gpt-text-edit reply detected user={} pid={} "
            "step={} len={}",
            user_id, pid_e, step_e, len(text),
        )
        await _on_gpt_text_edit_reply(msg, pid_e, step_e, from_text=text)
        return

    # 5) Иначе — может это ответ на edit-запрос (per-frame image prompt).
    if msg.reply_to_message is not None:
        await _on_edit_reply(msg)


def _hero_count_kb(pid: int) -> InlineKeyboardMarkup:
    """Клавиатура 0-9: сколько героев сгенерировать.
    Ноль — отдельным рядом (визуально подчёркиваем «пропустить»)."""
    rows = [
        [InlineKeyboardButton(text="0 · без героев (пропустить шаг)",
                              callback_data=f"hero_cnt:{pid}:0")],
        [
            InlineKeyboardButton(text=str(n),
                                 callback_data=f"hero_cnt:{pid}:{n}")
            for n in (1, 2, 3, 4, 5)
        ],
        [
            InlineKeyboardButton(text=str(n),
                                 callback_data=f"hero_cnt:{pid}:{n}")
            for n in (6, 7, 8, 9)
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _hero_brief_question_text(idx: int, total: int) -> str:
    return (
        f"Опиши героя <b>{idx}/{total}</b> одним сообщением: "
        "визуал, стиль, одежда, стиль рисовки, любые "
        "характерные детали.\n\n"
        "Пример: «Девушка-киборг, 25 лет, серебряные волосы каре, "
        "лицо с резкими чертами, неоновые татуировки. Одежда: "
        "чёрная кожаная куртка с кибер-вставками, серый свитер, "
        "узкие чёрные брюки, тяжёлые ботинки. Стиль рисовки — "
        "cyberpunk semi-realistic, кинематографичное освещение.»"
    )


def _hero_variation_kb(pid: int, hero_idx: int) -> InlineKeyboardMarkup:
    """Клавиатура 1..5: кол-во вариаций для героя hero_idx."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text=str(n),
                callback_data=f"hero_var:{pid}:{hero_idx}:{n}",
            )
            for n in (1, 2, 3, 4, 5)
        ]
    ])


def _hero_reset_menu_kb(pid: int) -> InlineKeyboardMarkup:
    """Подменю «4. Hero», когда параметры уже заданы:
    можно либо продолжить (запустить генерацию / достать недостающее),
    либо сбросить параметры и задать всё заново."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="▶ Продолжить",
            callback_data=f"hero_menu:{pid}:continue",
        )],
        [InlineKeyboardButton(
            text="✏ Изменить только описания и вариации",
            callback_data=f"hero_menu:{pid}:reset_briefs",
        )],
        [InlineKeyboardButton(
            text="🎨 Сменить стиль (всё с начала)",
            callback_data=f"hero_menu:{pid}:reset_all",
        )],
    ])


def _hero_reset_menu_text(project: Project) -> str:
    overrides = dict(getattr(project, "prompt_overrides", None) or {})
    style = overrides.get("hero_style") or "—"
    n = project.hero_count
    descriptions = list(project.hero_descriptions or [])
    variations = list(project.hero_variations or [])
    desc_lines: list[str] = []
    for i in range(1, (n or 0) + 1):
        d = descriptions[i - 1] if i - 1 < len(descriptions) else None
        v = variations[i - 1] if i - 1 < len(variations) else None
        d_short = (d[:50] + "…") if d and len(d) > 50 else (d or "—")
        v_str = str(v) if v else "—"
        desc_lines.append(
            f"  • Герой {i}: «{d_short}», вариаций: <code>{v_str}</code>"
        )
    body = (
        "<b>Шаг 4 (Hero) — что делаем?</b>\n\n"
        f"Стиль: <code>{style}</code>\n"
        f"Героев: <code>{n if n is not None else '—'}</code>\n"
    )
    if desc_lines:
        body += "\n".join(desc_lines) + "\n\n"
    else:
        body += "\n"
    body += (
        "▶ <b>Продолжить</b> — донабрать недостающее или запустить генерацию.\n"
        "✏ <b>Изменить только описания и вариации</b> — стиль и кол-во героев "
        "оставляем, описания/вариации перезаполним заново.\n"
        "🎨 <b>Сменить стиль (всё с начала)</b> — сбросить ВСЁ, начать с "
        "выбора стиля."
    )
    return body


def _hero_variation_question_text(idx: int, total: int) -> str:
    return (
        f"Сколько вариаций сделать для героя <b>{idx}/{total}</b>?\n\n"
        "1 — только один кадр (без вариаций).\n"
        "2..5 — первый кадр сгенерим как основу, "
        "следующие — с этой же основой как референсом "
        "(одно и то же лицо, разные ракурсы/одежда)."
    )


async def _save_hero_brief_and_run(
    msg: Message, project_id: int, hero_idx: int
) -> None:
    """Сохраняет описание героя `hero_idx` (1..N) и сразу спрашивает
    кол-во вариаций для него (инлайн-кнопки 1..5).

    После выбора вариаций (см. `on_hero_variation_cb`) и заполнения всех
    `n_total` описаний+вариаций — статус проекта переходит в
    `generating_hero` и воркер начинает генерацию.
    """
    text = (msg.text or "").strip()
    if len(text) < 5:
        await msg.answer(
            "Слишком короткое описание. Тыкни «4. Hero» в меню заново и "
            "напиши подробнее."
        )
        return
    async with session_scope() as s:
        project = (
            await s.execute(select(Project).where(Project.id == project_id))
        ).scalar_one_or_none()
        if project is None:
            await msg.answer(f"Проект #{project_id} не найден")
            return
        n_total = project.hero_count or 1
        descriptions = list(project.hero_descriptions or [])
        # Пишем в нужный индекс (1..N → 0..N-1).
        idx0 = hero_idx - 1
        while len(descriptions) <= idx0:
            descriptions.append("")
        descriptions[idx0] = text
        project.hero_descriptions = descriptions
        # Для совместимости со старым кодом — в hero_description
        # кладём первое описание.
        if idx0 == 0:
            project.hero_description = text
        try:
            _sheet_for_project(project).write_general(
                hero_description=descriptions[0] if descriptions else None,
                status=project.status.value,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("hero_description xlsx write failed: {}", e)
    # После описания — спрашиваем кол-во вариаций для этого героя.
    user_id = msg.from_user.id if msg.from_user else 0
    if user_id:
        _pending_hero_variation[user_id] = (project_id, hero_idx)
    await msg.answer(
        f"Сохранено описание героя {hero_idx}/{n_total}.\n\n"
        + _hero_variation_question_text(hero_idx, n_total),
        reply_markup=_hero_variation_kb(project_id, hero_idx),
        parse_mode="HTML",
    )


async def _save_hero_var_modifier_and_continue(
    msg: Message, project_id: int, hero_idx: int, var_idx: int
) -> None:
    """Сохраняет текст «отличий» для конкретной вариации героя
    (var_idx ∈ 2..count). Дальше: либо просим следующую вариацию того же
    героя, либо переходим к следующему герою / запуску генерации."""
    text = (msg.text or "").strip()
    if len(text) < 3:
        await msg.answer(
            "Слишком коротко. Опиши отличия чуть подробнее (хотя бы 3 "
            "символа) — отправь сообщение ещё раз."
        )
        # Возвращаем pending, чтобы перехватить следующее сообщение.
        user_id = msg.from_user.id if msg.from_user else 0
        if user_id:
            _pending_hero_var_modifier[user_id] = (
                project_id, hero_idx, var_idx,
            )
        return
    async with session_scope() as s:
        project = (
            await s.execute(select(Project).where(Project.id == project_id))
        ).scalar_one_or_none()
        if project is None:
            await msg.answer(f"Проект #{project_id} не найден")
            return
        n_total = project.hero_count or 1
        variations = list(project.hero_variations or [])
        idx0 = hero_idx - 1
        # Кол-во вариаций героя.
        count = (
            int(variations[idx0]) if idx0 < len(variations) and variations[idx0]
            else 1
        )
        modifiers_all = list(project.hero_variation_modifiers or [])
        while len(modifiers_all) <= idx0:
            modifiers_all.append([])
        cur = list(modifiers_all[idx0] or [])
        # var_idx 2..count → индекс в массиве модификаторов = var_idx - 2.
        slot = var_idx - 2
        while len(cur) <= slot:
            cur.append("")
        cur[slot] = text
        modifiers_all[idx0] = cur
        project.hero_variation_modifiers = modifiers_all
    user_id = msg.from_user.id if msg.from_user else 0
    await msg.answer(
        f"Сохранено: герой {hero_idx}/{n_total}, отличия вариации "
        f"{var_idx}/{count}."
    )
    # Если у этого героя есть ещё вариации — спрашиваем следующую.
    next_var = var_idx + 1
    if next_var <= count:
        if user_id:
            _pending_hero_var_modifier[user_id] = (
                project_id, hero_idx, next_var,
            )
        await msg.answer(
            _hero_var_modifier_question_text(hero_idx, n_total, next_var, count),
            parse_mode="HTML",
        )
        return
    # Все модификаторы для этого героя собраны — идём к продолжению flow.
    await _continue_hero_flow_after_step(msg, user_id, project_id, hero_idx, n_total)


async def _run_plan_xlsx(
    msg: Message, project_id: int, prompt_name: str, topic: str
) -> None:
    """Запускает xlsx-flow для шага «План»:

    1) Бэкапим текущий project.xlsx в old/<timestamp>.xlsx.
    2) Открываем новый чат ChatGPT, прикрепляем project.xlsx, шлём промт
       (тема + содержимое выбранного файла-промта).
    3) Скачиваем файл из ответа GPT, подменяем им project.xlsx.
    4) Шлём результат юзеру в TG.

    Никаких изменений в orchestrator — этот шаг полностью идёт мимо воркера.
    Если что-то падает — восстанавливаем xlsx из бэкапа.
    """
    from datetime import datetime
    from pathlib import Path as _Path

    from app.services.xlsx_versioning import (
        backup_to_old,
        replace_with,
        validate_xlsx,
    )

    async with session_scope() as s:
        project = (
            await s.execute(select(Project).where(Project.id == project_id))
        ).scalar_one_or_none()
        if project is None:
            await msg.answer(f"Проект #{project_id} не найден")
            return
        slug = project.slug

    proj_xlsx = _Path(settings.data_dir) / "videos" / slug / "project.xlsx"
    if not proj_xlsx.exists():
        await msg.answer(
            f"project.xlsx не найден: <code>{proj_xlsx}</code>",
            parse_mode="HTML",
        )
        return

    prompt_path = plib.prompt_path("plan", prompt_name)
    if not prompt_path.exists():
        await msg.answer(
            f"Файл промта не найден: <code>{prompt_path}</code>",
            parse_mode="HTML",
        )
        return

    # Мастер-промт → .md файл, сопр. сообщение → текст в чат.
    # Аналогично step 2 и step 5: промт уходит файлом, не текстом.
    from app.services.prompt_library import get_project_prompt
    try:
        master = get_project_prompt(project, "plan")
    except FileNotFoundError:
        master = (
            "# plan\n\n"
            "Мастер-промт для шага «План» ещё не настроен. "
            "Открой prompts/01_plan/default.md и опиши там задачу."
        )

    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    out_dir = proj_xlsx.parent / "tmp_gpt"
    out_dir.mkdir(parents=True, exist_ok=True)
    downloaded = out_dir / f"plan_{ts}.xlsx"

    # Мастер-промт + тема → .md файл
    prompt_file = out_dir / f"prompt_plan_{ts}.md"
    prompt_content = f"Тема ролика: {topic}\n\n{master.strip()}"
    prompt_file.write_text(prompt_content, encoding="utf-8")

    # Сопр. сообщение — короткий текст в чат (без дублирования мастер-промта).
    accompanying = gtb.get_effective_text(
        project, "plan", topic=topic, prompt_file_name=prompt_file.name,
    )
    text_was_overridden = gtb.has_override(project, "plan")

    override_note = (
        "\n<i>✏️ Сопр. сообщение: отредактировано пользователем</i>"
        if text_was_overridden else ""
    )
    logger.info(
        "plan_xlsx: prompt_file={}, size={}, accompanying_len={}, xlsx={}",
        prompt_file, prompt_file.stat().st_size,
        len(accompanying), proj_xlsx,
    )
    await msg.answer(
        f"▶ <b>План</b> (xlsx-flow)\n"
        f"Проект #{project_id} «{topic}»\n"
        f"Промт: <code>{prompt_name}</code>{override_note}\n"
        f"Файл промта: <code>{prompt_file.name}</code> "
        f"({prompt_file.stat().st_size} байт)\n\n"
        f"Открываю ChatGPT, прикрепляю xlsx + промт-файл, жду ответ. "
        f"До 15 минут. Не закрывай Chrome.",
        parse_mode="HTML",
    )

    backup: _Path | None = None
    try:
        async with browser_session() as bs:
            gpt = ChatGPTBot(bs)
            await gpt.new_conversation()
            # Промт-файл + xlsx — как вложения, сопр. текст — в чат.
            reply = await gpt.ask_with_files(
                accompanying.strip(), [prompt_file, proj_xlsx], timeout=900
            )
            logger.info(
                "plan_xlsx: GPT reply len={} (project #{}, prompt={})",
                len(reply or ""),
                project_id,
                prompt_name,
            )
            await gpt.download_attachment_from_last_reply(
                downloaded, timeout=900
            )
    except Exception as e:  # noqa: BLE001
        logger.exception("plan_xlsx failed: {}", e)
        await msg.answer(
            f"❌ ChatGPT вернул ошибку: {e}\n"
            f"project.xlsx не подменён, можно попробовать ещё раз."
        )
        return

    validation_err = validate_xlsx(downloaded)
    if validation_err is not None:
        logger.warning(
            "plan_xlsx: скачанный файл не валиден ({}): {}",
            validation_err,
            downloaded,
        )
        await msg.answer(
            f"❌ ChatGPT прислал невалидный xlsx: {validation_err}\n"
            f"Файл: <code>{downloaded}</code>\n"
            f"project.xlsx не подменён, можно попробовать ещё раз.",
            parse_mode="HTML",
        )
        return

    # Бэкап старого + подмена.
    try:
        backup = backup_to_old(proj_xlsx)
        replace_with(proj_xlsx, downloaded)
    except Exception as e:  # noqa: BLE001
        logger.exception("plan_xlsx replace failed: {}", e)
        await msg.answer(f"❌ Не смог подменить project.xlsx: {e}")
        return

    # Обновляем статус проекта + СИНХРОНИЗИРУЕМ xlsx → БД.
    # ROOT FIX: без этого `project.general_plan` остаётся NULL, и при
    # следующем рестарте бота `_recompute_all_projects` откатывает
    # статус на `new`. Юзер видит «всё откатилось».
    try:
        from app.services.xlsx_sync import reload_from_xlsx
        from app.services.xlsx_v8_import import import_v8_xlsx

        async with session_scope() as s:
            project = (
                await s.execute(
                    select(Project).where(Project.id == project_id)
                )
            ).scalar_one_or_none()
            if project is not None:
                # v8-импортёр (для нового шаблона с листом «Общий план»).
                # keep_fields=False — свежий xlsx от GPT, перезаписываем.
                try:
                    info_v8 = await import_v8_xlsx(
                        s, project, proj_xlsx, keep_fields=False
                    )
                    logger.info(
                        "plan_xlsx: v8 import → {}", info_v8
                    )
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        "plan_xlsx: v8 import failed: {}", e
                    )
                # Старый v7-формат (на случай миграции).
                try:
                    info = await reload_from_xlsx(s, project, proj_xlsx)
                    logger.info(
                        "plan_xlsx: v7 reload_from_xlsx → {}", info
                    )
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        "plan_xlsx: v7 reload_from_xlsx failed: {}", e
                    )
                project.status = ProjectStatus.plan_ready
    except Exception as e:  # noqa: BLE001
        logger.warning("plan_xlsx status update failed: {}", e)

    backup_note = (
        f"\nПредыдущая версия: <code>old/{backup.name}</code>"
        if backup is not None
        else ""
    )
    await msg.answer(
        f"✅ План готов. project.xlsx обновлён.{backup_note}",
        parse_mode="HTML",
    )
    try:
        await msg.answer_document(
            FSInputFile(str(proj_xlsx)),
            caption=f"project.xlsx — план «{prompt_name}» по теме «{topic}»",
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("plan_xlsx send doc failed: {}", e)


async def _run_script_xlsx(
    msg: Message, project_id: int, prompt_name: str
) -> None:
    """Запускает xlsx-flow для шага 2 «Закадровый текст»:

    1) Открываем новый чат ChatGPT, прикрепляем project.xlsx, шлём промт.
    2) Ждём ответ, скачиваем txt-файл из ответа GPT.
       Если GPT не приложил файл, но дал длинный inline-ответ — берём его.
    3) Старый voiceover.txt (если есть) бэкапим в old/<ts>_voiceover.txt.
    4) Сохраняем новый txt как data/videos/<slug>/voiceover.txt.
    5) Статус проекта → script_ready, шлём txt в TG.

    Никаких изменений в orchestrator — этот шаг полностью идёт мимо воркера.
    """
    import shutil
    from datetime import datetime
    from pathlib import Path as _Path

    async with session_scope() as s:
        project = (
            await s.execute(select(Project).where(Project.id == project_id))
        ).scalar_one_or_none()
        if project is None:
            await msg.answer(f"Проект #{project_id} не найден")
            return
        slug = project.slug
        topic = project.topic

    proj_xlsx = _Path(settings.data_dir) / "videos" / slug / "project.xlsx"
    if not proj_xlsx.exists():
        await msg.answer(
            f"project.xlsx не найден: <code>{proj_xlsx}</code>",
            parse_mode="HTML",
        )
        return

    prompt_path = plib.prompt_path("script", prompt_name)
    if not prompt_path.exists():
        await msg.answer(
            f"Файл промта не найден: <code>{prompt_path}</code>",
            parse_mode="HTML",
        )
        return
    prompt_text = prompt_path.read_text(encoding="utf-8").strip()

    voiceover = proj_xlsx.parent / "voiceover.txt"
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    out_dir = proj_xlsx.parent / "tmp_gpt"
    out_dir.mkdir(parents=True, exist_ok=True)
    downloaded = out_dir / f"voiceover_{ts}.txt"

    # Промт идёт отдельным .txt-файлом — так просил юзер. В самом
    # сообщении в чат остаётся короткая инструкция без дублирования данных.
    prompt_file = out_dir / f"prompt_script_{ts}.txt"
    prompt_file.write_text(
        f"# Инструкция для GPT (шаг 2 «Закадровый текст»)\n"
        f"# Тема ролика: «{topic}»\n\n"
        f"{prompt_text}\n",
        encoding="utf-8",
    )

    # Сопр. сообщение — берём override юзера, либо собираем дефолт.
    chat_msg = gtb.get_effective_text(
        project, "script", prompt_file_name=prompt_file.name
    )
    text_was_overridden = gtb.has_override(project, "script")

    override_note = (
        "\n<i>✏️ Сопр. сообщение: отредактировано пользователем</i>"
        if text_was_overridden else ""
    )
    await msg.answer(
        f"▶ <b>Закадровый текст</b> (xlsx-flow)\n"
        f"Проект #{project_id} «{topic}»\n"
        f"Промт: <code>{prompt_name}</code>{override_note}\n\n"
        "Открываю ChatGPT, прикрепляю <code>prompt.txt</code> + "
        "<code>project.xlsx</code>, жду ответ. До 15 минут. Не закрывай Chrome.",
        parse_mode="HTML",
    )

    reply_text = ""
    try:
        async with browser_session() as bs:
            gpt = ChatGPTBot(bs)
            await gpt.new_conversation()
            reply_text = await gpt.ask_with_files(
                chat_msg, [prompt_file, proj_xlsx], timeout=900
            )
            logger.info(
                "script_xlsx: GPT reply len={} (project #{}, prompt={})",
                len(reply_text or ""),
                project_id,
                prompt_name,
            )
            # GPT должен вернуть txt файл — всегда скачиваем файл из ответа.
            logger.info("script_xlsx: скачиваю txt файл из ответа ChatGPT")
            await gpt.download_attachment_from_last_reply(
                downloaded, timeout=900
            )
    except Exception as e:  # noqa: BLE001
        logger.exception("script_xlsx failed: {}", e)
        await msg.answer(
            f"❌ ChatGPT вернул ошибку: {e}\n"
            f"voiceover.txt не подменён, можно попробовать ещё раз."
        )
        return

    if not downloaded.exists() or downloaded.stat().st_size < 10:
        await msg.answer(
            f"❌ Скачанный txt пустой или повреждён: "
            f"<code>{downloaded}</code>",
            parse_mode="HTML",
        )
        return

    # Бэкап старого + сохранение нового.
    backup: _Path | None = None
    try:
        if voiceover.exists():
            old_dir = voiceover.parent / "old"
            old_dir.mkdir(parents=True, exist_ok=True)
            backup = old_dir / f"{ts}_voiceover.txt"
            shutil.copy2(voiceover, backup)
        shutil.copy2(downloaded, voiceover)
    except Exception as e:  # noqa: BLE001
        logger.exception("script_xlsx replace failed: {}", e)
        await msg.answer(f"❌ Не смог записать voiceover.txt: {e}")
        return

    # Обновляем статус проекта + СОХРАНЯЕМ script_text в БД.
    # ROOT FIX: без `project.script_text` рекомпьют статуса откатывает
    # проект на `plan_ready` после рестарта (см. compute_actual_status).
    voiceover_text = ""
    try:
        voiceover_text = voiceover.read_text(encoding="utf-8").strip()
    except Exception as e:  # noqa: BLE001
        logger.warning("script_xlsx: не смог прочитать voiceover.txt: {}", e)
    try:
        async with session_scope() as s:
            project = (
                await s.execute(
                    select(Project).where(Project.id == project_id)
                )
            ).scalar_one_or_none()
            if project is not None:
                if voiceover_text:
                    project.script_text = voiceover_text
                    logger.info(
                        "script_xlsx: project.script_text сохранён ({} симв)",
                        len(voiceover_text),
                    )
                project.status = ProjectStatus.script_ready
    except Exception as e:  # noqa: BLE001
        logger.warning("script_xlsx status update failed: {}", e)

    backup_note = (
        f"\nПредыдущая версия: <code>old/{backup.name}</code>"
        if backup is not None
        else ""
    )
    await msg.answer(
        f"✅ Закадровый текст готов. voiceover.txt сохранён "
        f"({voiceover.stat().st_size} байт).{backup_note}",
        parse_mode="HTML",
    )
    try:
        await msg.answer_document(
            FSInputFile(str(voiceover)),
            caption=(
                f"voiceover.txt — закадровый текст "
                f"(промт «{prompt_name}»)"
            ),
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("script_xlsx send doc failed: {}", e)


async def _run_split_xlsx(
    msg: Message, project_id: int, prompt_name: str
) -> None:
    """Запускает xlsx-flow для шага 3 «Разбивка на блоки»:

    1) Открываем новый чат ChatGPT, прикрепляем 3 файла —
       prompt_split_<ts>.txt + project.xlsx + voiceover.txt — шлём короткое
       сообщение. Просим GPT вписать разбивку в нужные ячейки project.xlsx
       и вернуть обновлённый .xlsx.
    2) Скачиваем xlsx из ответа GPT, бэкапим старый project.xlsx и
       подменяем его новым (как Шаг 1 «План»).
    3) Статус проекта → frames_ready, шлём обновлённый xlsx в TG.
    """
    from datetime import datetime
    from pathlib import Path as _Path

    from app.services.xlsx_versioning import (
        backup_to_old,
        replace_with,
        validate_xlsx,
    )

    async with session_scope() as s:
        project = (
            await s.execute(select(Project).where(Project.id == project_id))
        ).scalar_one_or_none()
        if project is None:
            await msg.answer(f"Проект #{project_id} не найден")
            return
        slug = project.slug
        topic = project.topic

    proj_xlsx = _Path(settings.data_dir) / "videos" / slug / "project.xlsx"
    if not proj_xlsx.exists():
        await msg.answer(
            f"project.xlsx не найден: <code>{proj_xlsx}</code>",
            parse_mode="HTML",
        )
        return
    voiceover = proj_xlsx.parent / "voiceover.txt"
    if not voiceover.exists():
        await msg.answer(
            f"voiceover.txt не найден: <code>{voiceover}</code>\n"
            "Сначала пройди Шаг 2 «Закадровый текст».",
            parse_mode="HTML",
        )
        return

    prompt_path = plib.prompt_path("split", prompt_name)
    if not prompt_path.exists():
        await msg.answer(
            f"Файл промта не найден: <code>{prompt_path}</code>",
            parse_mode="HTML",
        )
        return
    prompt_text = prompt_path.read_text(encoding="utf-8").strip()

    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    out_dir = proj_xlsx.parent / "tmp_gpt"
    out_dir.mkdir(parents=True, exist_ok=True)
    downloaded = out_dir / f"split_{ts}.xlsx"

    # Промт идёт отдельным .txt-файлом, плюс project.xlsx и voiceover.txt —
    # так просил юзер. xlsx нужен, чтобы GPT видел исходную структуру
    # (лист «Общий план» и т.п.), voiceover.txt — то, что режем на блоки.
    prompt_file = out_dir / f"prompt_split_{ts}.txt"
    prompt_file.write_text(
        f"# Инструкция для GPT (шаг 3 «Разбивка на блоки»)\n"
        f"# Тема ролика: «{topic}»\n\n"
        f"{prompt_text}\n",
        encoding="utf-8",
    )

    chat_msg = gtb.get_effective_text(
        project, "split", prompt_file_name=prompt_file.name
    )
    text_was_overridden = gtb.has_override(project, "split")

    override_note = (
        "\n<i>✏️ Сопр. сообщение: отредактировано пользователем</i>"
        if text_was_overridden else ""
    )
    await msg.answer(
        f"▶ <b>Разбивка на блоки</b> (xlsx-flow)\n"
        f"Проект #{project_id} «{topic}»\n"
        f"Промт: <code>{prompt_name}</code>{override_note}\n\n"
        "Открываю ChatGPT, прикрепляю <code>prompt.txt</code> + "
        "<code>project.xlsx</code> + <code>voiceover.txt</code>, жду "
        "обновлённый xlsx. До 15 минут. Не закрывай Chrome.",
        parse_mode="HTML",
    )

    backup: _Path | None = None
    try:
        async with browser_session() as bs:
            gpt = ChatGPTBot(bs)
            await gpt.new_conversation()
            reply = await gpt.ask_with_files(
                chat_msg,
                [prompt_file, proj_xlsx, voiceover],
                timeout=900,
            )
            logger.info(
                "split_xlsx: GPT reply len={} (project #{}, prompt={})",
                len(reply or ""),
                project_id,
                prompt_name,
            )
            await gpt.download_attachment_from_last_reply(
                downloaded, timeout=900
            )
    except Exception as e:  # noqa: BLE001
        logger.exception("split_xlsx failed: {}", e)
        await msg.answer(
            f"❌ ChatGPT вернул ошибку: {e}\n"
            f"project.xlsx не подменён, можно попробовать ещё раз."
        )
        return

    validation_err = validate_xlsx(downloaded)
    if validation_err is not None:
        logger.warning(
            "split_xlsx: скачанный файл не валиден ({}): {}",
            validation_err,
            downloaded,
        )
        await msg.answer(
            f"❌ ChatGPT прислал невалидный xlsx: {validation_err}\n"
            f"Файл: <code>{downloaded}</code>\n"
            f"project.xlsx не подменён, можно попробовать ещё раз.",
            parse_mode="HTML",
        )
        return

    # Бэкап старого project.xlsx + подмена.
    try:
        backup = backup_to_old(proj_xlsx)
        replace_with(proj_xlsx, downloaded)
    except Exception as e:  # noqa: BLE001
        logger.exception("split_xlsx replace failed: {}", e)
        await msg.answer(f"❌ Не смог подменить project.xlsx: {e}")
        return

    # Обновляем статус проекта + СИНХРОНИЗИРУЕМ xlsx → БД (создаём Frame'ы).
    # ROOT FIX: без Frame-строк в БД `compute_actual_status` видит
    # fr_total=0 и откатывает статус на `script_ready` после рестарта.
    try:
        from app.services.xlsx_sync import reload_from_xlsx
        from app.services.xlsx_v8_import import import_v8_xlsx

        async with session_scope() as s:
            project = (
                await s.execute(
                    select(Project).where(Project.id == project_id)
                )
            ).scalar_one_or_none()
            if project is not None:
                # v8-импортёр: тут лежат voiceover-блоки на листе «план»
                # R49, по которым создаём Frame'ы и обновляем script_text.
                try:
                    info_v8 = await import_v8_xlsx(
                        s, project, proj_xlsx,
                        keep_fields=False,
                        update_frames_voiceover=True,
                    )
                    logger.info(
                        "split_xlsx: v8 import → {}", info_v8
                    )
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        "split_xlsx: v8 import failed: {}", e
                    )
                # Старый v7-формат (на случай миграции).
                try:
                    info = await reload_from_xlsx(s, project, proj_xlsx)
                    logger.info(
                        "split_xlsx: v7 reload_from_xlsx → {}", info
                    )
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        "split_xlsx: v7 reload_from_xlsx failed: {}", e
                    )
                project.status = ProjectStatus.frames_ready
    except Exception as e:  # noqa: BLE001
        logger.warning("split_xlsx status update failed: {}", e)

    backup_note = (
        f"\nПредыдущая версия: <code>old/{backup.name}</code>"
        if backup is not None
        else ""
    )
    await msg.answer(
        f"✅ Разбивка готова. project.xlsx обновлён.{backup_note}",
        parse_mode="HTML",
    )
    try:
        await msg.answer_document(
            FSInputFile(str(proj_xlsx)),
            caption=(
                f"project.xlsx — разбивка на блоки "
                f"(промт «{prompt_name}»)"
            ),
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("split_xlsx send doc failed: {}", e)


async def _create_new_project(msg: Message) -> None:
    """Создаёт новый проект. Вход — только название проекта (короткое),
    тема ролика спрашивается отдельно при запуске шага 1 «План».
    v8-шаблон копируется в data/videos/<slug>/project.xlsx."""
    name = (msg.text or "").strip()
    if not name:
        await msg.answer("Пустое название. Нажми «📁 Новый проект» ещё раз.")
        return
    # Название — только для slug и отображения. Тема (topic) спрашивается
    # отдельно при запуске шага 1 (может быть длинным подробным описанием).
    topic = ""
    hero_mode = "auto"  # сохраняем в DB по умолчанию, больше не спрашиваем.

    slug_base = (
        re.sub(r"[^a-zа-я0-9]+", "-", name.lower(), flags=re.IGNORECASE).strip("-")[:40]
        or "rolik"
    )
    async with session_scope() as s:
        i = 1
        slug = slug_base
        while (
            await s.execute(select(Project).where(Project.slug == slug))
        ).scalar_one_or_none():
            i += 1
            slug = f"{slug_base}-{i}"
        # Создаём со статусом `new` — воркер его не трогает, ждёт первого
        # клика «Запустить шаг 1» в меню проекта.
        project = Project(
            slug=slug, topic=topic, hero_mode=hero_mode, status=ProjectStatus.new
        )
        s.add(project)
        await s.flush()
        pid = project.id
        try:
            sheet = ProjectSheet(
                file_path=settings.data_dir / "videos" / slug / "project.xlsx",
            )
            sheet.ensure_initialized(project_id=pid, slug=slug)
            sheet.write_general(
                topic=topic,
                slug=slug,
                hero_mode=hero_mode,
                status=project.status.value,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("project_sheet init failed: {}", e)

        # Перечитываем проект для рендера меню
        proj_obj = (
            await s.execute(select(Project).where(Project.id == pid))
        ).scalar_one_or_none()
    if proj_obj is None:
        await msg.answer("Не удалось создать проект — попробуй ещё раз.")
        return
    await msg.answer(
        f"Проект создан: #{pid} «{name}»\n"
        f"(slug: <code>{slug}</code>)\n\n"
        "Тема ролика будет задана при запуске шага 1 «План».\n"
        "Дальше выбери генератор картинок / видео и параметры.",
        parse_mode="HTML",
    )
    # Запускаем мастер настроек (выбор генераторов/разрешений).
    await send_wizard_question(msg.bot, msg.chat.id, proj_obj)
    logger.info("new project {} '{}'", pid, slug)


# ---------------------------------------------------------------------------
# Старый HITL-callback (картинки кадров): hitl:<id>:<action>

@dp.callback_query(F.data.startswith("hitl:"))
async def on_hitl_callback(cb: CallbackQuery) -> None:
    if cb.from_user.id != settings.telegram_owner_chat_id:
        await cb.answer("Нет доступа", show_alert=True)
        return
    try:
        _, hitl_id_s, action = (cb.data or "").split(":", 2)
        hitl_id = int(hitl_id_s)
    except Exception:
        await cb.answer("Плохой callback", show_alert=True)
        return

    if action == "original":
        # Шлём оригинал (send_document, без TG-сжатия). Файл лежит в
        # payload.photo_path.
        from pathlib import Path as _Path

        from aiogram.types import FSInputFile

        async with session_scope() as s:
            req = (
                await s.execute(select(HITLRequest).where(HITLRequest.id == hitl_id))
            ).scalar_one_or_none()
            if req is None:
                await cb.answer("HITL не найден", show_alert=True)
                return
            photo_path = (req.payload or {}).get("photo_path")
        if not photo_path:
            await cb.answer("Нет исходного файла в payload", show_alert=True)
            return
        if not _Path(photo_path).exists():
            await cb.answer(
                f"Файл не найден: {photo_path}", show_alert=True
            )
            return
        try:
            await cb.bot.send_document(
                settings.telegram_owner_chat_id,
                FSInputFile(photo_path),
                caption=f"Оригинал (без сжатия TG): {_Path(photo_path).name}",
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("send original failed: {}", e)
            await cb.answer(f"Ошибка: {e}", show_alert=True)
            return
        await cb.answer("Файл отправлен как document")
        return

    if action == "edit":
        async with session_scope() as s:
            req = (
                await s.execute(select(HITLRequest).where(HITLRequest.id == hitl_id))
            ).scalar_one_or_none()
            if req is None:
                await cb.answer("HITL не найден", show_alert=True)
                return
            if req.decision is not HITLDecision.pending:
                await cb.answer(f"Уже обработан: {req.decision.value}", show_alert=True)
                return
            frame = None
            if req.frame_id is not None:
                frame = (
                    await s.execute(select(Frame).where(Frame.id == req.frame_id))
                ).scalar_one_or_none()
            current_prompt = (frame.image_prompt if frame else None) or "(пусто)"
            ask_msg = await cb.bot.send_message(
                settings.telegram_owner_chat_id,
                (
                    f"✏️ Новый промт для кадра #{frame.number if frame else '?'}.\n"
                    f"Текущий:\n\n<pre>{_html_escape(current_prompt)}</pre>\n\n"
                    f"Ответь на это сообщение новым текстом — перегенерирую."
                ),
                parse_mode="HTML",
            )
            req.payload = {
                **(req.payload or {}),
                "edit_ask_message_id": ask_msg.message_id,
            }
            try:
                orig = cb.message
                if orig is not None:
                    if orig.photo or orig.video:
                        new_caption = ((orig.caption or "") + "\n\n✏️ Жду новый промт…").strip()
                        await orig.edit_caption(caption=new_caption[:1024], reply_markup=None)
                    else:
                        existing = orig.text or orig.html_text or ""
                        await orig.edit_text(
                            (existing + "\n\n✏️ Жду новый промт…")[:4096],
                            parse_mode="HTML",
                            reply_markup=None,
                        )
            except Exception:
                pass
        await cb.answer("Жду новый текст")
        return

    async with session_scope() as s:
        req = (
            await s.execute(select(HITLRequest).where(HITLRequest.id == hitl_id))
        ).scalar_one_or_none()
        if req is None:
            await cb.answer("HITL не найден", show_alert=True)
            return
        if req.decision is not HITLDecision.pending:
            await cb.answer(f"Уже обработан: {req.decision.value}", show_alert=True)
            return
        decision = {
            "approve": HITLDecision.approved,
            "regen": HITLDecision.regenerate,
            "reject": HITLDecision.rejected,
        }.get(action, HITLDecision.pending)
        req.decision = decision

        # В ручном режиме 🔁 на hero-карточке = «перезапустить шаг 4
        # для текущего героя» (с тем же описанием). На ✅ — если у проекта
        # больше одного героя и не все ещё сделаны, возвращаем проект в
        # generating_hero, чтобы воркер сгенерил следующего.
        regen_step_msg = ""
        if req.kind is HITLKind.approve_hero:
            project = (
                await s.execute(
                    select(Project).where(Project.id == req.project_id)
                )
            ).scalar_one_or_none()
            if project is not None:
                payload = req.payload or {}
                cur_hi = int(payload.get("hero_index") or 1)
                cur_vi = int(payload.get("variation_index") or 1)
                n_total = project.hero_count or 1
                # Кол-во вариаций именно этого героя.
                vars_cfg = list(project.hero_variations or [])
                n_var = 1
                if cur_hi - 1 < len(vars_cfg):
                    try:
                        n_var = int(vars_cfg[cur_hi - 1] or 1)
                    except (TypeError, ValueError):
                        n_var = 1
                n_var = max(1, min(5, n_var))

                if action == "regen":
                    # 🔁 эту же вариацию — выйдет в generating_hero,
                    # generate_hero.run() увидит, что (hi, vi) не
                    # одобрены, и перегенерит ровно её.
                    project.status = ProjectStatus.generating_hero
                    regen_step_msg = (
                        f"\n\n▶ Перегенерирую герой {cur_hi}/{n_total} "
                        f"вариацию {cur_vi}/{n_var}."
                    )
                elif action == "approve":
                    # ✅ — определяем что дальше: следующая вариация
                    # этого же героя; следующий герой; или всё готово.
                    if cur_vi < n_var:
                        project.status = ProjectStatus.generating_hero
                        regen_step_msg = (
                            f"\n\n▶ Перехожу к герою {cur_hi}/{n_total} "
                            f"вариации {cur_vi + 1}/{n_var}."
                        )
                    elif cur_hi < n_total:
                        project.status = ProjectStatus.generating_hero
                        regen_step_msg = (
                            f"\n\n▶ Перехожу к герою "
                            f"{cur_hi + 1}/{n_total}, вариация 1."
                        )
                    # Если это последняя вариация последнего героя —
                    # статус остаётся hero_ready (шаг полностью завершён).
    await cb.answer(f"Решение: {action}")
    badge = {
        HITLDecision.approved: "✅ Одобрено",
        HITLDecision.regenerate: "🔁 Перегенерация",
        HITLDecision.rejected: "❌ Отклонено",
    }.get(decision, "")
    if regen_step_msg:
        badge = badge + regen_step_msg
    await _hide_buttons_with_badge(cb.message, badge)


def _html_escape(s: str) -> str:
    import html as _h

    return _h.escape(s)[:3500]


async def _hide_buttons_with_badge(msg: Any, badge: str) -> None:
    try:
        if msg is None:
            return
        if msg.photo or msg.video:
            new_caption = ((msg.caption or "") + f"\n\n{badge}").strip()
            await msg.edit_caption(caption=new_caption[:1024], reply_markup=None)
        else:
            existing = msg.text or msg.html_text or ""
            await msg.edit_text(
                (existing + f"\n\n{badge}")[:4096],
                parse_mode="HTML",
                reply_markup=None,
            )
    except Exception:
        pass


async def _build_gpt_text_for_edit(pid: int, step_code: str) -> str:
    """Собирает «текущее» сопр. сообщение для шага: либо override юзера,
    либо дефолт. Используется кнопкой «📥 Получить файл», чтобы дать юзеру
    готовый шаблон для редактирования.

    Контекст для шага `img_pr` (voiceover_line / n_frames) подтягивается
    из реальных кадров проекта. Для `script` / `split` используется
    placeholder-имя файла «prompt.txt» — фактическое имя зависит от
    timestamp и не критично для редактирования.
    """
    async with session_scope() as s:
        project = (
            await s.execute(select(Project).where(Project.id == pid))
        ).scalar_one_or_none()
        if project is None:
            raise RuntimeError(f"Проект #{pid} не найден")

        ctx: dict = {}
        if step_code == "img_pr":
            frames = (
                await s.execute(
                    select(Frame)
                    .where(Frame.project_id == pid)
                    .order_by(Frame.number)
                )
            ).scalars().all()
            if frames:
                ctx["voiceover_line"] = "-".join(
                    (fr.voiceover_text or "").strip() for fr in frames
                )
                ctx["n_frames"] = len(frames)
        return gtb.get_effective_text(project, step_code, **ctx)


def _clear_pending_gpt_text_edit(user_id: int, pid: int, step_code: str) -> None:
    """Снимает все pending-записи (по message_id и по user_id) для
    указанной комбинации (user, project, step). Безопасно вызывать
    несколько раз."""
    for k, v in list(_pending_gpt_text_edit.items()):
        if v == (user_id, pid, step_code):
            _pending_gpt_text_edit.pop(k, None)
    cur = _pending_gpt_text_edit_by_user.get(user_id)
    if cur is not None and cur == (pid, step_code):
        _pending_gpt_text_edit_by_user.pop(user_id, None)


async def _on_gpt_text_edit_reply(
    msg: Message, pid: int, step_code: str, *, from_text: str
) -> None:
    """Обработчик ответа юзера на «✏️ Сопр. сообщение». Сохраняет
    новый текст в `Project.gpt_text_overrides[step_code]`."""
    user_id = msg.from_user.id if msg.from_user else 0
    text = (from_text or "").strip()
    if not text:
        await msg.reply("Пустой текст — ничего не сохранил.")
        return
    if text.lower() == "отмена":
        _clear_pending_gpt_text_edit(user_id, pid, step_code)
        await msg.reply("Отменено. Override не сохранён.")
        return
    if not gtb.is_supported(step_code):
        await msg.reply(f"Шаг {step_code!r} не поддерживает override.")
        return
    async with session_scope() as s:
        project = (
            await s.execute(select(Project).where(Project.id == pid))
        ).scalar_one_or_none()
        if project is None:
            await msg.reply(f"Проект #{pid} не найден.")
            return
        await gtb.set_override(s, project, step_code, text)
    _clear_pending_gpt_text_edit(user_id, pid, step_code)
    human = plib.STEP_HUMAN_NAMES.get(step_code, step_code)
    logger.info(
        "gpt-text override saved: user={} pid={} step={} len={}",
        user_id, pid, step_code, len(text),
    )
    await msg.reply(
        f"✅ Отредактировано — сопр. сообщение для шага «{human}» "
        f"(проект #{pid}) обновлено. При следующем запуске пойдёт "
        f"твой текст ({len(text)} символов)."
    )


async def _on_edit_reply(msg: Message) -> None:
    """Если пользователь ответил на наше edit-запрос-сообщение — записываем
    новый текст в frame.image_prompt, ставим decision=edit_prompt."""
    reply_to_id = msg.reply_to_message.message_id if msg.reply_to_message else None
    if reply_to_id is None:
        return
    new_prompt = (msg.text or "").strip()
    if not new_prompt:
        return
    async with session_scope() as s:
        rows = (
            await s.execute(
                select(HITLRequest)
                .where(HITLRequest.decision == HITLDecision.pending)
                .order_by(HITLRequest.id.desc())
                .limit(30)
            )
        ).scalars().all()
        req = None
        for r in rows:
            if (r.payload or {}).get("edit_ask_message_id") == reply_to_id:
                req = r
                break
        if req is None:
            return
        if req.frame_id is None:
            return
        frame = (
            await s.execute(select(Frame).where(Frame.id == req.frame_id))
        ).scalar_one_or_none()
        if frame is None:
            return
        frame.image_prompt = new_prompt
        req.decision = HITLDecision.edit_prompt
        req.payload = {
            **(req.payload or {}),
            "edited_prompt": new_prompt[:2000],
        }
        hitl_tg_msg_id = req.tg_message_id
        project = (
            await s.execute(select(Project).where(Project.id == frame.project_id))
        ).scalar_one_or_none()
        if project is not None:
            try:
                _sheet_for_project(project).write_frame(
                    frame.number, image_prompt=new_prompt
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("xlsx write_frame(image_prompt) failed: {}", e)
    if hitl_tg_msg_id:
        try:
            await msg.bot.edit_message_caption(
                chat_id=settings.telegram_owner_chat_id,
                message_id=hitl_tg_msg_id,
                caption="✏️ Промт изменён — перегенерирую",
                reply_markup=None,
            )
        except Exception:
            pass
    await msg.reply("✏️ Промт обновлён. Перегенерирую картинку с ним.")


# ---------------------------------------------------------------------------
# Уведомления о завершении шагов
# Вызывается из _run_worker_loop после успешного advance_project.

# Статусы, переход в которые ИНИЦИИРУЕТ юзер своим approve в TG-карточке
# (✅ на hero-варианте / на готовой картинке). После такого approve мы НЕ
# хотим лишний раз присылать «✅ Шаг завершён + меню проекта» — юзер только
# что сам ткнул кнопку, под approved-карточкой уже стоит «✅ Одобрено», и
# любое всплытие меню только мешает («не нужно каждый раз возвращать мне
# главное меню» — комментарий пользователя).
_HITL_DRIVEN_READY_STATUSES: set[str] = {
    ProjectStatus.hero_ready.value,
    ProjectStatus.images_ready.value,
}


async def notify_step_done(
    bot: Bot,
    project_id: int,
    prev_status: str,
    new_status: str,
) -> None:
    """Шлёт в TG короткое уведомление о завершении шага.

    Вызывается воркером ПОСЛЕ commit, поэтому всегда читаем актуальное
    состояние проекта из БД. prev_status и new_status передаются явно для
    диагностики.

    Поведение:
      * для статусов из `_HITL_DRIVEN_READY_STATUSES` (`hero_ready`,
        `images_ready`) сообщение НЕ отправляется — юзер только что сам
        approve-нул карточку, под ней уже есть «✅ Одобрено», лишнее меню
        мешает.
      * для остальных переходов посылается короткий текст без inline-меню
        — постоянная reply-клавиатура (Главное меню / Последний проект /
        Назад) и так всегда видна, кнопка-меню в этом сообщении только
        дублирует.
    """
    logger.info(
        "notify_step_done: project={}, {} → {}",
        project_id,
        prev_status,
        new_status,
    )
    if new_status in _HITL_DRIVEN_READY_STATUSES:
        logger.info(
            "notify_step_done: статус {} — это HITL-одобрение, "
            "TG-уведомление пропускаю (под approve-карточкой уже badge).",
            new_status,
        )
        return
    async with session_scope() as s:
        project = (
            await s.execute(select(Project).where(Project.id == project_id))
        ).scalar_one_or_none()
        if project is None:
            logger.warning(
                "notify_step_done: project #{} не найден", project_id
            )
            return
        slug = project.slug
        status_val = project.status.value
        try:
            await bot.send_message(
                settings.telegram_owner_chat_id,
                f"✅ Шаг завершён: статус <b>{status_val}</b>",
                parse_mode="HTML",
            )
            logger.info(
                "notify_step_done: сообщение отправлено для project #{}",
                project_id,
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "notify_step_done: send_message провалился для project #{}",
                project_id,
            )

    # Для enrich-шагов (enrich_1_ready..enrich_5_ready) присылаем
    # обновлённый project.xlsx как документ.
    if new_status.startswith("enrich_") and new_status.endswith("_ready"):
        from pathlib import Path as _Path

        xlsx_path = _Path(settings.data_dir) / "videos" / slug / "project.xlsx"
        if xlsx_path.exists():
            try:
                await bot.send_document(
                    settings.telegram_owner_chat_id,
                    FSInputFile(str(xlsx_path)),
                    caption=(
                        f"📥 Результат: project.xlsx после «{new_status}»\n"
                        f"Проект #{project_id}"
                    ),
                )
            except Exception:  # noqa: BLE001
                logger.exception(
                    "notify_step_done: send_document xlsx failed for #{}",
                    project_id,
                )


# ---------------------------------------------------------------------------
# build_bot — поднимаем сессию (с прокси если задан)


async def build_bot() -> tuple[Bot, Dispatcher]:
    proxy_url = settings.telegram_proxy_url
    if proxy_url:
        logger.info("telegram: using proxy {}", _mask_proxy_url(proxy_url))
        if proxy_url.startswith(("socks4://", "socks5://", "socks5h://")):
            try:
                from aiohttp_socks import ProxyConnector  # type: ignore[import-not-found]
            except ImportError as e:
                raise RuntimeError(
                    "Для SOCKS-прокси поставь aiohttp-socks: pip install aiohttp-socks"
                ) from e
            import aiohttp
            from aiogram.client.session.aiohttp import AiohttpSession

            class _SocksSession(AiohttpSession):
                def __init__(self, proxy: str) -> None:
                    super().__init__()
                    self._proxy_url_socks = proxy

                async def create_session(self) -> aiohttp.ClientSession:  # type: ignore[override]
                    if self._session is None or self._session.closed:
                        connector = ProxyConnector.from_url(self._proxy_url_socks)
                        self._session = aiohttp.ClientSession(connector=connector)
                    return self._session

            bot = Bot(settings.telegram_bot_token, session=_SocksSession(proxy_url))
        else:
            from aiogram.client.session.aiohttp import AiohttpSession

            bot = Bot(
                settings.telegram_bot_token,
                session=AiohttpSession(proxy=proxy_url),
            )
    else:
        bot = Bot(settings.telegram_bot_token)
    return bot, dp


def _mask_proxy_url(url: str) -> str:
    try:
        from urllib.parse import urlparse, urlunparse

        p = urlparse(url)
        if p.username or p.password:
            netloc = f"{p.username or ''}:***@{p.hostname}"
            if p.port:
                netloc += f":{p.port}"
            return urlunparse(p._replace(netloc=netloc))
        return url
    except Exception:
        return url
