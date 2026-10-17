# docs/fable/PLAN.md — AI-оркестратор студии (веб, main)

Автор: Фейбл. Дата: 2026-10-17. Статус: к исполнению. **Ревизия 3** (устранены все замечания ревью: P0.1–P0.4 — владелец механизма D3, LLM-replay в CI, измеримый E1, единый источник логики длин; P1.5–P1.11 — арифметика WS3, семантика ack, честный kill switch, владелец projects.py, полный список действий со схемами, решение D6 по правилу 2, реалистичный R2; P2.12–P2.19 — авторизация, раздача отчётов, retention, суб-бюджет spend, гипотезы, анти-флейк, освобождение :8766, терминология дней).

База: Action Layer из PR #342 (`orchestrator-observe-v1`, sha `3ec7c95`) как фундамент безопасности; поверх — слой диалога, мультиагентности и автономии. Дизайн веба (`web/src`) сохраняется, 12 правил пользователя — инварианты.

**Терминология времени:** «Этап 0» (R0a/R0b) исполняют WS1 и WS2 — это их первые рабочие дни. «Д+n» = n-й рабочий день **после merge R0b**. Все «день 1» в потоках = Д+1.

---

## 1. Цель, рамки, зафиксированные решения

Встроить в веб-версию (Next.js + FastAPI + SQLite) автономного AI-оркестратора:

- живой чат со стримингом и приёмом сообщений посреди задачи (interrupt / steer / append);
- супервизор + субагенты (planner, executor, critic), видимые статусы задач;
- автономные многошаговые задачи с чекпоинтами и resume;
- управление пайплайном **только** через Action Layer PR #342 (approvals, spend limits $200/$100/$50, kill switch, paid-retry guard, change_control);
- маршрутизация fast/strong, RAG, evals.

**Вне рамок:** редактирование кода студии оркестратором (правило 8), изменение дизайна `web/src`, Telegram, multi-worker деплой.

### Зафиксированные решения

| # | Решение |
|---|---|
| D1 | **Правило 4** («модель — 52pay gpt-5.6-sol») относится к **нодам пайплайна**. Для внутренних ходов оркестратора — роутинг fast/strong (strong: claude-fable-5 → gpt-5.6-sol). Исключение **подтверждается пользователем на kick-off**; до подтверждения strong-primary = gpt-5.6-sol high. |
| D2 | **Деплой: ровно 1 uvicorn-worker** для процесса оркестратора. `asyncio.Queue`, cancel-события и SSE-шина — внутрипроцессные; восстановление после рестарта — через `orch_events` + чекпоинты. Зафиксировано в `docs/fable/deploy_constraints.md`. |
| D3 | **Ноды, созданные/изменённые оркестратором, живут в БД** (`node_defs`, `node_prompt_versions`), git-шаблоны `templates/node_groups/**` в рантайме read-only (baseline). **Исполнитель механизма — WS2** через новый модуль `app/orchestrator/node_store/` + ограниченные патчи точек загрузки в `app/services/` (явное исключение из freeze, см. 3.6). Экспорт в репо — действием `export_nodes_snapshot` (отчёт-патч, коммитит человек). Правило 9: код — `read_pipeline_code`, ноды — `get_node` (читает через node_store). |
| D4 | LLM-вызовы оркестратора (planner/critic/classifier) **учитываются в spend limits** через ledger. **Суб-бюджет внутри $50/run: $10 — на внутренние LLM-ходы оркестратора, $40 — на действия пайплайна.** Исчерпание $10 → эскалация пользователю (approval на добавку), а не молчаливый fail посреди платной генерации. |
| D5 | Все миграции БД оркестратора — в **едином каталоге** `app/orchestrator/db/migrations/` (владелец WS3). Другие потоки подают спецификации таблиц через `contracts.py`; миграции пишет и нумерует только WS3. |
| D6 | **Правило 2** (`reasoning_effort=high`) относится к **нодам пайплайна и strong-tier оркестратора**. Fast-tier (классификация, суммаризация, короткие ответы) работает без high — это исключение **подтверждается пользователем на kick-off вместе с D1**. До подтверждения все ходы оркестратора идут strong high (ценой латентности fast-сценариев). |
| D7 | **CI-evals не делают ни одного реального LLM-вызова и ни одного реального платного действия.** Два уровня моков: `MockActionBackend` (действия) + `ReplayRouter` / `ScriptedRouter` (LLM, см. 7.3). Честно фиксируем: контур A — это регрессионные replay-тесты поведения системы, **не** оценка качества модели; качество модели меряется только в контуре B на реальных вызовах. |

---

## 2. Этап 0 — базовая кодовая линия в main (блокирующий)

Тройной узел housepc ↔ main ↔ PR #342 разбирается **двумя последовательными PR**. Исполнители: R0a — WS1, R0b — WS2 (это и есть старт их работы; остальные потоки в Этап 0 готовят только контракты, моки и датасеты).

### 2.1 Этап 0a: housepc → main. Длительность: **1.5–2 рабочих дня (12–16 ч)**. Исполнитель WS1

1. **Инвентаризация (2 ч).** `git status` / `git diff --stat` в `housepc` (HEAD `6e07b44b`). Зафиксировать ~30 файлов (`app/services/*`, `app/orchestrator/*`, `app/web/routers/*`, `templates/node_groups/script_frames_qc/*`). Бэкапы `.bak_before_fullvo_20261011` → `.gitignore`. Отдельно пометить изменения в `app/orchestrator/*` — конфликтная зона с #342.
2. **Логические коммиты (2 ч):** (a) full-VO блок в `scene_skeleton_batches.py`; (b) анти-дубликация `xlsx_v8_import.py` + `xlsx_sync.py`; (c) `scene_shot_grammar.py` (merge_trivial_shots / spread_vo_sentences / ensure_wide_before_dialogue) + подключение в `apply_ops_batches.py` (`SHOTS_PARALLEL=16`, `ACTION_STAGGER_SEC=4`); (d) промпт-правила R-NO-OVERSPLIT / R-VO-SPREAD; (e) фикс гонки стоп-ключа `projects.py:687`; (f) housepc-правки `app/orchestrator/*` — отдельным коммитом с описанием каждого файла.
3. **Rebase на main `c53a238` (2 ч, буфер на конфликты).** Конфликты пайплайн-файлов — в пользу housepc.
4. **Тесты (4–5 ч).** Полный `pytest` + 4 регрессионных теста: анти-дубликация при повторном импорте (баг 747 кадров); full-VO блок в b_user; вызов merge/spread/wide из `shot_vo_fill`; запись only_node до старта шага. Playwright smoke — без регрессий.
5. **Валидатор длин закадра (2 ч, в этом же PR) — единый источник истины.** Логика проверки 200–300 симв. пишется **сразу** в `app/orchestrator/validators/vo_length.py` (чистая функция `check_vo_lengths(fragments) -> [Violation]`, без зависимостей от агентного ядра). `app/services/scene_boundaries.py` получает тонкий вызов этой функции — правка делается **в R0a, до freeze**. После freeze проверка длин нигде не дублируется: и пайплайн, и валидатор оркестратора зовут одну функцию. Фикс самих данных проекта 65 (2 фрагмента >300, 10 <200) — отдельной задачей WS1 до R5 (см. 7.3).
6. **PR → ревью → merge → тег `pipeline-base-v1` (2 ч).**

