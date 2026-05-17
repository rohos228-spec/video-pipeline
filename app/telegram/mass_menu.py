"""Telegram-меню «Массовое создание» — клавиатуры и форматирование текста.

Callback-схема:
  mass:list                          → список массовых
  mass:new                           → создать новый (попросить имя)
  mass:open:<bid>                    → открыть массовый
  mass:topics:<bid>                  → меню «Темы»
  mass:add_text:<bid>                → добавить темы текстом (по строке)
  mass:dl_xlsx:<bid>                 → скачать topics.xlsx
  mass:upload_xlsx:<bid>             → загрузить новый topics.xlsx
  mass:progress:<bid>                → таблица прогресса
  mass:settings:<bid>                → меню snapshot настроек
  mass:start:<bid>                   → запустить очередь (running)
  mass:pause:<bid>                   → пауза очереди
  mass:resume:<bid>                  → вернуть в running
  mass:retry_paused:<bid>            → вернуть все paused-подпроекты в очередь
  mass:delete:<bid>                  → подтверждение удаления
  mass:delete_yes:<bid>              → удалить безвозвратно (с папкой)
  mass:delete_keep:<bid>             → удалить, оставив папку
  mass:prod:<bid>                    → меню «Постоянный продукт»
  mass:prod_name:<bid>               → запросить название продукта
  mass:prod_desc:<bid>               → запросить описание продукта
  mass:prod_photo:<bid>              → запросить референс-фото продукта
  mass:prod_clear:<bid>              → удалить постоянный продукт

  mass:sub:<bid>:<pid>               → открыть подпроект (использует
                                       обычное project_menu_kb с возвратом
                                       сюда же)
"""

from __future__ import annotations

import html as _html

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.models import BatchProject, Project, ProjectStatus


def mass_list_kb(batches: list[BatchProject]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text="➕ Создать новый массовый", callback_data="mass:new")],
    ]
    for b in batches:
        label = f"📁 {b.name}"
        rows.append([
            InlineKeyboardButton(text=label, callback_data=f"mass:open:{b.id}")
        ])
    rows.append([InlineKeyboardButton(text="⬅ В главное меню", callback_data="menu:root")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def mass_main_kb(batch: BatchProject, sub_count: int) -> InlineKeyboardMarkup:
    # Кнопка управления очередью меняется по текущему статусу.
    if batch.status.value == "running":
        queue_row = [InlineKeyboardButton(
            text="🛑 Стоп / пауза (прервать шаги)",
            callback_data=f"mass:pause:{batch.id}",
        )]
    elif batch.status.value == "paused":
        queue_row = [InlineKeyboardButton(
            text="▶ Снять с паузы",
            callback_data=f"mass:resume:{batch.id}",
        )]
    else:
        queue_row = [InlineKeyboardButton(
            text="▶ Запустить очередь",
            callback_data=f"mass:start:{batch.id}",
        )]
    prod_btn_text = "📦 Постоянный продукт"
    prod = (batch.meta or {}).get("permanent_product") if batch.meta else None
    if prod and prod.get("name"):
        prod_btn_text = f"📦 Продукт: {_short(prod.get('name'), 18)}"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                text=f"📝 Темы ({sub_count})",
                callback_data=f"mass:topics:{batch.id}",
            )],
            [InlineKeyboardButton(
                text=prod_btn_text,
                callback_data=f"mass:prod:{batch.id}",
            )],
            queue_row,
            [InlineKeyboardButton(
                text="🔄 Вернуть paused в очередь",
                callback_data=f"mass:retry_paused:{batch.id}",
            )],
            [InlineKeyboardButton(
                text="📊 Прогресс",
                callback_data=f"mass:progress:{batch.id}",
            )],
            [InlineKeyboardButton(
                text="⚙ Настройки шаблона",
                callback_data=f"mass:settings:{batch.id}",
            )],
            [InlineKeyboardButton(
                text="🗑 Удалить весь массовый",
                callback_data=f"mass:delete:{batch.id}",
            )],
            [InlineKeyboardButton(
                text="⬅ К списку массовых",
                callback_data="mass:list",
            )],
        ]
    )


