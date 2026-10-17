# WS5 — контракты роутера

Временный источник, пока `LLMResponse` не переедет в `app/orchestrator/contracts.py`
(владелец WS3). TODO(WS3): перенести типы из `types.py` и эту спецификацию
таблицы в контракт и миграцию. Миграцию пишет только WS3.

Время везде — ISO-8601 UTC, секунды, суффикс `Z`: `2026-10-17T05:00:00Z`.

## router

```text
router.call(tier, messages, stream=False, *, run_id=None, task_id=None,
            agent_role=None, budget_scope=None) -> LLMResponse
router.classify(message) -> Classification
router.call_batch([{"tier", "messages", ...}, ...]) -> [LLMResponse]
```

`run_id`, `task_id`, `agent_role`, `budget_scope` — keyword-only, как
`ModelRouter` в ws3-r3 (`04a5506a`). Супервизор передаёт их всегда.
`budget_scope=None` трактуется как `internal`. `task_id` — строка.
Дополнительно, не из протокола: `temperature`, `max_tokens`, `risk`,
`mutating`, `on_delta` (тоже keyword-only). У `classify` сверх протокола
keyword-only `agent_role`, `risk`, `mutating`, `run_id`, `task_id`.

`classify` — **async**. Спорный tier делает LLM-ход; синхронная функция не может
его дождаться. Это отступление от сигнатуры в плане, иначе Replay/Live нечем
вызвать.

Реализации: `LiveRouter` (`router_impl=live`), `ReplayRouter` (`replay`),
`ScriptedRouter` (`scripted`). Фабрика: `build_router("live"|"replay"|"scripted")`.

`Classification`:

| поле | смысл |
|---|---|
| `tier` | логический fast\|strong. Его меряет E6. Не зависит от D6 |
| `mode` | `MessageMode`: `interrupt` \| `steer` \| `append` \| `task` |
| `deterministic` | tier решили правила, LLM не звали |
| `confidence` | 1.0 у правил; иначе уверенность классификатора |
| `effective_tier` | tier после D6. Пока D6 выключен, fast → strong |
| `reason` | короткая метка правила |
| `kill_switch` | bool, дефолт false. Поле `ClassifyResult` из ws3-r3 |

`mode=task` — то же значение, что `MessageMode.task` в ws3-r3: новая задача,
не interrupt/steer/append. Режим считают правила. Модель подсказывает mode
только если правило режима не сработало. `kill_switch` модель не ставит.

`kill_switch=true` важнее обычного interrupt: `mode` в этом случае всегда
`interrupt`. Фразы до LLM: «аварийный стоп», «kill switch», «стоп всё»,
«останови всё немедленно». Tier считается теми же правилами (без других
strong-сигналов это fast). «стой» / «стоп» / «отмена» остаются обычным
interrupt с `kill_switch=false`. Классификатор для аварийных фраз не
вызывается.

`reason` и `effective_tier` в `ClassifyResult` ws3-r3 нет. Это единственные
лишние поля классификации.

## LLMResponse

Поля как у `LLMResponse` ws3-r3 (`04a5506a`), включая необязательные
`task_status: TaskStatus | None` и `approval: ApprovalEvent | None`.

| поле | тип |
|---|---|
| `text` | str |
| `call_id` | str, uuid попытки, которая вернула ответ |
| `model` | str |
| `provider` | `52pay` \| `kie` |
| `tier` | фактический fast\|strong (после D6) |
| `finish_reason` | str, как вернула модель. Пустой, если модель не звали |
| `usage` | `{prompt_tokens, completion_tokens, reasoning_tokens}` |
| `cost_usd` | float |
| `latency_ms` | int |
| `fallback_index` | int, 0 = основная модель |
| `deterministic` | bool, tier выбрали правила |
| `fixture_key` | str \| None |
| `task_status` | `TaskStatus \| None`. При бюджете — `TaskStatus.waiting_approval` |
| `approval` | `ApprovalEvent \| None`. Поля ниже |

