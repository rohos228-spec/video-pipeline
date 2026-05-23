# Phase E.4 Migration Progress

> Tracker для разбиения `app/telegram/bot.py` (8200 LOC) на тонкие модули.
>
> Обновляется по мере merge'а каждого мини-PR'а. См.
> [`E4_MIGRATION_GUIDE.md`](E4_MIGRATION_GUIDE.md) для алгоритма миграции.

## 📊 Сводка

| Шаг | Описание | Статус |
|---|---|---|
| **Step 1** | `callback_registry.py` (58 CB-префиксов) | ✅ Done (PR #39) |
| **Step 2** | `keyboards/` foundation (common + 4 модуля) | ✅ Done (PR #39) |
| Step 3 | Extract main_menu handlers from `bot.py` / `menu.py` | ❌ TODO |
| Step 4 | Extract HITL handlers (`hitl:*`) | ❌ TODO |
| Step 5 | Extract project_menu handlers (`proj:*`) | ❌ TODO |
| Step 6 | Extract mass-creation handlers (`mass:*`) | ❌ TODO |
| Step 7 | Extract wizard handlers (`wiz:*`) | ❌ TODO |
| Step 8 | Extract test-prompt + visual-lab handlers | ❌ TODO |
| Step 9 | Finalize — bot.py ≤ 200 LOC | ❌ TODO |

## 📦 Foundation (Steps 1-2 — готово)

### app/telegram/callback_registry.py
- **58 CB-префиксов** покрывают 100% callback'ов в репо
  (см. `docs/CALLBACK_INVENTORY.md`).
- AST-инвариант в CI (`tests/test_all_callbacks_in_cb_registry.py`).
- `make_callback(prefix, *parts)` с 64-byte guard.

### app/telegram/keyboards/
| Модуль | Фабрики | Для миграции какой группы |
|---|---|---|
| `common.py` | `make_callback`, `row_back_menu`, `kb_back_to_main`, `kb_yes_no`, `kb_hitl_4buttons`, `kb_session_summary` | универсальные |
| `main_menu.py` | `kb_main_menu`, `kb_mass_pause_resume` | step 3 |
| `project_menu.py` | `kb_project_menu`, `kb_project_delete_confirm`, `kb_reset_step_confirm` | step 5 |
| `hitl_buttons.py` | `kb_hitl_image`, `kb_hitl_video`, `parse_hitl_callback` | step 4 |
| `wizard.py` | `kb_wizard_start`, `kb_wizard_choice`, `kb_wizard_confirm` | step 7 |

### scripts/migrate_callback_to_cb.py
Auto-rewrite: `callback_data="x:y"` → `make_callback(CB.X_Y, ...)`.

Прогон на текущем коде:
```
$ python -m scripts.migrate_callback_to_cb app/telegram --recursive
Total: 6 files, 134 matches (0 TODO).
```

### scripts/cb_inventory.py
Auto-генератор [`CALLBACK_INVENTORY.md`](CALLBACK_INVENTORY.md):
- 58 CB-префиксов · 139 callback_data в коде
- Маркирует dead buttons (нет handler'а), unused CB-константы
- В CI: `--fail-on-unknown` блокирует если новый callback не в CB

## 🛡️ Регрессионные тесты

- `tests/test_all_callbacks_in_cb_registry.py` — каждый callback покрыт CB.
- `tests/test_audit_buttons.py` — нет callback'ов > 64 байт.
- `tests/test_handlers_registered.py` — snapshot dp (защита от удаления).
- `tests/test_hitl_callbacks_consistency.py` — `services/hitl.py` ↔ `kb_hitl_image` совместимы.

## 🚦 Что блокирует steps 3-9 сейчас

| Файл | LOC | Кто правит в OPEN PR'ах | Готов к миграции |
|---|---|---|---|
| `bot.py` | 8 209 | #36, #38, #40, #41 | ⛔ HOLD |
| `menu.py` | 795 | #22, #25, #27, #31, #33 | ⛔ HOLD |
| `mass_menu.py` | 500 | #25, #27, #31 | ⛔ HOLD |
| `wizard.py` | 672 | #25, #27, #31 | ⛔ HOLD |
| `prompt_picker.py` | 253 | #25, #27, #31 | ⛔ HOLD |
| `mass_prompt_picker.py` | 244 | #25, #27, #31 | ⛔ HOLD |
| `test_prompt_menu.py` | 134 | #22, #25, #27, #31 | ⛔ HOLD |

**Все** кандидаты на миграцию активно правят параллельные cursor-агенты.
Любая моя миграция сейчас даст merge-конфликты.

## 🎯 Что нужно сделать перед стартом steps 3-9

1. **Merge или close** PR'ы #22/#25/#27/#31 — они правят те же файлы.
2. После — взять **одну** handler-группу из таблицы выше.
3. Прогнать `python -m scripts.migrate_callback_to_cb <файл> --apply`.
4. Перенести handler'ы в `app/telegram/handlers/<name>.py`.
5. Регистрировать router в `bot.py` через `dp.include_router()`.
6. Тесты + manual smoke в боте.
7. PR ≤ 400 LOC.

См. полный 7-шаговый алгоритм в [`E4_MIGRATION_GUIDE.md`](E4_MIGRATION_GUIDE.md).

## 📈 Метрики до и после

| Метрика | До PR #39 | После PR #39 | После steps 3-9 (цель) |
|---|---|---|---|
| `bot.py` LOC | 7 189 | 8 209 (+canonical merge) | ≤ 200 |
| `handlers/` модулей | 0 | 2 (ai_agent, debug) | ~10 |
| `keyboards/` модулей | 0 | 5 | 5+ |
| Callback prefixes в CB | 0 | 58 | 58+ |
| Тесты | 100 | 353 | 400+ |
| Ruff errors | 112 | 0 | 0 |
| Mypy strict (мои модули) | n/a | 33 модуля clean | весь app/ |

## 🔗 Связанные документы

- [PLAN.md](../../opt/cursor/artifacts/PLAN.md) — изначальный план (живёт в артефактах).
- [AGENTS.md](../AGENTS.md) — правила для ИИ-агентов.
- [HANDOVER.md](../HANDOVER.md) — живой контекст разработки.
- [E4_MIGRATION_GUIDE.md](E4_MIGRATION_GUIDE.md) — алгоритм миграции.
- [TRIAGE_2026-05-23.md](TRIAGE_2026-05-23.md) — что закрыть, что merge.
- [CALLBACK_INVENTORY.md](CALLBACK_INVENTORY.md) — где какой callback.