def mass_product_kb(batch: BatchProject) -> InlineKeyboardMarkup:
    """Меню постоянного продукта (применяется ко всем роликам массового)."""
    prod = (batch.meta or {}).get("permanent_product") if batch.meta else None
    has_prod = bool(prod and prod.get("name"))
    rows = [
        [InlineKeyboardButton(
            text="✏ Название продукта",
            callback_data=f"mass:prod_name:{batch.id}",
        )],
        [InlineKeyboardButton(
            text="📝 Описание продукта",
            callback_data=f"mass:prod_desc:{batch.id}",
        )],
        [InlineKeyboardButton(
            text="🖼 Прислать референс-фото",
            callback_data=f"mass:prod_photo:{batch.id}",
        )],
    ]
    if has_prod:
        rows.append([InlineKeyboardButton(
            text="🗑 Удалить продукт",
            callback_data=f"mass:prod_clear:{batch.id}",
        )])
    rows.append([InlineKeyboardButton(
        text="⬅ К меню массового",
        callback_data=f"mass:open:{batch.id}",
    )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def product_text(batch: BatchProject) -> str:
    """Текст экрана «Постоянный продукт»."""
    prod = (batch.meta or {}).get("permanent_product") if batch.meta else None
    head = f"<b>📦 Постоянный продукт массового «{_html.escape(batch.name)}»</b>"
    if not prod or not prod.get("name"):
        return (
            head + "\n\nПродукт не задан.\n\n"
            "Постоянный продукт — это товар/предмет, который должен "
            "появляться в каждом ролике массового (например, пенка для "
            "рта или конкретный аксессуар). Бот передаст его в промпт "
            "плана и сценария → GPT учтёт его при генерации.\n\n"
            "Заполни:\n"
            "1) Название (как называть в кадре и сценарии)\n"
            "2) Описание (что это, как выглядит, как использовать)\n"
            "3) Референс-фото (опционально — изображение для художника)"
        )
    name = prod.get("name") or ""
    desc = prod.get("description") or ""
    photo = prod.get("reference_image_path") or ""
    lines = [
        head,
        "",
        f"<b>Название:</b> {_html.escape(name)}",
    ]
    if desc:
        lines.append(f"<b>Описание:</b>\n{_html.escape(_short(desc, 800))}")
    if photo:
        lines.append("<b>Референс:</b> 🖼 загружен")
    else:
        lines.append("<b>Референс:</b> не загружен")
    return "\n".join(lines)


def mass_topics_kb(batch: BatchProject) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                text="📝 Добавить темы текстом",
                callback_data=f"mass:add_text:{batch.id}",
            )],
            [InlineKeyboardButton(
                text="📥 Скачать topics.xlsx",
                callback_data=f"mass:dl_xlsx:{batch.id}",
            )],
            [InlineKeyboardButton(
                text="📤 Залить topics.xlsx",
                callback_data=f"mass:upload_xlsx:{batch.id}",
            )],
            [InlineKeyboardButton(
                text="⬅ К меню массового",
                callback_data=f"mass:open:{batch.id}",
            )],
        ]
    )