## Как супервизор узнаёт про $10

Роутер WS5 **списывает** внутренний суб-бюджет. Это расходится с фразой
мини-RFC #350 («суб-бюджет $10 внутренних LLM не списывается роутером»):
там лимит смотрит валидатор по `estimated_cost`, а `SpendSnapshot` — снимок.
Здесь учёт уже в `SpendLedger`, чтобы следующий ход не ушёл в модель.

Механизм — **поля ответа, не исключение**. `RouterExhausted` и
`RouterError` при исчерпании не бросаются.

1. Каждый успешный `router.call` с `budget_scope=internal` прибавляет
   `cost_usd` в ledger прогона. `run_id` обязателен: без него
   `ValueError("internal budget requires run_id")`. Общего бакета
   `"_anonymous"` нет — иначе все ходы без id делили бы один лимит $10
   и после исчерпания блокировались бы навсегда. `spent(None)` для #351
   возвращает `0.0` и ничего не блокирует. `budget_scope=pipeline` в
   счётчик $10 не входит, тоже требует `run_id` и ограничен своим
   потолком `pipeline_sub_budget_usd` (по умолчанию $40). Другое
   значение `budget_scope` — `ValueError("budget_scope must be
   internal|pipeline")` до вызова и до записи в леджер.
2. Ход, после которого `spent >= 10`, всё равно возвращает текст модели.
   На том же объекте `LLMResponse`:
   - `task_status == "waiting_approval"` (`TaskStatus.waiting_approval`);
   - `approval` — `ApprovalEvent` с `reason == "budget_exhausted"`
     (`ApprovalReason.budget_exhausted` в ws3-r3).
   В `orch_llm_calls` у этой попытки `status=ok`.
3. Следующий internal-вызов того же `run_id` транспорт не трогает,
   и со `stream=True` тоже: проверка стоит до `_provider_complete`.
   `text == ""`, те же `task_status` и `approval`. Строку вызова не пишем.
   Стриминговый chat-запрос 52pay несёт `stream_options.include_usage=true`.
   Если usage пустой, а текст есть, стоимость не ноль: completion-токены
   ≈ `len(text)/4` (минимум 1) по прайсу strong. То же у нестримингового
   ответа без usage. Списание идёт в `SpendLedger`.
4. Супервизор узнаёт это функцией `budget_exhausted_response` из контракта:
   `task_status` равен `waiting_approval` и `approval.reason` равен
   `budget_exhausted`. Дальше он пишет статус задачи и событие
   `approval_required`. Исключение для этого не нужно.

Исчерпание **не** заводит отдельный статус задачи. Поля `ApprovalEvent`:

| поле | значение при бюджете |
|---|---|
| `task_id` | int или None (нечисловой id уходит в `payload.task_ref`) |
| `action` | `""` |
| `reason` | `"budget_exhausted"` |
| `risk` | `""` |
| `change_id` | None |
| `payload` | `spent_usd`, `budget_usd`, `run_id` |

`budget_scope=pipeline` в суб-бюджет $10 не входит. У него свой потолок
$40 (`pipeline_budget_usd` / `pipeline_sub_budget_usd`). Сигнал тот же:
ход, после которого pipeline-расход достигает потолка, возвращает текст
модели, `task_status=waiting_approval` и `approval.reason=budget_exhausted`.
В `payload.budget_usd` лежит потолок pipeline, не $10. Следующий
pipeline-вызов того же `run_id` транспорт не трогает, в том числе
`stream=True`. Internal на том же прогоне от pipeline-расхода не
блокируется. `totals()["budget_exhausted"]` по-прежнему только про
internal (`internal_spent >= $10`). Enum статуса вызова остаётся
`ok|timeout|error|fixture_miss`.

## Чтение расхода для WS1

