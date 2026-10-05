# Контролируемые изменения (Change Control)

Этап 4 плана: точечные изменения параметров, промптов и разрешённой логики ноды. Серьёзные изменения
только с участием разработчика, свободной переписи кода нет. Код: `app/services/change_control/`,
действия: `app/services/action_layer/builtin_change.py`, REST: `/api/change-control`.

Поток: **предложение -> подтверждение оператора в плане слоя действий -> версия + запись значения ->
чтение обратно и сверка -> откат одним планом**. Применяет только человек: подтверждение создаётся
отдельным вызовом оператора (`POST /api/action-layer/plans/{id}/approve`), привязано к хэшу плана и
одноразово. Планировщик и модель могут только предлагать (`change.propose`).

## Уровни

| tier | что | применяется ли |
|---|---|---|
| T0 | совет: текст до 4000 символов | нет, исполнителя нет |
| T1 | параметр из явного списка или выбор **готового** варианта промпта | да, после подтверждения |
| T2 | новый **текст** промпта ноды проекта (новый вариант-файл + выбор слота ноды) | да, после подтверждения |
| T3 | код, логика, общие файлы: текст-diff для разработчика (`status=developer_review`) | **никогда**, ни одной функции применения |

T3 в проект не пишет вообще: ни файлов, ни настроек, ни версий. В diff T3 отклоняется содержимое,
похожее на секреты.

## Что можно менять (allow-list)

Только ноды типов `plan`, `script`, `split`, `excel_gpt` (по канвасу проекта; `enrich_*` = `excel_gpt`).
Остальное отклоняется (`node_type_not_allowed`). Данные: `app/services/change_control/tiers.py`.

| вид (`kind`) | tier | где разрешён | candidate |
|---|---|---|---|
| `prompt_variant` | T1 | все 4 типа | `{"variant": "<имя существующего варианта>"}` |
| `step_params` | T1 | plan, script, split | `{"step": "<шаг ноды>", "values": {поле: число}}` |
| `node_options` | T1 | excel_gpt | `{"values": {поле: значение}}` |
| `prompt_text` | T2 | все 4 типа | `{"text": "<полный новый текст>"}` |

Поля и границы (потолок и пол, ничего выше потолка):

- `step_params`: `plan.duration_seconds` и `script.duration_seconds` 5..1800; `split.cell_min_chars` 1..1000,
  `split.cell_max_chars` 1..2000, `split.cell_avg_min` и `cell_avg_max` 1..2000. За одно применение значение
  меняется не более чем в 2 раза; порядок `min <= avg_min <= avg_max <= max` сохраняется. Менять можно только
  параметр, у которого **уже есть** значение (иначе откатывать не к чему).
- `node_options`: `outputMode` (`text|project_file|sidecar`), `emitKinds` (непустое множество из
  `result|reply_txt|analysis|inputs`), `checkFix`, `useSnapshot` (булевы). Нормализаторы те же, что у
  `PATCH /gpt-operator/{node}`; значение, которое нода подменила бы, отклоняется.
- Нельзя: модель и провайдер, лимиты и бюджеты, вход ноды (`takeFromEdges`, файлы), роль, граф, общие
  (мастер-)файлы промптов, `prompt_overrides` проекта, код, SQL, секреты, сеть.

Выбор варианта и текст пишутся только в слот ноды `meta.prompt_slot_variants[node]["main"]`. Общие файлы
`prompts/<шаг>/<имя>.md` не перезаписываются: T2 создаёт новый файл `cc-<нода>-<хэш8>.md` (другое
содержимое под тем же именем отклоняется). Файл виден в библиотеке всех проектов как вариант, но выбран
только в этом проекте. Если у проекта есть явный `prompt_overrides[шаг]`, он важнее слота: предложение
отклоняется с пояснением (`prompt_overrides` через PATCH проекта синхронизирует общий
`active_variants.json`, поэтому контур его не трогает). Проекты с компоновкой промпта из блоков
(`use_blocks_v2`) для T1/T2 по промпту отклоняются: изменение не повлияло бы на запрос.

## Проверки

При предложении и повторно при применении (детерминированно, без сети и моделей):