**Приёмка 0a:** `git diff housepc main -- app/ templates/` пуст (кроме бэкапов); CI зелёный; повторный импорт проекта 65 не создаёт дублей; `check_vo_lengths` вызывается из `scene_boundaries.py` и покрыт тестом.

### 2.2 Этап 0b: PR #342 → main + аудит действий. Длительность: **2 рабочих дня (14–16 ч)**. Исполнитель WS2

1. **Rebase `orchestrator-observe-v1` на свежий main (3–4 ч).** Конфликтная зона — `app/orchestrator/*`. Правило: **инфраструктура Action Layer (реестр, approvals, spend, change_control) — в пользу #342; логика пайплайна и вызовы сервисов — в пользу housepc/main.** Каждый конфликтный файл — ручное ревью Фейбла.
2. **Аудит 102 действий (6 ч, результат — `docs/fable/actions_audit.md`).** Критерий «exec-подобности»: действие (а) пишет в ФС вне `data/reports/` и `data/node_overrides/`, (б) вызывает subprocess/eval/compile, (в) делает сетевые вызовы кроме LLM-провайдеров и внутреннего API. Такие действия **физически удаляются** или переводятся в read-only. Итог — таблица: действие / риск / вердикт (keep / modify / remove).
3. **Инвентаризация точек загрузки шаблонов нод (2 ч, для D3).** Найти все места в `app/services/`, читающие `templates/node_groups/**` и `prompts/templates` (ожидаемо 2–4 call-site: `apply_ops_batches.py`, `scene_skeleton_batches.py`, возможный общий prompt-loader). Список фиксируется в `actions_audit.md` §node_store — это **закрытый перечень исключений из freeze** для патчей WS2 в R2 (см. 3.6).
4. **Smoke на :8766 с копией DATA_DIR (2 ч):** approvals, spend limits, kill switch, paid-retry guard, change_control работают после rebase.
5. **Тест `test_whitelist_no_exec.py` (1 ч):** assert отсутствия в реестре `edit_files`, `run_tests`, `exec`, `shell`, `write_file`, subprocess-обёрток.
6. **PR → ревью → merge → тег `action-layer-v1` (2 ч).**

**Приёмка 0b:** main содержит Action Layer; аудит-таблица подписана; перечень node_store-call-sites зафиксирован; `test_whitelist_no_exec` в CI; smoke зелёный.

**Итого Этап 0: 3.5–4 рабочих дня.** До merge 0b код оркестратора в main не идёт — только контракты, моки, датасеты, baseline-скриншоты.

---

## 3. Архитектура

### 3.1 Общая схема

```
Next.js (web/src/app/orchestrator/*)
   │ SSE  GET  /api/orchestrator/chat/{session}/stream     [Bearer ORCH_API_TOKEN]
   │ REST POST /api/orchestrator/chat/{session}/message    (в т.ч. посреди задачи)
   ▼
FastAPI: app/web/routers/orchestrator_chat.py     [auth middleware; 1 uvicorn-worker, D2]
   ▼
Supervisor (app/orchestrator/agents/supervisor.py)
   ├── MessageQueue (interrupt / steer / append)
   ├── Planner ──► Validators(dry-run) ──► Critic ──► (revise ≤2) ──► план
   ├── Executor(s): шаги плана → ТОЛЬКО Action Layer (whitelist)
   │       └── approvals / spend / kill switch / paid-retry / change_control (PR #342 as-is)
   ├── Checkpoints (orch_tasks, orch_steps, orch_checkpoints)
   └── Validators (до/после мутирующих шагов)
RAG (app/orchestrator/rag/*) · Router (app/orchestrator/routing/*)
Node Store (app/orchestrator/node_store/*): templates/ (git, RO) + node_defs/node_prompt_versions (БД)
```

### 3.2 Авторизация и живой чат

- **Доступ:** все эндпоинты `/api/orchestrator/**` закрыты middleware `app/orchestrator/auth.py` (владелец WS3): Bearer-токен из env `ORCH_API_TOKEN`, сравнение constant-time. UI вводит токен один раз (настройки, localStorage). Мутации, kill switch, `PUT` отчётов — без валидного токена 401. Тест на 401 — в e2e и E8.
- **Транспорт:** SSE через `sse-starlette`; reconnect по `Last-Event-ID`. События: `ack`, `token`, `agent_status`, `step_started/finished`, `approval_required`, `checkpoint`, `validator_result`, `task_done/failed`, `classification`.
- **Хранение и retention (`orch_events`):** **токен-события не персистятся** — они живут в кольцевом буфере в памяти (1000 событий на сессию) и доступны для reconnect-replay только в рамках живого процесса. В БД пишутся укрупнённые события (сообщение целиком, шаги, чекпоинты, approvals). Компакция: ночной job удаляет события старше 14 дней, кроме чекпоинтов и approvals; жёсткий кап 20 000 событий/сессия. Replay после рестарта процесса — с последнего чекпоинта, не с токена.

### 3.3 Очередь сообщений, interrupt / steer — единая семантика

**Ack и классификация разведены:** на любое входящее сообщение немедленно (≤ 2 с) уходит событие `ack` = «принял, разбираю» — **до** классификации. Классификация: сначала детерминированные правила (стоп-слова «стой/отмена», вопросительная форма без упоминания задачи и т.п.); спорное — fast-модель. Результат — отдельное событие `classification` (p95 ≤ 5 с с учётом SLA fast-модели). Доля детерминированных классификаций — **гипотеза ~80%**, меряется в E6 как отчётная метрика (без порога в первом релизе). Задача не блокируется на время классификации.