def mass_progress_kb(batch: BatchProject, subs: list[Project]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    # До 30 кнопок-ссылок на подпроекты (по 2 в ряду для краткости).
    line: list[InlineKeyboardButton] = []
    for p in subs[:60]:
        icon = _status_icon(p.status)
        label = f"{icon} #{p.batch_position or '?'} {_short(p.topic, 14)}"
        line.append(InlineKeyboardButton(
            text=label,
            callback_data=f"mass:sub:{batch.id}:{p.id}",
        ))
        if len(line) == 2:
            rows.append(line)
            line = []
    if line:
        rows.append(line)
    if len(subs) > 60:
        rows.append([InlineKeyboardButton(
            text=f"… ещё {len(subs) - 60} (открой topics.xlsx)",
            callback_data=f"mass:dl_xlsx:{batch.id}",
        )])

    rows.append([
        InlineKeyboardButton(
            text="🔄 Обновить",
            callback_data=f"mass:progress:{batch.id}",
        ),
        InlineKeyboardButton(
            text="📥 Скачать topics.xlsx",
            callback_data=f"mass:dl_xlsx:{batch.id}",
        ),
    ])
    rows.append([
        InlineKeyboardButton(text="⬅ К меню массового", callback_data=f"mass:open:{batch.id}")
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def mass_delete_confirm_kb(batch: BatchProject) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                text="❌ Удалить полностью (вместе с файлами)",
                callback_data=f"mass:delete_yes:{batch.id}",
            )],
            [InlineKeyboardButton(
                text="📦 Удалить из БД, файлы оставить",
                callback_data=f"mass:delete_keep:{batch.id}",
            )],
            [InlineKeyboardButton(
                text="⬅ Отмена",
                callback_data=f"mass:open:{batch.id}",
            )],
        ]
    )


