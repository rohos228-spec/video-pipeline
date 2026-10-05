# Слой действий (Action Layer)

Позволяет оркестратору выполнять то, что сейчас делают кнопки интерфейса, по явному плану:
каталог действий, preview без побочных эффектов, подтверждение оператора для платного и
разрушающего, бюджет, аварийный стоп, журнал. Код: `app/services/action_layer/`, REST: `/api/action-layer`.

Контракт ниже стабилен: поля `ActionSpec`, API реестра, JSON плана и REST-ответы менять только
с обратной совместимостью.

## Понятия

- **Действие (ActionSpec)** - запись каталога: id, схема параметров, риск, оценка стоимости, исполнитель.
- **План (Plan)** - упорядоченные шаги `action_id + params + depends_on`. Шаги идут строго по очереди.
- **Preview** - проверка параметров и предусловий, оценка стоимости, список разрушающих эффектов.
  Исполнители не вызываются, запись через контекст запрещена (`WriteNotAllowed`).
- **Хэш плана** - sha256 от шагов и свойств безопасности их действий (риск, схема). Текст запроса в хэш не входит.
- **Подтверждение (approval)** - одноразовая серверная запись, привязанная к хэшу плана.

## Риски

| tier | смысл | подтверждение |
|---|---|---|
| `read` | ничего не меняет | не нужно |
| `local_safe` | пишет локально, без денег | не нужно |
| `paid` | `spends_money=True` | нужно |
| `destructive` | `destructive=True`, необратимая потеря | нужно |

Действие может быть и платным, и разрушающим: `risk="destructive"`, `spends_money=True`, `destructive=True`.
Правила регистрации (иначе `ActionRegistryError`): `destructive` строго равен tier `destructive`;
`spends_money` требует tier `paid` или `destructive`; `paid` требует `spends_money`;
платному нужен `estimator`; `executor` - async; схема параметров - объект с
`additionalProperties: false` и только поддерживаемыми ключевыми словами.

## Как зарегистрировать действие

```python
from app.services.action_layer.spec import (
    ActionSpec, CostEstimate, Precondition, registry)

async def run(ctx, params):                      # ctx: ActionContext
    data = await ctx.call("POST", f"/projects/{params['project_id']}/something")
    return {"ok": True, "credits_spent": 3.0}    # credits_spent необязателен

async def project_ok(ctx, params):               # None = выполнено, строка = причина отказа
    await ctx.call("GET", f"/projects/{params['project_id']}")

registry.register(ActionSpec(
    id="area.name", title_ru="...", description_ru="...",
    params_schema={"type": "object", "additionalProperties": False,
                   "required": ["project_id"],
                   "properties": {"project_id": {"type": "integer", "minimum": 1}}},
    executor=run, risk="paid", spends_money=True,
    estimator=lambda p: CostEstimate(2.0, 6.0, "основание оценки"),
    preconditions=(Precondition("project_ok", "Проект существует", project_ok),),
    idempotent=False, reversible=False))
```

- `ctx.call(method, path, params=, json=)` вызывает **существующий** эндпоинт внутри процесса
  (ASGI-клиент того же приложения, сети нет), путь без `/api`. Так сохраняются валидация эндпоинтов,
  сессии проектов и события. Ошибка >= 400 -> `ActionCallError`.
- `idempotent=True` - повтор безвреден (значение выставляется). Только такой шаг (и `read`)
  повторяется после рестарта; остальные в «неизвестном» состоянии помечаются `unknown` и требуют ручного разбора.
- Фоновое действие: `executor` запускает задачу, `poll(ctx, params, started) -> JobState` опрашивает,
  `stop(ctx, params)` останавливает; `poll_interval_s` задаёт период.
- Пакеты и модули регистрируются так же (`registry.register` / `register_many`, все-или-ничего),
  дубликат id запрещён. Встроенный срез монтажа: `builtin_montage.py`, грузится `ensure_builtin_actions()`.

### API реестра

`registry.register(spec)`, `register_many(list)`, `unregister(id)`, `get(id)` (KeyError),
`find(id)` (None), `list(risk=None, prefix=None)`, `validate_params(id, params) -> list[str]`.

## ActionSpec: поля