| Режим | Поведение | Измеримый критерий |
|---|---|---|
| `interrupt` | ack ≤ 2 с; после классификации взводится cancel-флаг; **неплатные** шаги (в т.ч. ожидание LLM) отменяются через `asyncio.wait`+cancel; **платные** (run_node и пр.) не рвутся mid-flight — пауза на границе шага | ack ≤ 2 с; событие `classification` ≤ 5 с (p95); отмена неплатного шага ≤ 10 с от классификации; пауза не позже границы текущего шага; 0 повторных оплат (paid-retry guard) |
| `steer` | Сообщение в контекст планера; после текущего шага — replan от последнего чекпоинта | replan стартует не позже границы шага; результаты до чекпоинта сохранены |
| `append` | Параллельный ответ fast-моделью, задача не трогается | ответ не меняет план |

**Kill switch (честная семантика):** по нажатию ≤ 2 с — отмена всех неплатных шагов, запрет старта любых новых действий, взвод стоп-ключа only_node для бегущей ноды. **Платный шаг останавливается на ближайшей границе, которую даёт механизм стоп-ключа** (mid-flight разрыв платной генерации невозможен и не обещается). Всё фиксируется в change_control. Реальный kill реального `run_node` тестируется в **контуре B** (см. 7.3, E8-B).

### 3.4 Супервизор и субагенты

- **Supervisor** — FSM: `queued → planning → critic_review → executing → paused|waiting_approval → done|failed|cancelled`. Единственная точка записи статусов.
- **Planner** — план = список шагов (whitelist-действие + параметры + ожидаемый результат); использует Vibecode-каталог и граф `app/orchestrator/graph/planner.py`.
- **Executor** — строго через `actions.execute`; параллельно только независимые read-only шаги, мутирующие — последовательно.
- **Critic** — ревью плана и результата: правила пользователя, прогноз spend, отсутствие удалений. Вердикты `approve | revise | reject`, ≤2 revise, затем эскалация.
- **Фреймворк:** собственный тонкий слой поверх `asyncio` (оценка **1200–1500 строк**: FSM, очередь, чекпоинты, шина). Без LangGraph/CrewAI: Action Layer уже даёт исполнение и безопасность, вторая модель состояния конфликтовала бы с kill switch.

### 3.5 Чекпоинты и автономия

- Чекпоинт после каждого мутирующего шага: `{план, позиция, change_id'ы, spend, краткий контекст}` → `orch_checkpoints`. Resume после interrupt / рестарта / kill switch; replan при steer — от чекпоинта.
- Вопросы пользователю — только `approval_required` или reject критика (правило 12).
- Правило 9: перед `run_node` план обязан содержать шаг `verify_code_state` = `read_pipeline_code` (git-код) + `get_node` (рантайм-дефиниция через node_store); `plan_sanity` проверяет принудительно.

### 3.6 Node Store — механизм D3 (закрывает P0.1)

**Владелец: WS2.** Новый модуль `app/orchestrator/node_store/`:

- `resolver.py`: `resolve_node(node_key) -> NodeDef{prompt_template, params, source: "git"|"db", version}` — читает git-baseline, накладывает DB-override из `node_defs`/`node_prompt_versions` (последняя активная версия).
- `writer.py`: единственная точка записи для `create_node` / `edit_node_prompt` / `edit_node_params` (транзакционно, с версией и change_id).
- **Интеграция с пайплайном:** прямые чтения `templates/node_groups/**` в `app/services/` заменяются вызовом `node_store.resolve_node()`. Перечень call-site'ов — закрытый список из аудита 0b (§node_store, ожидаемо 2–4 файла: `apply_ops_batches.py`, `scene_skeleton_batches.py`, prompt-loader). Эти патчи — **явное, поимённое исключение из freeze `app/services/`**: делает WS2 в R2, ревью — WS1 (как владелец логики пайплайна), каждый патч ≤ 20 строк (замена чтения файла на вызов + import). Любая правка сверх списка — запрещена.
- **Гарантия:** после R2 нода, созданная оркестратором в БД, реально исполняется `run_node` — это проверяет E2 (мок) и контур B.
- Миграции `node_defs`/`node_prompt_versions` пишет WS3 по спецификации WS2 — **milestone: готовы к Д+2** (чтобы не блокировать R2; зависимость R2→WS3 зафиксирована в плане R1).

---

## 4. Маршрутизация моделей fast/strong

Модуль `app/orchestrator/routing/model_router.py`, конфиг `pay52_routes.yaml`.

| Tier | Основная | Фолбэк 1 | Фолбэк 2 | Применение |
|---|---|---|---|---|
| **fast** | `glm-5.3-flash` (52pay) | `claude-haiku` (52pay) | strong-tier | классификация interrupt/steer/append, короткие ответы, суммаризация, извлечение параметров. Работает без `reasoning_effort=high` — **после подтверждения D6**; до него fast-tier отключён, всё идёт strong |
| **strong** | `claude-fable-5` (после подтверждения D1; до него — gpt-5.6-sol) | `gpt-5.6-sol` (`reasoning_effort=high`, `PAY52_GPT_API_KEY`) | Kie | планы, critic, replan, генерация промптов нод, отчёты |

**Детерминированные критерии (до LLM):**
1. strong: мутирующая задача (риск ≥ medium), сообщение > 800 симв., упомянуты план/ревью/промпт/отчёт, любой ход planner/critic.
2. fast: read-only вопрос, классификация, подтверждение, суммаризация.
3. Спорное → fast-классификатор, порог уверенности 0.8; ниже → strong.

**SLA:** fast p95 < 3 с. Таймауты: 60 с fast / 300 с strong; 2 ретрая с бэкоффом → следующая модель. Лог в `orch_llm_calls` (model, tier, latency, tokens, cost, deterministic_flag) → spend ledger (D4, суб-бюджет $10/run на внутренние ходы). Разнос параллельных батчей 2–5 с (правило 1).

**Интерфейс для моков (D7):** роутер — единственная точка LLM-вызовов; за интерфейсом `router.call()` стоят три реализации: `LiveRouter` (прод), `ReplayRouter` (фикстуры), `ScriptedRouter` (сценарные ответы для тестов FSM/очереди).

---

## 5. RAG

### 5.1 Библиотеки (оценка и выбор)

| Кандидат | Вердикт | Причина |
|---|---|---|
| LangChain / LlamaIndex | ❌ | тяжёлые зависимости, своя LLM-абстракция конфликтует с роутером |
| Chroma / Qdrant | ❌ | отдельный сервис, избыточно при SQLite |
| **sqlite-vec** | ✅ | векторный поиск в SQLite, zero-ops, MIT |
| **FastEmbed** + `multilingual-e5-small` | ✅ | локальные эмбеддинги без GPU, Apache-2.0; обёртка добавляет обязательные префиксы `query:` / `passage:` (учтено в чанкере и ретривере) |
| **rank-bm25** | ✅ | лексический канал (точные имена нод/ключей) |
| FTS5 | ✅ запасной | нулевая зависимость |

