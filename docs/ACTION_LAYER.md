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

Действие, которое стирает или пересобирает готовый результат, тоже `destructive`, даже если денег не тратит: `montage.run_montage` удаляет прежний `final.mp4` и ASR-артефакты, поэтому требует подтверждения оператора (preview: «будет удалён прежний final.mp4 ... платных вызовов нет»); макрос `smart.assemble_video` с ним тоже показывает один план с одним подтверждением. Остальные `local_safe` проверены: они пишут только настройки, очередь, текст и связи в БД, новые файлы или обратимые обмены слотов, готовые файлы не стирают.

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
| POST | `/attachments` | multipart `file` + `project_id` -> `{attachment_id, name, size, kind, sha256}`; только с localhost (как остальные изменяющие ручки); 404 `project_not_found`, 413 `too_large`, 415 `type_not_allowed`/`mime_not_allowed`/`content_mismatch`, 422 `empty_file`/`bad_name` |
| GET | `/attachments/rules` | какие виды файлов принимаются: `{kinds:{image:{title_ru, extensions, max_bytes}, ...}}` |
| DELETE | `/attachments/{id}?project_id=` | убрать ещё не использованный файл; 409 `attachment_consumed`, 404 `attachment_not_found` |
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
- **Что видит модель.** Не весь реестр, а не более `ACTION_LAYER_PLANNER_MAX_CATALOG` (по умолчанию 24)
  действий: офлайн-предфильтр `planner_catalog.select_catalog` без сети и модели. Отбор по основам слов запроса
  (название, теги, префикс id, короткое описание, таблица синонимов), контексту проекта (выбранные сцена/кадр при
  «этот/эту», идущая работа, пустая доска) и встроенным макросам (подходящий макрос подтягивает действия своих
  шагов). Всегда включены `montage.get_board` и `project.stop`. Редкие слова весят больше частых (idf); слабые
  совпадения отсекаются, расплывчатые запросы (до двух слов) берутся шире. Если ничего не подошло или остаток запроса
  после вырезанных фраз вне каталога («удали проект», git, правка кода, терминал, пароли) слабый, показывается
  небольшой безопасный набор (чтение и остановка) и подсказка `hint`: модель должна вернуть уточнение. Запись
  действия компактная: `id`, короткое название `t`, `risk`, параметры `p` (`имя*:тип`, `*` обязательный, `{a|b}`
  значения, `[..]` список, `int[1..25]` диапазон); длинных описаний нет. Запрос и контекст проекта передаются как
  данные в JSON. Тексты кадров и тема в контекст не попадают. Если лимит не меньше размера реестра, фильтр
  выключен и показывается весь каталог.
- **Контекст проекта.** Собирает `planner_context.py`: статус, число кадров, выбранные сцена/кадр (`selected`:
  номер, frame_id и frame_id головы сцены; интерфейс шлёт frame_id доски, номер тоже понимается), карта кадров
  (`frames`: `номер,frame_id,роль,шоты,картинки,видео`, где роль p - сцена, c - её шот, а картинки/видео - номера
  шотов, у которых файл есть; файлы смотрятся по именам в папках `scenes` и `videos` проекта), очередь правок доски
  (`queue`), ноды графа (у нод группы script_frames_qc есть вид и имя их промта), последние предложения изменений
  (`proposals`: порядковый номер n, id, digest, статус, цель) и версии целей (`versions`). В промпт попадает только
  то, что нужно показанным действиям: карта кадров - если у действий есть параметры кадра, очередь - для
  `cancel_queue_ops`, предложения - для `change.apply/get`, версии - для `change.rollback/get`, ноды - для `node.*`
  и `change.propose`, варианты мастер-промтов - для `prompt.*`, uuid кадров - для меню съёмки (только названные в
  запросе и выбранный кадр). Карта кадров записывается одной строкой, чтобы промпт оставался маленьким (кадров
  больше 80: окно вокруг выбранного).
- **Параметры без `*`.** Правило для модели: необязательные параметры не спрашивать, а опускать. Кроме того
  сервер сам подставляет то, что модель выдумывать не должна (`planner_rules.fill_defaults`, до строгой
  проверки; заданное моделью не трогается): `project_id`; `image_generator`, `image_resolution`, `video_generator`,
  `video_resolution` для `montage.apply_queue`, `generate_scene_images`, `improve_scene` (настройки проекта, иначе
  умолчания панели; считаются тем же кодом, что и регенерация панели; модель их в промпте не видит);
  `max_image_ops`/`max_video_ops` у `apply_queue` по очереди названных кадров (без `frame_numbers` - всей
  очереди); `max_image_ops` у `improve_scene` по размеру сцены (не больше 25); шот у `swap_slots`, `move_image`,
  `set_video_trim`, только если он однозначен (у кадра один шот или файл есть только в одном). Каждое подставленное
  значение, которого человек не называл, попадает в конец `explanation_ru`: «По умолчанию подставлено:
  image_generator=..., ...», поэтому видно в превью до подтверждения. Лимиты запуска ноды (`node.run_step`:
  `min/max_llm_requests`, `max_input/output_tokens`) при построении плана через `POST /plans/from-text` сервер берёт
  из того же расчёта, что и `node.get_run_cost_basis` (`planner_autofill.py`); если расчёт невозможен, поля остаются
  пустыми, а превью показывает причину. Значения по умолчанию остальных параметров (метод разбора аудио `nemo_auto`,
  вставка группы в конец канваса, `kind=parent` у `insert_frame`, `kind=both` у `swap_shots`, `scene_prompt` из
  данных кадра) уже внутри исполнителей действий.
