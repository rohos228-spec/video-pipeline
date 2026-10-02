# Оставшиеся текстовые/LLM-JSON ноды: что нужно для ограниченного повтора оркестратором

Репозиторий: `C:\video-pipeline\video-pipeline-observe-v1` (только чтение, `.env` не открывался, тесты и сеть не запускались).
Все ссылки вида `файл:строка` относятся к этому репозиторию. Если что-то нельзя определить по коду, это сказано прямо.
Охват: `enrich_1..5`, `sfx_plan`, `scene_design` (legacy), `sd_agent`, `sd_assemble`, `animation_prompts`, текстовая часть `hero`/`items`.

## 0. Итог в одной таблице

| Нода | Capability сейчас | Что есть в журнале | Вердикт по повтору | Главная причина |
|---|---|---|---|---|
| `enrich_1..5` | отдельной записи нет (`node_capabilities.py:136-141` даст `unsupported`); живые ноды хранятся как `excel_gpt` (`node_capabilities.py:58-60`, `managed=True`, повтор включён) | полный (как у excel_gpt) | это та же нода, что `excel_gpt`; отдельно включать нечего. Риск только в старых холстах с типом `enrich_N` | алиас есть лишь в `observation.py:769`, остальные места его не знают, но закрыты по умолчанию (fail-closed) |
| `sfx_plan` | запись есть, `managed=False`, только `READ_ACTIONS` (`node_capabilities.py:71-73`) | вход, разбор (без `payload_required`), результат; нет `data_applied` | `retry_same` возможен, это лучший первый кандидат, но нужны 5 правок кода (раздел 2.2) | ноде нечем подтвердить результат в `reconcile` (`actions_recovery.py:557-558`) и нет типизированного JSON-инцидента |
| `scene_design` (legacy-тип) | нет, `unsupported` | нет (контракт описан только для `sd_agent` camera и `sd_assemble`, `scene_contracts.py:99-104`) | только человек | устаревший тип, делит статус и шаг с веером sd_* |
| `sd_agent` (skeleton/characters/world/camera/action) | нет, `unsupported`; описательный контракт только для camera | только camera (`runner.py:382`); у остальных четырёх агентов только `model_*` | повтор небезопасен, включать только наблюдение и диагностику | веер из 5 нод идёт одним шагом; неоднозначная идентичность ноды; чекпоинты на диске; частичные коммиты |
| `sd_assemble` | нет, `unsupported` | вход, разбор, результат (`scene_contracts.py`) | повтор небезопасен | перед запросом к модели нода коммитит новые Frame-строки (`camera_expand.py:595`, `scene_design.py:326`); при `chrono_dyn` вообще нет вызова LLM |
| `animation_prompts` | нет, `unsupported`; описательный контракт `animation_prompts_contract()` с `managed: False` | вход, разбор батча, результат | сначала только наблюдение; повтор условно (только сбой до первого коммита батча) | коммит каждого батча, vision-вход, тихий локальный fallback, нет `payload_required` |
| `hero`, `items` (текстовая часть) | нет, `human_only` в `MEDIA_RECOVERY_DECISIONS` (`node_capabilities.py:91-98`) | нет разбора; только `model_*` | только человек | текст — свободный промпт картинки внутри платной генерации с HITL; отделить нельзя |

## 1. Общая механика, на которую ссылаются таблицы

### 1.1. Как нода попадает под управляемый запуск и как работает повтор (пронумерованные ворота)

| № | Ворота | Где в коде | Что значит для текстовой ноды |
|---|---|---|---|
| G1 | Тип инцидента строго `invalid_json` или `missing_payload`, инцидент актуален и не `evidence_incomplete` | `actions_recovery.py:174-178`, `decision_v1.py:23,151,166` | любое другое (`contract_rejected`, `api_error`, `uncertain_request`) повтор запрещает |
| G2 | `capability.managed` и действие входит в `capability.actions` | `actions_recovery.py:226-229`, `claim` `:397-400`, `managed_launch.py:69,143-148`, `observation.py:777-787` (`require_managed_capability` → hold) | без записи в `_CAPABILITIES` с `managed=True` нода в managed-режиме даже не стартует |
| G3 | Все `model_start` попытки имеют полный `model_end`, есть `node_error`, нет ни одного `data_applied`/`apply_input`/`apply_error` | `actions_recovery.py:235-240` | повтор невозможен после любого применения данных, в том числе частичного |
| G4 | Если `native_json_retry=True`, нужен факт `json_retry` | `actions_recovery.py:244-245` | у нод без внутреннего JSON-повтора поле обязано быть `False` |
| G5 | Нативные повторы исчерпаны: `abandoned_at`, `total_fails[last_running] >= 9`, не «спит», проект `paused`; NodeRun в `failed`; нет другой активной ноды | `actions_recovery.py:265-276`, `step_failure_policy.py:28-30,415-444` | счётчик общий на статус `*_ing`, не на ноду |
| G6 | `input_revision` не изменился | `actions_recovery.py:111-146,280-286` | хэшируется: текстовые колонки Project, таблицы Frame/Scene/FrameText/SceneDesignCell, ключи meta с `canvas`/`workflow_graph`, `settings`, все `prompts/**/*.md`, шесть файлов кода (`enrich_xlsx.py`, `pipeline.py`, `gpt_client.py`, `db_apply.py`, `node_write_contract.py`, `gpt_text_builder.py`). Не хэшируются: Entity, `meta.scene_registry`, `meta.ai_jobs`, `meta.scene_design`, файлы-чекпоинты на диске, код `sfx_plan.py`/`scene_design/*`/`make_animation_prompts.py` |
| G7 | Тело первого запроса повтора совпадает с исходным | `actions_recovery.py:413-428`; маскирование штампов `:82-91,316-325` | поле `volatile_attachment_names` нужно, только если в тексте запроса есть имена файлов с временными метками |
| G8 | Бюджет, один повтор на ноду (UNIQUE) | `actions_recovery.py:246-251,50-53` | `retry_same` и `retry_profile` делят один повтор |
| G9 | Проверка результата в `reconcile` | `actions_recovery.py:499-561` | обязательно: `node_done`, `node_end`, `result_confirmed` (= `data_applied` И `parse_result.payload_present`, либо разрешённый QC no-op), `graph_matches`, `audit_available`, `no_user_stop`, `permission_active`, `requests_complete`, `transactional_done` |
| G10 | Продолжение после проверки | `actions_recovery.py:594-633`; `recovery_continuation='none'` — ничего не продолжает и HITL не трогает (`:600-609`), для `none` done-переход принимается как доказательство границы (`:516-538`) | |
| G11 | Смена профиля | `profiles_v1.py:115` (`profile_compatible`), `:72-87` (`effective_body` принимает только Responses с `input`-строкой или списком `{role,content:str}`, `messages` и multimodal отвергаются), `:209-215` (любой вариант кроме `original-contract-v1` требует записанного `parse_result.payload_required=True`), `:55-70` и `:23` (транспорт только синтетический, `PRODUCTION_CATALOG={}`) | на деле `retry_profile` сейчас возможен только на синтетическом петлевом транспорте; на ноде с провайдером vibecode режим chat/completions (`gpt_api.py:1054-1066`) и профиль не применим |
| G12 | Как диагностика типизирует сбой | `diagnosis.py:104-106,130-131` (`invalid_json`/`missing_payload` только при `parse_result.payload_required is True` и `parsed is False` или `payload_present is False`), `:193-197` (`domain_check`/`runtime_check` failed → `contract_rejected`), `:206-217` (`model_error` → `api_error`/`uncertain_request`), `:218-221` (любой `node_error` без типизированных фактов → `contract_rejected`, причина «неизвестна», статус `human_review`) | нода без `payload_required` в разборе никогда не получит повторяемый инцидент |