Итог: гибрид = sqlite-vec (dense) + rank-bm25 (sparse), слияние RRF (k=60), top-8. БД `data/orch_rag.db` (рантайм-артефакт, в `.gitignore`).

### 5.2 Корпуса и обновление

| Корпус | Источник | Чанкинг | Обновление |
|---|---|---|---|
| Правила пользователя | `docs/fable/user_rules.md` (канонизация 12 правил) | 1 правило = 1 чанк; **всегда инжектятся целиком в system planner/critic**, не только через retrieval | по hash файла |
| История проектов | `project.db` (статусы, темы, итоги шагов) | по проекту/этапу, 300–600 токенов | инкрементально по `updated_at`, фон каждые 10 мин |
| История чатов | `orch_messages` | окна 10 сообщений + суммаризация fast | закрытие сессии + каждые 50 сообщений |
| Каталог нод/промптов | git baseline + `node_defs`/`node_prompt_versions` (через node_store) + Vibecode-каталог | 1 нода = 1–3 чанка | watcher mtime (git) + хук после `create_node`/`edit_node_prompt` (БД) |

API: `rag.retrieve(query, corpora, top_k)`, `rag.reindex(corpus)`. Reindex — один фоновый воркер, WAL.

---

## 6. Whitelist действий (строгий)

**Принципы:**
- Только действия из реестра. `edit_files`, `run_tests`, `exec`, shell — **отсутствуют физически** (не флагом). Гарантия — аудит 0b + `test_whitelist_no_exec` в CI + E8.
- Старый чат `db_browser.py`: **режим `fix_bugs` с `edit_files`/`run_tests` удаляется в R2.** `diagnose_only` помечается deprecated в R2, удаляется в R6. Файл целиком во владении WS2.
- Мутации — через change_control + approvals по риску.
- **Итог реестра:** 102 действия #342 минус удалённые по аудиту 0b **плюс ровно 11 новых** (полный список ниже, схемы для всех). Финальная таблица — в `docs/fable/actions_audit.md`.

### 6.1 Новые действия: read-only (риск none) — 3 шт.

#### 1. `read_pipeline_code`
```json
{"params": {"path": {"type": "string", "required": true, "note": "проверка по allowlist"},
            "max_bytes": {"type": "integer", "default": 65536}},
 "returns": {"content": "string", "truncated": "boolean"}}
```
Allowlist (`actions/read_allowlist.yaml`): разрешено `app/services/**`, `app/web/routers/**`, `app/orchestrator/**`, `templates/**`, `prompts/templates/**`, `web/src/**` (только `.py`, `.md`, `.ts`, `.tsx`, `.yaml`, `.sql`); запрещено всегда: `.env*`, `data/**`, `**/*.key`, `**/secrets*`, `project.db`, пути вне репо; нормализация пути, запрет symlink и `..`. Тест отказов (`.env`, `data/`) — в E8.

#### 2. `rag_search`
```json
{"params": {"query": {"type": "string", "required": true},
            "corpora": {"type": "array", "items": "enum[rules,projects,chats,nodes]", "default": ["nodes","rules"]},
            "top_k": {"type": "integer", "default": 8, "max": 16}},
 "returns": {"chunks": "[Chunk{text, source, score}]"}}
```

#### 3. `run_validators`
```json
{"params": {"project_id": {"type": "integer", "required": true},
            "validators": {"type": "array", "items": "string", "default": "all"},
            "context": {"type": "object"}},
 "returns": {"results": "[ValidatorResult{name, pass, details, blocking}]"}}
```

Существующие read-only (из 102): `get_project_state`, `list_nodes`/`get_node` (теперь через node_store), `get_scenes`/`get_shots`, `get_run_logs`, `get_spend_report`.

### 6.2 Новые действия: пайплайн (approvals + spend + paid-retry) — 2 шт.

`run_node` существует в реестре #342 (`POST /api/projects/{id}/steps/excel_gpt/run?node_key=...&only_node=true&mode=resume`, risk high, платно). Новые обёртки:

#### 4. `stop_node` (risk: medium)
```json
{"params": {"project_id": {"type": "integer", "required": true},
            "node_key": {"type": "string", "required": true},
            "reason": {"type": "string", "required": true}},
 "semantics": "взводит стоп-ключ only_node (механизм projects.py); остановка — на границе, которую даёт стоп-ключ, НЕ mid-flight",
 "returns": {"stop_key_set": "boolean"}}
```
Если при реализации обнаружится нативный stop-эндпоинт — используется он; правки в `projects.py` (если нужна хоть строка) делает **WS2** (владелец файла после R0a, см. таблицу владения).

#### 5. `resume_node` (risk: medium)
```json
{"params": {"project_id": {"type": "integer", "required": true},
            "node_key": {"type": "string", "required": true}},
 "semantics": "обёртка над run?mode=resume; paid-retry guard применяется"}
```

### 6.3 Новые действия: ноды и отчёты (change_control обязателен) — 6 шт.

#### 6. `create_node` (risk: medium; high — если новая группа)
```json
{"params": {
   "node_key": {"type": "string", "pattern": "^n_[a-z0-9_]+$", "required": true},
   "group": {"type": "string", "pattern": "^[a-z0-9_]+$", "required": true,
             "note": "существующая группа — medium; НОВАЯ группа разрешена (правило 8), но риск high → approval"},
   "title": {"type": "string", "maxLength": 120, "required": true},
   "prompt_template": {"type": "string", "required": true},
   "params": {"type": "object", "default": {"model": "gpt-5.6-sol", "reasoning_effort": "high"}},
   "position": {"type": "object", "properties": {"after_node_key": {"type": "string"}}}},
 "effects": ["node_store.writer → node_defs + node_prompt_versions; catalog.register_node(); rag.reindex('nodes')"],
 "rollback": "change_control: удаление записи node_defs и из каталога"}
```

#### 7. `edit_node_prompt` (risk: medium)
```json
{"params": {
   "node_key": {"type": "string", "required": true},
   "patch_mode": {"enum": ["replace_full", "replace_section", "append_section"], "required": true},
   "section_anchor": {"type": "string", "note": "обязателен для *_section"},
   "content": {"type": "string", "required": true},
   "reason": {"type": "string", "required": true}},
 "effects": ["новая версия в node_prompt_versions (git не трогается, D3); rag.reindex('nodes')"],
 "rollback": "откат на предыдущую версию"}
```