- **Подсказки по действиям (`notes`).** Правила для отдельных действий (`planner_rules.ACTION_NOTES`) идут в
  промпт отдельным блоком `notes` и только для показанных действий, а не в общий системный текст: так он короче.
  Там же допустимые значения покрытия кадра (план, ракурс, движение, свет: «наезд», «отъезд», «сверху» и т.д.),
  отличие `generate_scene` (пишет текст сцены) от `generate_scene_images` (рисует картинки), `delete_frame` (любой
  кадр) от `delete_child_frame` (только если сказано «дочерний»), шкала громкости. Значения покрытия и шкала
  громкости ещё и нормализуются кодом: «общий план» -> `ОБЩИЙ`, `parent_number` вне `coverage_kind` убирается,
  `bgm_level` 0.2 (доля) -> 20 с пометкой в `explanation_ru`.
- **Мастер-промты: шаг и вариант.** `step_code` берётся из слов запроса («шага script»), затем оттуда, где есть
  названный вариант (по файлам `prompts/`); название в нескольких шагах (например `main` у `script` и `excel_gpt`) -
  вопрос с вариантами шагов; шаг по статусу проекта используется только при слове «текущий». Ответ модели с другим
  `step_code` исправляется (`planner_rules.resolve_prompt_target`). Имя для чтения/записи по умолчанию - выбранный в
  проекте вариант, иначе `default`. Удаление выбранного в проекте варианта и `default` превращается в вопрос
  («удалять его нельзя») с другими вариантами шага.
- **Шот не назван.** Берётся единственный шот кадра. `swap_slots`: шот 1 у обоих кадров, а если у обоих по два шота -
  два шага (1 с 1, 2 с 2). Удаление картинки/видео и постановка перегенерации в очередь, когда файлы есть в обоих
  шотах, сервер превращает в вопрос с вариантами «шот 1» / «шот 2» (для очереди ещё «оба шота»;
  `planner_rules.shot_ambiguity`): угадывать нельзя.
- **Ответ модели.** `{steps:[{action_id, params, depends_on}], needs_clarification, explanation_ru}` и необязательное
  `clarification_options` (до 4 коротких вариантов ответа, только вместе с вопросом; лишнее и пустое отбрасывается).
  Любые поля подтверждения, оператора, запуска, инструментов на любом уровне отклоняют план целиком
  (`forbidden_field`).
- **Уточнения со списком вариантов.** В ответе `POST /plans/from-text` есть `clarification_options` (список строк,
  пустой, если вариантов нет) и `attachment_request` (`null` или `{action_id, params, formats}`); старые поля не
  менялись. Интерфейс может показать варианты кнопками (тип добавлен в `web/src/lib/action-layer.ts`).
- **Строгая проверка.** Неизвестный id, лишние/неверные параметры (`registry.validate_params`), чужой
  `project_id`, шагов больше `ACTION_LAYER_MAX_STEPS`, неверный `depends_on` -> весь план отклонён. Проверка идёт по полному реестру, но id, которого не было в промпте
  (`action_not_offered`), тоже отклоняется: модель может использовать только показанное (422
  `planner_rejected`, поле `reason`), без починки и без повторного запроса.
- **Повтор.** Ровно один повтор, и только когда ответ можно исправить: ошибка формата (обрезан, не JSON, неверный
  каркас) или неверные параметры шага (`bad_params`, `bad_step`, `bad_depends_on`). Модель получает свой ответ и текст
  ошибки проверки (`validation_error`) и исправляет JSON. Отказы по
  безопасности (`forbidden_field`, `unknown_action`, `action_not_offered`, `foreign_project`) никогда не повторяются.
  Журнал: первая неудачная попытка - исход `invalid_params`.
- **Сбой сети.** Транспортная ошибка или таймаут повторяются автоматически один раз (после паузы 0,5 с; в журнале
  это `attempt=2`; запрос планировщика ничего не запускает, повтор безопасен). Отказ провайдера без шанса на успех
  (401/402/400) и любые отказы проверки не повторяются. Если сбой и после повтора: 502 `transport_error`,
  `retryable: true` и текст «сеть/модель недоступны, повторите» (для неисправимого отказа `retryable: false`).
  Права на повтор независимы (формат, сеть, вопрос), так что всего не больше четырёх POST на запрос.
- **Вопрос о параметре с умолчанием.** Если модель вернула только вопрос о том, что сервер уже знает (генератор и
  разрешение проекта, проект, метод разбора аудио, «этот» кадр или сцена из выбора в интерфейсе), сервер один раз
  повторяет запрос, подставив значения (`clarification_resolved`, исход в журнале `clarify_autorepair`). Для
  удалений, причин, подтверждений, сумм и лимитов этого не делается никогда: вопрос уходит человеку
  (`planner_rules.resolve_clarification`). Размер основного промпта не растёт: добавляется только короткое
  сообщение в повторе.