def mass_settings_kb(batch: BatchProject, ms: dict) -> InlineKeyboardMarkup:
    """(BLOCK B) Полноценное меню «⚙ Настройки массовой» — ряд
    переключателей/инкрементов, сохраняется в batch.settings_snapshot
    ["mass_settings"]. Суб-проекты получают эти значения при
    старте очереди (apply_mass_settings_to_subs).

    Коллбэки:
      mass:setnum:<bid>:<field>:<delta>     # +/- для int-полей
      mass:tog:<bid>:<field>                # bool toggle / auto_review_kinds.<kind>
      mass:setval:<bid>:<field>:<value>     # фиксированное значение
    """
    def _bool_btn(label: str, field: str) -> InlineKeyboardButton:
        on = bool(ms.get(field))
        icon = "✅" if on else "⚪"
        return InlineKeyboardButton(
            text=f"{icon} {label}",
            callback_data=f"mass:tog:{batch.id}:{field}",
        )

    def _int_row(label: str, field: str, *, fmt: str = "{v}") -> list[InlineKeyboardButton]:
        v = int(ms.get(field, 0))
        return [
            InlineKeyboardButton(
                text="−",
                callback_data=f"mass:setnum:{batch.id}:{field}:-1",
            ),
            InlineKeyboardButton(
                text=f"{label}: {fmt.format(v=v)}",
                callback_data="mass:noop",
            ),
            InlineKeyboardButton(
                text="+",
                callback_data=f"mass:setnum:{batch.id}:{field}:+1",
            ),
        ]

    rows: list[list[InlineKeyboardButton]] = [
        [_bool_btn("Auto-mode (GPT вместо кнопок)", "auto_mode")],
        _int_row("Enrich слотов", "enrich_slots_count"),
        _int_row("Hero count", "hero_count"),
        _int_row("Hero variations", "hero_variations"),
        [_bool_btn("Excel-режим персонажей", "excel_hero_enabled")],
        [_bool_btn("BGM включён", "bgm_enabled")],
        _int_row("BGM уровень (%)", "bgm_level"),
        _int_row("Пауза между sub (мин)", "pause_minutes"),
        _int_row(
            "Макс параллельность", "max_parallelism",
        ),
    ]
    # Auto-review kinds — свои toggle'ы.
    kinds_on = set(ms.get("auto_review_kinds") or [])
    rows.append([InlineKeyboardButton(
        text="— GPT проверяет тексты —", callback_data="mass:noop",
    )])
    for kind, label in (
        ("approve_plan", "План (plan)"),
        ("approve_script", "Закадровый текст (script)"),
    ):
        icon = "✅" if kind in kinds_on else "⚪"
        rows.append([InlineKeyboardButton(
            text=f"{icon} {label}",
            callback_data=f"mass:tog:{batch.id}:auto_review_kinds.{kind}",
        )])
    rows.append([InlineKeyboardButton(
        text="— GPT-vision проверяет —", callback_data="mass:noop",
    )])
    for kind, label in (
        ("approve_hero", "Персонажи (hero)"),
        ("approve_images", "Картинки (images)"),
        ("approve_videos", "Видео (videos)"),
        ("approve_final", "Финальное видео (final)"),
    ):
        icon = "✅" if kind in kinds_on else "⚪"
        rows.append([InlineKeyboardButton(
            text=f"{icon} {label}",
            callback_data=f"mass:tog:{batch.id}:auto_review_kinds.{kind}",
        )])
    rows.append([InlineKeyboardButton(
        text="⬅ К меню массового",
        callback_data=f"mass:open:{batch.id}",
    )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def mass_settings_text(batch: BatchProject, ms: dict) -> str:
    """(BLOCK B) Текстовое описание текущих настроек."""
    head = f"<b>⚙ Настройки массовой «{_html.escape(batch.name)}»</b>"
    return (
        f"{head}\n"
        f"\n<b>Режим выполнения:</b>"
        f"\n  Auto-mode: {'ДА' if ms.get('auto_mode') else 'НЕТ (ручные кнопки)'}"
        f"\n  Макс параллельность: {ms.get('max_parallelism')}"
        f"\n  Пауза между sub'ами: {ms.get('pause_minutes')} мин"
        f"\n\n<b>Генерация:</b>"
        f"\n  Enrich слотов: {ms.get('enrich_slots_count')} (1..5)"
        f"\n  Hero count: {ms.get('hero_count')}"
        f"\n  Hero variations: {ms.get('hero_variations')}"
        f"\n  Excel-режим персонажей: {'ДА' if ms.get('excel_hero_enabled') else 'НЕТ'}"
        f"\n\n<b>BGM:</b>"
        f"\n  Включён: {'ДА' if ms.get('bgm_enabled') else 'НЕТ'}"
        f"\n  Уровень: {ms.get('bgm_level')}%"
        f"\n\n<b>GPT-vision включён для:</b>"
        f"\n  {', '.join(ms.get('auto_review_kinds') or []) or '— (всё auto-approve)'}"
        f"\n\n<i>Изменения сохраняются в settings_snapshot и применяются"
        f"\nко всем sub-проектам в статусе 'new' при старте очереди. Суб'ы,"
        f"\nкоторые уже в работе, НЕ перезаписываются.</i>"
    )


def batch_header(batch: BatchProject, sub_count: int, progress: dict) -> str:
    """Текстовый блок-заголовок для меню массового."""
    pct = (
        int(round(100 * (progress.get("done", 0) / sub_count)))
        if sub_count else 0
    )
    queue_icon = {
        "new": "⏳ не запущена",
        "running": "▶ работает",
        "paused": "⏸ пауза",
        "done": "✅ завершена",
    }.get(batch.status.value, batch.status.value)
    return (
        f"<b>📁 {_html.escape(batch.name)}</b>\n"
        f"slug: <code>{_html.escape(batch.slug)}</code>\n"
        f"очередь: {queue_icon}\n"
        f"подпроектов: {sub_count}\n"
        f"готовы: {progress.get('done', 0)} ({pct}%) · "
        f"в работе: {progress.get('in_progress', 0)} · "
        f"в очереди: {progress.get('queued', 0)} · "
        f"paused: {progress.get('paused', 0)} · "
        f"failed: {progress.get('failed', 0)}"
    )


def progress_text(batch: BatchProject, subs: list[Project], progress: dict) -> str:
    """Полный текст экрана прогресса с компактной таблицей."""
    head = batch_header(batch, len(subs), progress)
    if not subs:
        return (
            head
            + "\n\nТем ещё нет. Жми «📝 Темы» → «Добавить темы текстом»."
        )
    lines = ["", "<pre>", " №   Шаг                          Стат"]
    lines.append("──   ───────────────────────────  ────")
    for p in subs[:60]:
        icon = _status_icon(p.status)
        step_label = _step_label(p.status)
        lines.append(
            f"{(p.batch_position or 0):>3}  {step_label:<27}  {icon}"
        )
    if len(subs) > 60:
        lines.append(f"... ещё {len(subs) - 60} подпроектов (см. topics.xlsx)")
    lines.append("</pre>")
    return head + "\n" + "\n".join(lines)


def topics_text(batch: BatchProject, subs: list[Project]) -> str:
    """Текст экрана со списком тем."""
    head = (
        f"<b>📝 Темы массового «{_html.escape(batch.name)}»</b>\n"
        f"всего: {len(subs)}"
    )
    if not subs:
        return (
            head
            + "\n\nТем пока нет.\n\n"
            "Способы добавить:\n"
            "1) «📝 Добавить темы текстом» — пишешь темы по одной на строку\n"
            "2) «📥 Скачать topics.xlsx» → заполнить → «📤 Залить topics.xlsx»"
        )
    listing = []
    for p in subs[:30]:
        icon = _status_icon(p.status)
        listing.append(
            f"{icon} #{p.batch_position or '?'}. {_html.escape(_short(p.topic, 60))}"
        )
    if len(subs) > 30:
        listing.append(f"… ещё {len(subs) - 30} (открой xlsx)")
    return head + "\n\n" + "\n".join(listing)


def _short(text: str | None, n: int) -> str:
    text = text or ""
    if len(text) <= n:
        return text
    return text[: n - 1] + "…"


def _status_icon(status: ProjectStatus) -> str:
    if status is ProjectStatus.published:
        return "✅"
    if status is ProjectStatus.paused:
        return "⏸"
    if status is ProjectStatus.failed:
        return "❌"
    if status is ProjectStatus.new:
        return "⏳"
    if status.value.endswith("_ready"):
        return "🟡"
    return "🔄"


def _step_label(status: ProjectStatus) -> str:
    """Короткое описание текущего шага для таблицы."""
    s = status.value
    mapping = {
        "new": "ожидает",
        "planning": "1. план (идёт)",
        "plan_ready": "1. план готов",
        "scripting": "2. сценарий",
        "script_ready": "2. сценарий готов",
        "splitting": "3. разбивка",
        "frames_ready": "3. разбивка готова",
        "generating_hero": "4. персонажи",
        "hero_ready": "4. персонажи готовы",
        "generating_items": "4. предметы",
        "items_ready": "4. предметы готовы",
        "enriching_1": "5. EXCEL #1",
        "enrich_1_ready": "5. EXCEL #1 готов",
        "enriching_2": "5. EXCEL #2",
        "enrich_2_ready": "5. EXCEL #2 готов",
        "enriching_3": "5. EXCEL #3",
        "enrich_3_ready": "5. EXCEL #3 готов",
        "enriching_4": "5. EXCEL #4",
        "enrich_4_ready": "5. EXCEL #4 готов",
        "enriching_5": "5. EXCEL #5",
        "enrich_5_ready": "5. EXCEL #5 готов",
        "generating_image_prompts": "6. промты картинок",
        "image_prompts_ready": "6. промты готовы",
        "generating_images": "7. картинки",
        "images_ready": "7. картинки готовы",
        "generating_animation_prompts": "8. промты анимации",
        "animation_prompts_ready": "8. промты анимации готовы",
        "generating_videos": "9. видео",
        "videos_ready": "9. видео готовы",
        "generating_audio": "10. аудио",
        "audio_ready": "10. аудио готово",
        "assembling": "11. сборка",
        "assembled": "11. собрано",
        "publishing": "публикация",
        "published": "опубликовано",
        "paused": "пауза",
        "failed": "ошибка",
    }
    return mapping.get(s, s)