#### 8. `edit_node_params` (risk: medium)
```json
{"params": {
   "node_key": {"type": "string", "required": true},
   "params_patch": {"type": "object", "required": true, "note": "model, reasoning_effort, parallel, stagger_sec, temperature, max_tokens"},
   "reason": {"type": "string", "required": true}},
 "constraints": ["понижение reasoning_effort с high — только через approval (правило 2)", "stagger_sec ∈ [2,5] (правило 1)"],
 "rollback": "change_control"}
```

#### 9. `create_report` (risk: low)
```json
{"params": {
   "title": {"type": "string", "required": true},
   "project_id": {"type": "integer", "required": true},
   "sections": {"type": "array", "items": {"heading": "string", "html": "string", "data_table": "object?"}, "required": true},
   "editable": {"type": "boolean", "default": true}},
 "effects": ["HTML → data/reports/{project_id}/{slug}.html + запись orch_reports"],
 "security": ["серверный bleach (allowlist: h1–h4,p,ul,ol,li,table,tr,td,th,b,i,code,pre,a[href http/https],img[src /api/orchestrator/reports/*]) при создании И при каждом update",
              "клиентский DOMPurify перед рендером"],
 "rollback": "версия отчёта в change_control"}
```
**Раздача отчётов (P2.13):** файлы `data/reports/**` **не** монтируются статикой. Единственный путь — `GET /api/orchestrator/reports/{id}` (роутер WS2, за auth-токеном): отдаёт санированный HTML с заголовками `Content-Security-Policy: default-src 'none'; img-src 'self'; style-src 'unsafe-inline'` и `X-Content-Type-Options: nosniff`. Изображения — только через тот же роут (`/api/orchestrator/reports/{id}/assets/*` с path-нормализацией).

#### 10. `update_report` (risk: low) — редактирование из веба тоже действие, с тем же пайплайном санитизации
```json
{"params": {"report_id": {"type": "integer", "required": true},
            "sections_patch": {"type": "array", "required": true},
            "reason": {"type": "string"}},
 "security": ["bleach на каждый PUT — тот же allowlist, что create_report"],
 "rollback": "версия в change_control"}
```
`PUT /api/orchestrator/reports/{id}` — тонкий HTTP-фасад над этим действием (общий код санитизации, не дублируется).

#### 11. `export_nodes_snapshot` (risk: low)
```json
{"params": {"node_keys": {"type": "array", "items": "string", "default": "all_db_overrides"}},
 "effects": ["diff БД-нод относительно git-шаблонов → отчёт-патч в data/reports/; коммит в репо делает человек"],
 "rollback": "не требуется (read-only по отношению к рантайму)"}
```

---

## 7. Planner → Critic, валидаторы, evals

### 7.1 Цикл

```
intent → Planner(strong) → Validators(dry-run) → Critic(strong)
  → approve → Executor (валидаторы после каждой мутации)
  → revise (≤2) → Planner
  → reject → вопрос пользователю
```

### 7.2 Программные валидаторы (`app/orchestrator/validators/`)

| Валидатор | Правило | Проверка |
|---|---|---|
| `vo_length.py` | закадр 200–300/сцена (п.7) | **единственный источник логики длин** (создан в R0a, см. 2.1.5); его же зовёт `scene_boundaries.py` |
| `no_deletions.py` | нет удалений (п.3) | diff счётчиков и id до/после; уменьшение → блок + rollback |
| `wide_before_dialogue.py` | общий план перед диалогом (п.6) | последовательность типов кадров |
| `shot_merge.py` | склейка тривиальных кадров (п.5) | нет соседей под R-MERGE-TRIVIAL |
| `spend_guard.py` | $200/$100/$50 + суб-бюджет $10/$40 (D4) | прогноз до старта; факт после каждого платного шага; исчерпание $10 внутренних → эскалация |
| `plan_sanity.py` | корректность плана | все действия в whitelist, схемы валидны, есть `verify_code_state` перед run_node |

### 7.3 Evals — два контура, честная модель моков (D7)

**Контур A (CI, каждый PR): ноль реальных вызовов любого рода.**
- Действия: `MockActionBackend` — воспроизводит записанные ответы Action Layer (фикстуры с реальных прогонов проекта 65); paid-retry и spend — на мок-стоимостях.
- LLM: два режима. (1) **`ScriptedRouter`** — детерминированные сценарные ответы для тестов поведения (FSM, очередь, interrupt-тайминги, эскалации): проверяем механику, не модель. (2) **`ReplayRouter`** — фикстуры реальных ответов strong/fast, ключ = `(tier, sha256(normalized_messages))`; записываются автоматически при прогонах контура B; **miss фикстуры в CI = fail теста с меткой `fixture-miss`** (никогда не фолбэк в реальный вызов). Инвалидация: изменение system-промпта planner/critic/classifier меняет hash → CI краснеет `fixture-miss` → перезапись фикстур следующим прогоном контура B.
- **Честное ограничение:** контур A — регрессионные replay-тесты поведения системы. Качество рассуждений модели он не меряет; это делает только контур B.

**Контур B (pre-release, вручную перед R5-gate и R6): реальные LLM и реальные действия** на копии DATA_DIR проекта 65, кап $25/прогон, paid-retry guard активен. Побочный продукт — свежие LLM-фикстуры для контура A.

**Данные для E1 (закрывает P0.3):** три набора.
- `65-clean` — эталон, данные починены WS1 до R5 (12 фрагментов приведены к 200–300): E1 проверяет **0 ложных срабатываний**.
- `65-seeded` — клон с **засеянными нарушениями**: 12 фрагментов с известными id выведены за границы (6 — >300, 6 — <200) + 2 синтетических удаления кадра: E1 проверяет **recall/precision = 1.0** по известному списку.
- `65-canary` — клон для канарейки R6 (эталон не контаминируется).

| Eval | Задача | Порог | Контур |
|---|---|---|---|
| E1 | «Запусти Кадры для сцен 3–5 и проверь закадр» | план содержит verify→run_node→run_validators; на `65-seeded`: precision/recall = 1.0 по засеянным нарушениям; на `65-clean`: 0 ложных; 0 удалений | A (Replay+Mock) + B |
| E2 | «Создай ноду QC-отчёта по длинам закадра» | create_node валиден; нода в node_defs; `node_store.resolve_node` её возвращает; RAG находит | A; исполнимость ноды run_node'ом — B |
| E3 | «Усили R-NO-OVERSPLIT в промпте scenes_to_frames» | edit_node_prompt replace_section; правки только в целевой секции; rollback работает | A |
| E4 | steer посреди задачи | replan от чекпоинта ≤ 30 с; результаты до чекпоинта сохранены (механика — ScriptedRouter; replan-план — Replay) | A |
| E5 | interrupt | семантика 3.3: ack ≤ 2 с; classification ≤ 5 с p95; отмена неплатного ≤ 10 с; пауза на границе платного; 0 повторных оплат | A + B |
| E6 | маршрутизация | 40 размеченных запросов из реальной истории (разметка: пользователь + Фейбл); ≥ 90% простых на fast; 100% планов на strong; отчётная метрика — доля детерминированных классификаций (проверка гипотезы 80%, без порога) | A (классификатор — Replay) |
| E7 | отчёт | валидный HTML; санитизация 5 XSS-векторов на create И на update; редактирование и сохранение | A |
| E8 | безопасность | внедрение `edit_files` в план → reject plan_sanity; чтение `.env` → отказ; запрос без токена → 401; kill switch: ≤ 2 с отмена неплатных + взвод стоп-ключа. **100% строго** | A |
| E8-B | kill switch на реальном run_node | стоп-ключ взведён ≤ 2 с; нода остановилась на границе; 0 повторных оплат; change_control-запись | **B** |

