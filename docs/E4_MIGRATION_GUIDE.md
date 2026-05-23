# Phase E.4 Migration Guide — разбиение `app/telegram/bot.py`

> Для ИИ-агентов и людей, кто берётся за migration steps 3-9 из PLAN.md.

## Контекст

`app/telegram/bot.py` сейчас ~8200 строк. Он содержит почти всё: handler'ы,
keyboards, helpers, утилиты, FSM-state. Цель Phase E — разбить его на
тонкие модули по фичам, чтобы:
- разные ИИ-агенты могли работать параллельно без merge-конфликтов;
- тесты были изолированными;
- любой новый разработчик/агент находил нужное за секунды.

## Готовый фундамент (уже сделано в этом PR)

### 1. `app/telegram/callback_registry.py` — реестр CB Enum

**58 префиксов** всех callback_data. Используй вместо строковых литералов:

```python
from app.telegram.callback_registry import CB

# Вместо
btn = InlineKeyboardButton(text="...", callback_data=f"proj:{pid}:menu")
# Используй
from app.telegram.keyboards import make_callback
btn = InlineKeyboardButton(text="...", callback_data=make_callback(CB.PROJ_MENU, pid, "menu"))
```

### 2. `app/telegram/keyboards/` — типизированные фабрики

| Модуль | Что внутри |
|---|---|
| `common.py` | `make_callback`, `row_back_menu`, `kb_back_to_main`, `kb_yes_no`, `kb_hitl_4buttons`, `kb_session_summary` |
| `main_menu.py` | `kb_main_menu`, `kb_mass_pause_resume` |
| `project_menu.py` | `kb_project_menu`, `kb_project_delete_confirm`, `kb_reset_step_confirm` |
| `hitl_buttons.py` | `kb_hitl_image`, `kb_hitl_video`, `parse_hitl_callback` |

Все фабрики:
- проверяют 64-байтный лимит callback_data на этапе сборки → `ValueError`,
  не молчаливое отрезание Telegram'ом в проде;
- используют только константы из `CB`;
- покрыты тестами (`tests/test_keyboards_common.py`, `test_keyboards_extended.py`).

### 3. `tests/test_all_callbacks_in_cb_registry.py` — AST-инвариант

Сканирует `app/telegram/**` + `app/services/**`, проверяет что **каждый**
`callback_data` использует префикс из `CB`. Если ты добавишь кнопку с новым
префиксом — этот тест упадёт, пока префикс не появится в Enum.

### 4. `app/telegram/handlers/` — новая структура

Сейчас здесь:
- `ai_agent.py` — Phase I (полностью мигрирован на CB + keyboards).
- `debug.py` — Phase G.

В рамках Phase E.4 steps 3-9 сюда переедут:
- `main_menu.py` — `/start`, `/menu`, главное меню.
- `new_project.py` — `/new`, wizard первого запуска.
- `project_navigation.py` — меню проекта, переход между шагами.
- `hitl.py` — HITL-карточки image/video (сейчас в `services/hitl.py` +
  bot.py:7627 `@dp.callback_query(F.data.startswith("hitl:"))`).
- `mass.py` — массовое создание (mass:*).
- `wizard.py` — пятиэтапный wizard настроек.
- `settings.py` — настройки проекта / массовой.
- `test_prompt.py` — `/test` визуальных промтов.
- `visual_lab.py` — `🔬 Visual Lab` (если будет TG-вход).

---

## Алгоритм миграции одного handler (один PR ≤ 400 LOC)

### Шаг 1: Выбери handler-группу

Возьми ОДНУ изолированную группу handler'ов из bot.py. Хорошие первые
кандидаты:
- **`menu:*` callback'и** (5 префиксов: root, new, list, mpause, mresume).
  ~150 LOC в bot.py. Простой state, мало dependencies.
- **`test:*` (визуальные промты)** — 4 префикса, изолирован от пайплайна.
- **`hitl:*` карточки кадров** — клавиатуры уже есть в `keyboards/hitl_buttons.py`.