### 1.2. Нативный повтор на уровне шага (одинаков для всех нод ниже)

* Исключение из `advance_project` ловит `app/main.py:420-426` → `record_step_failure` (`step_failure_policy.py:294`).
* Счётчик `total_fails` ведётся по ключу running-статуса (`:415-418`): 3 неудачи подряд → сон (30 мин, либо 2 мин для сообщений с маркерами формата, `:36-66`), 9 неудач → `abandoned_at`, статус `paused` (`:429-444`). Это и есть «исчерпание нативного повтора» для G5.
* 402 / «нет кредитов» и `infra_db_locked` не наращивают счётчик (`:315-351`, `:353-378`), поэтому повтор по ним невозможен по G5.
* Внутренние повторы запроса (`gpt_api.chat`, цикл на `:2809-3099`, число попыток `settings.gpt_max_retries`) не создают `json_retry`; каждый POST — отдельный `model_start` с зарядом бюджета (`observation.py:597-598`). Разбиение «1→2→4» (`gpt_api.py:2592-2708`) срабатывает только при вложении `db_frames.json`; у нод без него (sfx_plan, sd_*, animation) ветка пропускается.

### 1.3. Что создаёт HITL

HITL-запрос внутри шага создают только `plan`, `script`, `hero` (legacy-вариации), `images`, `videos`, `assemble` (`make_plan.py:81`, `make_script.py:100`, `generate_hero.py:923`, `generate_images.py:2023`, `generate_videos.py:1079`, `assemble.py:588`). Ноды из этого отчёта HITL внутри шага не создают. Карта `auto_advance.py:100-200` лишь сопоставляет `*_ready` с видом HITL для автоподтверждения в `auto_mode` (`_apply_approve`, `auto_advance.py:822`); это нативная логика, оркестратор в неё не вмешивается. Явно сказано, что оркестратор HITL не снимает: `diagnosis.py:246-253`, `actions_recovery.py:600-609`.

### 1.4. Подтверждённые общие дефекты (влияют на несколько нод)

1. **`read_contract` для `sfx_plan` отдаёт контракт excel_gpt.** `sfx_plan` имеет `result_kind='structured'` и поэтому в `decision_v1.py:315-358` попадает в ветку excel_gpt (`node_config`, `_batch_footer`), а не в описание SFX. Сейчас это уже происходит при `managed=False`.
2. **Журнал разбора не пригоден для типизации у sfx/scene/animation:** ни один из трёх `parse_result` не содержит `payload_required` (`sfx_plan.py:408-411`, `scene_contracts.py:581-590`, `native_animation_contracts.py:331-340`). Результат: сбой разбора превращается в `contract_rejected` по `runtime_check` (`diagnosis.py:196-197`), а не в `invalid_json`.
3. **`result_confirmed` требует `data_applied`**, которое пишет только `db_apply.apply_ops` через `@observe_apply` (`db_apply.py:1261`, `observation.py:687-706`). `sfx_plan` в `db_apply` не пишет (записывает `meta.ai_jobs` и файл `sfx_plan.json`, `plan_sfx.py:48-53`, `sfx_plan.py:238-251`).
4. **Верификаторы в managed-режиме не блокируют.** Для `split`/`image_prompts`/`plan`/`script` при провале проверки идёт `set_held` + `raise` (`node_capabilities.py:287-297,395,473-478`; вызов `observation.py:813-816`). Для sfx/scene/animation результат только пишется (`observation.py:790-812`), нода остаётся «успешной».
5. **Идентичность ноды при веере sd_agent неоднозначна** (`observation.py:729-735`): в managed — `RuntimeError`, в observe — запись «gap» и отсутствие контекста попытки.