**Порог релиза:** контур A: E1–E8 pass ≥ 90% (E8 = 100%), 3 прогона подряд; контур B: E1, E5, E8-B — 1 чистый прогон, плюс исполнимость ноды из E2. Retrieval-метрика RAG: precision@8 ≥ 0.8 на 50 размеченных запросах (разметка пользователь + Фейбл).

---

## 8. Поэтапный rollout

| Этап | Содержание | Длит. | Зависимости | Приёмка |
|---|---|---|---|---|
| **R0a** | housepc → main + единый vo_length (п. 2.1) | **1.5–2 дня** | — | diff пуст, CI зелёный, тег `pipeline-base-v1` |
| **R0b** | PR #342 → main + аудит 102 действий + инвентаризация node_store-call-sites (п. 2.2) | **2 дня** | R0a | тег `action-layer-v1`, аудит-таблица, `test_whitelist_no_exec` |
| **R1** | Каркас: contracts.py (freeze Д+2), **миграции node_defs/node_prompt_versions к Д+2**, остальные миграции, auth middleware, SSE-чат read-only, FSM без субагентов, базовый UI; WS4 снимает visual-baseline Д+1 | **3 дня** (Д+1…Д+3) | R0b | стриминг fast-ответа в UI за токеном; 401 без токена; reconnect-replay; e2e smoke |
| **R2** | Whitelist-реестр; удаление fix_bugs/edit_files/run_tests; **node_store + патчи call-sites по списку 0b**; 11 новых действий; санитизация и раздача отчётов; approvals/kill switch в чат | **5 дней** (Д+1…Д+5) | R0b (‖ R1; node_defs-миграции от WS3 к Д+2) | действия end-to-end на тест-проекте; нода из БД резолвится node_store; kill из UI ≤ 2 с (семантика 3.3); XSS-тесты зелёные |
| **R3** | Мультиагентность: planner→critic→executor, чекпоинты, interrupt/steer, роутер, валидаторы | **6 дней** (Д+6…Д+11) | R1 + R2 | E4/E5 (контур A) зелёные; план ≥5 шагов автономно с 1 approval; синтетическое удаление кадров блокируется |
| **R4** | RAG: 4 корпуса, гибридный поиск, инжекция правил, reindex-хуки | **3 дня**, ‖ R3 | R1 | precision@8 ≥ 0.8 на 50 запросах; правила всегда в system |
| **R5** | Evals контур A + фиксы; контур B один прогон (запись фикстур); данные `65-clean`/`65-seeded` готовы (WS1) | **3 дня** (Д+12…Д+14) | R3 + R4 | пороги п. 7.3 |
| **R6** | **Освобождение :8766:** остановка инстанса PR #342 (ветка влита в R0b), архив его DATA_DIR. Канарейка: клон `65-canary` на :8766, лимит $50/run, усиленные approvals; затем main :8765. Удаление `diagnose_only`. | **2 дня** (Д+15…Д+16) | R5 | 0 security-инцидентов (исполнение вне whitelist / превышение лимита / несанкционированная мутация / XSS); 0 удалений сцен/кадров; подтверждение пользователя |

**Критический путь: R0a(2) → R0b(2) → R2(5) → R3(6) → R5(3) → R6(2) = 20 рабочих дней чистыми** (R1 ‖ R2 и короче; R4 ‖ R3 и короче). **Фиксируем план на 24 рабочих дня** (буфер 20% на конфликты 0b, хирургию db_browser и стабилизацию R5).

---

## 9. Параллельные workstreams (6 потоков)

Общие правила: контракты (`ActionResult`, `OrchEvent`, `ValidatorResult`, `LLMResponse`, `Chunk`, `NodeDef`, спецификации таблиц) фиксируются в `app/orchestrator/contracts.py` в Д+1 и **замораживаются к Д+2**; дальше — мини-RFC (issue + ревью затронутых потоков), арбитр — Фейбл, SLA 24 ч. Миграции БД — только WS3 (D5). Изменение чужих файлов — только issue владельцу. Единственное санкционированное исключение — патчи node_store-call-sites (3.6): WS2 исполняет, WS1 ревьюит, список закрыт аудитом 0b.

### WS1 — Pipeline Base & Validators
- **Цель:** Этап 0a; `check_vo_lengths` как единый источник + интеграция в `scene_boundaries.py` (в R0a); остальные 5 валидаторов; наборы данных `65-clean` / `65-seeded` / `65-canary`.
- **Владение:** `app/services/**` **только до конца R0a, затем freeze** (исключения после freeze: `vibecode_catalog.py` → WS2 с R1; `projects.py` → WS2 с R1; node_store-патчи — WS2 по списку 0b с ревью WS1); `app/orchestrator/validators/**`; `tests/validators/**`; `tests/pipeline_regression/**`.
- **Интерфейс:** `validators.run(name, project_id, context) -> ValidatorResult{pass, details, blocking}` (для WS3 и действия `run_validators` WS2).
- **Тесты:** 4 регрессии пайплайна, unit на каждый валидатор, синтетика «удаление кадра», тест, что `scene_boundaries.py` и `vo_length.py` зовут одну функцию.
- **DoD:** R0a принят; 6 валидаторов, покрытие ≥ 90%; `65-clean` в норме 200–300, `65-seeded` с реестром засеянных нарушений.
- **Оценка: 5 дней** (2 R0a + 3 валидаторы и данные).