- **Только то, что просили.** Правило в промпте (не добавлять запуск, генерацию, монтаж, публикацию, платные и
  удаляющие шаги, которых нет в запросе) и проверка на сервере (`planner_rules.drop_unrequested`): шаг из таблицы
  `GUARD_HINTS` (платные, разрушающие и фоновые действия, загрузки), для которого в запросе нет ни одного
  подходящего слова, убирается из плана вместе с зависящими от него шагами; в `explanation_ru` пишется «Убрано как
  не запрошенное: ...». Если не осталось ни одного шага, отказа нет: ответ - вопрос `needs_clarification` «Вы хотите: удалить кадр 24? Подтвердите, я подготовлю шаг.» с вариантами `["Да, подготовить", "Нет, другое"]`, `steps = []`, `planner.guard_question = true` (в журнале `error = unrequested_action`, исход `clarify`). Ничего не планируется и не выполняется, пока человек не ответит: интерфейс присылает исходную просьбу и `Уточнение: Да, подготовить`; только такой ответ в конце запроса снимает проверку «есть ли слово о действии» (`planner_rules.guard_confirmed`), и шаг всё равно идёт в обычное превью с подтверждением и запуском. «Нет, другое» возвращает просьбу описать действие, модель не вызывается. Отказы сервера (обход подтверждений, чужой аккаунт) и вопросы про массовое удаление и публикацию проверяются раньше и сильнее. Код ошибки `unrequested_action` остаётся в журнале и в старых клиентах, но планировщик больше не поднимает его как исключение. Пример: «громкость музыки 0.2
  и включи субтитры» больше не даёт `montage.run_montage`. Действие, которого нет в таблице, не убирается никогда;
  тест `test_every_risky_builtin_action_has_explicit_guard_hints` требует запись в таблице для каждого нового
  платного/разрушающего/фонового действия. Макросы (`/plans/from-macro`) этой проверки не касаются.
- **Загрузки файлов.** Файл из текста не получить. Без вложений `montage.upload_image/video/voice/music/ref`,
  `node.upload_file`, `node.upload_check_agent` планировщик не превращает в шаги: ответ - уточнение «Прикрепите файл
  кнопкой загрузки. Он нужен для действия «...» (кадр 5, шот 1; формат: PNG)» с `clarification_options =
  ["Прикрепить файл"]` и `attachment_request` (какое действие и с какими параметрами применить после загрузки; поля
  `source_path`, `data_base64`, `filename` из ответа модели выбрасываются). Если модель сама спросила путь к файлу, а
  запрос про загрузку, вопрос заменяется тем же стандартным. С вложениями (`attachments` в теле запроса, раздел
  «Файлы командной строки» ниже) путь подставляет сервер.
- **Контур изменений.** `change.apply` берёт `proposal_id` и `expected_digest` из `proposals` («предложение 3» =
  `n=3`), `change.rollback`/`change.get` - `target` и текущую версию из `versions`. Предложение без содержимого
  («предложи изменение промпта») требует уточнения: `candidate` придумывать нельзя.
- **Лимит.** `ACTION_LAYER_PLANNER_CALLS_PER_HOUR` (по умолчанию 30) на оператора за скользящий час; считается
  каждый POST (повтор тоже). Превышение: 429 `rate_limited`, запрос к модели не отправляется.
- **Журнал.** Каждый вызов модели - строка `planner_call` (model, токены, оценка USD по прайсу каталога, не
  откалибрована, исход, `catalog_offered`/`catalog_total`/`catalog_no_match`, `prompt_chars`); связь с созданным планом - `planner_plan_linked`.
- **Оценка точности.** `tests/fixtures/action_planner_eval.json` (382 случая на русском: чтение, правки, очередь,
  сцены, удаление, ноды, мастер-промты, контур изменений, загрузки, уточнения, отказы; у каждого семейства действий
  несколько формулировок: «убери», «сделай», «мне нужно», опечатки, «кадр 5», «пятый кадр», «сцена 5»; ожидаемые
  шаги записаны подмножеством параметров, значения проекта - плейсхолдерами `$frame.22`, `$proposal.3.id`,
  `$uuid.4`, `$target.shots_prompt`, которые раскрывает сид; `clarify_contains` и `explanation_contains` проверяют
  текст уточнения и строку про умолчания).
  Без сети: `tests/test_action_planner_eval_offline.py` (предфильтр показывает все ожидаемые действия, запросы вне
  каталога не показывают платных и разрушающих), `tests/test_action_planner_oracle.py` (все случаи через настоящий
  планировщик с «идеальной» моделью-заглушкой: каталог, строгая проверка, умолчания, защита от лишнего, загрузки,
  оценка; плюс параллельные запросы к одной БД), `tests/test_action_planner_rules.py`,
  `tests/test_action_planner_seed_context.py`, `tests/test_action_planner_flow.py`,
  `tests/test_action_planner_eval_script.py`.
  **Новый случай в набор:** `scripts/add_eval_case.py --id ... --text ... --step ACTION_ID '{...}'` (исходы `plan`/`clarify`/`refuse`,
  `--dry-run`, `--json`, `--strict`): проверяет id и фразу на дубликат, действия и параметры по реестру, плейсхолдеры по сиду, пишет файл
  атомарно в прежнем формате; тесты `tests/test_add_eval_case.py`. Формат случая и порядок для команды - `docs/ORCHESTRATOR_QUICKSTART.md`.
  **Сквозная проверка через HTTP:** `tests/test_e2e_action_layer_http.py` (фраза -> превью -> approve -> run -> история на реалистичном
  сиде: чтение, локальная запись, платное и потолок кредитов, стирающее и повтор подтверждения, аварийный стоп, вложение и загрузка,
  устаревший план, чужой проект); реальный сервер с тестовыми действиями и браузерный проход - `scripts/e2e_command_line_ui_server.py`,
  `scripts/e2e_command_line_ui_smoke.mjs`.
  **Живой прогон (платный)**: `scripts/eval_action_planner.py --live --env-file <файл> --max-requests 300
  --max-usd <лимит> [--concurrency 4] [--shard i/n] [--only ID,ID] [--out отчёт.json]`. По умолчанию проект - реалистичный сид
  `scripts/eval_seed.py` (30 кадров по сценам из 3, 2 и 1 кадра, шот 2 у части кадров, картинки и видео на диске,
  очередь правок, ноды `n_excel_gpt_fw_script/action/shots/qc/report`, пять предложений изменений с версиями,
  мастер-промты `main`/`draft` во временной копии `prompts/`); `--no-seed-realistic` оставляет пустой проект. `--only ID,ID,...` запускает ровно эти случаи (дешёвый перезапуск
  упавших; неизвестный id останавливает прогон до запросов); итоговая проверка - полный набор.
  `--seed-data` теперь необязателен. `--concurrency` задаёт число одновременных запросов (по умолчанию 4: 250
  запросов примерно за 12 минут), `--shard i/n` берёт каждый n-й случай начиная с i-го (шарды не пересекаются и в
  сумме дают весь набор), `--max-usd` и `--est-usd` держат потолок трат и при параллельности: новый запрос
  стартует, только если потрачено + (в полёте + 1) x оценка не превышает потолок. Внутри скрипта (только в его
  процессе и временной БД) лимит планировщика поднят до 10000 вызовов в час, а потолки проекта и суток локального
  бюджета - до 1000 USD: каждый вызов резервирует 0.05 USD, и ~200 вызовов упираются в потолок проекта 10 USD.
  Настоящий потолок живого прогона - `--max-usd`. Отчёт: строка на случай (`ok`, ожидаемые и полученные шаги,
  уточнение, варианты, подставленные умолчания) и сводка с `failed_ids`.