---

## 2. Детально по нодам

### 2.1. `enrich_1..enrich_5`

| Пункт | Факты |
|---|---|
| 1. Кто исполняет, LLM, модель, промпт | Диспетчер `pipeline.py:151-158` → `enrich_xlsx.run` (`enrich_xlsx.py:1236`) для всех пяти слотов и для `excel_gpt` (один и тот же код; слот берётся из статуса, `_resolve_slot_idx` `:1129`). Реестр: `node_registry.py:74-88`. Старые типы `enrich_N` при сохранении графа превращаются в `excel_gpt` (`excel_gpt_node.py:203-216`, вызовы `workflows.py:54,88`, `settings_default.py:89`). Основной вызов `run_operator_api` (`enrich_xlsx.py:2557`, `gpt_operator_client.py:166,321`) → `gpt_api.chat` (`gpt_operator_client.py:377`). Другие маршруты внутри той же ноды: пакетные `run_apply_ops_batched` (`enrich_xlsx.py:2369,2510`), `run_scene_grammar_batched` (`:2183`), проходы разметки (`_run_four_node_markup_pass` `:453`). Модель: `bind_project_llm(project, status)` (`pipeline.py:132`, `llm_override.py:50-`) — переопределение с канвас-ноды, иначе `settings.gpt_model_effective` (`gpt_api.py` строка `use_model = ... or "gpt-5.5"` в `chat`). Промпт: `read_resolved_project_prompt` (`enrich_xlsx.py:1326`) из `prompts/05_excel_gpt`, слот `main` ноды; старые каталоги `prompts/05a..05e_enrich_N` лежат в репозитории. |
| 2. Контракт, разбор, применение | JSON apply-ops `{"ops":[{frame_uuid,fields}],"characters":[],"scenes":[],"report"}`; для QC-режима текстовый вердикт или `{"ops":[]}`. Разбор `db_apply.extract_apply_ops_json`, факт `record_parse` с `payload_required = not is_check` (`gpt_operator_client.py:436,632`). Валидаторы: фильтр полей `node_write_contract.filter_ops_for_node` (`enrich_xlsx.py:2306`), покрытие N/N (`:2752-2768`), harness-гейт `excel_gpt` (`:830-832`). Применение `db_apply.apply_ops` (`db_apply.py:1261`): поля Frame, Entity по `code` (`upsert_characters` `db_apply.py:1026-1071`), `meta.scene_registry` (`:735-745`); все операции присваивания, повторное применение безопасно. Исключение `replace_frames` в этой ноде не используется. Пакетные маршруты коммитят каждый батч (`enrich_xlsx.py:2306-2324`), а одиночный маршрут коммитит до проверки покрытия (`:2725-2768`), то есть возможен «применено, затем отказ». |
| 3. Нативный повтор и исчерпание | Один JSON-повтор (`gpt_operator_client.py:557-632`, факт `json_retry` на `:561`), TXT-повтор для report_only (`:397-420`), транспортные повторы `chat`; на уровне шага 3×3 и 9 неудач (раздел 1.2). Исчерпание = G5. |
| 4. Классы сбоев и диагностика | Битый/пустой apply-ops при `payload_required=True` → `invalid_json`/`missing_payload` (G12); отказ покрытия/`ApplyOpsError`/harness → `runtime_check`/`node_error` → `contract_rejected`; QC-вердикт fail → `domain_check` → `contract_rejected` (`diagnosis.py:193-195`); сеть/5xx → `api_error`; таймаут → `uncertain_request` + hold. |
| 5. Безопасен ли повтор | Для типа `excel_gpt` уже включён: `retry_same`/`retry_profile`/`finish`, `profile_compatible=True`, `native_json_retry=True`, продолжение `excel_gpt_chain` (`node_capabilities.py:58-60,30-33`). Повтор остаётся безопасным благодаря G3: любой применённый батч блокирует повтор. Профиль на vision-входе падает при формировании тела (`profiles_v1.py:81-86`). **Для типа `enrich_N` в снимке графа** `capability_for` вернёт `unsupported` (`node_capabilities.py:136-141`) и `validate_scope` откажет (`managed_launch.py:69`, `require_managed_capability`), то есть закрыто, а не опасно. Алиас действует только внутри `observe_attempt` (`observation.py:769`). Что менять: **ничего в `NodeCapability`.** Если нужны живые старые холсты, правильнее мигрировать граф (`migrate_enrich_nodes`), чем добавлять алиас: `managed_launch.native_entry` ставит `active_excel_gpt_node_key` только при `effective_node_type(node)=='excel_gpt'` (`managed_launch.py:261-263`), а `effective_node_type` возвращает `enrich_N` без изменений (`excel_gpt_node.py:76-91`). |
| 6. Пробелы наблюдения | Явных `node_input`/`node_output`/`node_result_verification` в `enrich_xlsx.py` нет (поиск `record_fact` в файле пуст). Покрыто через `observe_model`, `record_parse`, `observe_apply`, `json_retry`, `domain_check`. Побочные проходы (`_run_four_node_markup_pass`, `_ensure_four_node_scene_shots`, `run_scene_grammar_batched`) дают `model_*` и `data_applied`, но не `parse_result`, поэтому их сбой не типизируется как `invalid_json`. Флаг `managed` для типа `excel_gpt` `True`; для `enrich_N` — `unsupported`. |
| 7. Границы HITL | Внутри шага HITL нет. Между шагами на холсте могут стоять `hitl_*` ноды (`node_registry.py:18-20`) и QC-ворота (`gate_allows_successors`, `gpt_operator.py:1760`). Продолжение excel-цепочки после проверки (`_after_excel_gpt_done`, `actions_recovery.py:594-633`) двигает только следующие excel-слоты по стрелкам, HITL не одобряет. |
| 8. Порядок и тесты | Нулевой этап. Тесты (заглушки, раздел 4): охранный тест, что снимок с `enrich_N` отклоняется `validate_scope` с понятной причиной; тест, что `slot_index_from_node` и `migrate_enrich_nodes` дают `excel_gpt` для `enrich_1..5`. |