- T2: длина 40..200000, не меньше 25% и не больше 4x от текущего текста, нет управляющих символов, текст
  изменился, **все** `{{...}}`-плейсхолдеры и маркеры темы (`[ВСТАВЬ ТЕМУ]`, `(тема ролика)`) текущего
  промпта сохранены, новых плейсхолдеров нет, сохранены маркеры контракта ответа (`contract_markers.json`:
  `"*"` = маркер обязателен, если он есть в текущем тексте; `"шаг/имя"` = обязателен всегда), нет новых
  совпадений с шаблонами секретов (`observation._CREDENTIAL_TEXT`). Каждое нарушение: `lint_failed` и
  список `errors`.
- Применение: tier, digest (`expected_digest` равен хэшу предложения, строка не подменена), статус,
  проект не выполняет шаг (`project_busy`), версия цели равна `base_version`, текущее значение проекта
  равно ожидаемому (`stale_base`), проверки выше, необязательные «теневые» хуки.
- После записи значение читается обратно и сверяется. Расхождение или сбой записи: прежнее значение
  возвращается, фиксируется строкой версии `auto_rollback`, предложение `failed`, ответ `verification_failed`.
  Если вернуть не удалось: `rollback_failed`, предложение `failed` с пометкой «нужен ручной разбор».
- Нарушение проверки при применении: предложение `failed`, **ничего не записано**.

«Теневой» прогон: реестр `shadow.register_shadow_hook(name, fn)` (по умолчанию пуст, платных вызовов контур
не делает). Хук получает копию предложения и возвращает `{"ok": bool, "note": str}`; не ok или исключение
останавливает применение (`shadow_failed`), результаты сохраняются в `shadow` предложения.

## Версии, CAS, откат

Таблицы (создаёт `ensure_change_control_schema`, вызывается рядом с `ensure_action_layer_schema`):
`change_proposals`, `config_versions` (append-only, UPDATE/DELETE блокируются триггерами), `config_pointers`.

Цель (`target`) = `project:<id>:<node_key>:<prompt|options|step_params>`. Для цели ведётся указатель текущей
версии. Запись новой версии и сдвиг указателя идут одной транзакцией: `UPDATE ... WHERE current_version=base`
либо вставка при отсутствии; конфликт = `stale_base`.

- Первое применение создаёт версию 1 (`baseline`, снимок живого значения) и версию 2 (`apply`).
- Откат (`change.rollback`) пишет **новую** версию (`rollback`) с содержимым родителя текущей версии;
  строки не удаляются. Откат отката возвращает применённое значение.
- Статусы предложения: `proposed -> approved -> applied -> rolled_back`; `failed`, `rejected`,
  `developer_review` (только T3). `approved` ставится на время применения.
- Откат закрепляет **прежнее эффективное значение**, а не снимает ключ: слот промпта и ключи
  `excel_gpt_nodes` / `node_step_params` нельзя удалить через `PATCH /projects/{id}` (защита от затирания
  `meta`). Поэтому после отката слот ноды остаётся заполненным прежним вариантом.
- Колонки `rejected_by`, `reject_reason`, `rejected_at` добавляются к существующей таблице `change_proposals` при старте (`ALTER TABLE ... ADD COLUMN`, только SQLite).
- Откат не возвращает данные, файлы, потраченные запросы.

## Связь с ревизиями

Изменение пишется в те поля, которые входят в `revisions.compute_input_revision` ноды (слот промпта, файл
выбранного варианта, `excel_gpt_nodes`, `node_step_params`). Поэтому отложенные билеты восстановления
**этой** ноды устаревают, других нод это не касается; `structure_hash` и разрешения запуска не меняются
(проверено тестами `tests/test_change_control_revisions.py`). Для параметров шага откат возвращает прежнюю
ревизию побайтно; для слота промпта ревизия после отката отличается от исходной (слот закреплён).

## Базовый промпт

`GET /prompt-base` использует то же серверное разрешение варианта, что проверки T2 (`PromptHandler._resolve` в
`targets.py`), поэтому `base_text_sha256` равен `candidate.base_text_sha256` предложения T2, построенного на
этом же состоянии. `source`: `slot` (слот ноды), `project_override` (`prompt_overrides` проекта),
`global_active` (общий активный вариант шага), `default`, `missing` (файл варианта не найден или не
читается). Текст ограничен `prompt_limits.max_chars`; при превышении `truncated: true`, а `text_chars` остаётся
полной длиной файла. Доступно только для нод, где разрешён `prompt_text`; иначе 422 `kind_not_allowed` /
`node_type_not_allowed`.