### WS2 — Action Layer, Whitelist & Node Store
- **Цель:** аудит 0b; whitelist-реестр; **node_store (D3) + патчи call-sites**; 11 новых действий со схемами; санитизация и раздача отчётов; удаление fix_bugs; обёртки stop/resume (включая правки `projects.py` при необходимости); `read_allowlist.yaml`.
- **Владение:** `app/orchestrator/actions/**`; `app/orchestrator/node_store/**`; `app/web/routers/orchestrator_actions.py`; `app/web/routers/db_browser.py` **целиком** (fix_bugs — удалить в R2, diagnose_only — в R6); `app/web/routers/projects.py` (**с R1**, передача от WS1 актом); `app/services/vibecode_catalog.py` (с R1); node_store-патчи в `app/services/` по списку 0b (ревью WS1); `docs/fable/actions_audit.md`; `tests/actions/**`.
- **Интерфейсы:** `actions.execute(name, params, task_ctx) -> ActionResult{ok, data, change_id, cost}`; `actions.registry()`; `node_store.resolve_node()` / `node_store.writer` (потребители: пайплайн, WS3, WS6); `ApprovalEvent` в SSE-шину; вызов `rag.reindex()` (реализация WS6). Спецификации таблиц `node_defs`, `node_prompt_versions`, `orch_reports` → WS3 к Д+1.
- **Тесты:** `test_whitelist_no_exec`, схемы всех 11 действий, rollback, paid-retry на run_node, XSS (5 пэйлоадов × create и update), allowlist-отказы, резолв DB-ноды пайплайном.
- **DoD:** критерии R0b и R2.
- **Оценка: 7 дней** (2 R0b + 5 R2 — с учётом хирургии db_browser на 2434 строки и node_store).

### WS3 — Agents Core & DB
- **Цель:** FSM супервизора, очередь interrupt/steer (семантика 3.3: ack/classification разведены), чекпоинты, цикл planner→critic, SSE-шина с ring-buffer и retention-компакцией, auth middleware, чат-роутер, **все миграции БД** (orch_* + node_defs/node_prompt_versions — **milestone Д+2** для WS2).
- **Владение:** `app/orchestrator/agents/**`; `checkpoints.py`; `queue.py`; `events.py`; `contracts.py`; `auth.py`; `app/orchestrator/db/migrations/**`; `app/web/routers/orchestrator_chat.py`; `docs/fable/deploy_constraints.md`; `tests/agents/**`.
- **Интерфейсы:** потребляет `actions.execute` (WS2), `validators.run` (WS1), `router.call/classify` (WS5), `rag.retrieve` (WS6); эмитит `OrchEvent{type, task_id, payload, seq}` (WS4).
- **Зависимости:** ScriptedRouter и мок actions с Д+1; реальная интеграция с конца R2.
- **Тесты:** FSM-переходы; ack ≤ 2 с до классификации; interrupt во время мок-LLM (отмена ≤ 10 с); пауза на границе платного шага; resume от чекпоинта; 2 revise → эскалация; replay после рестарта; 401 без токена; компакция orch_events.
- **DoD:** критерии R1 и R3; E4/E5 контур A зелёные.
- **Оценка: 9 дней** (3 R1 + 6 R3 — честная арифметика вместо прежних 7).

### WS4 — Web UI
- **Цель:** чат со стримингом, панель статусов агентов/задач, approve/kill-кнопки, ввод токена, редактор HTML-отчётов (DOMPurify; рендер через API-роут с CSP), визуальная регрессия существующих страниц.
- **Владение:** `web/src/app/orchestrator/**`; `web/src/components/orchestrator/**`; `web/src/lib/orchestratorApi.ts`; `web/src/lib/orchestratorTypes.ts`; `web/scripts/gen_orch_types.py`; `web/e2e/orchestrator/**`; `web/e2e/visual/**`. Другие директории `web/src` — не трогает.
- **Анти-флейк визуальной регрессии (P2.17):** baseline Д+1 на 10 ключевых страницах; фиксированные шрифты в контейнере CI; CSS-инъекция `*{animation:none!important;transition:none!important;caret-color:transparent}`; детерминированные данные через мок-API; `toHaveScreenshot({maxDiffPixelRatio: 0.01})`; 2 retry. Критерий: дифф ≤ 1% пикселей, не «строгий 0».
- **Интерфейсы:** SSE-контракт `OrchEvent` + REST WS2/WS3; типы генерируются из `contracts.py`; мок-SSE-сервер до готовности WS3.
- **Тесты:** Playwright: стриминг, reconnect, interrupt-кнопка (ack в UI ≤ 2 с), approve, редактирование отчёта, 401-флоу.
- **DoD:** R1 UI + полный UI к R3; визуальная регрессия в пределах порога.
- **Оценка: 6 дней.**

### WS5 — Model Router
- **Цель:** fast/strong, детерминированный классификатор + LLM-фолбэк, фолбэк-цепочки, учёт стоимости → ledger (D4, суб-бюджет), интерфейс Live/Replay/Scripted (D7).
- **Владение:** `app/orchestrator/routing/**` (model_router.py, pay52_routes.yaml, classifier.py, replay.py, scripted.py); `tests/routing/**`. Таблица `orch_llm_calls` — спецификация здесь, миграция у WS3.
- **Интерфейсы:** `router.call(tier|auto, messages, stream) -> LLMResponse`; `router.classify(message) -> {tier, mode, deterministic}`.
- **Тесты:** фолбэк при таймауте, детерминированные правила, cost → ledger, разнос 2–5 с, `fixture-miss` в ReplayRouter — fail, не живой вызов.
- **DoD:** E6 ≥ 90% на 40 запросах; fast p95 < 3 с; доля детерминированных классификаций репортится.
- **Оценка: 3 дня.**

### WS6 — RAG & Evals
- **Цель:** 4 корпуса, гибридный retriever (префиксы e5), eval-харнесс E1–E8 + E8-B, `MockActionBackend`, запись/воспроизведение LLM-фикстур (пайплайн с контуром B), датасеты E6 и precision@8.
- **Владение:** `app/orchestrator/rag/**`; `docs/fable/user_rules.md`; `evals/orchestrator/**` (run_evals.py, моки, фикстуры, реестр засеянных нарушений `65-seeded`); `tests/rag/**`.
- **Интерфейсы:** `rag.retrieve(query, corpora, top_k) -> [Chunk]`; `rag.reindex(corpus)`; формат LLM-фикстур — совместно с WS5 (contracts, Д+1).
- **Тесты:** precision@8 на 50 запросах; инкрементальный reindex; идемпотентность; запись/воспроизведение фикстур; fixture-miss.
- **DoD:** критерии R4 + R5 (контур A).
- **Оценка: 6 дней.**

### Таблица владения файлами (без пересечений)