### 2.2. `sfx_plan`

| Пункт | Факты |
|---|---|
| 1. Исполнитель, LLM, модель, промпт | `pipeline.py:171-174` → `plan_sfx.run` (`plan_sfx.py:18`) → `plan_sfx_events` (`sfx_plan.py:164`) → `text_job` (`ai_result_io.py:77`) → `gpt_client.gpt_ask_fresh` → `gpt_api.chat`. Один логический запрос, максимум 2 попытки `text_job` (`sfx_plan.py:169`, цикл `ai_result_io.py:103`), каждая со своими транспортными повторами. Промпт — константа `_PLAN_PROMPT` в коде (`sfx_plan.py:142-161`), не из `prompts/`. Модель: `bind_project_llm` по узлу `sfx_plan`, иначе значение по умолчанию. Режим «выключено» (`SFX_ENABLED=false`) переводит в `sfx_plan_ready` без работы (`plan_sfx.py:26-31`); настройка по умолчанию включена (`settings.py:239`). |
| 2. Контракт, разбор, применение | JSON `{"events":[{frame_number,t_start,duration,kind,prompt,gain,duck}]}`. Разбор `parse_json_object` (`ai_result_io.py:214-238`) и `_parse_events` (`sfx_plan.py:116-139`); валидатор `validate_events` (`:78-113`): вид из 7, `t_start` в границах, длительность 0.3–8 с, `gain` 0.05–1, непустой prompt, зазор ≥0.8 с, ≤3 события на кадр (число событий «4–12» из промпта не проверяется). Применение: чекпоинт `meta.ai_jobs["sfx_plan"]` при успехе (`ai_result_io.py:127-128`), файл `sfx_plan.json` (`sfx_plan.py:238-251`), `meta["sfx_plan"]=True` (`plan_sfx.py:48-50`), коммит в конце (`:53`). В таблицы Frame/Scene нода не пишет, `db_apply` не использует. Идемпотентность: файл перезаписывается, чекпоинт при повторе возвращается без запроса к модели (`ai_result_io.py:94-97`, 0 попыток). Если сбой случился между сохранением чекпоинта в памяти и коммитом, чекпоинт откатится и повтор заново оплатит один запрос. |
| 3. Нативный повтор | `text_job` с обратной связью (до 2 попыток) + `chat`-повторы + шаг 3×3/9 (раздел 1.2). Факта `json_retry` нет. Сообщение об отказе `ai_job sfx_plan: не прошло валидацию` не содержит маркеров короткого сна, поэтому сон 30 минут (`step_failure_policy.py:36-66`). |
| 4. Классы сбоев и диагностика | Невалидный JSON или провал валидатора после обеих попыток: `text_job` бросает `RuntimeError` (`ai_result_io.py:142`) **до** `record_sfx_parse`, `parse_result` нет → `node_error` → `contract_rejected`, причина «неизвестна», `human_review` (`diagnosis.py:218-221`). Провал валидации уже после `text_job` (`sfx_plan.py:221-232`) пишет `parse_result` без `payload_required` + `runtime_check failed` → `contract_rejected`. Транспорт → `api_error`/`uncertain_request`. `invalid_json`/`missing_payload` у ноды не возникают. |
| 5. Безопасен ли повтор | По сути да: нет побочных эффектов вне meta/файла, нет внешней оплаты за пределами одного запроса, нет HITL. Что менять (в порядке зависимости): (a) `node_capabilities.py:71-73`: `managed=True`, действия `READ_ACTIONS + ('retry_same','finish')`; `native_json_retry=False` (нет `json_retry`, иначе G4 откажет), `recovery_continuation='none'` (нет excel-цепочки; доказательство границы по committed done, `actions_recovery.py:516-538`), `volatile_attachment_names=False` (в запросе только JSON-таймлайн, `sfx_plan.py:186-199`), `profile_compatible=False` на первом этапе (тело подходит под `effective_body`, но варианты, кроме `original-contract-v1`, потребуют `payload_required`, а продакшен-каталог профилей пуст, G11). (b) `record_sfx_parse` (`sfx_plan.py:391-418`): добавить `payload_required: True` и `payload_present`, а для сбоя до разбора ловить `RuntimeError` из `text_job` в `plan_sfx_events` и писать `parse_result` с `parsed=False`; иначе `invalid_json` не возникнет (G12). (c) привязка к запросу: `record_sfx_parse` сегодня вызывается без `reply`, поэтому `request_id=None`, а инцидент получит `evidence_incomplete` (`diagnosis.py:132`) и G1 откажет; нужно передавать id завершённого запроса (например, через `text_job` вернуть `last_request_id` или принимать callback; `text_job` вызывается только из `sfx_plan.py:207`, радиус изменения мал). (d) доказательство результата в `reconcile`: `result_confirmed` требует `data_applied` (`actions_recovery.py:557-558`), а sfx его не пишет; нужен либо новый путь «`node_result_verification` passed + разобранный payload» по данным capability (это добавит поле в `NodeCapability`, а `contract()` строится через `asdict`, `node_capabilities.py:35-37`, и попадает в `ManagedLaunch.policy` — учесть), либо отдельный честный факт применения. Подставлять фиктивный `data_applied` нельзя. (e) `decision_v1.read_contract` (`:315-358`): отдельная ветка для sfx (сейчас отдаётся контракт excel_gpt). (f) в managed-режиме блокировать успех при `node_result_verification != passed` по образцу `observe_split_result` (`observation.py:813-816`, `node_capabilities.py:463-478`), иначе `SFX_ENABLED=false` или пустой план пройдёт как «успех». |
| 6. Пробелы наблюдения | Есть: `node_input` (`record_sfx_plan_input` `sfx_plan.py:350`), `parse_result` + `runtime_check` (`:391`), `node_output` + `node_result_verification` (`observe_sfx_result` `:445`, вызов `observation.py:790-796` и при ошибке `:820-824`). Нет: `payload_required`, `request_id` на разборе, факта сбоя `text_job`, `apply_input`/`data_applied`. Флаг `managed=False`. |
| 7. Границы HITL | HITL-запроса нет (`plan_sfx.py`). После `sfx_plan_ready` стоит нативное автопродвижение в `sfx_gen` (платный ElevenLabs, `auto_advance.py:189-193`); в managed-запуске это блокируется областью узлов (`check_managed_start`, `actions_v1.py:193-215`, `node_status_machine.py:193,298`). Повтор sfx_plan не должен включать `sfx_gen` в область запуска. |
| 8. Порядок и тесты | Первый кандидат после нулевого этапа. Тесты: раздел 4 (S1–S8). Обновить `tests/test_text_nodes_recovery_capabilities.py:45-59` (наборы `compatible` и «никто больше не получил действий повтора» содержат `sfx_plan`). |

