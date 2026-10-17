# WS5 — контракты роутера

Временный источник, пока `LLMResponse` не переедет в `app/orchestrator/contracts.py`
(владелец WS3). TODO(WS3): перенести типы из `types.py` и эту спецификацию
таблицы в контракт и миграцию. Миграцию пишет только WS3.

Время везде — ISO-8601 UTC, секунды, суффикс `Z`: `2026-10-17T05:00:00Z`.

## router

```text
router.call(tier|auto, messages, stream) -> LLMResponse
router.classify(message, *, agent_role=None, risk=None, mutating=None) -> Classification
router.call_batch([{"tier", "messages", ...}, ...]) -> [LLMResponse]
```

`classify` — **async**. Спорный tier делает LLM-ход; синхронная функция не может
его дождаться. Это отступление от сигнатуры в плане, иначе Replay/Live нечем
вызвать.

Реализации: `LiveRouter` (`router_impl=live`), `ReplayRouter` (`replay`),
`ScriptedRouter` (`scripted`). Фабрика: `build_router("live"|"replay"|"scripted")`.

`Classification`:

| поле | смысл |
|---|---|
| `tier` | логический fast\|strong. Его меряет E6. Не зависит от D6 |
| `mode` | `interrupt` \| `steer` \| `append` \| `task` |
| `deterministic` | tier решили правила, LLM не звали |
| `confidence` | 1.0 у правил; иначе уверенность классификатора |
| `effective_tier` | tier после D6. Пока D6 выключен, fast → strong |
| `reason` | короткая метка правила |

`mode=task` — обычный ход, не interrupt/steer/append. Режим всегда
считают правила (стоп-слова, steer, вопрос без упоминания задачи).
Модель может подсказать mode только если правило режима не сработало.

## LLMResponse

Согласованный черновик, плюс одно поле.

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
| `task_status` | `waiting_approval` или None. Имя из `TaskStatus` (WS3) |
| `approval` | `ApprovalEvent` или None. См. ниже |

Исчерпание суб-бюджета $10 **не бросает** исключение и **не** заводит
отдельный статус задачи. Супервизор WS3 переводит задачу в
`TaskStatus.waiting_approval` (`"waiting_approval"`). Причина лежит на
`ApprovalEvent`, поля как в `contracts.py` WS3:

| поле | значение при бюджете |
|---|---|
| `task_id` | int или None (нечисловой id уходит в `payload.task_ref`) |
| `action` | `""` |
| `reason` | `"budget_exhausted"` |
| `risk` | `""` |
| `change_id` | None |
| `payload` | `spent_usd`, `budget_usd`, `run_id` |

Ход, который пересёк лимит, возвращает текст модели и `finish_reason`
модели, плюс `task_status="waiting_approval"` и этот `ApprovalEvent`.
В `orch_llm_calls` у него `status=ok`. Следующий internal-вызов того же
`run_id` в модель не идёт: `text=""`, тот же `task_status` и
`ApprovalEvent`. Строку вызова не пишем — попытки провайдера не было,
а в `LLMCallStatus` нет отдельного значения (`ok|timeout|error|fixture_miss`).

`budget_scope=pipeline` в суб-бюджет $10 не входит.

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

Сток in-memory ledger тоже в процессе. `SpendLedger.hydrate(run_id, spent)`
нужен после рестарта, сумма — `SUM(cost_usd)` по `run_id` и
`budget_scope='internal'` и `status='ok'`.

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

Батч всегда параллельный. Зазор между стартами — одно число из [2, 5] секунд
на весь батч (`i * gap` от общего t0).

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