| Путь | Владелец |
|---|---|
| `app/services/**` до конца R0a, затем freeze; `app/orchestrator/validators/**` (вкл. `vo_length.py` — единый источник логики длин); `tests/validators/**`; `tests/pipeline_regression/**` | **WS1** |
| `app/services/vibecode_catalog.py` (с R1); `app/web/routers/projects.py` (с R1, акт передачи от WS1); node_store-патчи в `app/services/` строго по списку 0b (ревью WS1); `app/orchestrator/actions/**`; `app/orchestrator/node_store/**`; `app/web/routers/orchestrator_actions.py`; `app/web/routers/db_browser.py` (целиком); `docs/fable/actions_audit.md`; `tests/actions/**` | **WS2** |
| `app/orchestrator/agents/**`; `checkpoints.py`; `queue.py`; `events.py`; `contracts.py`; `auth.py`; `app/orchestrator/db/migrations/**`; `app/web/routers/orchestrator_chat.py`; `docs/fable/deploy_constraints.md`; `tests/agents/**` | **WS3** |
| `web/src/app/orchestrator/**`; `web/src/components/orchestrator/**`; `web/src/lib/orchestratorApi.ts`; `web/src/lib/orchestratorTypes.ts`; `web/scripts/gen_orch_types.py`; `web/e2e/orchestrator/**`; `web/e2e/visual/**` | **WS4** |
| `app/orchestrator/routing/**`; `tests/routing/**` | **WS5** |
| `app/orchestrator/rag/**`; `docs/fable/user_rules.md`; `evals/orchestrator/**`; `tests/rag/**` | **WS6** |
| `templates/node_groups/**` — коммиты только в R0a (WS1), в рантайме read-only (D3); индексируется WS6 read-only | **WS1 (R0a) → freeze** |
| `docs/fable/PLAN.md`; интеграционные merge; арбитраж contracts | **Фейбл** |

**Рантайм-артефакты (в `.gitignore`, не в ownership):** `data/orch_rag.db` (WS6-воркер), `data/reports/**` (handler create_report), `data/node_overrides/**` (снапшоты). Структуру директорий создаёт WS2 в R2.

---

## 10. Риски и митигации

| Риск | Вер. | Митигация |
|---|---|---|
| Конфликты rebase #342 поверх housepc в `app/orchestrator/*` | **высокая** | этап R0b с бюджетом 2 дня; правило разрешения зафиксировано (2.2.1); ручное ревью каждого файла |
| Потеря housepc-правок (R0a) | средняя | коммиты до rebase; 4 регрессионных теста; diff-сверка как критерий приёмки |
| D3 не доходит до пайплайна (ноды в БД не исполняются) | средняя | node_store с владельцем (WS2), закрытый список call-site-патчей из аудита 0b, ревью WS1, проверка исполнимости в контуре B |
| Interrupt рвёт платный вызов → двойная оплата | средняя | семантика 3.3: платные не отменяются, пауза на границе; paid-retry guard; чекпоинт до платного шага; E5 в обоих контурах |
| Evals жгут деньги в CI | закрыт | D7: MockActionBackend + Replay/ScriptedRouter, fixture-miss = fail; реальные вызовы только в контуре B с капом $25 |
| Фикстуры LLM протухают при правке промптов | средняя | hash-ключ фикстур; fixture-miss явный; перезапись из контура B — штатная процедура R5 |
| Задача падает по spend на этапе планирования | средняя | D4: суб-бюджет $10/$40, исчерпание внутреннего → эскалация с approval, не fail |
| Classifier шлёт сложное на fast | средняя | детерминированные правила до LLM; мутирующие планы принудительно strong; мониторинг orch_llm_calls; E6 |
| XSS через отчёты | средняя | bleach на create и каждый update (единый код через действие update_report), DOMPurify, CSP на API-роуте раздачи, статика не монтируется, E7 |
| Несанкционированный доступ к оркестратору | средняя | auth-токен на всех `/api/orchestrator/**`, 401-тесты в E8 и e2e |
| Утечка секретов через read_pipeline_code | средняя | allowlist/denylist, нормализация путей, тесты отказов в E8 |
| Утечка exec через легаси db_browser | средняя | fix_bugs удаляется физически в R2, diagnose_only — в R6; файл целиком у WS2; `test_whitelist_no_exec`; E8 = 100% |
| Kill switch не останавливает платный шаг мгновенно | принято | семантика честно ограничена границей стоп-ключа (3.3); E8-B на реальном run_node в контуре B |
| Разбухание orch_events | низкая | токены не персистятся; ночная компакция, 14 дней, кап 20k/сессия |
| Дрейф рантайм-нод и репо | закрыт | D3: ноды в БД, templates RO, `export_nodes_snapshot` |
| Удаление сцен/кадров автономной задачей | низкая | `no_deletions` блокирующий + rollback; E1 на `65-seeded` |
| Multi-worker ломает очереди/SSE | низкая | D2: 1 worker; replay через orch_events + чекпоинты |
| Флейк визуальной регрессии | средняя | фиксированные шрифты, отключение анимаций, мок-данные, порог 1%, retry (9/WS4) |
| :8766 занят инстансом PR #342 | закрыт | явный шаг R6: остановка инстанса (ветка уже в main), архив DATA_DIR |
| sqlite-vec конкуренция записи | низкая | отдельная БД, WAL, один reindex-воркер |
| Переполнение контекста | средняя | RAG вместо полного контекста старого чата; суммаризация fast |
| 52pay недоступен | низкая | фолбэк-цепочки, Kie, kill switch |
| Контаминация канарейки и evals | закрыт | канарейка на `65-canary`, эталон и `65-seeded` не трогаются |

---

## 11. Сводная оценка

| Поток | Трудоёмкость |
|---|---|
| WS1 | 5 дней |
| WS2 | 7 дней |
| WS3 | 9 дней |
| WS4 | 6 дней |
| WS5 | 3 дня |
| WS6 | 6 дней |
| Интеграция/стабилизация R5–R6 (сверх потоков) | 3 дня |
| **Суммарная трудоёмкость** | **≈ 39 человеко-дней** |
| **Критический путь** (R0a→R0b→R2→R3→R5→R6) | **20 рабочих дней чистыми; план — 24 дня с буфером 20%** |

---

## 12. Первый шаг — сегодня

1. Инвентаризация housepc и старт PR Этапа 0a (WS1).
2. Параллельно: подтверждение у пользователя решений **D1 и D6** (strong-primary и fast без high); черновик `contracts.py` и спецификаций таблиц, включая `node_defs`/`node_prompt_versions` (WS3 по спецификации WS2); заготовка аудит-таблицы и инвентаризации node_store-call-sites (WS2).
3. До merge R0b никакой код оркестратора в main не идёт — только контракты, моки, датасеты, baseline-скриншоты WS4.