### 2.3. `scene_design` (legacy), `sd_agent`, `sd_assemble`

Общее: `node_registry.py:50-67` (`scene_design`, `sd_agent`, `sd_assemble`); `sd_*` на холсте — это `excel_gpt`-ноды с маркером `data.sd_agent` (`excel_gpt_node.py:62-91`); обратные карты отдают `sd_agent`/`sd_assemble` (`node_registry.py:152-155`); диспетчер `pipeline.py:139-146` → `scene_design.run` / `run_assemble`.

#### `scene_design` (legacy-тип)
Capability нет, контракт `scene_contract` для него `unsupported` (`scene_contracts.py:99-104` требует `node_type=="sd_agent"`). В старых холстах закрывается как один узел (`run_sync.py:957-964`). Только человек; в плане отдельной работы не требует.

#### `sd_agent` (веер: skeleton, characters, world, camera, action)

| Пункт | Факты |
|---|---|
| 1. Исполнитель, LLM, модель, промпт | `scene_design.run` (`scene_design.py:137`): при необходимости `sd_skeleton.run_skeleton` (`:178`), затем `runner.run_category_agents` (`:182`, `runner.py:734`) волнами (legacy: chars/world/action → camera; chrono_dyn: chars/world → action → camera; `agents.py:19-30`). Вызов — `gpt_client.gpt_ask_fresh` в `runner._run_one_agent` (`runner.py:438`, вызов `:453`); `action`/`camera` дробятся на чанки (`runner.py:530-731`, `max_retries=0`, один повтор при 5xx, делёж /2,/4 при ёмкостных ошибках). Скелет: ещё один «repair»-запрос (`runner.py:497`). Промпт: вариант ноды из `prompts/05_excel_gpt`, затем `sd_<agent>.md`, затем `prompts/scene_design/<agent>.md` (`agents.py:243-273`). Модель: `bind_project_llm` берёт ключ ноды только при `only_agent` (`llm_override.py:65-70`); при полном веере используется общая настройка. |
| 2. Контракт, разбор, применение | JSON-срез агента со списком `LIST_KEY[agent]`; `parse_agent_slice` (`agents.py:925`) + агентные валидаторы (action/camera/skeleton). Применение: `slice_to_cells` + `store_cells` (`scene_design.py:200-203`, `cells.py:412`): удаляет и вставляет ячейки агента кусками с коммитом каждого (не атомарно), чекпоинт-файлы `scene_design/<agent>.json` и `meta.scene_design` (`runner.py:178-221`). Повторная запись агента идемпотентна (удаление по агенту), но между DELETE и вставкой есть коммиты. |
| 3. Нативный повтор | Чекпоинты агентов пропускают уже готовые (`runner.py:788`), при сбое action/camera чекпоинт инвалидируется (`:821`, после проверки покрытия битов скелета `:896`); шаг 3×3/9 по ключу `scene_designing` (общий для всех пяти агентов). 402 останавливает шаг без счётчика (`step_failure_policy.py:315-351`, `agent_chunks.is_credits_failure`). |
| 4. Сбои и диагностика | Все ошибки агентов — `SceneDesignAgentError` («нет JSON», «пустой список», «агент вернул error»). Для camera: `parse_result` без `payload_required` + `runtime_check native_scene_camera_parse failed` → `contract_rejected`. Для остальных четырёх агентов: только `model_*` + `node_error` → `contract_rejected` («причина неизвестна»). Транспорт → `api_error`. |
| 5. Безопасен ли повтор | **Нет.** (i) G3: нативно нода коммитит ячейки и чекпоинты; (ii) идентичность: при полном веере все `sd_agent` NodeRun переводятся в `running` (`run_sync.py:778-795`), `observe_attempt` видит больше одного ключа и в managed падает (`observation.py:729-731`); в observe-режиме пишет gap и не создаёт контекст; (iii) G7: первый запрос повторной попытки зависит от чекпоинтов предыдущих агентов (готовый агент пропускается), поэтому тело первого запроса меняется, а порядок запросов внутри параллельной волны недетерминирован (`asyncio.gather`, `runner.py:869`) → `check_body` заблокирует `actions_recovery.py:427-428`; (iv) G6: чекпоинты на диске и `meta.scene_design` не входят в `input_revision`; (v) `recover_node` перезапускает шаг через `project.status = running` (`actions_recovery.py:348`), а шаг `scene_designing` без `only_agent` снова поднимет весь веер, не одну ноду. Допустимо только наблюдение/диагностика. |
| 6. Пробелы наблюдения | Крючки есть только у camera: `_record_native_scene_input(..., "camera")` (`scene_design.py:161`), `_scene_journal_attempt` отсекает любой маркер кроме `camera`/`assemble` (`runner.py:382`), `scene_agent_slice` (`scene_design.py:185-186`), `observe_scene_result` (`observation.py:797-805,825-833`). У `skeleton`/`characters`/`world`/`action`: только `model_*`; второй запрос repair-pass скелета и чанки action/camera (схема `schema_valid=None` при `depth>0`) без итогового разбора. `managed=False`, capability нет. |
| 7. Границы HITL | HITL нет; `scene_agents_ready → scene_assembling` и `scene_design_ready → generating_hero` описаны как «без HITL» (`auto_advance.py:120-131`). Следующая нода `hero` платная, в области управляемого запуска её быть не должно. |
| 8. Порядок | Режим `managed=True` с `READ_ACTIONS` (как у `split`) возможен для **camera** только после решения вопроса идентичности и добавления блокирующего верификатора; в целом отложить на последнее место. |