## Аудит

Каждое действие дописывает строку в `action_layer_history`: `change_proposed`, `change_rejected` (`outcome: rejected`, `error` = причина), `change_applied`
(`outcome`: `applied | refused | stale_base | auto_rolled_back | rollback_failed`), `change_rolled_back`
(`rolled_back | refused | stale_base | auto_rolled_back | rollback_failed`). `params` содержит
`proposal_id`, `target`, `version`, `tier`, `digest`; текст промпта в журнал не попадает. Автор
предложения хранится в `change_proposals.proposer`, подтвердивший в `approved_by` (берётся из
израсходованного подтверждения плана, а не из параметров).

## Кто предложил

REST: автор из `AuthDep` (`web_operator:<хэш>` или `local_web_operator`), поля автора в теле нет. Действие
`change.propose`: если план создан планировщиком (`action_layer_plan_context.source = 'planner'`), автор
`planner:<оператор>`, иначе оператор. Применять и откатывать могут только личности, прошедшие проверку
подтверждения (не `planner:`, `model:`, `agent:`, `system`). Предложение планировщика применяется так же, как
любое другое: оператор подтверждает отдельный план с `change.apply`.

## REST `/api/change-control` (все за AuthDep, POST ещё и LocalOnly)

Эндпоинтов одобрения, применения и отката **нет** (проверено тестом по OpenAPI). Отклонение есть, но оно только закрывает предложение и ни при каких условиях не меняет значения проекта.

| метод | путь | ответ |
|---|---|---|
| GET | `/allow-list` | `{tiers, node_types, step_param_limits, max_step_param_factor, prompt_limits}` |
| GET | `/proposals?project_id=&status=&tier=&node_key=&limit=` (limit 1..200, по умолчанию 50) | `{count, items:[proposal]}`; в списке `diff` пустой, `candidate.text` заменён на `candidate.text_chars` |
| GET | `/proposals/{id}` | `proposal` + `current_version` (для T1/T2); 404 `proposal_not_found` |
| POST | `/proposals` | тело ниже, ответ `proposal` (+ `deduplicated: true`, если такое предложение уже открыто) |
| POST | `/proposals/{id}/reject` | тело `{reason}` (3..500 символов, лишние поля -> 422); ответ `proposal` со `status:"rejected"`. Только из `proposed` или `developer_review`; `approved`, `applied`, `rolled_back`, `failed`, `rejected` -> 409 `status_not_rejectable`; не человек -> 403 `operator_required`; нет предложения -> 404. Пишет `rejected_by`, `reject_reason`, `rejected_at` и событие `change_rejected` |
| GET | `/prompt-base?project_id=&node_key=` | текущий базовый промпт ноды: `{text, source, variant, text_chars, truncated, base_text_sha256, project_id, node_key, node_type, step}`; при отсутствии файла `source:"missing"`, `text:""` и `missing_reason` (по-русски). Только чтение |
| GET | `/versions?target=&limit=` (limit 1..500) | `{target, current_version, items:[version]}` по убыванию версии; `current_version=0`, если версий нет |

Тело `POST /proposals` (лишние поля -> 422):
`{tier: "T0|T1|T2|T3", kind, rationale (1..1000), candidate: object, project_id?: int, node_key?: string}`.
`project_id` и `node_key` обязательны для T1/T2. Допустимые пары: T0 `suggestion` (`{text}`); T1
`prompt_variant | node_options | step_params`; T2 `prompt_text`; T3 `developer_review`
(`{summary (<=500), diff (<=100000), path?: string}`).

`proposal`: `id, tier, project_id, node_key, kind, target, base_version, base_digest, candidate, diff, digest,
rationale, proposer, status, error, approved_by, approved_at, applied_version, shadow, applicable, can_apply,
rejected_by, reject_reason, rejected_at, stale, created_at, updated_at, applied_at`. `stale = true`, если предложение открыто (`proposed`/`approved`, T1/T2), а текущая версия цели уже не равна его `base_version`: интерфейс может предложить отклонить. `candidate` содержит серверные поля: `variant_sha256` (T1 вариант),
`variant` и `base_text_sha256` (T2), нормализованные значения. `diff`: unified diff для текста, строки
`было -> стало` для остального. `digest` подставляется в `change.apply` как `expected_digest`.
`can_apply = applicable and status in (proposed, approved)`.

