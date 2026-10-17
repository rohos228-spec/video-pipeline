# Ограничения деплоя оркестратора

Зафиксировано планом (ревизия 3), решение **D2**. Процесс оркестратора — это тот же
uvicorn, что поднимает Studio (`app/main.py` → `uvicorn.Config` без `workers`).

## Один worker

- Ровно **1 uvicorn-worker** на процесс оркестратора.
- Не задавать `--workers` больше 1 и не поднимать второй процесс на ту же SQLite и ту же шину.
- `asyncio.Queue` входящих сообщений, cancel/pause-флаги супервизора и SSE-шина
  (кольцо token-событий, подписчики) живут **в памяти этого процесса**.
- Второй worker их не увидит: ack, отмена неплатного шага и стрим разъедутся.

## Что переживает рестарт

- Укрупнённые события — таблица `orch_events` (сообщение целиком, шаги, чекпоинты, approvals).
- Монотонный `seq` сессии — `orch_event_seq` (растёт и для token, чтобы номер не переиспользовался).
- Позиция задачи — `orch_checkpoints` (`план`, `позиция`, `change_id`, `spend`, краткий контекст).
- Строка задачи и шаги — `orch_tasks` / `orch_steps`. Нетерминальная задача после рестарта
  продолжается (см. ниже), а не остаётся только записью.
- Token-события **не** пишутся в БД. Кольцо на 1000 событий на сессию годится только для
  reconnect в живом процессе (`Last-Event-ID`). После рестарта клиент продолжает с последнего
  чекпоинта, а не с оборванного токена.

## База

- SQLite, тот же файл, что `settings.sqlite_path` (WAL у соединения студии).
- Каталога Alembic в репозитории нет: таблицы оркестратора накатывает раннер
  `app/orchestrator/db/migrations/` при первом обращении.
- Один `*.sql` — один идемпотентный скрипт. `executescript` и строка журнала
  не одна транзакция: файл, оборванный посередине, может примениться частично
  и не попасть в `orch_schema_migrations`. `IF NOT EXISTS` спасает текущие
  файлы; `ALTER` (как `009`) при повторе не идемпотентен.
- Если уже накатанный файл потом дописали (так было с `002`), локальную БД
  оркестратора нужно пересоздать. Журнал не переигрывает записанное имя.
- Один writer. Не шарить файл БД между процессами оркестратора.
- После рестарта Studio lifespan вызывает `Supervisor.recover_after_restart`:
  - `queued` снова попадают в память очереди. Если сессия свободна, стартует самая
    старая строка; остальные ждут её терминального статуса.
  - `planning`, `critic_review` и `executing` продолжаются с последнего чекпоинта
    (`orch_checkpoints`, иначе план и позиция из строки задачи).
  - `paused` и `waiting_approval` сами не стартуют и держат очередь сессии.
- Платный шаг со строкой `orch_steps.status=running` (исход неизвестен) сам
  повторно не исполняется. Задача ждёт `waiting_approval` с причиной
  `paid_outcome_unknown`. В событии `approval_required` есть `forecast_usd`
  (`forecast_cost` этого шага) и три кнопки:
  - «Шаг выполнен, продолжить» (`decision=approve`) — шаг `done`, попадает в
    `_paid_done`, `execute` не вызывается, план идёт со следующего шага;
  - «Перезапустить шаг» (`decision=retry_step`) — сервер снова отдаёт прогноз и
    вооружает отдельный grant. Исполнение только по `retry_grant`: ключ =
    действие + публичные params + признак retry, одноразовый, через `_budget_stop`
    (пока стоп бюджета включён, ключ не сгорает и `execute` не вызывается);
  - «Остановить задачу» (`decision=deny`) — шаг и задача `failed`, без `execute`.
- `grant_approval` без `decision` остаётся прежним: для `paid_outcome_unknown`
  он не вызывает `execute` и не пишет шаг в `_paid_done`.

## `POST /api/orchestrator/chat/{session_id}/approve`

Тот же Bearer `ORCH_API_TOKEN`, что и у остальных `/api/orchestrator/**`.
Роут добавлен к проводу ещё на `ws3-r3.3` и остаётся на `ws3-r3.4`. Тело
`ApproveIn` в `export_contract_schemas()` не входит. `ApprovalDecision`,
`ApprovalDecisionBody` и `rules.approval_decision_labels` входят в экспорт
вместе с версией `ws3-r3.4`.

Тело — pydantic `extra=forbid`:

- `change_id`: строка или `null`, по умолчанию `null`. Это id из `approval_required`
  (`orch-appr-<task_id>-<n>`). Его по-прежнему назначает только сервер.
- `decision`: `approve`, `retry_step`, `deny` или `null`, по умолчанию `null`.
  `null` — прежний `grant_approval` без кнопки.
- `retry_grant`: bool, по умолчанию `false`.

Роут вызывает `grant_approval(session_id, decision, retry_grant=..., change_id=...)`.
`ApprovalGrantError` отдаётся тем же HTTP-кодом (404 или 409). `detail` — короткая
причина без токена и без секретов. Нет задачи у сессии — 404 `not found`.

Неизвестное `decision`, лишнее поле или неверный тип тела — **422**. Строка задачи
при этом не меняется.

200 возвращает объект с полями `executed`, `reason`, `forecast_usd`, `retry_armed`,
`change_id`, `decision`. `executed: true` бывает у удавшегося `retry_grant`.
Пустой `decision` на старом риске запускает шаг, как раньше, и не ставит `executed`.

Последовательность `retry_step`:

1. `POST {change_id, decision: "retry_step"}` — в ответе `forecast_usd` и
   `retry_armed: true`. `execute` не вызывается.
2. `POST {change_id, retry_grant: true}` — шаг выполняется ровно один раз.
3. Следующий такой POST возвращает `reason: "retry_grant_spent"` и шаг не повторяет,
   даже если задача уже ушла из `waiting_approval`.

`retry_armed` в этом ответе и в `approval_required`: `true` только после `retry_step`,
пока отдельный grant не потрачен. После потребления `retry_grant`, а также после
`approve` и `deny`, значение `false` (флаг вооружения снимается).

## Доступ

- Все пути `/api/orchestrator/**` закрыты middleware `app/orchestrator/auth.py`.
- Токен — переменная окружения `ORCH_API_TOKEN` (Bearer). Сравнение через `hmac.compare_digest`.
- Нет заголовка, пустой токен или неверное значение — **401**. Само значение в логи не попадает.
- Пока переменная не задана, эндпоинты оркестратора закрыты.

## Компакция `orch_events`

Фоновая задача рантайма вызывает `compact_events` каждые `ORCH_EVENT_COMPACT_HOURS`
часов (по умолчанию 6) и останавливается в `Runtime.shutdown`. Сама функция:

- удаляются события старше **14 дней**, кроме типов `checkpoint`, `approval_required` и `approval_decided`;
- жёсткий кап **20 000** событий на сессию (сначала старые обычные, затем при необходимости и защищённые).

## Чего этот процесс не делает

- Multi-worker и отдельный сервис очередей — вне рамок.
- Реальные LLM-вызовы и платные действия в CI запрещены (D7). Если пакета
  `app.orchestrator.routing` нет, прод-рантайм остаётся на `FallbackRouter` и
  перечисляет `routing` в `Runtime.runtime_degraded`. `build_router()` в этом
  случае не вызывается. До WS2 `actions.execute` — заглушка без пайплайна.
- LangGraph / CrewAI не используются: состояние задачи — FSM супервизора.
- Раздача `data/reports/**` статикой запрещена (это роутер WS2, не этот процесс).