#### `sd_assemble`

| Пункт | Факты |
|---|---|
| 1. Исполнитель, LLM, модель, промпт | `scene_design.run_assemble` (`scene_design.py:249`). Режим `chrono_dyn`: локальная склейка, **LLM не вызывается** (`:360-392`, `build_local_assembler_payload`). Иначе `runner.run_assembler_chunked` (`runner.py:979`, вызов `scene_design.py:397`) с `gpt_ask_fresh`, до `_MAX_ASSEMBLE_ATTEMPTS=2` циклов (`:27,395`). Промпт `ag.load_prompt("assemble")`. |
| 2. Контракт, разбор, применение | JSON `{characters, scenes, ops, report}` (`agents.parse_assembler_payload`, `agents.py:996-1014`); валидатор `assembler.validate_payload`; границы сцен принудительно из `camera_expand` (`force_scenes_from_chrono`). Применение `apply_scene_design` → `db_apply.apply_ops` без `node_kind` (`apply.py:48-76`): Entity по `code`, `meta.scene_registry`, поля Frame/`attrs` (`scene_contracts.py` `output_fields`), затем `scene_space.ingest_project` (исключения проглатываются, `scene_design.py:446-463`). Перед запросом нода вставляет дочерние Frame (`subdivide_vo_frames_by_camera`, `scene_design.py:326`; защита `already_subdivided`, `camera_expand.py:635`) и при ошибке всё равно коммитит (`scene_design.py:470-476`). |
| 3. Нативный повтор | До 2 циклов с обратной связью; шаг 3×3/9 по ключу `scene_assembling`. |
| 4. Сбои | `validate_payload` problems → `parse_result` + `runtime_check native_scene_assembly_parse failed` → `contract_rejected`; `SceneDesignAgentError`; транспорт → `api_error`; `harness scene_asm` после коммита статуса (`scene_design.py:500-502`) → `node_error`. `invalid_json` невозможен (нет `payload_required`). |
| 5. Безопасен ли повтор | **Нет.** G6 заблокирует: перед сбоем уже вставлены дети Frame, таблица Frame входит в `input_revision` (`actions_recovery.py:117,123`). При `chrono_dyn` нет модельных запросов, а G3 требует непустой `model_start` (`:237`). Entity, `scene_registry`, `scene_space` не входят в хэш и не проверяются (`scene_contracts.py` `unchecked_effects`). |
| 6. Пробелы | Наблюдение хорошее (вход, разбор, `observe_scene_result`); нет `payload_required`, нет `request_id` у чанкового слияния (`link_model_request=False`, `scene_design.py:367-385`), `managed=False`. |
| 7. HITL | Нет. |
| 8. Порядок | Последним, только read-only с блокирующим верификатором. |

### 2.4. `animation_prompts`