`spend_guard` в #351 (`d37c63e4`) читает леджер только публично.
`SpendLedger._pipeline` не нужен.

```python
from app.orchestrator.routing.ledger import get_spend_ledger

ledger = get_spend_ledger()
spent = ledger.spent(run_id)                 # только internal, $10
cap = ledger.internal_budget_usd             # 10
pipe = ledger.pipeline_spent(run_id)         # не входит в $10
same = ledger.read_pipeline_spent(           # то же число, project_id игнорируется
    project_id=project_id,
    run_id=run_id,
)
```

`pipeline_spent(run_id)` — позиционный вызов, как в адаптере после
`TypeError` на лишнем `project_id`. `read_pipeline_spent` — keyword-only,
адаптер пробует его первым. Оба имени возвращают pipeline-scope этого
`run_id`. Пустой или неизвестный `run_id` даёт `0.0`. `spent(None)` тоже `0.0`:
#351 зовёт так, когда `run_id` нет, и это не общий процессный бакет.

Стабильный снимок:

```python
view = ledger.totals(run_id)
# view["internal_spent_usd"]   — входит в лимит $10
# view["pipeline_spent_usd"]   — не входит
# view["internal_limit_usd"]
# view["internal_remaining_usd"]
# view["budget_exhausted"]     — internal_spent >= limit
```

`get_spend_ledger()` — процессный синглтон, роутер без своего ledger
пишет в него. Вызов потокобезопасен. Пустой или неизвестный `run_id`
у `totals` даёт нулевой расход и `budget_exhausted=False` (лимит леджера
на месте). `SpendReader.read_totals(*, project_id, run_id)` из #351 —
action layer WS2 (`daily/project/run/internal/actions_spent_usd`), не
этот леджер. Pipeline-scope, если его прочитали, лежит отдельно как
`pipeline_scope_spent_usd` и не подменяет `actions_spent_usd` WS2.
Таблицы `orch_llm_calls` здесь нет.

## Фикстура (совместно с WS6)

Один JSON на ключ, файл `{key}.json`.

```text
key = sha256(canonical_json({
  tier, messages, temperature, max_tokens, reasoning_effort
}))
```

`canonical_json`: UTF-8, `sort_keys=true`, разделители `,` и `:`, `ensure_ascii=false`.
`messages` — список `{role, content}` (лишние поля отбрасываются).
`temperature`/`max_tokens`: целые числа остаются int (`1` и `1.0` — один ключ).
`reasoning_effort`: `null` или строка. Регистр текста сообщений не меняется.

`tier` и `reasoning_effort` в ключе — **после** D1/D6, для primary-хопа:

- D6 выключен, запросили fast → в ключе `tier=strong`, `reasoning_effort=high`;
- D6 включен, fast → `tier=fast`, `reasoning_effort=null`;
- D1 выключен, strong → effort primary `high` (модель `gpt-5.6-sol`);
- D1 включен, strong → effort primary `high` (модель `claude-fable-5`).

Модели в ключе нет: фикстура — ответ на логический запрос, не на хоп фолбэка.
Replay не идёт по цепочке и не делает живой вызов.

Тело:

```json
{
  "key": "<sha256>",
  "request_meta": {
    "tier": "strong",
    "temperature": 0,
    "max_tokens": 120,
    "reasoning_effort": "high",
    "messages_sha256": "<sha256 messages>"
  },
  "response": {
    "text": "...",
    "call_id": "recorded-call",
    "model": "gpt-5.6-sol",
    "provider": "52pay",
    "tier": "strong",
    "finish_reason": "stop",
    "usage": {
      "prompt_tokens": 0,
      "completion_tokens": 0,
      "reasoning_tokens": 0
    },
    "cost_usd": 0.0,
    "latency_ms": 0,
    "fallback_index": 0,
    "deterministic": false,
    "fixture_key": "<sha256>"
  },
  "recorded_at": "2026-10-17T00:00:00Z",
  "recorded_model": "gpt-5.6-sol"
}
```