`id` (`area.name`), `title_ru`, `description_ru`, `params_schema`, `executor`, `risk`, `spends_money`,
`destructive`, `reversible`, `idempotent`, `preconditions`, `estimator(params) -> CostEstimate | None`,
`destructive_effects(params) -> str`, `poll`, `stop`, `poll_interval_s`, `scope`, `tags`.
`CostEstimate(low, high, basis)`: `None` = неизвестно, а не 0; неизвестная цена платного шага блокирует запуск.

Схема параметров: `type, properties, required, additionalProperties, enum, const, minimum, maximum,
minLength, maxLength, pattern, items, minItems, maxItems` (jsonschema в проекте нет, валидатор свой).

## Подтверждение

1. `POST /plans/preview` создаёт план в статусе `proposed` (действия не выполняются).
2. Оператор отдельным вызовом `POST /plans/{id}/approve` создаёт подтверждение. Личность берётся из
   `AuthDep` в том же формате, что в `observations` (`web_operator:<hash>` или `local_web_operator`
   при выключенной авторизации). Идентификаторы `planner:`, `model:`, `agent:`, `system` отклоняются.
3. Подтверждение: привязано к хэшу плана, живёт `ACTION_LAYER_APPROVAL_TTL_S` (по умолчанию 600 с),
   расходуется атомарно при `POST /plans/{id}/run`. Новое подтверждение отменяет прежние.
4. В теле плана и запуска нет полей подтверждения (лишние поля -> 422); `run` не принимает id подтверждения.
5. План только из `read`/`local_safe` подтверждения не требует. План, не прошедший проверку на approve
   (содержимое или определения действий изменились, превышение потолка), не подтверждается.
6. Остановленный (`stopped`) платный план можно продолжить только с новым подтверждением;
   прерванный рестартом (`running` без живой задачи) продолжается по уже израсходованному подтверждению
   только в окне `ACTION_LAYER_RESUME_WINDOW_S` (по умолчанию 3600 с) от последней активности плана;
   позже `run` закрывает план как `stopped` (`stop_reason="resume_window_expired"`) и отвечает 403
   `approval_required`: нужно новое подтверждение (`approve`, затем `run`).

## Бюджет и стоп

- Потолок на план: `ACTION_LAYER_SESSION_CAP_CREDITS` (по умолчанию 200 кредитов). Перед платным шагом
  атомарно резервируется верхняя оценка; после шага резерв сверяется с `credits_spent` из результата.
  Не помещается или оценка неизвестна -> шаг не стартует, план `failed`.
- Перед каждым шагом (и перед запуском) проверяется `kill_switch`: включён -> шаги не идут,
  план `stopped` с `stop_reason="kill_switch_active"`.
  Kill switch действует и на уже идущий фоновый шаг: на каждом опросе (`poll_interval_s`) он проверяется,
  при включении вызывается `stop` действия, шаг закрывается как `cancelled` ("stopped by kill switch"),
  план `stopped` с `kill_switch_active`. Резерв стоимости остаётся (факт неизвестен).
- Резерв при остановке сохраняется (консервативно). Повторное подтверждение остановленного плана
  резервирует стоимость заново, поэтому повторные остановки одного плана расходуют потолок.
  `spent_credits`/`reserved_credits` в `GET /plans/{id}` - резерв/факт по журналу (верхняя граница);
  `actual_credits` - только то, что действия сами сообщили как `credits_spent` (`null`, если ничего).
- Один живой план на проект. Таймаут шага `ACTION_LAYER_STEP_TIMEOUT_S`; максимум шагов `ACTION_LAYER_MAX_STEPS`.
- `stop`: флаг в БД, остановка между шагами и вызов `stop` текущего фонового действия, если оно есть.

## Таблицы

`action_layer_plans`, `action_layer_steps` (уникальный `idempotency_key = "<plan_id>:<idx>"`),
`action_layer_approvals`, `action_layer_spend` и `action_layer_history`. История и журнал расходов
append-only (триггеры SQLite блокируют UPDATE/DELETE). Создаются `ensure_action_layer_schema(conn)` на старте.

## REST (все за AuthDep)

