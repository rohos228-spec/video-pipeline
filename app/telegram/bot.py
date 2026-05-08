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
    step_by_code,
)
from app.telegram.prompt_picker import (
    delete_kb as _prompt_delete_kb,
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
    is_wizard_complete,
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

# Последний открытый юзером проект — для кнопки «📁 Последний проект»
# в постоянной reply-клавиатуре. Обновляется при открытии меню проекта,
# нажатии шага, выборе промта и т.п.  user_id → project_id.
_last_project_by_user: dict[int, int] = {}


def _remember_project(user_id: int, project_id: int) -> None:
    """Запоминает что юзер сейчас работает с этим проектом — для кнопки
    «📁 Последний проект» в постоянной клавиатуре."""
    _last_project_by_user[user_id] = project_id


def _clear_pending_state(user_id: int) -> None:
    """Сбрасывает все pending-состояния юзера (используется при кликах на
    кнопки постоянной клавиатуры — Главное меню / Назад)."""
    _pending_topic_input.pop(user_id, None)
    _pending_hero_brief.pop(user_id, None)
    _pending_prompt_name.pop(user_id, None)
    _pending_prompt_upload.pop(user_id, None)
    _pending_plan_topic.pop(user_id, None)
    _pending_plan_prompt.pop(user_id, None)
    _pending_script_prompt.pop(user_id, None)
    _pending_split_prompt.pop(user_id, None)


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
        await cb.answer()
        await cb.message.answer(
            project_header(project),
            parse_mode="HTML",
            reply_markup=project_menu_kb(project),
        )


@dp.callback_query(F.data.regexp(r"^proj:\d+:step:[a-z_]+$"))
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
        if not is_step_runnable(step, project.status):
            await cb.answer(
                f"Сначала пройди шаг до {step.requires.value if step.requires else '?'}",
                show_alert=True,
            )
            return
        if project.status is step.running_status:
            await cb.answer("Этот шаг уже выполняется")
            return

        # Шаг 1 (План) — новый xlsx-flow.
        #   1) спрашиваем у юзера тему ролика текстом
        #   2) после ввода темы — список файлов-промтов из prompts/01_plan/
        #   3) после выбора — uploadим project.xlsx + промт в ChatGPT,
        #      ждём ответ, скачиваем обновлённый xlsx, подменяем,
        #      старый кладём в old/<timestamp>.xlsx.
        if step.code == "plan":
            from pathlib import Path as _Path
            proj_xlsx = (
                _Path(settings.data_dir) / "videos" / project.slug / "project.xlsx"
            )
            if proj_xlsx.exists():
                _pending_plan_topic[cb.from_user.id] = pid
                await cb.answer()
                await cb.message.answer(
                    "Напиши <b>тему ролика</b>, по которой будет сделан план.\n"
                    "Я добавлю её в начало промта и отправлю в ChatGPT вместе "
                    "с твоим project.xlsx.",
                    parse_mode="HTML",
                )
                return
            # xlsx-файла нет — упадём в старую логику ниже.

        # Шаг 2 (Закадровый текст) — новый xlsx-flow.
        #   1) сразу показываем picker промтов из prompts/02_script/
        #   2) после выбора — uploadим project.xlsx + промт в ChatGPT,
        #      ждём ответ, скачиваем txt, сохраняем как voiceover.txt,
        #      статус → script_ready, шлём txt в TG.
        if step.code == "script":
            from pathlib import Path as _Path
            proj_xlsx = (
                _Path(settings.data_dir) / "videos" / project.slug / "project.xlsx"
            )
            if proj_xlsx.exists():
                overrides = dict(project.prompt_overrides or {})
                _pending_script_prompt[cb.from_user.id] = pid
                await cb.answer()
                await cb.message.answer(
                    _prompt_picker_text("script", overrides),
                    reply_markup=_prompt_picker_kb(pid, "script", overrides),
                    parse_mode="HTML",
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

        # Шаг 4 (Hero) — многоэтапный:
        #   1) если hero_count ещё не задан → кнопки 0-9 «сколько героев?»
        #   2) если задан и описаний недостаточно → запрашиваем описание
        #      следующего героя у юзера текстом
        #   3) если все описания собраны → выставляем generating_hero и
        #      воркер запускает генерацию по очереди
        if step.code == "hero":
            if project.hero_count is None:
                await cb.answer()
                await cb.message.answer(
                    "Сколько персонажей-героев сгенерировать? "
                    "Выбери число (0 — без героев, шаг будет пропущен).",
                    reply_markup=_hero_count_kb(pid),
                )
                return
            n = project.hero_count
            descriptions = list(project.hero_descriptions or [])
            if n == 0:
                # Пользователь раньше выбрал «0 героев» — шаг сразу готов.
                project.status = ProjectStatus.hero_ready
                await cb.answer("0 героев — шаг пропущен")
                await cb.message.answer(
                    f"✅ Шаг 4 пропущен (0 героев). Можно идти к шагу 5."
                )
                return
            if len(descriptions) < n:
                # Нужно описать ещё одного.
                next_idx = len(descriptions) + 1
                _pending_hero_brief[cb.from_user.id] = (pid, next_idx)
                await cb.answer()
                await cb.message.answer(
                    _hero_brief_question_text(next_idx, n),
                )
                return
            # Все описания собраны → запускаем генерацию первого/следующего.
            project.status = step.running_status
            slug = project.slug
            topic = project.topic
            await cb.answer(f"Запускаю: {step.title}")
            await cb.message.answer(
                f"▶ Шаг {step.n}: <b>{step.title}</b> "
                f"({n} героев, описаний собрано: {len(descriptions)})\n"
                f"Проект #{pid} «{topic}» (slug: <code>{slug}</code>)\n"
                f"Воркер подхватит за ~15 сек.",
                parse_mode="HTML",
            )
            return

        # Если у шага есть мастер-промт и в проекте ещё не выбран
        # вариант (или указанный файл пропал) — показываем picker и НЕ
        # запускаем шаг до выбора. Это ключевая часть Push C.
        if step.code in plib.STEP_FOLDERS:
            overrides = dict(project.prompt_overrides or {})
            chosen = overrides.get(step.code)
            need_picker = (
                not chosen
                or not plib.is_valid_prompt_name(chosen)
                or not plib.prompt_path(step.code, chosen).exists()
            )
            if need_picker:
                await cb.answer()
                await cb.message.answer(
                    _prompt_picker_text(step.code, overrides),
                    reply_markup=_prompt_picker_kb(pid, step.code, overrides),
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
        await cb.answer()
        await cb.message.answer(
            _prompt_picker_text(step_code, overrides),
            reply_markup=_prompt_picker_kb(pid, step_code, overrides),
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

        # Особый случай: «План» в xlsx-режиме.
        # Юзер сначала ввёл тему, теперь выбрал файл-промт.
        # Запускаем upload xlsx → ChatGPT → download прямо отсюда.
        if step_code == "plan":
            uid = cb.from_user.id
            pending_plan = _pending_plan_prompt.get(uid)
            if pending_plan is not None and pending_plan[0] == pid:
                topic = pending_plan[1]
                _pending_plan_prompt.pop(uid, None)
                await cb.answer(f"Запускаю план: {name}")
                # Не блокируем callback handler надолго — отдельной таской.
                asyncio.create_task(
                    _run_plan_xlsx(cb.message, pid, name, topic)
                )
                return

        # Особый случай: «Закадровый текст» (Step 2) в xlsx-режиме.
        if step_code == "script":
            uid = cb.from_user.id
            pending_script_pid = _pending_script_prompt.get(uid)
            if pending_script_pid is not None and pending_script_pid == pid:
                _pending_script_prompt.pop(uid, None)
                await cb.answer(f"Запускаю закадровый текст: {name}")
                asyncio.create_task(
                    _run_script_xlsx(cb.message, pid, name)
                )
                return

        # Особый случай: «Разбивка на блоки» (Step 3) в xlsx-режиме.
        if step_code == "split":
            uid = cb.from_user.id
            pending_split_pid = _pending_split_prompt.get(uid)
            if pending_split_pid is not None and pending_split_pid == pid:
                _pending_split_prompt.pop(uid, None)
                await cb.answer(f"Запускаю разбивку: {name}")
                asyncio.create_task(
                    _run_split_xlsx(cb.message, pid, name)
                )
                return

        human = plib.STEP_HUMAN_NAMES.get(step_code, step_code)
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
            f"Введи имя нового варианта мастер-промта для шага "
            f"«{human}» одним сообщением.\n"
            f"Допустимые символы: <code>A-Z a-z 0-9 _ -</code>, длина 1-64.\n"
            f"Например: <code>horror_dark_v1</code>",
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
        await cb.message.answer(
            _prompt_picker_text(step_code, overrides),
            reply_markup=_prompt_picker_kb(pid, step_code, overrides),
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
            "Имя содержит недопустимые символы или пустое. Допустимы "
            "<code>A-Z a-z 0-9 _ -</code>, длина 1-64. Попробуй ещё раз "
            "или нажми «⬅ Отмена» в picker'е.",
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
            f"📝 Создан шаблон <b>{name}.md</b> для шага «{human}».\n\n"
            f"Открой файл, замени содержимое на свой мастер-промт и пришли "
            f"<b>обратно как документ</b> в этот чат. После возврата я "
            f"сохраню его и сразу выберу для проекта."
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


@dp.message(F.document)
async def on_document_message(msg: Message) -> None:
    """Принимаем `.md`-файл с отредактированным мастер-промтом."""
    if not is_owner(msg):
        return
    user_id = msg.from_user.id if msg.from_user else 0
    if user_id not in _pending_prompt_upload:
        return  # не ждём ничего — игнорим (это может быть просто файл)
    await _handle_prompt_upload(msg)


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
    await cb.message.answer_document(
        FSInputFile(str(xlsx_path)),
        caption=(
            f"📥 project.xlsx (#{pid})\n"
            f"Открой в Excel, поправь нужные ячейки, сохрани.\n"
            f"Потом нажми «🔄 Перечитать xlsx» в меню проекта."
        ),
    )


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

    # 0c) «⬅ Назад» — у нас одноуровневая навигация (главное меню →
    #     меню проекта), так что «назад» = главное меню.
    if text == PERSISTENT_BACK_TEXT:
        _clear_pending_state(user_id)
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
        # Сохраняем тему в pending — дальше ждём выбора файла-промта.
        _pending_plan_prompt[user_id] = (pending_plan_pid, topic)
        async with session_scope() as s:
            project = (
                await s.execute(
                    select(Project).where(Project.id == pending_plan_pid)
                )
            ).scalar_one_or_none()
            overrides = (
                dict(project.prompt_overrides or {}) if project else {}
            )
        await msg.answer(
            f"Тема: <b>{topic}</b>\n\n"
            + _prompt_picker_text("plan", overrides),
            reply_markup=_prompt_picker_kb(
                pending_plan_pid, "plan", overrides
            ),
            parse_mode="HTML",
        )
        return

    # 3) Если ждём имя нового мастер-промта (после клика «+ Новый» в picker'е)
    pending_name = _pending_prompt_name.get(user_id)
    if pending_name is not None:
        pid_p, step_p = pending_name
        _pending_prompt_name.pop(user_id, None)
        await _handle_prompt_name_input(msg, pid_p, step_p)
        return

    # 4) Иначе — может это ответ на edit-запрос
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


async def _save_hero_brief_and_run(
    msg: Message, project_id: int, hero_idx: int
) -> None:
    """Сохраняет описание героя с индексом `hero_idx` (1..N).

    Если собраны все N описаний — выставляет статус generating_hero и
    воркер на следующем тике запускает generate_hero по очереди.
    Иначе — просит следующее описание."""
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
        all_done = (
            len(descriptions) >= n_total
            and all(d.strip() for d in descriptions[:n_total])
        )
        if all_done:
            project.status = ProjectStatus.generating_hero
        try:
            _sheet_for_project(project).write_general(
                hero_description=descriptions[0] if descriptions else None,
                status=project.status.value,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("hero_description xlsx write failed: {}", e)
        slug = project.slug
    if all_done:
        await msg.answer(
            f"✅ Собраны все описания ({n_total}/{n_total}). "
            f"Запускаю генерацию — воркер подхватит за ~15 сек.\n"
            f"Генерирую по очереди; после каждого героя пришлю карточку на "
            f"одобрение (✅/🔁/❌)."
        )
    else:
        next_idx = hero_idx + 1
        # Значения hero_idx в _pending_hero_brief под юзера уже очищены
        # вызывающим handler'ом — ставим следующий индекс.
        user_id = msg.from_user.id if msg.from_user else 0
        if user_id:
            _pending_hero_brief[user_id] = (project_id, next_idx)
        await msg.answer(
            f"Сохранён герой {hero_idx}/{n_total}.\n\n"
            + _hero_brief_question_text(next_idx, n_total),
            parse_mode="HTML",
        )


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
    prompt_text = prompt_path.read_text(encoding="utf-8").strip()

    full_prompt = (
        f"Тема ролика: {topic}\n\n"
        f"{prompt_text}\n\n"
        "Прикреплённый файл — текущий project.xlsx этого ролика. "
        "Заполни его согласно инструкции выше и пришли мне обратно как "
        ".xlsx (без обрезок и компрессии). Кратким текстом ответь — что "
        "сделал — но главное верни файл."
    )

    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    out_dir = proj_xlsx.parent / "tmp_gpt"
    out_dir.mkdir(parents=True, exist_ok=True)
    downloaded = out_dir / f"plan_{ts}.xlsx"

    await msg.answer(
        f"▶ <b>План</b> (xlsx-flow)\n"
        f"Проект #{project_id} «{topic}»\n"
        f"Промт: <code>{prompt_name}</code>\n\n"
        f"Открываю ChatGPT, прикрепляю xlsx, жду ответ. До 5 минут. "
        f"Не закрывай Chrome.",
        parse_mode="HTML",
    )

    backup: _Path | None = None
    try:
        async with browser_session() as bs:
            gpt = ChatGPTBot(bs)
            await gpt.new_conversation()
            reply = await gpt.ask_with_file(
                full_prompt, proj_xlsx, timeout=600
            )
            logger.info(
                "plan_xlsx: GPT reply len={} (project #{}, prompt={})",
                len(reply or ""),
                project_id,
                prompt_name,
            )
            await gpt.download_attachment_from_last_reply(
                downloaded, timeout=180
            )
    except Exception as e:  # noqa: BLE001
        logger.exception("plan_xlsx failed: {}", e)
        await msg.answer(
            f"❌ ChatGPT вернул ошибку: {e}\n"
            f"project.xlsx не подменён, можно попробовать ещё раз."
        )
        return

    if not downloaded.exists() or downloaded.stat().st_size < 100:
        await msg.answer(
            f"❌ Скачанный файл пустой или повреждён: "
            f"<code>{downloaded}</code>",
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

    # Обновляем статус проекта.
    try:
        async with session_scope() as s:
            project = (
                await s.execute(
                    select(Project).where(Project.id == project_id)
                )
            ).scalar_one_or_none()
            if project is not None:
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

    chat_msg = (
        f"Тема ролика: «{topic}».\n\n"
        f"Прикреплены 2 файла:\n"
        f"  1. {prompt_file.name} — инструкция, что именно делать.\n"
        f"  2. project.xlsx — рабочая таблица ролика (план, структура).\n\n"
        "Сделай всё, что написано в первом файле (инструкция), опираясь на "
        "второй (project.xlsx).\n\n"
        "Пришли результат обычным текстом в чат (можно с переносами "
        "строк). Без маркеров, без .txt-файлов в ответе (если всё же решишь "
        "ответить файлом — работает и этот fallback)."
    )

    await msg.answer(
        f"▶ <b>Закадровый текст</b> (xlsx-flow)\n"
        f"Проект #{project_id} «{topic}»\n"
        f"Промт: <code>{prompt_name}</code>\n\n"
        "Открываю ChatGPT, прикрепляю <code>prompt.txt</code> + "
        "<code>project.xlsx</code>, жду ответ. До 10 минут. Не закрывай Chrome.",
        parse_mode="HTML",
    )

    reply_text = ""
    try:
        async with browser_session() as bs:
            gpt = ChatGPTBot(bs)
            await gpt.new_conversation()
            reply_text = await gpt.ask_with_files(
                chat_msg, [prompt_file, proj_xlsx], timeout=600
            )
            logger.info(
                "script_xlsx: GPT reply len={} (project #{}, prompt={})",
                len(reply_text or ""),
                project_id,
                prompt_name,
            )
            raw = (reply_text or "").strip()
            # 1. Главный путь — inline-ответ в чате.
            if len(raw) >= 200:
                logger.info(
                    "script_xlsx: беру inline-ответ из чата, len={}",
                    len(raw),
                )
                downloaded.write_text(raw, encoding="utf-8")
            else:
                # 2. Fallback: GPT всё-таки приложил .txt файл — пробуем скачать.
                logger.warning(
                    "script_xlsx: inline-ответ короткий (len={}), пробую "
                    "скачать файл из ответа.",
                    len(raw),
                )
                try:
                    await gpt.download_attachment_from_last_reply(
                        downloaded, timeout=60
                    )
                except Exception as e:  # noqa: BLE001
                    raise RuntimeError(
                        f"GPT не вернул закадровый текст: inline-ответ "
                        f"слишком короткий (len={len(raw)}), файл не скачался ({e}). "
                        f"Сырой ответ GPT: {raw!r}"
                    ) from e
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

    # Обновляем статус проекта.
    try:
        async with session_scope() as s:
            project = (
                await s.execute(
                    select(Project).where(Project.id == project_id)
                )
            ).scalar_one_or_none()
            if project is not None:
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
       сообщение.
    2) Ждём ответ, скачиваем txt-файл из ответа GPT (или берём inline).
    3) Старый voiceover.txt бэкапим в old/<ts>_voiceover.txt.
    4) Сохраняем новый txt как data/videos/<slug>/voiceover.txt.
    5) Статус проекта → frames_ready, шлём txt в TG.
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
    downloaded = out_dir / f"split_{ts}.txt"

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

    chat_msg = (
        f"Тема ролика: «{topic}».\n\n"
        f"Прикреплены 3 файла:\n"
        f"  1. {prompt_file.name} — инструкция, что именно делать.\n"
        f"  2. project.xlsx — рабочая таблица ролика (план, структура).\n"
        f"  3. voiceover.txt — закадровый текст, который нужно разбить "
        f"на блоки.\n\n"
        "Сделай всё, что написано в первом файле (инструкция), опираясь "
        "на структуру из project.xlsx и применяя к voiceover.txt.\n\n"
        "Пришли результат обычным текстом в чат (можно с переносами "
        "строк, блоками, нумерацией). Без маркеров, без .txt-файлов в ответе "
        "(если всё же решишь ответить файлом — работает и этот fallback)."
    )

    await msg.answer(
        f"▶ <b>Разбивка на блоки</b> (xlsx-flow)\n"
        f"Проект #{project_id} «{topic}»\n"
        f"Промт: <code>{prompt_name}</code>\n\n"
        "Открываю ChatGPT, прикрепляю <code>prompt.txt</code> + "
        "<code>project.xlsx</code> + <code>voiceover.txt</code>, жду ответ. "
        "До 15 минут. Не закрывай Chrome.",
        parse_mode="HTML",
    )

    reply_text = ""
    try:
        async with browser_session() as bs:
            gpt = ChatGPTBot(bs)
            await gpt.new_conversation()
            reply_text = await gpt.ask_with_files(
                chat_msg,
                [prompt_file, proj_xlsx, voiceover],
                timeout=900,
            )
            logger.info(
                "split_xlsx: GPT reply len={} (project #{}, prompt={})",
                len(reply_text or ""),
                project_id,
                prompt_name,
            )
            raw = (reply_text or "").strip()
            # 1. Главный путь — inline-ответ в чате.
            if len(raw) >= 200:
                logger.info(
                    "split_xlsx: беру inline-ответ из чата, len={}",
                    len(raw),
                )
                downloaded.write_text(raw, encoding="utf-8")
            else:
                # 2. Fallback: GPT всё-таки приложил .txt файл — пробуем скачать.
                logger.warning(
                    "split_xlsx: inline-ответ короткий (len={}), пробую "
                    "скачать файл из ответа.",
                    len(raw),
                )
                try:
                    await gpt.download_attachment_from_last_reply(
                        downloaded, timeout=60
                    )
                except Exception as e:  # noqa: BLE001
                    raise RuntimeError(
                        f"GPT не вернул разбивку: inline-ответ слишком "
                        f"короткий (len={len(raw)}), файл не скачался ({e}). "
                        f"Сырой ответ GPT: {raw!r}"
                    ) from e
    except Exception as e:  # noqa: BLE001
        logger.exception("split_xlsx failed: {}", e)
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
        old_dir = voiceover.parent / "old"
        old_dir.mkdir(parents=True, exist_ok=True)
        backup = old_dir / f"{ts}_voiceover.txt"
        shutil.copy2(voiceover, backup)
        shutil.copy2(downloaded, voiceover)
    except Exception as e:  # noqa: BLE001
        logger.exception("split_xlsx replace failed: {}", e)
        await msg.answer(f"❌ Не смог записать voiceover.txt: {e}")
        return

    # Обновляем статус проекта.
    try:
        async with session_scope() as s:
            project = (
                await s.execute(
                    select(Project).where(Project.id == project_id)
                )
            ).scalar_one_or_none()
            if project is not None:
                project.status = ProjectStatus.frames_ready
    except Exception as e:  # noqa: BLE001
        logger.warning("split_xlsx status update failed: {}", e)

    backup_note = (
        f"\nПредыдущая версия: <code>old/{backup.name}</code>"
        if backup is not None
        else ""
    )
    await msg.answer(
        f"✅ Разбивка готова. voiceover.txt обновлён "
        f"({voiceover.stat().st_size} байт).{backup_note}",
        parse_mode="HTML",
    )
    try:
        await msg.answer_document(
            FSInputFile(str(voiceover)),
            caption=(
                f"voiceover.txt — разбивка на блоки "
                f"(промт «{prompt_name}»)"
            ),
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("split_xlsx send doc failed: {}", e)


async def _create_new_project(msg: Message) -> None:
    """Создаёт новый проект. Вход — только название проекта,
    никаких флагов. v8-шаблон копируется в data/videos/<slug>/project.xlsx."""
    name = (msg.text or "").strip()
    if not name:
        await msg.answer("Пустое название. Нажми «📁 Новый проект» ещё раз.")
        return
    topic = name
    hero_mode = "auto"  # сохраняем в DB по умолчанию, больше не спрашиваем.

    slug_base = (
        re.sub(r"[^a-zа-я0-9]+", "-", topic.lower(), flags=re.IGNORECASE).strip("-")[:40]
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
        f"Проект создан: #{pid} «{topic}»\n\n"
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
                if action == "regen":
                    # Откатываем счётчик одобренных героев на текущей
                    # позиции (просто ставим generating_hero — шаг
                    # перегенерит того же героя).
                    project.status = ProjectStatus.generating_hero
                    regen_step_msg = (
                        "\n\n▶ Запускаю генерацию hero заново "
                        "(с тем же описанием)."
                    )
                elif action == "approve":
                    n_total = project.hero_count or 1
                    approved_idx = (
                        (req.payload or {}).get("hero_index") or 1
                    )
                    if approved_idx < n_total:
                        project.status = ProjectStatus.generating_hero
                        regen_step_msg = (
                            f"\n\n▶ Перехожу к герою "
                            f"{approved_idx + 1}/{n_total}."
                        )
                    # Если approved_idx == n_total — статус остаётся
                    # hero_ready (шаг полностью завершён).
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

async def notify_step_done(
    bot: Bot,
    project_id: int,
    prev_status: str,
    new_status: str,
) -> None:
    """Шлёт в TG уведомление с обновлённым меню проекта после успешного шага.

    Вызывается воркером ПОСЛЕ commit, поэтому всегда читаем актуальное
    состояние проекта из БД. prev_status и new_status передаются явно для
    диагностики.
    """
    logger.info(
        "notify_step_done: project={}, {} → {}",
        project_id,
        prev_status,
        new_status,
    )
    async with session_scope() as s:
        project = (
            await s.execute(select(Project).where(Project.id == project_id))
        ).scalar_one_or_none()
        if project is None:
            logger.warning(
                "notify_step_done: project #{} не найден", project_id
            )
            return
        try:
            await bot.send_message(
                settings.telegram_owner_chat_id,
                (
                    f"✅ Шаг завершён: статус <b>{project.status.value}</b>\n"
                    + project_header(project)
                ),
                parse_mode="HTML",
                reply_markup=project_menu_kb(project),
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