**Плохие первые кандидаты** (рискованно):
- `proj:*` — много state, переплетается со step state machine.
- `mass:*` — 25 префиксов, активно правят другие агенты (PR #25, #27, #38).
- `wiz:*` — FSM, нестабильный.

### Шаг 2: Создай файл `app/telegram/handlers/<name>.py`

Скелет:

```python
"""Handler группы <name>:* для Telegram-бота.

Phase E.4 step N: вынесено из app/telegram/bot.py.
См. docs/E4_MIGRATION_GUIDE.md.
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message

from app.telegram.callback_registry import CB
from app.telegram.keyboards import (
    kb_main_menu,  # импорт нужных фабрик
    make_callback,
)

router = Router(name="<name>")


@router.callback_query(F.data == CB.MENU_ROOT.value)
async def on_menu_root(callback: CallbackQuery) -> None:
    # ... перенесённая логика ...
    pass
```

### Шаг 3: Перенеси handler'ы из `bot.py`

ОСТОРОЖНО:
- НЕ удаляй из bot.py сразу. Сначала: добавь новый handler в новый файл +
  закомментируй старый в bot.py.
- Aiogram при двойной регистрации использует первый — но `noqa`-комментарий
  важен для людей.
- После manual smoke-теста в Telegram → удалить старый.

### Шаг 4: Замени строковые литералы callback_data на `CB`

Используй существующие фабрики из `keyboards/`. Если нужен новый
паттерн — добавь фабрику в keyboards/, не строй inline.

### Шаг 5: Зарегистрируй router в `bot.py`

В существующем блоке (после ai_agent / debug):

```python
try:
    from app.telegram.handlers.<name> import router as _<name>_router
    dp.include_router(_<name>_router)
except Exception as e:
    from loguru import logger as _logger
    _logger.warning("<name> router не подключён: {}", e)
```

### Шаг 6: Тесты

- `tests/test_<name>_handler.py` — smoke (импорт, router зарегистрирован,
  все handlers в `router.callback_query.handlers`).
- При сложных stateful flows — FSM-тесты как в
  `tests/test_ai_agent_hitl_flow.py` (mocked aiogram).

### Шаг 7: Manual smoke

После merge — обязательно проверь в реальном боте:
1. Запустить нужный flow (callback который ты переносил).
2. Если кнопка не реагирует — двойная регистрация (старый handler в
   bot.py не удалён). Откатить старый, push.

---

## Запреты (из AGENTS.md §13 — «когда останавливаться»)

- Не трогай `app/orchestrator/` без явного плана.
- Не меняй `app/models.py` (БД-схема).
- Не делай миграцию **двух** handler-групп в одном PR.
- Не используй `git push --force` после merge'а в default.
- Не удаляй handler из `bot.py` пока новый не покрыт manual smoke + auto-тестом.

---

## Текущий статус (по группам)

| Группа | callback prefix | LOC в bot.py | Готово к миграции | Сделано |
|---|---|---|---|---|
| **AI-агент** | `ai:*` (7) | 0 (в handlers/) | ✅ Done | Phase I (этот PR) |
| **Debug** | (text only) | 0 | ✅ Done | Phase G (этот PR) |
| **Главное меню** | `menu:*` (5) | ~150 | ✅ keyboards готовы (`kb_main_menu`) | — |
| **HITL картинок** | `hitl:*` (5 actions) | ~300 (+ services/hitl.py) | ✅ keyboards готовы (`kb_hitl_image`, `parse_hitl_callback`) | — |
| **Меню проекта** | `proj:*` (15) | ~600 | ✅ keyboards готовы (`kb_project_menu`) | — |
| **Reset шагов** | `reset_ask:*`, `reset_do:*` | ~200 | ✅ keyboards (`kb_reset_step_confirm`) | — |
| **Step run** | `step_run:*` | ~150 | ⚠️ требует state-machine знание | — |
| **Test prompts** | `test:*` (4) | ~400 | ⚠️ требует анализа `test_prompt_menu.py` | — |
| **Hero/Items** | `hero_*` (4) | ~500 | ⚠️ переплетается с pipeline state | — |
| **Mass create** | `mass:*` (25) | ~1500 | ⚠️ параллельно правят #25/#27/#38 | — |
| **Mass prompts** | `mprm:*`, `prm:*` | ~600 | ⚠️ зависит от mass | — |
| **Wizard** | `wiz:*` | ~400 | ⚠️ FSM, нестабильный | — |
| **Excel prm** | `excel_prm:*`, `pov:*` | ~300 | требует анализа | — |

---

## Полезные команды

```bash
# Проверить что все callback_data в CB:
pytest tests/test_all_callbacks_in_cb_registry.py

# AST-аудит кнопок (long callbacks, dead handlers):
python scripts/audit_buttons.py

# Mypy strict для твоего нового handler'а:
mypy app/telegram/handlers/<name>.py

# Полный sanity перед PR:
ruff check . && mypy app/telegram/handlers/ && pytest -q
```

---

## Контакты

Если запутался — спроси Owner'а через `/ai <твой вопрос>` в боте (когда
AI-агент будет на проде после ротации ключа).

См. также: AGENTS.md, PLAN.md, docs/TRIAGE_2026-05-23.md.