- **Устаревший контекст.** `context_version` = хэш статуса проекта, числа кадров, структуры кадров (номер, id, роль,
  число шотов), узлов, настроек генерации и выбранных сцены/кадра. Файлы, очередь, предложения изменений и версии
  в хэш не входят (меняются часто).
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
  `smart.shots_report`, `smart.run_node_step`, `smart.voiceover_remount`, `smart.assemble_settings_remount`, `smart.delete_frame_check`, `smart.stop_and_check`.
- Платные макросы выносят заявленные лимиты и настройки в параметры (`max_image_ops`, `max_video_ops`,
  `image_generator`, `image_resolution`, при необходимости `video_*`); текущие значения даёт
  `montage.get_cost_basis` (макрос `smart.cost_basis`). План требует подтверждения оператора.
- `smart.run_node_step`: читает настройки ноды, считает верхнюю оценку (`node.get_run_cost_basis`) и запускает
  `node.run_step`. Заявку стоимости (`declare` из результата оценки) передают параметрами макроса; без неё стоимость
  НЕИЗВЕСТНА и план нельзя подтвердить (см. «Оценка прогона текстовой ноды»).
- Таблицы: `action_layer_plan_context`, `action_layer_macros` (создаются `ensure_action_layer_schema`).

## Загрузка файлов (`ctx.call(files=...)`)

`ctx.call(method, path, params=, files={"file": FileUpload})`: multipart только для POST/PUT/PATCH и без `json`.
Принимается только `FileUpload` из `context.py`; сырые bytes, tuple и пути отклоняются (`UploadRejected`), в
контексте только для чтения запись по-прежнему запрещена (`WriteNotAllowed`).

- Источник файла в параметрах действия: ровно один из `source_path` (путь на сервере строго внутри `DATA_DIR`,
  без `..`, без скрытых файлов и папок, симлинк наружу отклоняется) или `data_base64` (до 512 КБ после
  декодирования) вместе с `filename`.
- Политики по виду загрузки (`UPLOAD_POLICIES`): допустимые расширения, предел размера и проверка содержимого по
  сигнатуре (аудио 100 МБ, `.png` для слота сцены 30 МБ, `.mp4` 300 МБ, реф-картинка 30 МБ, файл ноды 25 МБ,
  агент проверки 1 МБ; текстовые типы должны быть UTF-8). Имя файла очищается до безопасного basename.
  Слот картинки сцены принимает только `.png`: эндпоинт ищет и архивирует прежнюю картинку по маске `*.png`.
- Проверка повторяется в `ctx.call`: объект, собранный мимо `FileUpload.build`, не пройдёт. Лимит на запрос 512 МБ.
- В preview (предусловие `upload_source`) файл целиком не читается: проверяются путь, расширение, размер и начало.

## Файлы командной строки (вложения)

Фраза «загрузи озвучку» / «загрузи картинку в кадр 5» теперь доводится до плана: файл выбирается кнопкой
«Прикрепить файл» рядом с командной строкой, загружается на сервер, а `POST /plans/from-text` получает его id.

**Загрузка (`POST /api/action-layer/attachments`).** Файл хранится как
`DATA_DIR/attachments/<id проекта>/<uuid>_<безопасное имя>`; запись о нём лежит в таблице
`action_layer_attachments` (миграция `0006_action_attachments`, идемпотентная: `id, project_id, operator, name,
kind, ext, content_type, size, sha256, rel_path, created_at, consumed_at, consumed_plan_id`). Проверки, по порядку:

- проект существует; тело не больше потолка по `Content-Length` ещё до разбора multipart (иначе 413);
- имя очищается: только последняя часть пути (`..`, `/`, `\` отбрасываются), допустимы буквы, цифры, `._ -`, не
  больше 100 символов, зарезервированные имена Windows (`CON`, `NUL`, `COM1`, `LPT1`...) получают префикс `_`,
  скрытые имена невозможны;
- расширение из белого списка по видам: `image` png/jpg/jpeg/webp (30 МБ), `video` mp4/mov/webm (до 200 МБ),
  `audio` mp3/wav/m4a/ogg (100 МБ), `excel` xlsx/xls (25 МБ), `json` (5 МБ), `text` txt/md (1 МБ; для агента
  проверки ноды). Общий потолок одного файла - `ACTION_LAYER_ATTACHMENT_MAX_MB` (по умолчанию 200), у вида свой
  меньший; действует меньший из двух;
- заявленный MIME должен подходить к виду (пустой и `application/octet-stream` допустимы, решает расширение);
- файл читается потоком с подсчётом размера и sha256; превышение останавливает приём и стирает временный файл;
- сигнатура содержимого соответствует расширению (PNG/JPEG/WEBP/WAV/MP3/OGG/MP4/MOV/WEBM/XLSX/XLS), текст и JSON
  должны быть корректным UTF-8, JSON ещё и разбираться.

Любой отказ не оставляет ни файла, ни строки. Уборка: `cleanup_unused()` удаляет неиспользованные вложения старше
`ACTION_LAYER_ATTACHMENT_RETENTION_DAYS` (7; 0 = не удалять) вместе с файлами и брошенными `.part`; вызывается не
чаще раза в час при загрузке. Использованные (по ним создан план) остаются: путь лежит в шаге плана.

**План с вложением.** `POST /plans/from-text` принимает необязательный `attachments: [attachment_id]` (до 5,
нужен `project_id`). До модели сервер проверяет каждый id: он существует (404 `attachment_not_found`), принадлежит
этому проекту (403 `attachment_wrong_project`), не использован другим планом (409 `attachment_consumed`) и файл на
диске (410 `attachment_missing`). Модель видит только `attachments: [{id, name, kind, size}]`: пути в промпте нет.
Шаг загрузки модель пишет без `source_path`/`data_base64`/`filename` (если написала, они отбрасываются), а сервер
(`planner_uploads.py`) подбирает вложение:

- вид и формат подходят действию (озвучка и музыка - audio; картинка слота и реф - image, слот только PNG; видео
  слота - video, только MP4; файл ноды - excel/json/text; агент проверки - text) и размер в пределах лимита
  действия - тогда в шаг ставится `source_path = attachments/<проект>/<uuid>_<имя>` (внутри DATA_DIR, дальше
  работает прежняя проверка источника в preview и при запуске);
- не подходит - уточнение по-русски («Файл «cat.png» не подходит для действия «Загрузить озвучку»: это картинка, а
  нужен файл: аудио ...»), план не создаётся, файл остаётся свободным;
- подходящих несколько - вопрос «Какой из файлов?» с именами в вариантах (имя в тексте запроса снимает вопрос);
  двум шагам один файл не достаётся;
- не хватает кадра/ноды - вопрос об этом, а не угадывание; без вложений остаётся прежнее «Прикрепите файл...».

Если модель вместо шага задала вопрос, а запрос - загрузка и файл прикреплён, шаг собирается из слов запроса и
вложения (кадр, нода), как и раньше выбрасывая пути из ответа модели.

Файл считается использованным, когда по нему создан план: сервер атомарно помечает его (`consumed_at`) до
`create_plan`; если план не создался (`plan_invalid`), пометка снимается, а после создания к строке привязывается
`consumed_plan_id`. Вложения, которые план не использовал, не тратятся. Ответ содержит `attachments_used` (id
подставленных файлов); интерфейс убирает их из списка. Эти id пишутся и в `planner_plan_linked` (только id).

**Интерфейс.** Под строкой команды кнопка «Прикрепить файл» (принимает только разрешённые типы), у каждого файла имя,
размер, полоса и процент загрузки, ошибка и «Убрать» (готовый файл удаляется и на сервере). Пока файл грузится,
«Составить план» заблокирована. Те же id уходят с первым запросом и с каждым повтором после уточнения; когда
планировщик просит файл, в карточке уточнения есть кнопка «Составить план с файлом», повторяющая исходный запрос
с прикреплёнными файлами. Правила (расширения и потолки) интерфейс берёт из `GET /attachments/rules`, запасные -
в `web/src/lib/attachment-view.ts`.

**Оценка планировщика.** Случаи фикстуры с `context.attachments: [{kind, name, size}]` получают поддельные
вложения (`scripts/eval_seed.py: fake_attachments`; файлов на диске нет, нужны только id, имя, вид и размер),
скрипт `scripts/eval_action_planner.py` и тест-оракул передают их в `plan_from_text(attachments=...)`. Ожидаемый
`source_path` в фикстуре пишется префиксом `attachments/`.

## Волна 3 каталога

| id | tier | флаги | что делает и что проверено по коду |
|---|---|---|---|
| `montage.upload_voice` | destructive | не обратимо | `audio/voice_full.<ext>`, `meta.montage_voice_path`; прежний файл с тем же расширением перезаписывается без архива |
| `montage.upload_music` | destructive | не обратимо | `music/bgm.<ext>`, `meta.bgm_path`; перезапись без архива |
| `montage.upload_image` | destructive | не обратимо | PNG в слот (`finalize_scene_image`): прежняя картинка уходит в `old/scenes`, видео кадра `stale`; программного отката нет |
| `montage.upload_video` | destructive | не обратимо | MP4 в слот: прежний клип в `old/videos`, `stale` снимается |
| `montage.upload_ref` | local_safe | не идемпотентно | новый ручной реф кадра (новый файл, прежние не трогает); имя обязательно |
| `node.upload_file` | destructive | не обратимо | `.xlsx` удаляет прежние xlsx ноды и выключает «брать со стрелок»; одноимённый файл перезаписывается; в режиме проверки `.txt/.md` становится агентом проверки |
| `node.upload_check_agent` | destructive | не обратимо | перезаписывает агента проверки, включает `checkMode` и источник «агент» |
| `node.clear_check_agent` | destructive | не обратимо | удаляет загруженные файлы агента с диска |
| `project.pause` | local_safe | идемпотентно | останавливает выполняющуюся ноду, статус `paused`, прежний статус в `meta.paused_from_status`; не под Actions-v1 |
| `project.resume` | destructive + платное | стоимость неизвестна | возвращает статус до паузы; если это был running-шаг, воркер сам его продолжит и потратит деньги; нужна пауза, ничего не идёт, не под Actions-v1 |
| `project.continue_pipeline` | destructive + платное | стоимость неизвестна | снимает ручной стоп и паузу, продвигает `*_ready -> running` (`maybe_auto_advance(force=True)`); следующий шаг может быть платным; ничего не идёт, не под Actions-v1 |
| `project.reload_xlsx` | destructive | не обратимо | `POST /xlsx/reload`: поля проекта, закадр, промты и атрибуты кадров в БД перезаписываются значениями из `project.xlsx`, недостающие кадры создаются; правки только в БД теряются |
| `montage.set_send_to_main_pc` | local_safe | идемпотентно | флаг `assemble.send_to_main_pc`; при включённом `FLEET_ENABLED` после `music_ready` проект помечается для hub-очереди монтажа и уходит на основной ПК (ASR и сборка там); сам запуск идёт позже, не этим действием. Включить при выключенном fleet нельзя |
| `prompt.get_master` | read | `scope=global` | текст общего промта шага, длина, sha256 |
| `prompt.save_master` | destructive | `scope=global`, не обратимо | `PUT /prompt-files/{step}/{name}`: общий файл для ВСЕХ проектов (для имён группы script_frames_qc это файл шаблона группы в репозитории). Результат: `previous_sha256`, `previous_chars`, `new_sha256`; прежняя версия остаётся в истории версий Studio, восстановить её из каталога нельзя |
| `prompt.create_master` | local_safe | `scope=global` | новый вариант, существующее имя не перезаписывается |
| `prompt.delete_master` | destructive | `scope=global`, не обратимо | удаляет файл варианта, `default` нельзя; результат содержит sha256 удалённого |
| `node.get_run_cost_basis` | read | | верхняя оценка прогона текстовой ноды (ниже) |
| `node.run_step` | destructive + платное | оценка есть у Каркас/Площадка/Шоты/QC | заменена версия из первого среза: добавлены необязательные параметры заявки стоимости |

`project.resume` и `project.continue_pipeline` нельзя подтвердить: оценка явно неизвестна, потому что воркер может
начать любой шаг (картинки, видео, LLM), а какой именно, из параметров не определить. Запуск через слой для них
закрыт, пока не будет честной оценки шага; кнопки интерфейса работают как раньше.

## Медиа: возобновление по известной задаче провайдера

Код: `builtin_media_recovery.py`, эндпоинты `app/web/routers/media_recovery.py`, сервис `app/services/media_admission.py`.

| id | tier | флаги | что делает и что проверено по коду |
|---|---|---|---|
| `media.get_recovery_assessment` | read | идемпотентно | вердикт `media_recovery.assess` по ноде (`videos`, `hero`, `items`, `images`, `music`, `audio`, `sfx_gen`, `assemble`): открытая задача Kie, готовый файл, упавшая задача или неизвестный исход; допускает ли это надзор и код отказа. Ничего не отправляет |
| `media.resume_poll` | local_safe | идемпотентно, без оценки стоимости | спрашивает Kie про УЖЕ известную задачу (`videos`, `items`, `music`) и скачивает готовый результат внутри `media_spend.resume_only()`; после выполнения сверяет журнал задач (`new_paid_requests` = 0, иначе результат не подтверждён) и перечитывает файл (ffprobe или декодирование) |
| `media.list_unknown_outcomes` | read | идемпотентно | ключи проекта, у которых прошлая платная отправка (Kie или Outsee) без известного исхода блокирует повтор: провайдер, возраст, окно `MEDIA_RESUME_WINDOW_S`, до какого времени блокировка действует. Ничего не меняет |
| `media.release_unknown` | destructive | не обратимо, не идемпотентно, `scope=project` | снимает блокировку неизвестного исхода ОДНОГО провайдера (`kie` или `outsee`) по ОДНОМУ ключу после сверки кабинета провайдера. Снова открывает платную отправку: ВТОРОЕ СПИСАНИЕ возможно (это же написано в предпросмотре). Нужна письменная причина (10-500 символов), разовое серверное подтверждение оператора; кто подтвердил, берётся из подтверждения плана, не из параметров. Журнал действий получает запись до и после. Сам ничего не отправляет |

`media.release_unknown`: уровень `destructive` (а не `paid`), потому что действие не тратит деньги само, а снимает защиту от второй траты, и
оценка стоимости для него не имеет смысла; подтверждение, привязанное к хэшу плана, и русский текст предупреждения те же. Если ключ
заблокирован и Kie, и Outsee, снимается только названный провайдер. Отказы предпросмотра: идёт шаг проекта (`project_idle`), у этого
провайдера по ключу нет блокировки в окне (`blocked`). Эндпоинты: `GET /projects/{id}/media-recovery/unknown-outcomes`,
`POST /projects/{id}/media-recovery/release-unknown` (аварийный стоп и идущий шаг проекта их отклоняют). Планировщик: слова
«блокировка», «неизвестный исход», «разблокируй» оставляют шаг; без причины, провайдера или ключа в запросе он должен задать вопрос.

Почему `local_safe`, а не `paid`: режим «только возобновление» отказывает любому новому платному запросу (Kie createTask, Kling, Outsee,
ElevenLabs) до отправки, а провайдеру уходит только запрос состояния оплаченной задачи и скачивание. Если бы действие могло отправить
хотя бы один новый платный запрос, оно было бы `paid` с подтверждением. Повторную платную отправку по провалу действие не выполняет.
Отказы предпросмотра: идёт шаг проекта (`project_idle`), нет задачи, которую допустимо докачать (`resume_available`, с причиной по
коду вердикта); аварийный стоп даёт 409 при выполнении. В таблице подсказок против лишнего (`GUARD_HINTS`) обе записи есть: слова
«докачай», «возобнови» оставляют `media.resume_poll`, «проверь, что с задачей Kie» оставляет только чтение.

## Оценка прогона текстовой ноды

Код: `node_run_cost.py`. Только ноды Каркас, Площадка, Шоты, QC группы `script_frames_qc` (они идут через Vibecode);
код-нода отчёта стоит 0; любая другая нода и любой нештатный выбранный промт - неизвестно (fail-closed).
Оценка названа «верхняя оценка, не откалибровано».

Как считается (по коду `apply_ops_batches` и `enrich_xlsx`, на реальных данных проекта):

1. Пачки: те же `select_frames_for_batches` и `split_vo_units` по 8 ячеек закадра, жирная ячейка идёт соло. Данных
   берётся не меньше, чем при реальном прогоне (выход ноды и промты из снимка не вычитаются).
2. Запросов на пачку: перебор худшего исхода `_run_adaptive` (1 -> 2 -> 4, повтор недостающих кадров, повтор листа при
   недоборе), по два запроса на вызов (повтор JSON в `run_operator_api`). Для пачки из 8 и более кадров это 44 запроса.
3. Токены входа на запрос: файл промта ноды x1.2 + сопроводительный текст проекта + допуски на подсказки, футер,
   системную часть, строки «кадр N = uuid» и (для Каркаса) план проекта + самый большой `db_frames_batch*.json`;
   2 символа на токен; плюс 4000 токенов на хвосты продолжения оборванного ответа.
4. Токены выхода: запрос уходит без `max_tokens`, поэтому потолок допущен: 8000 токенов на рассуждение плюс допуск на
   ячейку по виду ноды (Каркас 1500 симв., Площадка 2000, Шоты 4700, QC 2000).
5. Цена: `api_tracker_hook.vibecode_cost` по снимку прайса Vibecode x3 (`PRICE_MARKUP` каталога, не откалибровано),
   в кредитах Kie (`CREDIT_USD`). Нижняя граница: по одному запросу на пачку.

Допущения, которые код не гарантирует: неуспешные попытки транспорта (таймаут, сеть) не тарифицируются; фактический
расход Vibecode ключа не сверялся с балансом. `credits_spent` действие не возвращает: резерв остаётся полностью.

Порядок: `node.get_run_cost_basis` (читает проект, промт и граф `/db/projects/{id}/graph`) отдаёт `declare` =
`min_llm_requests`, `max_llm_requests`, `max_input_tokens`, `max_output_tokens`. Эти значения входят в параметры
`node.run_step` (и в хэш плана). Предусловие `run_cost_declared` и исполнитель пересчитывают базис и отказывают, если
заявлено меньше расчёта (данные выросли, промт сменили). Без заявки стоимость неизвестна и план не подтверждается.

Оценка может быть в десятки раз выше типичного прогона и не поместиться в потолок `ACTION_LAYER_SESSION_CAP_CREDITS`
(по умолчанию 200): тогда `approve` отвечает 409 `over_budget`. Это следствие худшего исхода, а не ошибка; потолок
меняется настройкой, а калибровка по реальным повторам снизит верхнюю границу.

## Покрытие кнопок интерфейса (сверка 2026-10-07)

Сверялась инвентаризация контролов (74: панель монтажа M01-M54, меню съёмки S01-S04, группа script_frames_qc и меню нод G01-G16) с реестром (на тот момент 84 действия; сейчас 102: волна 5 и `publish.send`, см. `docs/ACTION_LAYER_WAVE5.md`). Сверка по именам действий и схемам параметров; выполнение кнопки и действия на живом сервере не сравнивалось.

| Контрол | Действие каталога | Примечание |
|---|---|---|
| M01, M02 Применить правки | `montage.apply_queue` | `frame_numbers` для чипа «#N»; video-only очередь доступна (ограничение кнопки интерфейса на слой не распространяется) |
| M04 Отменить выбранные | `montage.cancel_queue_ops` | |
| M05 Галерея Outsee | `montage.recover_outsee`, `montage.recover_status` | |
| M06 Разбор аудио | `montage.align_audio` | `method`, `force_asr` |
| M07 Монтаж | `montage.run_montage`, `montage.montage_status` | destructive: удаляет final.mp4 |
| M08, M09 Голос и музыка с компьютера | `montage.upload_voice`, `montage.upload_music` | только с вложением |
| M10 Настройки сборки | `montage.set_assemble_params`, `montage.set_send_to_main_pc` | хвост, громкость, субтитры, пропуск заставки; отправка на ПК отдельным действием |
| M11 Обновить | `montage.get_board` | |
| M14, M16 Новая сцена, шот в сцену | `montage.insert_frame` | `kind=parent` или `child` |
| M15, M18, M19 Объединить, отделить, разделить | `montage.merge_scenes`, `montage.split_scene`, `montage.explode_scene` | destructive |
| M17, M32, M35 Удалить кадр, дочерний | `montage.delete_frame`, `montage.delete_child_frame` | |
| M21, M22 Сгенерировать сцены, пересобрать куски | `montage.generate_scene` | `replace_ns` для кусков |
| M23, M24 Улучшить сцену, сгенерировать изображения | `montage.improve_scene`, `montage.generate_scene_images` | |
| M25, M26 Разобрать на кадры | `montage.set_scene_chain` | |
| M27 Якоря | `montage.set_anchors` | |
| M28 подобрать | `montage.suggest_scene_variants` | единственное действие уровня `paid` |
| M29 Закадр сцены | `montage.set_voiceover_span` | |
| M31 Закадр кадра | `montage.set_voiceover` | |
| M33, M34, M36 Действие, роль, покрытие | `montage.set_coverage` | авто-постановка `image_ai_change`, которую делает интерфейс, при прямом вызове не происходит: очередь строится явно (`montage.queue_ops`) |
| M37 Референсы | `montage.list_ref_assets`, `montage.link_ref`, `montage.upload_ref`, `montage.delete_manual_ref`, `montage.unlink_ref` | |
| M39, M48 Обмен слотов | `montage.swap_slots`, `montage.swap_shots` | |
| M40-M43, M49-M51 Перегенерация, промт, ИИзменение | `montage.queue_ops` | в очередь; деньги при `apply_queue` |
| M44, M52 Удалить картинку, видео | `montage.delete_image`, `montage.delete_video` | |
| M45, M53 Загрузить картинку, видео | `montage.upload_image`, `montage.upload_video` | только с вложением |
| M46 Перетащить картинку | `montage.move_image` | |
| M54 Trim | `montage.set_video_trim` | |
| S01-S03 Меню съёмки | `shot.edit_cell_voiceover`, `shot.add_cell`, `shot.edit_field`, `shot.get_menu` | |
| G01-G04 Группы | `group.insert`, `group.save_from_selection`, `group.update`, `group.delete`, `group.list`, `group.get` | |
| G05, G06 Запустить шаг | `node.run_step`, `node.get_run_cost_basis` | оценка есть только у Каркас/Площадка/Шоты/QC и код-ноды отчёта |
| G07 Отчёт | `group.shots_report` | |
| G08, G09 Пульт, загрузки | `node.get_config`, `node.set_config`, `node.upload_file`, `node.upload_check_agent`, `node.clear_check_agent` | |
| G10 Мастер-промты | `prompt.get_master`, `prompt.save_master`, `prompt.create_master`, `prompt.delete_master`, `node.set_prompt_variant` | |
| G13 Отключить ноду | `node.set_disabled` | |
| G15 Модель ноды | `node.get_llm_route` (только чтение) | |
| G16 Пауза, продолжить, стоп | `project.pause`, `project.resume`, `project.continue_pipeline`, `project.stop` | resume и continue нельзя подтвердить (цена неизвестна) |
| G16 Автопродвижение | `project.set_auto_mode` | добавлено в этой сверке |
| G12, G14 Открепить связи, удалить ноду | нет | см. «Не вошло в каталог» |
| M03, M12, M13, M20, M30, M38, M47, S04, G11 | не нужны | локальные действия интерфейса (выбор, лайтбокс, сворачивание, дорожки) |

`project.set_auto_mode` (`local_safe`, идемпотентно): `PATCH /projects/{id}` с `auto_mode`; результат берётся из ответа PATCH. Включение при идущем шаге отказывает (предусловие `auto_mode_enable_safe`), потому что флаг «ждать ручной ▶» (`arm_auto_await_manual_start`) ставится только вне running-статуса; выключение разрешено всегда. Под управлением Actions-v1 отказывает так же, как пауза. Само включение денег не тратит, но после ручного ▶ воркер идёт по цепочке сам, и следующие шаги могут быть платными: это написано в описании действия. Тест (`tests/test_action_layer_wave3_catalog.py`) проверяет запись в БД и отказ включения при идущем шаге; поведение воркера после ▶ тестом не проверялось.

Новые умные кнопки: `smart.voiceover_remount` (закадр кадра, разбор аудио, монтаж), `smart.assemble_settings_remount` (громкость музыки, монтаж), `smart.delete_frame_check` (удалить кадр, перечитать доску), `smart.stop_and_check` (стоп, статусы). Платных шагов в них нет; монтаж и удаление требуют подтверждения (внутри разрушающие действия).

Покрытие фразами (`tests/fixtures/action_planner_eval.json`, 382 случая): у каждого действия реестра, кроме трёх (`montage.link_ref`, `montage.unlink_ref`, `montage.delete_manual_ref`), есть случай, где оно ожидается в плане. Для этих трёх случаи уточняющие: id рефа модель из контекста не получает (в контексте только кадры), поэтому плана с их шагами фикстура не содержит. Синонимы русского предфильтра (`planner_catalog.SYNONYMS`) есть у всех 102 действий (проверено сравнением ключей с реестром; для 17 действий волны 5 и `publish.send` в основном наборе по 1-2 случая с префиксом `w5_`). Живой прогон модели по новым случаям не выполнялся: офлайн проверены каталог, строгая проверка и умолчания с «идеальной» моделью-заглушкой. Подписи параметров в карточке плана (`PARAM_RU` в `web/src/lib/action-layer.ts`) теперь есть у всех имён параметров действий и макросов; тест `test_every_action_and_macro_param_has_a_russian_label_in_the_ui` не даёт добавить новое имя без подписи.

## Не вошло в каталог и почему

Для этих действий интерфейса отдельного эндпоинта нет, действие не придумывалось.

| UI-действие | почему нет |
|---|---|
| G12 «Открепить связи» ноды | интерфейс убирает рёбра в состоянии холста и сохраняет граф целиком (`PATCH /projects/{id}` с `meta.canvas_graph` и `PUT /workflows/{id}`). Отдельного эндпоинта нет; действие было бы переписыванием всего графа с гонкой против открытого холста |
| G14 «Удалить ноду» | то же: событие холста и сохранение всего графа. Операция `remove_node` есть только внутри общего канала `apply-ops` агента чата (другая семантика: перешивает цепочку и правит снимок прогона), как действие каталога не заведена |
| G15 «Модель ноды» | у нод script_frames_qc выбор модели игнорируется (принудительно Vibecode «GPT 5.6 Sol», `llm_override`); менять нечего. Маршрут читает `node.get_llm_route` |