| Пункт | Факты |
|---|---|
| 1. Исполнитель, LLM, модель, промпт | `pipeline.py:163-164` → `make_animation_prompts.run` (`make_animation_prompts.py:165`) → `fill_animation_prompts` (`:171`). Вызовы: фаза 1 `gpt.ask_anim_pr_initial` (прикреплённый файл мастер-промпта `prompt_anim_pr_<YYYYMMDD_HHMMSS>.md`, `chatgpt_xlsx.py:312`, ошибки глотаются `:333-339`), затем батчи по `BATCH_SIZE=6` (`animation_prompt_gpt.py:38`) через `_ask_batch_until_ok` → `ask_anim_pr_batch` → `chat` с `max_retries=2` (`make_animation_prompts.py:93-132`). Если у кадров есть PNG, батч — vision-запрос с лентой (`build_batch_strip_path`, `animation_prompt_gpt.py:265`). Промпт: мастер `anim_pr` из `prompts/07_animation` как `system`, обрезка до 12 000 символов (`:297-303`). Модель: `bind_project_llm` по узлу `animation_prompts`. |
| 2. Контракт, разбор, применение | Не JSON-контракт, а пары «ID изображения / текст анимации» либо apply-ops (`apg.parse_animation_reply`, `parse_apply_ops_animation_reply`); отбраковка blob и текстов короче `MIN_ANIM_PROMPT_LEN=10`. Запись: `db_apply.apply_ops(..., node_kind="anim_pr")` (`:564`) с фильтром `ANIM_PROMPT_FIELDS` (`node_write_contract.py:25`) и `session.commit()` после **каждого** батча (`:594`); поля `Frame.animation_prompt`, статус, shot02-атрибуты. Идемпотентность: присваивание, выбор следующего батча по ещё пустым кадрам (`collect_batch_items`, `animation_prompt_gpt.py:553`), поэтому нативный повтор по сути «догонка». |
| 3. Нативный повтор | Внутри шага: 3 попытки при простое провайдера с задержками 45/90/180 с (`:32-35,93-132`), затем **тихий локальный fallback** `_fill_remaining_local` без LLM (`:386-393`, `:135-162`) для shot_01 и «пропуск остатка» для shot_02 (`:455-461`) — без ошибки; частичный ответ не считается сбоем (`:603-624`). Шаг 3×3/9 по ключу `generating_animation_prompts`; `_soft_retry_without_wipe` для `anim_pr` ничего не стирает (`step_failure_policy.py:179-184`). |
| 4. Сбои и диагностика | «битый JSON apply-ops», «не вернул пары», «нет валидных пар» (`:525-562`) → `parse_result` (схема `schema_valid=False`) + `runtime_check native_animation_prompt_batch failed` → `contract_rejected`; harness `anim_pr` (`:57-76`) → `runtime_check` failed → `contract_rejected`; `apply_ops` отклонён → `apply_error` → `contract_rejected`; провайдер → `api_error`. `invalid_json` невозможен (нет `payload_required`). Деградация через локальный fallback инцидента не создаёт. |
| 5. Безопасен ли повтор | Условно. Побочных платных эффектов нет (текст), HITL нет. Но: G3 запрещает повтор после первого батча (`data_applied` по каждому батчу); G11: vision-тело и `system` не проходят `effective_body` (список `content` с `input_image`, `profiles_v1.py:81-86`), поэтому `profile_compatible=False`; G7: лента PNG собирается при каждом запуске, детерминизм побайтово по коду не доказан (нельзя определить); первое тело — фаза 1 с именем файла с штампом, значит нужен `volatile_attachment_names=True`; при `skip_phase1` (часть кадров уже готова) тело первого запроса другое. Что менять для `retry_same`: `managed=True` + `retry_same`,`finish`; `native_json_retry=False`; `recovery_continuation='none'`; `volatile_attachment_names=True`; `payload_required=True` и `payload_present` в `record_animation_parse` (`native_animation_contracts.py:331-340`); в `reconcile` принять `data_applied` для `node_kind=anim_pr` (уже эмитится); различать «ошибка до первого батча» (повтор допустим) и «после» (человек). Реально полезнее не повтор, а диагностика деградации (локальный fallback и пропуск shot_02) — требует отдельного факта. |
| 6. Пробелы наблюдения | Есть: `node_input` (`record_animation_input`, `:293`), `parse_result` по батчам, `runtime_check`, `node_output`/`node_result_verification` (`observe_animation_result` `:348`, вызов `observation.py:806-809,834-838`), harness-проверка. Нет: `payload_required`, факта локального fallback и пропуска shot_02, блокировки при `verification != passed`. `managed=False`. |
| 7. HITL | Нет. Следующая нода `videos` платная, имеет HITL (`generate_videos.py:1079`); в область управляемого запуска её включать нельзя. |
| 8. Порядок | Третий этап: сначала `managed=True` с `READ_ACTIONS` и блокирующим верификатором; повтор — только после правок из п.5. |

### 2.5. `hero` и `items` (текстовая часть)

| Пункт | Факты |
|---|---|
| 1. Исполнитель, LLM | `hero`: `generate_hero.run` (`generate_hero.py:475`), текстовая часть — запрос промпта таблицы персонажа через `gpt.ask_fresh` до 3 попыток со «сжатием» (`:637-700`, Entity/Excel-режим `_generate_one_excel_character` `:1182-1300`), затем платная генерация картинки и HITL. `items`: `generate_items.run` (`generate_items.py:86`), самостоятельного текстового запроса нет; единственная текстовая часть — `gpt_rewrite=True` внутри `generate_image_with_retries` (`:153`, `outsee_retry.py:734-870`), срабатывающая после модерации. Промпт: `gtb.get_effective_text(project,"hero")`, `prompts/04b_items`. |
| 2. Контракт | Свободный текст (hero-промпт ≥100 символов, лимит `OUTSEE_PROMPT_MAX_CHARS`), JSON-контракта нет; артефакты `Artifact`(kind `hero_reference`/`item_reference`), файлы PNG. |
| 3. Нативный повтор | Для hero 3 запроса промпта; для картинки лестница до 6 платных отправок без durable-идентификатора (`node_capabilities.py:91-103`). |
| 4. Классы | `api_error`, `uncertain_request`, исключение шага → `contract_rejected`; разборного факта нет. |
| 5. Повтор | **Нет, только человек** (запись в `MEDIA_RECOVERY_DECISIONS`, `node_capabilities.py:91-103`; `hero_entity_contract(): managed False`, `native_entity_contracts.py:25-50`). Повтор перезапустил бы платную генерацию. |
| 6–8 | Описательный контракт и `read_contract` для hero есть (`decision_v1.py:262-264`), для items нет (в контракте записано «deferred»). Ничего не включать. |

---

## 3. Таблица наблюдения (что именно пишется нативным кодом)

| Нода | `model_*` | `node_input` | `parse_result` с `payload_required` | `json_retry` | `apply_input`/`data_applied` | `node_output`/`node_result_verification` | Блокирует успех в managed при провале |
|---|---|---|---|---|---|---|---|
| excel_gpt / enrich | да | нет | да (кроме check-режима) | да | да | нет | harness в самой ноде |
| sfx_plan | да | да | нет поля | нет | нет | да | нет |
| sd_agent camera | да | да | нет поля | нет | нет (ячейки не через `db_apply`) | да | нет |
| sd_agent прочие | да | нет | нет | нет | нет | нет | нет |
| sd_assemble | да (кроме chrono_dyn) | да | нет поля | нет | да | да | нет |
| animation_prompts | да | да | нет поля | нет | да (по батчам) | да | нет |
| hero / items | да | нет | нет | нет | нет | нет | нет |