`version`: `id, target, version, parent_version, kind (baseline|apply|rollback|auto_rollback), project_id,
node_key, content, digest, source_proposal_id, applied_by, applied_at`; в `content` текст заменён на
`text_chars`.

Ошибки: `{"detail": {"code", "message", "errors": [..]}}`. Коды предложения: `bad_tier`,
`kind_tier_mismatch`, `bad_rationale`, `proposer_required`, `bad_candidate`, `project_required`,
`project_not_found` (404), `bad_node_key`, `node_not_found`, `node_type_not_allowed`, `kind_not_allowed`,
`field_not_allowed`, `variant_not_found`, `lint_failed` (422, `errors` с причинами), `too_many_open` (429, до
50 открытых на проект), `bad_target`.

## Действия слоя действий

| id | tier | параметры | результат |
|---|---|---|---|
| `change.list` | read | `project_id?, status?, tier?, node_key?, limit?` | как `GET /proposals` |
| `change.get` | read | ровно одно: `proposal_id` или `target` | `proposal` или `{target, current_version, items}` |
| `change.propose` | local_safe | `tier, kind, rationale, candidate, project_id?, node_key?` | `{proposal_id, status, digest, tier, target, base_version, diff, deduplicated}` |
| `change.apply` | destructive, нужно подтверждение | `proposal_id` (32 hex), `expected_digest` (64 hex) | `{ok, proposal_id, status:"applied", target, version, previous_version, digest}` |
| `change.rollback` | destructive, нужно подтверждение | `target`, `expected_version` (>=2), `reason?` | `{ok, target, version, restored_from_version, previous_current, proposal_id}` |

Стоимость всех действий 0 кредитов. В preview `change.apply` и `change.rollback` проверяются предусловия
(`apply_ready`, `rollback_ready`: те же проверки, что при применении, без записи), поэтому устаревшее или
недопустимое предложение даёт `422 plan_invalid` ещё до подтверждения. `destructive_effects` показывает
оператору на русском: номер предложения (первые 8 символов) и контрольную сумму для `change.apply`; для
`change.rollback` название цели («параметры шага ноды «Разбивка на блоки», проект 7») и номер версии, без
внутренних id действий и без строки `target`.

Сценарий кнопки «Применить» в интерфейсе:
1. `POST /api/action-layer/plans/preview` `{steps:[{action_id:"change.apply", params:{proposal_id, expected_digest: proposal.digest}}]}`;
2. показать `preview.destructive_effects` и `proposal.diff`;
3. `POST /plans/{plan_id}/approve`, затем `POST /plans/{plan_id}/run`, опрос `GET /plans/{plan_id}`.

Откат: показывать, если `proposal.status == "applied"` и `current_version == proposal.applied_version`;
план `change.rollback` с `target = proposal.target`, `expected_version = current_version`. Если после
применения были другие изменения цели, откат отвечает `stale_base`.

Ошибки шага (в `steps[].error` как `ChangeRefused: <code>`): `operator_required` (403-класс, не человек),
`proposal_not_found`, `digest_mismatch`, `proposal_tampered`, `tier_not_applicable`, `status_not_applicable`,
`project_busy`, `stale_base`, `lint_failed`, `shadow_failed`, `verification_failed`, `rollback_failed`,
`nothing_to_roll_back`, `bad_target`, `node_not_found`, `project_not_found`.

## Ограничения

- Применение идёт через `PATCH /projects/{id}` (валидация и события проекта сохранены); запись значения и
  запись версии не в одной транзакции БД, согласованность держится порядком шагов, проверкой после записи и
  авто-откатом. Параллельные применения одной цели внутри процесса сериализуются блокировкой, между
  процессами защищает CAS указателя.
- Ролей «разработчик» и «оператор» нет: подтверждение одно (оператор). Серьёзное (T3) вообще не применяется.
- Качество результата не измеряется: контур проверяет контракт и целостность, а не смысл. После T2 нужен
  осмысленный контрольный прогон человеком.
- Файлы вариантов `cc-*.md` при откате остаются (история цела), новые варианты видны в общей библиотеке.
- Старый канал чат-оркестратора (`edit_files`, `git_commit_push`) контуру не подчиняется; его закрытие вне
  этого модуля.