Текст запроса в `request_meta` не кладём (его восстанавливает ключ).
Текст ответа лежит в `response.text` — это и есть записанный `LLMResponse`.

Нет файла или `key` внутри не совпал → `FixtureMiss` (`label=fixture-miss`).
Живой вызов из Replay запрещён. На каждую такую попытку пишется строка
`status=fixture_miss`.

Прогон Replay выдаёт **новый** `call_id` (в БД он UNIQUE на попытку).
Записанный `call_id` остаётся только в файле фикстуры.

## orch_llm_calls

Строка на **попытку** (каждый ретрай и каждый фолбэк). `id` назначает БД.
Текст промпта и ответа не хранится.

Предлагаемый DDL (пишет WS3, здесь не миграция):

```sql
CREATE TABLE orch_llm_calls (
  id INTEGER PRIMARY KEY,
  call_id TEXT NOT NULL UNIQUE,
  task_id TEXT NULL,
  run_id TEXT NULL,
  agent_role TEXT NULL
    CHECK (agent_role IS NULL OR agent_role IN
      ('supervisor','planner','critic','executor','classifier','summarizer')),
  requested_tier TEXT NOT NULL CHECK (requested_tier IN ('fast','strong','auto')),
  tier TEXT NOT NULL CHECK (tier IN ('fast','strong')),
  model TEXT NOT NULL,
  provider TEXT NOT NULL CHECK (provider IN ('52pay','kie')),
  reasoning_effort TEXT NULL,
  fallback_index INTEGER NOT NULL,
  attempt INTEGER NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('ok','timeout','error','fixture_miss')),
  error TEXT NULL,
  deterministic_flag INTEGER NOT NULL CHECK (deterministic_flag IN (0,1)),
  classifier_confidence REAL NULL,
  latency_ms INTEGER NOT NULL,
  prompt_tokens INTEGER NOT NULL,
  completion_tokens INTEGER NOT NULL,
  reasoning_tokens INTEGER NOT NULL,
  cost_usd REAL NOT NULL,
  budget_scope TEXT NOT NULL CHECK (budget_scope IN ('internal','pipeline')),
  router_impl TEXT NOT NULL CHECK (router_impl IN ('live','replay','scripted')),
  messages_sha256 TEXT NOT NULL,
  fixture_key TEXT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX ix_orch_llm_calls_run ON orch_llm_calls (run_id, created_at);
CREATE INDEX ix_orch_llm_calls_task ON orch_llm_calls (task_id);
CREATE INDEX ix_orch_llm_calls_fixture ON orch_llm_calls (fixture_key);
```

| поле | смысл |
|---|---|
| `call_id` | uuid попытки |
| `task_id`, `run_id` | NULL, если вызов вне задачи |
| `agent_role` | кто звал |
| `requested_tier` | что попросили: fast, strong или auto |
| `tier` | фактический после D6 |
| `model`, `provider` | хоп этой попытки. provider только `52pay` или `kie` |
| `reasoning_effort` | NULL у fast (после D6); у strong — `high` |
| `fallback_index` | 0 = основная модель цепочки |
| `attempt` | 1..3 (первый вызов + 2 ретрая) |
| `status` | `ok`, `timeout`, `error`, `fixture_miss` |
| `error` | NULL при ok. Промах фикстуры: `fixture-miss`. Бюджет сюда не пишется |
| `deterministic_flag` | 1, если tier выбрали правила, а не классификатор |
| `classifier_confidence` | NULL, если классификатор не участвовал |
| `latency_ms` | длительность попытки |
| `prompt_tokens`, `completion_tokens`, `reasoning_tokens` | usage попытки; у ошибки нули |
| `cost_usd` | по прайсу `pay52_routes.yaml` (USD / 1M). У ошибки 0 |
| `budget_scope` | `internal` (суб-бюджет $10) или `pipeline` |
| `router_impl` | `live` \| `replay` \| `scripted` |
| `messages_sha256` | sha256 канонических messages, без текста |
| `fixture_key` | ключ фикстуры или NULL у live |
| `created_at` | ISO UTC |