| метод | путь | назначение |
|---|---|---|
| GET | `/api/action-layer/catalog?risk=&prefix=` | `{count, actions:[ActionSpec JSON]}` |
| POST | `/plans/preview` | тело `{steps:[{action_id, params, depends_on}], request_text}` -> `{plan_id, preview}`; невалидный план -> 422 `{detail:{code:"plan_invalid", preview}}` |
| POST | `/plans/{id}/approve` | `{approval_id, plan_id, plan_hash, operator, expires_at}`; 409 `{code}`: `plan_changed`, `plan_invalid`, `over_budget`, `plan_not_approvable`, `approval_not_needed`, `operator_required` |
| POST | `/plans/{id}/run` | `{plan_id, status:"running"}`; 403 `approval_required`; 409: `already_running`, `plan_finished`, `plan_changed`, `kill_switch_active`, `project_busy` |
| GET | `/plans?status=&project_id=&limit=` | `{items:[{id, status, project_id, request_text, live, interrupted, created_at, updated_at}]}`, новые первыми (восстановление экрана выполнения после перезагрузки) |
| GET | `/plans/{id}` | статус плана, шаги (status, progress, result, error, cost), подтверждения, `spent_credits`, `reserved_credits`, `actual_credits`, `live`, `interrupted`, `resume_expired` |
| POST | `/plans/{id}/stop` | `{status, stop_requested}` |
| GET | `/history?plan_id=&limit=&before_id=` | `{total, items:[{id, created_at, plan_id, step_index, event, actor, request_text, action_id, params, outcome, error, cost_estimate, cost_recorded}]}` |

Формат `preview`: `ok, errors[], plan_hash, risk_max, needs_approval, steps[{index, action_id, title_ru, risk,
spends_money, destructive, reversible, valid, errors, precondition_failures, cost}], cost{low, high,
unknown_steps, known}, destructive_effects[{index, action_id, text}], budget{cap_credits, remaining_credits, fits}`.

Статусы плана: `proposed, running, succeeded, failed, stopped`. Шага: `pending, running, succeeded,
failed, skipped, unknown, cancelled`. События журнала: `plan_created, approval_created, approval_consumed,
run_requested, plan_started, step_started, step_succeeded, step_failed, step_skipped, step_unknown,
step_cancelled, budget_block, kill_switch_block, stop_requested, plan_finished`.

## Первый срез (только read и local_safe)

`montage.get_board`, `montage.apply_status`, `montage.montage_status`, `montage.recover_status` (read);
`montage.set_voiceover`, `montage.set_coverage`, `montage.queue_ops`, `montage.swap_shots` (local_safe).
Платные и разрушающие действия панели в этот срез не входят.

## Планировщик (запрос на русском -> план)

Код: `planner.py`. `POST /plans/from-text` `{text, project_id?, selected_scene?, selected_frame?}` строит
план и возвращает его **только как preview** (статус `proposed`), в том же формате, что `/plans/preview`, плюс
`explanation_ru`, `needs_clarification`, `context_version`, `planner{model, attempts, prompt_tokens,
completion_tokens, est_cost_usd, cost_calibrated}`. Планировщик не вызывает approve и run; подтверждает и
запускает оператор отдельными вызовами. Если модель просит уточнение, `plan_id` и `preview` равны `null`.

- **Транспорт.** Один непотоковый POST на утверждённый маршрут Vibecode (`decision_v1.vibecode_post`), JSON-режим.
  Ключ берётся только из настроек (`VIBECODE_API_KEY`); пустой ключ, неутверждённые хост/схема/путь маршрута и
  включённый kill switch отказывают **до** любого запроса (503 `key_missing` / `route_not_approved`, 409
  `kill_switch_active`). Лимит токенов провайдером не применяется, поэтому «обрезано» определяется по
  `finish_reason`/usage.
- **Что видит модель.** Каталог (id, название, описание, риск, схема параметров), структурный контекст проекта
  (статус, число кадров, узлы, выбранные сцена/кадр) и запрос. Запрос и контекст передаются как данные в JSON, в
  системной части их нет. Тексты кадров и тема в контекст не попадают.
- **Ответ модели.** `{steps:[{action_id, params, depends_on}], needs_clarification, explanation_ru}`. Любые поля
  подтверждения, оператора, запуска, инструментов на любом уровне отклоняют план целиком (`forbidden_field`).
