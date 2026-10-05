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
- **Что видит модель.** Не весь реестр, а не более `ACTION_LAYER_PLANNER_MAX_CATALOG` (по умолчанию 24)
  действий: офлайн-предфильтр `planner_catalog.select_catalog` без сети и модели. Отбор по основам слов запроса
  (название, теги, префикс id, короткое описание, таблица синонимов), контексту проекта (выбранные сцена/кадр при
  «этот/эту», идущая работа, пустая доска) и встроенным макросам (подходящий макрос подтягивает действия своих
  шагов). Всегда включены `montage.get_board` и `project.stop`. Редкие слова весят больше частых (idf); слабые
  совпадения отсекаются, расплывчатые запросы (до двух слов) берутся шире. Если ничего не подошло или остаток запроса
  после вырезанных фраз вне каталога («удали проект», git, правка кода, терминал, пароли) слабый, показывается
  небольшой безопасный набор (чтение и остановка) и подсказка `hint`: модель должна вернуть уточнение. Запись
  действия компактная: `id`, короткое название `t`, `risk`, параметры `p` (`имя*:тип`, `*` обязательный, `{a|b}`
  значения, `[..]` список, `int[1..25]` диапазон); длинных описаний нет. Контекст проекта (статус, число кадров,
  узлы, выбранные сцена/кадр) и запрос передаются как данные в JSON. Тексты кадров и тема в контекст не попадают.
  Если лимит не меньше размера реестра, фильтр выключен и показывается весь каталог.
- **Ответ модели.** `{steps:[{action_id, params, depends_on}], needs_clarification, explanation_ru}`. Любые поля
  подтверждения, оператора, запуска, инструментов на любом уровне отклоняют план целиком (`forbidden_field`).
- **Строгая проверка.** Неизвестный id, лишние/неверные параметры (`registry.validate_params`), чужой
  `project_id`, шагов больше `ACTION_LAYER_MAX_STEPS`, неверный `depends_on` -> весь план отклонён. Проверка идёт по полному реестру, но id, которого не было в промпте
  (`action_not_offered`), тоже отклоняется: модель может использовать только показанное (422
  `planner_rejected`, поле `reason`), без починки и без повторного запроса.
- **Повтор.** Ровно один повтор, и только при ошибке формата ответа: обрезан, не JSON, неверный каркас. Повтор
  содержит текст ошибки. Всего не больше двух POST на запрос. Транспортная ошибка не повторяется (502).
- **Лимит.** `ACTION_LAYER_PLANNER_CALLS_PER_HOUR` (по умолчанию 30) на оператора за скользящий час; считается
  каждый POST (повтор тоже). Превышение: 429 `rate_limited`, запрос к модели не отправляется.
- **Журнал.** Каждый вызов модели - строка `planner_call` (model, токены, оценка USD по прайсу каталога, не
  откалибрована, исход, `catalog_offered`/`catalog_total`/`catalog_no_match`, `prompt_chars`); связь с созданным планом - `planner_plan_linked`.
- **Оценка предфильтра.** `tests/fixtures/action_planner_eval.json` (запросы на русском с ожидаемыми id),
  `tests/test_action_planner_eval_offline.py` (полнота 100% без модели, запросы вне каталога не показывают платных и
  разрушающих действий) и `scripts/eval_action_planner.py --live` (платный, `--max-requests`, `--max-usd`).
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

## Не вошло в каталог и почему

Для этих действий интерфейса отдельного эндпоинта нет, действие не придумывалось.

| UI-действие | почему нет |
|---|---|
| G12 «Открепить связи» ноды | интерфейс убирает рёбра в состоянии холста и сохраняет граф целиком (`PATCH /projects/{id}` с `meta.canvas_graph` и `PUT /workflows/{id}`). Отдельного эндпоинта нет; действие было бы переписыванием всего графа с гонкой против открытого холста |
| G14 «Удалить ноду» | то же: событие холста и сохранение всего графа. Операция `remove_node` есть только внутри общего канала `apply-ops` агента чата (другая семантика: перешивает цепочку и правит снимок прогона), как действие каталога не заведена |
| G15 «Модель ноды» | у нод script_frames_qc выбор модели игнорируется (принудительно Vibecode «GPT 5.6 Sol», `llm_override`); менять нечего. Маршрут читает `node.get_llm_route` |