---

## 4. Рекомендуемый порядок включения и тесты (только заглушки)

Порядок (от безопасного к рискованному):

1. **Этап 0 — enrich_1..5.** Новых capability не нужно. Документировать, что включена нода `excel_gpt`; проверить в данных, остались ли снимки с типом `enrich_N` (по коду определить нельзя), при наличии — мигрировать графы.
2. **Этап 1 — `sfx_plan`, `retry_same`.** Правки (a)–(f) из раздела 2.2. Один запрос на попытку, без HITL, без записи в бизнес-таблицы.
3. **Этап 2 — `animation_prompts`, `managed=True` только с `READ_ACTIONS`** и блокирующим верификатором; затем, по решению, `retry_same` только для сбоя до первого коммита батча.
4. **Этап 3 — `sd_agent` camera и `sd_assemble`, только read-only**, после решения вопроса идентичности (раздел 2.3) и блокирующего верификатора.
5. **Никогда в рамках этой работы:** `scene_design` legacy, `hero`, `items`; для них закрепить «только человек» данными и тестом (по образцу `test_media_nodes_recovery_decision.py`).

Тесты (заглушки; только синтетические данные, без сети и платных вызовов):

```python
# tests/test_text_nodes_recovery_capabilities.py  (офлайн, данные capability)
def test_enrich_legacy_types_are_fail_closed():                 # E1
    """capability_for('enrich_1').managed is False; require_managed_capability raises."""
def test_enrich_slots_migrate_to_excel_gpt_on_graph_save():     # E2
    """migrate_enrich_nodes: enrich_1..5 -> type excel_gpt, slotIndex preserved."""
def test_sfx_plan_capability_flags_after_enablement():          # S1
    """managed, actions == READ_ACTIONS+retry_same+finish, native_json_retry False,
    recovery_continuation 'none', volatile False, profile_compatible False."""
def test_sfx_plan_parse_fact_is_payload_required_and_request_bound():  # S2
    """record_sfx_parse payload has payload_required True, payload_present, request_id not None."""
def test_sfx_plan_text_job_failure_emits_typed_parse_result():  # S3
    """patch gpt_ask_fresh -> '{bad'; plan_sfx_events raises; derive_incidents -> invalid_json, not evidence_incomplete."""
def test_sfx_plan_read_contract_is_sfx_specific():              # S4
    """decision_v1.read_contract for sfx_plan is not the excel_gpt structured branch."""
def test_no_other_node_gained_recovery_actions_updated():       # S5
    """edit existing test: remove sfx_plan from the 'no actions' list, keep hero/sd_*/animation_prompts/future."""

# tests/observe_v1/test_text_nodes_recovery.py  (БД, preconditions recover_node/reconcile)
async def test_sfx_plan_exhausted_invalid_json_is_admitted(): ...          # S6
async def test_sfx_plan_recovery_blocked_by_inner_apply_evidence(): ...    # G3: sfx has none; verify block if data_applied present
async def test_sfx_plan_verified_by_result_verification_not_data_applied(): ...  # S7 (после правки (d))
async def test_sfx_plan_disabled_flag_pass_through_is_not_verified(): ...  # S8: SFX_ENABLED=false -> no 'passed'
async def test_sfx_plan_checkpoint_hit_makes_no_model_request(): ...       # цикл: 0 запросов -> не 'verified'

# tests/observe_v1/test_native_animation_observation.py
def test_animation_parse_fact_has_payload_required(): ...                  # A1
async def test_animation_recovery_denied_after_first_batch_data_applied(): ...  # A2 (G3)
async def test_animation_local_fallback_emits_degraded_fact(): ...         # A3
def test_animation_profile_denied_for_vision_body(): ...                   # A4 (effective_body raises)

# tests/observe_v1/test_native_scene_observation.py
async def test_sd_agent_fanout_is_not_admitted_in_managed(): ...           # D1: >1 running NodeRun -> RuntimeError
async def test_sd_assemble_denied_after_child_frames_committed(): ...      # D2: input_revision changed
def test_sd_assemble_chrono_dyn_has_no_model_request_so_g3_denies(): ...   # D3
def test_hero_items_scene_design_stay_human_only(): ...                    # D4
```

Отдельно: сухой прогон по схеме `scripts/e2e_text_nodes_recovery.py` (режим `--probe-retry`) для `sfx_plan` на синтетическом транспорте перед любым живым прогоном.

## 5. Что нельзя определить по коду

* Сколько живых проектов хранят в снимках графа тип `enrich_N`, а не `excel_gpt` (данные, не код).
* Стоимость запроса `sfx_plan`/`animation_prompts`/sd-агентов: тарифы в `docs/KIE_COST_CALIBRATION.md` я не открывал, провайдерский биллинг от модели зависит.
* Побайтовая детерминированность ленты PNG (`compose_horizontal_strip`) и, следовательно, совпадение первого тела запроса при повторе vision-батча (`image_strip.py` не анализировался).
* Какой провайдер/режим (Responses или chat/completions) выбран у конкретного узла в живом проекте: он задаётся холстом и настройками (`gpt_api.py:1054-1066`); от этого зависит применимость профиля (G11).
* Как именно ведёт себя `ActionSupervisor.charge_request` при holds внутри внутреннего цикла повторов `chat` (`observation.py:597-631` против `gpt_api.py:3040-3099`): исход зависит от кода `actions_v1.charge_request`, который я не читал целиком.