Запись идёт через `LlmCallSink.record(LlmCallRecord)`. Подмена:
`InMemoryLlmCallSink` / `NullLlmCallSink`. WS3 ставит свою реализацию,
которая делает INSERT. Роутер БД не открывает.

Сток in-memory ledger тоже в процессе. После рестарта WS3 вызывает

```python
ledger.hydrate(
    run_id,
    internal_spent_usd,   # SUM(cost_usd) WHERE budget_scope='internal' AND status='ok'
    pipeline_spent_usd,   # SUM(cost_usd) WHERE budget_scope='pipeline' AND status='ok'
)
```

Без `run_id` `hydrate` бросает `ValueError`. Pipeline в лимит $10 не входит.

## Цепочки (pay52_routes.yaml)

Флаги `flags.d1_confirmed` / `flags.d6_confirmed`, по умолчанию false.
Env `ORCH_D1_CONFIRMED` / `ORCH_D6_CONFIRMED` перекрывают файл.

| | D выключен (дефолт) | D включен |
|---|---|---|
| strong | `gpt-5.6-sol` 52pay high → Kie `gpt-5-6-sol` high, stream | `claude-fable-5` 52pay high → `gpt-5.6-sol` 52pay high → Kie stream |
| fast | не используется, запрос уходит в strong | `glm-5.3-flash` → `claude-haiku-4-5` → strong. effort пустой |

Таймаут 60 с на fast-хоп, 300 с на strong-хоп. 2 ретрая, пауза
`backoff_base_s * 2^(attempt-1)` (1 с, затем 2 с), потом следующий хоп.
Таймаут, 429 и 5xx ретраятся. 401/400 на хопе ретраи не жгут — сразу фолбэк.

Батч всегда параллельный: `asyncio.gather`, без семафоров и очередей.
Сдвиг по умолчанию 0. Необязательный `call_batch(..., gap=секунды)`
сдвигает старт i-го элемента на `i * gap` от общего t0. Диапазон [2, 5]
не требуется и в main не переносится. `tier="auto"` классифицируется
до сборки батча, по одному вызову на элемент; внутри задачи
классификаторного хода нет.

`classify` при уже исчерпанном internal-бюджете модель не зовёт.
`Classification.task_status=waiting_approval` и
`Classification.approval.reason="budget_exhausted"`. Тот же сигнал
копируется, если сам ход классификатора пересёк лимит.

Live, 52pay: URL, ключ и wire-модель берёт `app.services.pay52_routes.resolve_text`
(карту не копируем). База — `settings.pay52_base_url` / `PAY52_BASE_URL`,
дефолт `https://api.52pay.com/v1`. `gpt-5-6-sol` → `gpt-5.6-sol`, ключ
`pay52_gpt_api_key`. Пока модуля нет в дереве, хоп не падает: база из settings
или env, ключ `PAY52_GPT_API_KEY`. `resolve_text` вернул None (пустой ключ
или неизвестная модель) — хоп пропускается, POST нет.

Kie — последний резерв. Слаг только `settings.gpt_model` / `GPT_MODEL`
(дефолт `gpt-5-6-sol`), не каталожный id с точками. Хоп всегда со
`stream=true`: длинный нестриминговый reasoning на Kie часто отвечает 524.
`GPT_API_KEY` (иначе `KIE_API_KEY`), `GPT_BASE_URL` или `https://api.kie.ai`,
`POST /codex/v1/responses`, заголовок `X-VP-Relay-Token` если задан `GPT_RELAY_TOKEN`.

Цены в yaml — оценки USD/1M, не прайс 52pay. `cost = (prompt*p + completion*c + reasoning*r) / 1e6`.