- **Строгая проверка.** Неизвестный id, лишние/неверные параметры (`registry.validate_params`), чужой
  `project_id`, шагов больше `ACTION_LAYER_MAX_STEPS`, неверный `depends_on` -> весь план отклонён (422
  `planner_rejected`, поле `reason`), без починки и без повторного запроса.
- **Повтор.** Ровно один повтор, и только при ошибке формата ответа: обрезан, не JSON, неверный каркас. Повтор
  содержит текст ошибки. Всего не больше двух POST на запрос. Транспортная ошибка не повторяется (502).
- **Лимит.** `ACTION_LAYER_PLANNER_CALLS_PER_HOUR` (по умолчанию 30) на оператора за скользящий час; считается
  каждый POST (повтор тоже). Превышение: 429 `rate_limited`, запрос к модели не отправляется.
- **Журнал.** Каждый вызов модели - строка `planner_call` (model, токены, оценка USD по прайсу каталога, не
  откалибрована, исход); связь с созданным планом - `planner_plan_linked`.
- **Устаревший контекст.** `context_version` = хэш статуса проекта, числа кадров, узлов и выбранных сцены/кадра.
  Версия хранится в `action_layer_plan_context`; `approve` и `run` для `proposed`-плана с изменившимся контекстом
  отвечают 409 `stale_context` (план строится заново). Проверка выполняется в `service.approve_plan` и
  `service.prepare_run` (`StalePlan`), поэтому обойти её нельзя; роутер только преобразует ошибку в 409. Планы без записи контекста (ручной preview) не проверяются.

## Макросы («умные кнопки»)

Код: `macros.py`. Макрос - именованный план с параметрами: `id, title_ru, description_ru, steps, params_schema`.
Шаблоны подставляются из `{{имя}}`: значение целиком сохраняет тип (`"{{frame}}"` -> число), необязательный
незаданный параметр опускает поле. Макрос не исполняется сам: `POST /plans/from-macro` разворачивает его и
отдаёт обычный preview, дальше те же подтверждение, бюджет и журнал.

| метод | путь | назначение |
|---|---|---|
| GET | `/macros` | встроенные + свои + общие; у каждого `available`, `missing_action_ids`, `mine` |
| POST | `/macros` | `{id?, title_ru, description_ru, steps, params_schema, shared}`; сохранение проверяет макрос (422 `macro_invalid`) |
| DELETE | `/macros/{id}` | только владелец; встроенные не удаляются (403) |
| POST | `/plans/from-macro` | `{macro_id, params, request_text?, selected_scene?, selected_frame?}` -> как `/plans/preview` + `macro_id`, `context_version` |

- Пользовательский макрос может ссылаться только на **зарегистрированные** действия, ключи параметров и
  обязательные поля проверяются по схеме действия, типы плейсхолдеров сверяются. Видимость по умолчанию только
  владельцу, `shared=true` открывает остальным (удалять и менять может только владелец). До 50 макросов на оператора.
- Макрос, чьё действие позже снято с регистрации, показывается как `available=false` и отказывает 409
  `macro_unavailable`; ошибки не бросает. Встроенные макросы ссылаются только на реально зарегистрированные
  действия (проверяется тестом на реестре): `smart.check_board`, `smart.cost_basis`, `smart.queue_frame_regen`,
  `smart.fix_voiceover`, `smart.scene_from_scratch` (split_scene, improve_scene, apply_queue, run_montage),
  `smart.single_frame_fix` (queue_ops, apply_queue), `smart.recover_generation`, `smart.assemble_video`,
  `smart.shots_report`, `smart.run_node_step`.
- Платные макросы выносят заявленные лимиты и настройки в параметры (`max_image_ops`, `max_video_ops`,
  `image_generator`, `image_resolution`, при необходимости `video_*`); текущие значения даёт
  `montage.get_cost_basis` (макрос `smart.cost_basis`). План требует подтверждения оператора.
- `smart.run_node_step`: стоимость `node.run_step` пока неизвестна, такой план виден в preview, но подтвердить и
  запустить его нельзя, пока для действия не появится оценка.
- Таблицы: `action_layer_plan_context`, `action_layer_macros` (создаются `ensure_action_layer_schema`).
