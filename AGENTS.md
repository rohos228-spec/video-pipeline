# AGENTS.md — правила для ИИ-агентов в этом репо

Документ — для **любого ИИ-агента** (Devin, Cursor BG, Codex, ChatGPT, наш
встроенный `/ai`-агент в Telegram), который правит этот код. Прочитай это
**перед** тем как трогать файлы.

---

## 1. Канонная ветка

**`vetka-final`** (после Phase 0).

> Промежуточное состояние (до rename): канонной считается
> `devin/1779156871-combine-A-and-C-physical-clicks`.
> Старый default `devin/windows-installer` будет переименован в
> `legacy/windows-installer-pre-2026-05-22` и зафризится.

- **PR'ы — только** в `vetka-final`.
- В `main` и `legacy/*` — **не коммитить**.
- `cursor/full-implementation` — текущий feature branch (агент-полу-автомат),
  PR из него → в `vetka-final` (или временно в `devin/1779156871-...`).

---

## 2. Naming веток

| Префикс | Когда |
|---|---|
| `feat/<scope>-<slug>` | Новая фича |
| `fix/<scope>-<slug>` | Багфикс |
| `chore/<scope>-<slug>` | Рефакторинг, типы, тесты, доки без поведения |
| `agent/<model>/<task-id>` | Экспериментальные ветки ИИ-агентов |
| `cursor/<descriptive>` | Cursor agent sessions (автоматически создаются) |

**Запреты**:
- `cursor/audit-*` без issue на доске — мы их закрываем массово.
- `devin/<timestamp>-*` без issue — то же.
- Креатив типа `feature_v2_fix_final_FINAL` — нет.

---

## 3. Размер и состав PR

- **Целевой размер**: ≤ 400 строк диффа (`git diff --stat`).
- **Один PR = одна задача.** Никаких «mass-gen + 403 fix + audit» в одном PR.
- Если задача большая — разбить на серию мини-PR'ов и связать через
  «Stacked PRs» (`gh pr create --base <previous-pr-branch>`).

---

## 4. Запреты (нарушение = revert)

1. **Никакого UI.Vision** — только Playwright/CDP.
2. **Никаких бесконечных retry-циклов.** `MAX_FAIL=3` в `app/main.py`.
3. **На 🔁 в HITL героя/кадра — НЕ дёргать ChatGPT повторно**, только
   `outsee.regenerate_image`.
4. **Не коммитить в `legacy/*` и `main`.**
5. **Не публиковать в соцсети** (`SOCIAL_PUBLISH_ENABLED=false`).
6. **Не хранить креды в репо.** SOCKS5, TG-токен, AITunnel-ключ —
   **только** в `.env` (gitignored).
7. **Не делать `git push --force`** без явного разрешения владельца.
8. **Не удалять файлы и не делать `git reset --hard`** без HITL-апрува.

---

## 5. Команды для проверки

Перед открытием PR — **локально**:

```bash
pip install -e .[dev]
ruff check .
ruff format --check .
mypy app
pytest -q
python -c "import app.main; import app.telegram.bot; \
           import app.orchestrator.pipeline; import app.orchestrator_api; \
           import app.ai_agent; print('imports ok')"
```

Все 5 шагов должны быть зелёные.

---

## 6. Куда смотреть в первую очередь

| Файл / папка | Зачем |
|---|---|
| `HANDOVER.md` | Живой контекст: что работает, что нет, тонкости |
| `HOW_TO_RUN.md` | Запуск на машине пользователя |
| `app/orchestrator/pipeline.py` | State machine конвейера |
| `app/telegram/bot.py` | TG-бот (в процессе разбиения, фаза E) |
| `app/bots/outsee.py` | Автоматизация outsee.io — самое нежное место |
| `app/models.py` | Схема БД (SQLAlchemy 2) |
| `app/orchestrator_api.py` | Локальный HTTP-API (127.0.0.1:8787) |
| `app/ai_agent/` | AI-агент в TG (Phase I) |
| `prompts/` | Мастер-промпты — НЕ ТРОГАТЬ без отдельного запроса |

---

## 7. Сценарий «как добавить кнопку»

1. Найди префикс в `app/telegram/callback_registry.py`. Если нет — добавь
   новую константу в `CB`.
2. Используй `CB.X.value` в `InlineKeyboardButton(callback_data=...)`.
3. Зарегистрируй handler: `@router.callback_query(F.data.startswith(CB.X))`.
4. У клавиатуры обязательно есть «Назад» и «В меню», если это экран.
5. Тест `tests/test_callback_registry.py` должен проходить.
6. Если callback > 64 байт — укоротить префикс.

---

## 8. Сценарий «как добавить шаг пайплайна»

