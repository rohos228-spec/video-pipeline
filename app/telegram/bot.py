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

from app.db import session_scope
from app.models import (
    Frame,
    HITLDecision,
    HITLKind,
    HITLRequest,
    Project,
    ProjectStatus,
)
from app.settings import settings
from app.storage import ProjectSheet
from app.storage import for_project as _sheet_for_project
from app.telegram.menu import (
    is_step_runnable,
    main_menu_kb,
    project_header,
    project_menu_kb,
    step_by_code,
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


def is_owner(msg: Message) -> bool:
    return msg.from_user is not None and msg.from_user.id == settings.telegram_owner_chat_id


# ---------------------------------------------------------------------------
# /start, /menu — главные команды

@dp.message(CommandStart())
async def cmd_start(msg: Message) -> None:
    if not is_owner(msg):
        return
    await msg.answer(
        "Готов. Команды:\n"
        "  /menu — главное меню (создание/просмотр проектов)\n"
        "  /status — список проектов\n"
        "  /status <id> — детали проекта"
    )


@dp.message(Command("menu"))
async def cmd_menu(msg: Message) -> None:
    if not is_owner(msg):
        return
    _pending_topic_input.pop(msg.from_user.id if msg.from_user else 0, None)
    await msg.answer(
        "Главное меню:\nЧто делаем?",
        reply_markup=main_menu_kb(),
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
    await cb.answer()
    await cb.message.answer("Главное меню:", reply_markup=main_menu_kb())


@dp.callback_query(F.data == "menu:new")
async def on_menu_new(cb: CallbackQuery) -> None:
    if cb.from_user.id != settings.telegram_owner_chat_id:
        await cb.answer("Нет доступа", show_alert=True)
        return
    await cb.answer()
    _pending_topic_input[cb.from_user.id] = True
    await cb.message.answer(
        "Введи тему ролика одним сообщением. "
        "После — создам проект и открою его меню.\n\n"
        "По умолчанию hero_mode=auto (создаём ГГ если он нужен по плану). "
        "Если хочешь явно — добавь `--hero` или `--no-hero`.",
        parse_mode="Markdown",
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
        if not is_step_runnable(step, project.status):
            await cb.answer(
                f"Сначала пройди шаг до {step.requires.value if step.requires else '?'}",
                show_alert=True,
            )
            return
        if project.status is step.running_status:
            await cb.answer("Этот шаг уже выполняется")
            return

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
      1) ввод темы нового проекта (после клика на «📁 Новый проект»)
      2) ответ на сообщение-запрос нового промта (HITL edit)
    """
    if not is_owner(msg):
        return

    # 1) Если ждём тему нового проекта
    user_id = msg.from_user.id if msg.from_user else 0
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

    # 3) Иначе — может это ответ на edit-запрос
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


async def _create_new_project(msg: Message) -> None:
    """Создаёт новый проект из текста сообщения (тема + опц. флаги hero)."""
    raw = (msg.text or "").strip()
    if not raw:
        await msg.answer("Пустая тема. Нажми «📁 Новый проект» ещё раз.")
        return
    # парсим флаг режима героя
    hero_mode = "auto"
    m = re.search(r"(--hero|--no-hero|--auto)\b", raw)
    if m:
        flag = m.group(1)
        hero_mode = {"--hero": "hero", "--no-hero": "no_hero", "--auto": "auto"}[flag]
        raw = (raw[: m.start()] + raw[m.end() :]).strip()
    topic = raw
    if not topic:
        await msg.answer("Тема пустая после удаления флагов.")
        return

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
        f"Проект создан: #{pid} «{topic}» (slug: <code>{slug}</code>)\n"
        f"Режим героя: {hero_mode}\n\n"
        "Сейчас зададу 5 технических вопросов — после них откроется "
        "меню шагов.",
        parse_mode="HTML",
    )
    # Запускаем мастер настроек (5 вопросов). До ответов шаги заблокированы.
    # bot = aiogram.Bot через msg.bot
    await send_wizard_question(msg.bot, msg.chat.id, proj_obj)
    logger.info("new project {} '{}' hero={}", pid, slug, hero_mode)


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