1. Создай `app/orchestrator/steps/<name>.py` с `async def run(project, ...)`.
2. Зарегистрируй в `app/orchestrator/pipeline.py` (state machine).
3. Если шаг имеет HITL — добавь карточку в `app/services/hitl.py`.
4. Шаг должен быть **идемпотентным**: повторный запуск с теми же входами
   не должен ломать БД.
5. Опиши шаг в `docs/PIPELINE.md`.
6. Smoke-тест: на mock-данных шаг проходит за ≤ 30 секунд.

---

## 9. Сценарий «как добавить endpoint в `orchestrator_api.py`»

1. Pydantic `BaseModel` для request и response.
2. `@app.post("/...")` / `@app.get("/...")` с `response_model=`.
3. Биндинг **только** на `127.0.0.1` (никогда `0.0.0.0`).
4. Никаких `eval`, `exec`, динамического SQL.
5. Тест в `tests/test_orchestrator_api.py`.

---

## 10. Сценарий «как добавить tool для AI-агента»

См. `app/ai_agent/tools/__init__.py` — там реестр.

1. Создай файл `app/ai_agent/tools/<name>.py`.
2. Объяви функцию + JSON-schema (`TOOL_SPEC: dict`).
3. Опасные tools (правка файлов, git_commit, gh_pr_create) **обязательно**
   проходят через HITL-апрув. См. `app/ai_agent/loop.py`.
4. `tools/__init__.py` — зарегистрируй в `ALL_TOOLS`.
5. Тест в `tests/test_ai_agent_tools.py`.

---

## 11. Lock-матрица (текущее состояние)

Если работаешь над модулем — обнови эту таблицу PR'ом в `AGENTS.md`.
Один модуль — один агент в момент времени.

| Модуль | Кто правит | Issue/PR |
|---|---|---|
| `app/telegram/bot.py` | (свободен) | — |
| `app/bots/outsee.py` | (свободен) | — |
| `app/orchestrator_api.py` | (свободен) | — |
| `app/ai_agent/*` | Cursor agent (full-impl) | (этот PR) |
| `app/services/visual_lab/*` | (свободен) | — |
| `app/storage/batch_sheet.py` | (свободен) | — |

---

## 12. Hand-off протокол

Когда задача переходит между агентами:
1. Текущий агент пишет в issue **итоговый комментарий**:
   - Сделано: X.
   - Не сделано: Y (почему).
   - Известные проблемы: Z.
   - Ветка: `feat/...`.
2. Прикладывает скрин / лог.
3. Меняет assignee + label `needs-review`.
4. Следующий агент **обязан** прочитать комментарии issue +
   `HANDOVER.md` перед стартом.

---

## 13. «Светофор» — когда останавливаться и спрашивать

Агент **обязан остановиться и спросить владельца**, если:
- PR трогает > 600 LOC.
- Удаляется тест.
- Меняется `app/models.py` (схема БД).
- Появляется Alembic-миграция.
- Меняется внешний API outsee / ChatGPT / orchestrator_api.
- Затрагиваются `prompts/*`.
- Меняется default-ветка или branch protection.

---

## 14. Безопасность

- Утечка кредов в коммит / чат — **критическая ошибка**. После — ротация
  у провайдера, не «коммит revert».
- `detect-secrets` в pre-commit обязателен.
- Известные паттерны для блокировки в pre-commit:
  - `sk-[a-zA-Z0-9]{20,}` (OpenAI/AITunnel),
  - `socks5://[^@]+:[^@]+@`,
  - `Bearer [A-Za-z0-9+/=]{30,}`.
- При обнаружении утечки **в чате** (например, токен пользователь скинул
  в `/ai`) — агент **не должен** записывать его в файл или коммит, должен
  предупредить владельца «токен скомпрометирован, ротируй».

---

## 15. Стиль коммитов

`<тип>(<scope>): <короткое описание>`

Где:
- `<тип>` ∈ `feat | fix | chore | docs | test | refactor`.
- `<scope>` ∈ `ai | telegram | bots | orchestrator | api | tests | docs`.

Тело коммита (опционально) — что и зачем, не «как».
Footer — `Closes #N`, `BREAKING CHANGE:` (если меняет публичный интерфейс).

---

## 16. Запуск AI-агента в Telegram

Если задача — поручить правку через `/ai <запрос>` в боте:
- Сессии длятся до 30 шагов или 200k токенов.
- На правку файла — **обязательный** HITL-апрув (✅/🔁/✏️/❌).
- Никакого shell-tool — только whitelist'нутые `run_pytest`/`run_ruff`/etc.
- Запрещены пути: `.env*`, `data/state.db*`, `.git/**`, `.venv/**`,
  `__pycache__/**`, `data/videos/**` (только read, не write).

См. подробности: `app/ai_agent/README.md` (создаётся в Phase I).

---

## 17. История изменений

- **2026-05-22** — создание документа (Phase A). Канон `vetka-final`,
  AI-агент через aitunnel.ru + `gpt-4o-mini`.
