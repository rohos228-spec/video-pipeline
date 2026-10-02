# Критический архитектурный разбор ядра оркестратора (video-pipeline-observe-v1)

Дата: 2026-10-02. Режим: только чтение кода и документов (тесты, запуск приложения, сеть, `.env` не использовались; репозиторий не менялся).
Область: `app/services/{observation,diagnosis,decision_v1,decision_config,decision_state,decision_transport,actions_v1,actions_recovery,profiles_v1,profiles_state,managed_launch,node_capabilities,telemetry,human_review_summary}.py`, `app/orchestrator/{node_spec,node_registry,pipeline,auto_advance}.py`, `app/web/routers/observations.py`, `web/src/components/orchestrator/*`, плюс смежные файлы, на которые указывают эти модули (`advance_runner.py`, `enrich_xlsx.py`, `prompt_library.py`, `prompt_history.py`, `api_tracker_hook.py`, `step_failure_policy.py`, `code_autofix.py`).

Условные обозначения:
- **[Ф]** — факт, проверен чтением кода/документа, указан `файл:строка`.
- **[О]** — мнение/оценка автора разбора (в т.ч. серьёзность).
- **[Г]** — гипотеза, прямо в коде не подтверждена и запуском не проверялась.
- Серьёзность: **Критично / Высокая / Средняя / Низкая** (всегда [О]).
- Все пути относительно `C:\video-pipeline\video-pipeline-observe-v1`.

---

## 0. Короткий вердикт

1. [Ф] Ядро — это не «движок оркестрации», а надстройка наблюдения/решений над старым линейным FSM (`docs/ARCHITECTURE.md:8-39`). Новый слой управляет только узким классом: **текстовые ноды (`plan`, `script`, `excel_gpt`) × исчерпанные `invalid_json`/`missing_payload`** — одно восстановление на ноду за execution. Всё остальное — «наблюдать + отдать человеку».
2. [Ф] Из 24 типов рабочих нод (23 в `WORK_NODES` + виртуальный `excel_gpt`) реальная запись в `_CAPABILITIES` есть у 12, `managed=True` — у 6 (`plan`, `script`, `split`, `image_prompts`, `excel_gpt`, `images`), действие «повтор» разрешено у 3 (`plan`, `script`, `excel_gpt`) (`app/services/node_capabilities.py:43-80`, `app/orchestrator/node_registry.py:38-125`).
3. [Ф] Декларативная регистрация (`NodeSpec`/`register_node`) существует (`app/orchestrator/node_spec.py:22-97`), но (а) не несёт 4 из полей способности, нужных восстановлению, (б) реестр верификаторов `_VERIFIERS` нигде не читается (`verifier_for` вызывается только в тесте), (в) ни одна реальная нода на неё не переведена. Поэтому подключение новой ноды сейчас = правки в 5–7 местах, включая if-цепочки в `observation.py`, `decision_v1.py`, `actions_v1.py`.
4. [О] Главный архитектурный риск — не нехватка функций, а **хрупкость «fail-closed без пути возврата»**: любая одиночная ошибка наблюдателя навсегда блокирует execution (разд. 5, п. E1–E3), а глобальный лимит тела журнала (32 МиБ без очистки) остановит все управляемые запуски. Это нужно исправить до расширения на новые ноды.
5. [О] Для этапа 4 (смена модели/промпта в пределах лимитов) безопасный фундамент частично есть (профили — неизменяемые overlay на одну попытку; история версий промптов — `prompt_history.py`), но отсутствуют: модель «предложение→одобрение→версия→применение→проверка→откат», роли (разработчик/оператор), отделение «конфигурации ноды» от «хеша графа», хранилище неизменяемых версий. Проект приведён в разд. 4.

---

## 1. Карта потока данных одного управляемого выполнения (end-to-end)

Нумерация — по порядку исполнения. Сначала запуск, затем рабочий тик, затем цикл «наблюдать→диагностировать→решить→исполнить→проверить→продолжить».

### 1.0. Старт процесса
- [Ф] `app/main.py:47-49` вызывает `migrate_observation_schema` → `Base.metadata.create_all` для **17** таблиц (`observation.py:1007-1024`; документ говорит 15 — расхождение, `docs/ORCHESTRATOR_CURRENT_STATE.md:55`). Это только аддитивное создание таблиц, не миграции колонок.
- [Ф] `app/web/api.py:234` создаёт задачу `Observer.loop()` (единственный супервизорный цикл; живёт в процессе веб-приложения).
- [Ф] Первая итерация `loop()` → `Observer.reload()` (`observation.py:194-203`): загружает активные `ExecutionControl`, затем `actions_v1.reload_permissions` (`actions_v1.py:220-239`: чистит in-memory `_permissions`, переводит прерванные запуски `dispatching` в `held` — `managed_launch.py:220-231`, загружает разрешения, затем `reload_recovery_restrictions` — `actions_recovery.py:37-45`).

### 1.1. Оператор запускает управляемое выполнение
1. UI `managed-launch-section.tsx:35` → `GET …/observations/launch-options` (`routers/observations.py:98-128`): по снапшоту графа строит список нод с флагом `supported = capability.managed` и причиной «исполнитель и проверка ещё не приняты» (строки 111-117).
2. UI → `POST …/observations/launch` (`observations.py:67-95`) под `observer._enable_lock` → `managed_launch.prepare` (`managed_launch.py:92-202`):
   - идемпотентность и «один запуск на workflow-run» (99-107);
   - проект простаивает, нет `user_stop`/backoff (`require_idle_project`, 40-51);
   - граф: ревизия совпадает с каноном проекта (119-124); `validate_scope` (54-89): свежие pending-ноды, один вход, связность, **каждая нода проходит `require_managed_capability`** (`node_capabilities.py:144-148`);
   - стратегии ⊂ `runtime_config.allowed_actions` ∩ действия способностей (141-148); профили — только из каталога владельца, хеш каталога (149-163);
   - лимит запросов ≤ `owner_budget_policy.max_requests` (164-168; по умолчанию 100 — `decision_config.py:20-29`);
   - одна транзакция создаёт `ManagedLaunch`, `ExecutionControl(mode=managed)`, `ActionExecutionControl`, `DecisionControl(enabled)`, `DecisionRuntimeBinding(config_hash)`, `ActionRecord` (180-197) и резервирует `ManagedRunAllowance` (`actions_v1.py:74-84`);
   - `install_permission` кладёт разрешение в in-memory `_permissions` (199-201).
3. `start_managed_node` (`observations.py:385-480`) → штатный `start_step(... managed_start=True)` (453) — нода переходит в `running` по оригинальному FSM; предварительно `evaluate_start`/`check_managed_start` (`actions_v1.py:193-217`, 636-649).

### 1.2. Рабочий тик (штатный воркер)
4. `app/main.py:302-344` (`_handle_one_advance`) → `advance_project_job` (`advance_runner.py:26-100`).
5. `worker_entry_admission` (`actions_v1.py:144-172`; вызов `advance_runner.py:28-31`): читает durable-контроль на каждый тик; held/неподтверждённое разрешение → тик не стартует. [Ф] Если `_observer is None` (процесс без веб-lifespan), допуск = `ordinary_mode` (153-156).
6. `async with observe_attempt(session, project)` (`advance_runner.py:60`; реализация `observation.py:708-871`):
   - определяет `node_key` из иммутабельного run и фактического `NodeRun` (716-737);
   - находит scope; при остановленном наблюдении всё равно восстанавливает бюджетный gate (738-748);
   - в managed: `actions_recovery.claim` (CAS билета восстановления, `actions_recovery.py:354-410`) и `input_revision` (`:111-146`);
   - `AttemptContext` + `runtime_config_hash` (763-766);
   - способность ноды: `capability_for("excel_gpt" if not native_type or native_type.startswith("enrich_") else native_type)` (768-769);
   - managed без принятой способности → `_hold` и исключение (776-783);
   - загрузка overlay профиля попытки (784-788).
7. `pipeline.advance_project` (`pipeline.py:84-189`): `if/elif` по `ProjectStatus` (132-184) вызывает `steps/<name>.run`.

### 1.3. Внутри исполнителя ноды
8. LLM-запрос через `gpt_api` (три функции с `@observe_model`: `gpt_api.py:1905, 2213, 2254`) → обёртка `observation.py:561-657`:
   `transform_request` (профиль, `profiles_v1.py:287-296`) → `check_body` (тело первого запроса восстановления совпадает с исходным, `actions_recovery.py:413-428`) → `ActionSupervisor.charge_request` (атомарный `UPDATE … requests_used+1 … WHERE used<limit`, `actions_v1.py:530-571`; **до** HTTP) → факт `model_start` + тело на диск (606-610) → вызов → `model_end` (620-626) + учёт стоимости в api-tracker (627-629, `_track_api_cost` 523-558) → неполный/salvaged ответ или таймаут → `hold_uncertain_request` без повтора (630-632, 652-653).
9. Разбор/применение: `record_parse` (660-684), `@observe_apply` вокруг `db_apply` (687-705); частные хуки на каждый тип ноды в `xlsx_step_runners.py` (11 вызовов `record_*`), `generate_images.py`, `make_animation_prompts.py`, `generate_sfx.py`.
10. Транзакционный журнал: `before_flush` (`observation.py:874-917`) пишет изменения `NodeRun` в `observation_outbox` **в той же транзакции**, что и бизнес-данные; `after_commit` (920-924) и `after_soft_rollback` (927-937) помечают неподтверждённый коммит (только в памяти, см. E3).
11. После выхода из `yield` — проверка результата **per-family** (`observation.py:790-816`): `observe_sfx_result`, `observe_scene_result`, `observe_animation_result`, затем (managed) `observe_project_text_result`, `observe_split_result`, `observe_image_prompt_result` (`node_capabilities.py:258-297, 389-395, 463-478`). Неудача проверки в managed → `ActionSupervisor.set_held(operator_request=False)` и исключение.
12. Завершение: `finish_attempt` (`profiles_v1.py:298-303`), `node_end`/`node_error` (`observation.py:853, 861`), `worker_finished` для билета восстановления (`actions_recovery.py:431-455`).

### 1.4. Супервизорный цикл (`Observer.loop`, `observation.py:347-365`, период 1 с, последовательно)
13. `catch_up` (316-345): outbox master + outbox каждого project.db → `observation_events` с курсором, один commit (`consume`, 273-307). Идёт по **всем** `ExecutionControl`, когда-либо созданным (строка 320-322).
14. `Diagnoser.catch_up` (`diagnosis.py:370-376`) → `update` (308-363): читает **всю** историю событий execution с курсора 0 (315-319) и пересобирает инциденты чистой функцией `derive_incidents` (70-272), пишет `Incident`.
15. `actions_recovery.reconcile` (465-634): для билетов в `reserved/queued/claimed/await_verify` сводит проверку (`verification`, 499-515; обязательные признаки 559-561); `verified` → продолжение (594-633).
16. `DecisionSupervisor.tick` (`decision_v1.py:483-583`):
    - **вход**: `step_failure.abandoned_at` и `total_fails ≥ 9` (3×3) + `NodeRun failed` + инцидент из `DIAGNOSTIC_INCIDENTS` + в области (`:490-508`) → `begin` (200-216, одна запись на (execution, incident));
    - `step` (403-481): `claim` с lease 120 с (218-225) → `_live` (359-374: stop/permission/аудит/граф/устаревший контекст/активный нативный запрос) → `_context` (102-192: инцидент, первые 20 фактов, `allowed_actions`, `eligible_profiles`, `recommended_profile_id`, лимиты) → запрос к модели решения через `HttpDecision.send` (634-677: маршрут, `charge_request`, ack шлюза) → `validate_proposal` (44-67) → действие.
17. Действия решения (`decision_v1.py:443-480`): `retry_same`/`retry_profile` → (профиль: `ProfilesSupervisor.select_for_decision` `profiles_v1.py:149-172` создаёт неизменяемый `ProfileOverlay`) → `recover_node` (`actions_recovery.py:149-351`): ~25 предусловий, создаёт `RecoveryAction` (UNIQUE на `(execution,node)` — один билет), `start_node_running` возвращает ноду в `running` (338), `project.status` ← прежний running-статус (348). `read_events`/`read_contract`/`wait` → ответ инструмента в историю → следующий шаг (≤3). `human_review` → `escalate` (376-401): `held=True`, сводка `REQUIRES_HUMAN_INTERVENTION` (`human_review_summary.py`). `finish` — только если билет уже `verified` (464-466).
18. Возврат в п. 6: воркер подхватывает билет через `claim`, повторно исполняет **исходный** исполнитель; п. 15 проверяет и закрывает билет; `tick` переводит решение `waiting_worker → done` (544-570).

### 1.5. Продолжение (continue)
19. Для текстовой цепочки `excel_gpt`: `_after_excel_gpt_done` (вызывается из `reconcile`, `actions_recovery.py:596-633`) → `_maybe_auto_chain_excel_gpt` (`enrich_xlsx.py:951-1013`) → `continue_managed_image` (`actions_v1.py:242-371`) — **продолжение только на ноду `images`** (строка 258) и **только в synthetic-режиме** (строки 280-282).
20. Для нативных `plan`/`script`: `recovery_continuation='none'` — исходный runtime сам ставит обязательный HITL, супервизор HITL не одобряет (`actions_recovery.py:600-609`).
21. Общая нативная автопродвижка (`auto_advance._commit_running_after_prepare`, `auto_advance.py:788-819`) в managed вызывает `check_managed_start` по следующей ноде графа — гейт общий, не по типам.

### 1.6. Человек
22. Эскалация → `ActionExecutionControl.held=True` (БД) + `update_permission(held=True)` (кэш); возобновление — только `POST …/actions/control` с `held:false` (`observations.py:349-360`, `actions_v1.py:486-528`), закрытие — `…/actions/close` (`actions_v1.py:573-633`). Снять `user_stop` супервизор не может (`:513-514`); [Ф] «продолжить» внутри managed после стопа не поддержано по построению (`docs/STAGE_STATUS_REPORT.md:80-82`).

### 1.7. Хранилища и «кто что считает источником истины» [Ф]
- master DB: `observation_*`, `action_*`, `decision_*`, `node_profile_overlays`, `managed_*`, `orchestrator_media_*`, `observation_incidents`.
- project.db: бизнес-данные + `observation_outbox` (транзакционно) + реплика NodeRun.
- Диск: тела запросов/ответов `data_dir/observations/bodies/<execution>/<sha256>.json` (`observation.py:246-271`).
- Память процесса: `_permissions` (`actions_v1.py:106`), `_active_nodes`/`_inflight_projects` (`actions_recovery.py:21-22`), `Observer.scopes/failures/commit_uncertainty/last_project_facts` (`observation.py:151-156`).
- «Управляется ли проект» одновременно представлено **четырьмя** способами: `_permissions`, `ExecutionControl.mode` (в БД и в кэше scope), `ActionExecutionControl.enabled`, `DecisionControl.enabled` — [О] источник рассинхронизаций.

---

## 2. Точки расширения и что зашито под тип ноды

### 2.1. Что реально является точкой расширения сегодня [Ф]
| Механизм | Где | Что позволяет |
|---|---|---|
| `NodeSpec` + `register_node` | `node_spec.py:22-81` | Одним вызовом заполнить `WORK_NODES`, обратные карты, `LINEAR_*`, `_CAPABILITIES`; хранить `verifier` (но он не используется — см. 2.3) |
| `WORK_NODES`/`_CAPABILITIES` (правка словарей) | `node_registry.py:38`, `node_capabilities.py:43` | Основной «боевой» способ добавить ноду |
| Флаги способности как данные | `node_capabilities.py:9-33`: `profile_compatible`, `volatile_attachment_names`, `native_json_retry`, `recovery_continuation` | Решения «может ли профиль / что делать после восстановления» уже вынесены из строк в данные — **правильное направление** |
| Декораторы `@observe_model/@observe_apply/@observe_check/@observe_review` | `observation.py:561, 687, 940, 445` | Обёртка существующих функций без их правки |
| `record_fact/emit/stage_project_fact` | `observation.py:377-420, 960` | Универсальная запись типизированных фактов |
| `decision_transport.current`, `llm_override.use_override`, `ContextVar _attempt` | `decision_transport.py`, `llm_override.py:36-49`, `observation.py:119` | Подмена транспорта/модели без изменения исполнителей |
| Каталог профилей в конфиге владельца | `decision_config.py:58-124`, `config/orchestrator-runtime.*.json` | Расширение набора «модель/глубина/лимит токенов/вариант промпта» без кода |
| `APPROVED_PROFILE_PROMPTS` | `prompt_library.py:990-994` | Список разрешённых вариантов промпта (суффиксы) |
| `ERROR_CATALOG` + `describe_error` | `error_catalog.py` | Единые коды ошибок |
| `MEDIA_RECOVERY_DECISIONS` | `node_capabilities.py:83-133` | Декларация «человек-только» для медиа; [Ф] **в рантайме не читается** (комментарий в `:85`; потребитель — только `tests/test_media_nodes_recovery_decision.py`) |

### 2.2. Что зашито под конкретный тип/семейство и блокирует «generic»-подключение [Ф]
| # | Место | Что зашито | Кого блокирует |
|---|---|---|---|
| H1 | `pipeline.py:132-184` | `if/elif` по `ProjectStatus` на каждую ноду; импорт модуля шага | любая новая нода (док `ADDING_NEW_AGENT.md:77-90` прямо предупреждает, что без ветки `run()` — мёртвый код) |
| H2 | `models.ProjectStatus` + `node_registry.py:145-159` | пара running/ready как enum; ручные «коллизии» `RUNNING_TO_NODE_TYPE[...]= "sd_agent"` (152-155); виртуальный тип `excel_gpt` через отдельные ветки `spec_for_type/_node/_step_code` (209-248) | ноды, делящие статус; рост enum |
| H3 | `services/step_registry.py` → `telegram/menu.STEPS` | отдельный реестр шагов/статусов для воркера (`running_statuses_list`) | рассинхрон с `WORK_NODES` (третий реестр) |
| H4 | `observation.py:769` | `"excel_gpt" if not native_type or native_type.startswith("enrich_")` — выбор способности по префиксу строки | enrich_N, любые нативные ноды с префиксом |
| H5 | `observation.py:790-816, 818-850` | цепочка `if capability.node_type in {'sfx_plan','sfx_gen'}` / `scene_native_node` / `== 'animation_prompts'` / `== 'image_prompts'` + вызов `observe_project_text_result`, `observe_split_result`, `observe_image_prompt_result` (813-816) | любая новая ноде с проверкой результата; `verifier_for` не используется |
| H6 | `observation.py:463` | словарь `{'approve_plan':'plan','approve_script':'script',…}` для ревью-хуков | новые HITL-ревью |
| H7 | `decision_v1.py:20-24` | `ACTIONS=(...)`, `RECOVERY_INCIDENTS={'invalid_json','missing_payload'}`, `DIAGNOSTIC_INCIDENTS` | новые классы отказов/действия |
| H8 | `decision_v1.py:241-357` (`read_contract`) | цепочка по `result_kind` (`unsupported/project_text/frame_set/frame_prompts/!=structured`) и по `node_type` (`hero`, `animation_prompts`, `sd_agent/sd_assemble` — 262-270); импорт приватной `enrich_xlsx._script_frames_qc_footer_kind` (244, 345) | «контракт ноды» читается моделью только для заранее известных семейств |
| H9 | `decision_v1.py:497-508` | вход Decision: `project.meta.step_failure.abandoned_at` + `MAX_TOTAL_FAILS` (политика нативного шага) | любая нода без нативного 3×3-счётчика (медиа, scene) |
| H10 | `actions_recovery.py:175` | `incident.type not in {"invalid_json","missing_payload"}` | любой другой класс отказов |
| H11 | `actions_recovery.py:111-146` | `input_revision` хеширует таблицы `Frame/Scene/FrameText/SceneDesignCell`, `settings.model_dump()`, **все** `prompts/**/*.md` и 6 файлов кода (`enrich_xlsx.py`, `pipeline.py`, `gpt_client.py`, `db_apply.py`, `node_write_contract.py`, `gpt_text_builder.py`) независимо от типа ноды | «отпечаток входа» невозможно описать на уровне ноды; любое изменение любого промпта аннулирует все ожидающие билеты (важно для этапа 4) |
| H12 | `actions_recovery.py:596-609` | продолжение: `recovery_continuation != "excel_gpt_chain"` → «ничего», иначе `_after_excel_gpt_done` | только две схемы продолжения |
| H13 | `actions_v1.py:258, 280-282, 342-349` | `continue_managed_image`: `node_type != "images"` → False; `cfg.mode != "synthetic"` → deny; входные проверки именно для картинок | «продолжить» только на `images` и только в synthetic |
| H14 | `managed_launch.py:143, 261` | множество стратегий `{'wait','human_review','retry_same','retry_profile'}`; `effective_node_type(node)=='excel_gpt'` для установки `active_excel_gpt_node_key` | новые стратегии; excel-специфика в общем входе |
| H15 | `node_capabilities.py:264, 303, 392, 468` | функции `verify_*`/`observe_*` внутри файла способностей с `node_type in {'plan','script'}`/`!='split'`/`!='image_prompts'`; `project_text_contract` рассчитан на два типа | файл способностей = смесь декларации и логики |
| H16 | `node_write_contract.py:202-216` | `allows_qc_noop`: `group == 'script_frames_qc'` и слот `'shots_qc_ru'` | [Ф] способность `excel_gpt` помечена `managed=True` как тип, но приёмка/контракт касается **одной группы**; `validate_scope` проверяет только тип (`managed_launch.py:69`) → UI покажет любую `excel_gpt`-ноду как «поддерживается» [О: завышение охвата] |
| H17 | `profiles_v1.py:72-87` | `effective_body` работает только с текстовым Responses `input` (str или список `{role,content:str}`); иначе `ValueError` | профили для vision/мультимодальных/чат-форматов |
| H18 | `diagnosis.py:49-61, 80-272` | `TITLES`/`RECOMMENDATIONS` — словари на русском; правила — одна функция ≈200 строк, ключи по `kind` события | добавление класса отказа = правка монолита |
| H19 | `step_failure_policy.py:34-71` | классификация длительности сна по **подстрокам русского текста исключения** («всё ещё неполный», «якорь», «биты»…) | хрупко; не связано с `ERROR_CATALOG` |
| H20 | UI `profiles-section.tsx:24, 34` | всегда `state.catalog[0]` — выбор профиля в UI отсутствует | несколько профилей |
| H21 | `observations.py:98-128` | поддержка ноды = `capability.managed` (булево) без уровня приёмки | «охват» невидим |

### 2.3. Предложение: декларативная регистрация возможностей/контрактов/верификаторов
[О] Цель: **подключение ноды = один файл `nodes/<name>.py` с `NodeSpec`**, остальное (диспетчеризация, способность, контракт для модели, верификатор, восстановление, продолжение, покрытие в UI) выводится из спецификации. Нужно не «новый движок», а довести уже начатый `NodeSpec` до полного описания.

```python
@dataclass(frozen=True)
class NodeSpec:
    # идентичность и FSM (уже есть)
    node_id, version, step_code, running_status, ready_status, linear
    executor: ExecutorRef            # "app.orchestrator.steps.generate_sfx:run"  -> замена pipeline.py:132-184
    # способность (полный набор полей NodeCapability, а не 7 из 11)
    capability: CapabilitySpec       # result_kind, phases, effects, actions, managed,
                                     # profile_compatible, native_json_retry,
                                     # volatile_attachment_names, recovery_continuation,
                                     # acceptance: AcceptanceLevel("declared"|"synthetic"|"real") + evidence_refs
    # контракт (читается Decision.read_contract и UI) — вместо if-цепочки decision_v1.py:257-357
    contract: ContractSpec           # describe(session, project, node_key) -> dict; input/output schema; allowed_fields
    # наблюдение
    observers: ObserverSpec          # input_recorder, parse_recorder, result_verifier(session, project)->Proof,
                                     # on_fail: "hold"|"gap"|"raise"  -> замена observation.py:790-850 и 813-816
    # восстановление
    recovery: RecoverySpec           # incident_types, strategies, exhaustion_probe(project,node)->bool,
                                     # continuation: "original_runtime"|"excel_chain"|Callable
    successor: SuccessorSpec | None  # managed-продолжение; вместо жёсткого "images" (actions_v1.py:258)
    input_fingerprint: Callable      # какие таблицы/файлы/промпты входят в хеш входа (вместо actions_recovery.py:111-146)
    tunables: tuple[TunableSpec,...] # этап 4: какие параметры/версии промпта можно менять и в каких пределах
    cost: CostSpec                   # класс стоимости, идемпотентность submit (поглощает MEDIA_RECOVERY_DECISIONS)
```

Дополнительно:
1. **Реестр событий** `kind → {обязательные поля, правило редакции, видимость в панели}` — заменить deny-list `panel_safe` (`observation.py:992-1004`) на allow-list по виду события и сделать правила диагностики подключаемыми (`DiagnosisRule(kind→incident_type, recoverable_by)`), вынеся из монолита `derive_incidents`.
2. **Набор «conformance»-тестов, параметризованный по реестру**: для каждого `NodeSpec` проверяет — есть исполнитель; `managed=True` ⇒ есть верификатор + запись фактов `node_input/parse_result/node_result_verification` + тест «подделанный результат отклоняется»; нет `capability_for(...).result_kind=='unsupported'` без явной причины; `ProjectStatus` содержит running/ready; нет рассинхрона трёх реестров (H2/H3).
3. **Уровень приёмки как данные** (`declared / synthetic-accepted / real-accepted / stale`), привязанный к хешу кода; UI покрытия (разд. 6) читает его, а не гадает.
4. Порядок миграции (малые безопасные шаги): ① расширить `register_node` до всех полей `NodeCapability`, `_CAPABILITIES` сделать производным (shim) → ② перенести 12 существующих записей в спецификации (механически) → ③ подключить `verifier_for` в `observe_attempt` вместо цепочки H5 → ④ `contract.describe` вместо H8 → ⑤ `successor` вместо H13 → ⑥ `executor` вместо H1 → ⑦ conformance-тесты. Оценка [О]: 5–8 рабочих дней одного разработчика без новых функций, при зелёном существующем наборе `tests/observe_v1` (но он требует специального окружения — см. разд. 8).

---

## 3. Пробелы относительно 6 этапов плана владельца

Сводка (серьёзность — [О]):

| Этап | Заявлено (docs) | Фактическое покрытие [Ф] | Главные пробелы | Серьёзность |
|---|---|---|---|---|
| 1. Единые логи/состояние | «Сделано» (`STAGE_STATUS_REPORT.md:9`) | Журнал есть и надёжен по замыслу (outbox в той же транзакции, курсор, restart-safe). Покрытие на уровне **запросов** — для текстовых LLM (3 функции `gpt_api`), Kie-картинок (`kie_http`→`media_transport`), SFX/anim/scene частично. | Нет хуков: `make_plan/make_script` (идут через `xlsx_step_runners`), `generate_hero/items/videos/audio/music/assemble` — 0 вызовов наблюдения в файлах шагов (проверено подсчётом). Видео/аудио/Outsee-запросы не трассируются на уровне HTTP. Стоимость пишется только для текстовых LLM (`observation.py:546-556`, `call_type="text"`); медиа-тарифы есть в `api_tracker_hook._MEDIA_RATES`, но оркестратор их не вызывает. Журнал: глобальный лимит тел 32 МиБ без очистки (E2). Диагностика/сбор читают всю историю каждую секунду (D1). | **Высокая** |
| 2. Диагностика | «Сделано» | Правила по видам событий, 11 типов инцидентов, детерминированно (`diagnosis.py:49-61`). Корневая причина часто `hypothesis=null` (`:88`). | Расширение классов = правка монолитной функции (H18). Нет корреляции «ничего не происходит» (зависший запрос без `node_error` не создаёт инцидент до остановки наблюдения — `diagnosis.py:222-227`). Нет повторной проверки после рестарта для `commit_uncertainty` (E3). Таймауты сознательно «uncertain → человек». | Средняя |
| 3. Самозапуск, контроль, повтор, стоп/продолжение | «Сделано; повтор подтверждён живьём» | Старт — только вручную оператором (`/launch`), нет планировщика. Повтор — один на ноду/execution (`actions_recovery.py:50-52`), только `plan/script/excel_gpt`. Стоп/продолжение внутри managed не поддержаны; путь «close → штатная ▶». | Нет «самозапуска» в смысле расписания/очереди; продолжение управляемой цепочки только на `images` и только synthetic (H13); для остальных — оператор. Живое восстановление `plan/script` **не пройдено** (баланс Kie, `STAGE_STATUS_REPORT.md:68-74`). Вход супервизора только после 9 нативных отказов и 30-минутных сонов (≥ ~1 ч, `step_failure_policy.py:28-31`) — [О] медленно для «самовосстановления». Для `split/image_prompts` восстановление выключено намеренно (`STAGE_STATUS_REPORT.md:57-66`). | **Высокая** |
| 4. Смена модели/агента, правка параметров/промптов в пределах лимитов; серьёзное — с подтверждением разработчика | «Частично» | Профиль = неизменяемый overlay на **одну** попытку (`profiles_v1.py:223-230`, UNIQUE `(execution,node)`), поля `model_id/reasoning_effort/max_output_tokens/prompt_variant` (4 значения). Смена агента: нет. Правка параметров ноды: нет. Правка промпта: только фиксированные суффиксы (`prompt_library.py:990-1000`). | Нет модели предложений, ролей, версий конфигурации, отката применённого, лимитов на параметры (TunableSpec), проверки «до/после». `PRODUCTION_CATALOG={}` не используется (`profiles_v1.py:23`); в UI всегда `catalog[0]` (H20). Решение владельца «не расширять права автоматики» (`STAGE_STATUS_REPORT.md:114`) — это ограничение, но не дизайн. Подробно — разд. 4. | **Высокая** (для целей плана) |
| 5. Единый цикл найти→выбрать→выполнить→проверить→продолжить/человек | «Сделано, один живой прогон» | Цикл реализован для одного класса × трёх нод. `MAX_STEPS=3` (`decision_v1.py:21`). Автовход: инциденты `DIAGNOSTIC_INCIDENTS` (4 типа), но действовать (retry) можно только при `invalid_json/missing_payload` (`actions_recovery.py:175`; `decision_v1.py:166`). Остальные → read-only диалог → `human_review`. | Нет цикла для медиа, scene, hero/items, music, assemble, publish. Продолжение (H12/H13) ограничено. Модель-«Decision» блокирует общий цикл при ожидании ответа (D2). Нет универсального понятия «верификатор ноды» (H5). Реальный модифицирующий цикл не гонялся для plan/script. | **Высокая** |
| 6. История действий, анти-цикл/лимиты стоимости, финальные проверки | «Частично» | История: `ActionRecord`, `RecoveryAction`, `DecisionRecord/Turn`, `ProfileOverlay` (UI показывает последние 100 — `observations.py:285`). Анти-цикл: 3 шага модели, один билет, подпись повторного решения (`decision_v1.py:434-435`), 3×3 нативных отказов. Лимит: счётчик запросов на execution. | См. разд. 5: нет глобального/суточного/денежного лимитов; нет watchdog зависших выполнений; рубильник `api_tracker_hook.check_api_allowed` нигде не вызывается; нет очистки журнала; история «append-only» мутируется (`status` ActionRecord меняется — `actions_recovery.py:335, 586`). Финальных проверок результата кроме структурных нет (`semantic_quality_verified=False` везде). | **Высокая** |

Дополнительно по связке с «владельцем» (стадия 3/6): [Ф] идентификация оператора — хеш токена или `local_web_operator` (`observations.py:272-275`); аутентификация выключена по умолчанию (`settings.py:403-404`) — см. разд. 5.

---

## 4. Этап 4 безопасно: «предложение → одобрение человеком → версионируемое применение → проверка → откат в один клик»

### 4.1. Что есть и что не подходит как есть [Ф]
- **Профили**: неизменяемый `ProfileOverlay` (`profiles_state.py:7-28`), привязан к хешам базы/графа/каталога/кандидата/тела запроса (`profiles_v1.py:223-230`); применяется на одну попытку через `transform_request` (`:287-296`); «откат» = `cancel` (`:237-254`) — отменяет только конфигурацию, данные/расход не откатывает (UI это пишет: `profiles-section.tsx:44`). Не хранит «предыдущее значение»; не меняет ничего персистентного → откатывать нечего, но и «улучшение» не закрепляется.
- **Версии промптов уже есть**: `app/services/prompt_history.py` — авто-архив **предыдущего** содержимого при `write_prompt_with_history` (151-165), список/чтение/восстановление версий (`list_prompt_versions`, `read_prompt_version`, `restore_prompt_version` — 168-172), метки, максимум 100 версий (`_MAX_VERSIONS`, старые удаляются — 140-150). Идентификатор версии — метка времени, а не хеш содержимого; индекс — изменяемый JSON; файлы промптов **глобальные** (`prompts/<шаг>/<имя>.md`), `write_prompt` без `_with_history` обходит архив (`prompt_library.py:526-538`); `delete_prompt` — `unlink` (541-556). Глобальная активная версия — `prompts/active_variants.json` (`prompt_active_global.py`). Каталог `prompts/` в этом клоне не отслеживается git как набор файлов (проверить не удалось: `git` отказывает из-за «dubious ownership») — [Г] история изменений промптов в VCS не гарантирована.
- **Параметры нод** лежат в `project.meta` (`node_step_params`, данные канвас-ноды `canvas_graph.nodes[].data`: модель/канал — `vibecode_catalog.resolve_node_choice`, `llm_override.bind_project_llm`) — это обычный JSON проекта без версий.
- **Коллизия с хешем графа**: `graph_version = digest({nodes, edges})` включает `data` нод (`actions_v1.py:404, 543`, `managed_launch.py:119`). [Ф] Любая правка модели/промпта-на-ноде через канвас меняет `graph_version` → разрешение, бюджет execution и все проверки `graph_matches` становятся «устаревшими» (`evaluate_start` → `graph_version_changed`, `actions_v1.py:645`). [О] Значит, «применить изменение к ноде» внутри уже идущего managed execution текущей архитектурой фактически запрещено; нужен второй хеш (структура графа vs. конфигурация ноды).
- **Коллизия с `input_revision`**: хеш всех `prompts/**/*.md` + настроек (`actions_recovery.py:136-138`) → любое сохранённое изменение любого промпта аннулирует все ожидающие билеты и одобренные профили (`stale config base version`, `profiles_v1.py:182-183`). [О] Для этапа 4 это одновременно защита и препятствие: нужно сужение хеша до фактически использованных нодой версий.
- **Отсутствуют роли**: `AuthDep` — одна общая сессия (`auth_sessions.py:15-53`); нет «разработчик» vs «оператор» (требование «серьёзное — подтверждает разработчик» не выразимо без ролей).
- **Параллельный неуправляемый канал**: старый чат-оркестратор (`db_browser.py:2404-2406` блокирует только managed-проект) умеет `edit_files`/`git_commit_push` по allowlist `app/`, `tests/`, `prompts/` (`code_autofix.py:13-17`; `db_browser.py:2703-2790`) — см. разд. 5.

### 4.2. Классы изменений и уровни разрешения [О — предложение]
| Тир | Что | Примеры | Кто подтверждает | Автоприменение |
|---|---|---|---|---|
| T0 | Выбор из **заранее одобренного каталога** без изменения смысла контракта | профиль из каталога владельца (уже есть), снижение `reasoning_effort` | уже заложено владельцем в запуске | да, в пределах execution (как сейчас) |
| T1 | Числовой/enum-параметр ноды **внутри заявленных границ** `TunableSpec` | `split.cell_max_chars` ±N%, `duration_seconds`, `max_output_tokens` ≤ потолка | оператор (1 клик), с превью | нет; только предложение |
| T2 | Новая версия **текста промпта** узла/проекта; смена модели вне каталога; изменение порядка/набора шагов | правка шаблона `shots_qc_ru` | оператор + «подтверждение разработчика» (отдельная роль/подпись) | нет |
| T3 | Запрещено моделью и оператором в этом контуре | код, SQL, бюджеты/лимиты, область (scope), HITL-ворота, секреты, сеть | только через разработку в репозитории | никогда |

### 4.3. Модель данных (master DB, аддитивные таблицы — **нужны реальные миграции**, `create_all` колонки не меняет)
```
config_versions            -- неизменяемые версии «артефактов конфигурации»
  version_id PK, artifact_kind('prompt'|'node_params'|'profile_set'), artifact_ref (напр. 'prompt:05_excel_gpt/shots_qc_ru'
  или 'node_params:{project_id}:{node_key}'), seq, content_sha256, content_json|content_text|blob_path,
  parent_version_id, created_by, created_at, change_id NULL      -- UNIQUE(artifact_ref, seq), UNIQUE(artifact_ref, content_sha256)
active_pointers            -- «что сейчас действует»: scope → версия, меняется только CAS
  scope_kind('project'|'node'|'global'), scope_id, artifact_ref, version_id, updated_at, updated_by, row_version
change_proposals
  proposal_id PK, project_id, execution_id, node_key, incident_id NULL,
  proposer('decision_model'|'operator'|'rule'), tier(T0..T3), target_kind, artifact_ref,
  base_version_id, base_content_sha256,            -- на что опирается предложение
  patch (структурированный diff: старое→новое), rationale(≤1000), evidence_ids[],
  dry_run{diff_text, limits_check, est_requests, est_tokens, affected_nodes[], invalidates{tickets,overlays}},
  status('draft'|'pending'|'approved'|'rejected'|'applied'|'verifying'|'verified'|'failed_verification'|'rolled_back'|'expired'),
  expires_at, created_at
change_approvals
  approval_id, proposal_id, approver_id, role('operator'|'developer'), decision, comment, signed_at, request_hash   -- хеш того, что видел утверждающий
change_applications
  application_id, proposal_id, applied_version_id, previous_version_id, applied_at, applied_by,
  verify_attempt_id, verify_result{structural, metrics_before, metrics_after, human_check}, rolled_back_by_application_id NULL
```
Принципы:
- Версия контента адресуется **хешем** (а не временем); хранится в БД (или в неизменяемом каталоге с хеш-именами) — не зависит от 100-версионного ограничения `prompt_history` (`_MAX_VERSIONS`). Существующую историю промптов импортировать как `parent`-цепочку.
- **Никогда не править общий файл** `prompts/<шаг>/<имя>.md`, влияющий на все проекты. Применение T2 = создать **новый именованный вариант** (напр. `auto-<node>-<hash8>`) и переключить выбор через уже существующие per-project механизмы (`prompt_overrides`, `prompt_slot_variants`, `resolve_project_prompt_name`, `prompt_library.py:745-792`); откат = вернуть прежний выбор (указатель), файлы не удаляются.
- `active_pointers` меняется только `UPDATE … WHERE version_id=:base AND row_version=:n` (CAS). Если база изменилась после одобрения — предложение переходит в `expired`, требует повторного рассмотрения.
- **Отделить хеши**: ввести `structure_hash` (id/типы нод и рёбра; для `ExecutionControl.graph_version`) и `config_revision` (по ноде: версии промпта + параметры + выбранная модель). `input_revision` строить из `config_revision` ноды + входных данных, а не из «всех промптов репозитория + кода». Это единственный способ допускать применение T1/T2 без разрушения бюджета и разрешений execution.

### 4.4. Конечный автомат и защитные условия
```
propose → [policy: тир, TunableSpec-границы, дедуп, лимит предложений на ноду/сутки]
        → dry-run (diff, оценка влияния, какие билеты/overlay станут устаревшими)
        → pending → (одобрение T1: оператор; T2: оператор + developer-подпись; хеш предложения в подписи)
        → apply (одна транзакция: новая версия + CAS указателя + audit-запись; бюджетные счётчики не трогаются)
        → verifying: один контрольный прогон ТОЛЬКО существующим исполнителем и верификаторами
                      (бюджет: из того же общего лимита; без обхода hold/stop/graph/audit)
        → verified  | failed_verification → авто-откат указателя + hold + human_review
        → rollback (один клик / CAS-возврат на previous_version_id; данные, файлы, расход НЕ откатываются)
```
- Модель решения получает **новое действие** `propose_change` с аргументами, проверяемыми схемой `TunableSpec` (как сейчас проверяется `retry_profile`, `decision_v1.py:62-65`), но результатом действия является **только запись `change_proposals`**; применение — отдельный путь после подписи человека. Модель никогда не вызывает apply.
- Ограничения: максимум 1 активное предложение на (нода, артефакт); потолок числа применений в сутки; запрет цепочек «предложение на основе непроверенного предыдущего»; запрет менять параметры, влияющие на безопасность (бюджеты/scope/allowed_actions/gate).
- «Качество» не измеряется (в коде везде `semantic_quality_verified=False`): для T2 обязательна **человеческая проверка результата** после контрольного прогона (кнопка «принять/откатить» с показом результата до/после).

### 4.5. Что нужно в UI (расширение `web/src/components/orchestrator/*`)
1. Раздел «Предложения изменений» (по execution/ноде): таблица со статусом, тиром (цветная метка), автором (модель/человек), доказательствами (ссылки на события — уже реализовано в `decision-section.tsx:48`), оценкой запросов/токенов.
2. Просмотр diff (унифицированный для текста промпта; «было → стало» для параметров), блок «что станет устаревшим» (билеты, профили).
3. Кнопки: «Одобрить (оператор)», «Подтвердить как разработчик» (виден только роли), «Отклонить», «Применить», «Откатить в один клик». Диалог отката показывает перечень того, что **не** откатывается (данные, файлы, потраченные запросы — формулировка уже есть в `profiles-section.tsx:44`).
4. Таймлайн версии: кто/когда/что поменял, хеши до/после, результат контрольного прогона, ссылка на повторный запуск.
5. Таблица «Настраиваемые параметры ноды» (из `TunableSpec`): текущее значение, границы, версия, последний автор; глобальный выключатель «заморозить изменения».
6. Исправить `profiles-section.tsx:24` (выбор профиля вместо `catalog[0]`).

### 4.6. Предпосылки (обязательные до включения)
- Роли и именованные учётки (минимум: `operator`, `developer`); сейчас единая общая сессия или её отсутствие.
- Закрыть/изолировать старый канал `edit_files`/`git_commit_push` для путей `app/services/*` оркестратора и `prompts/` (разд. 5, S6).
- Реальные миграции схемы (разд. 8, T8).
- Метрики «до/после» хотя бы структурные: доля валидного JSON, число нативных повторов, токены — берутся из журнала.

---

## 5. Анти-цикл, лимиты стоимости, устойчивость к рестарту

### 5.1. Что есть [Ф]
- Счётчик запросов на execution: `ActionExecutionControl.request_limit/requests_used` (`actions_v1.py:31-32`), резервируется атомарно **до** HTTP (`:554-559`), учитывает запросы воркера, Decision, GET-доставку ответа и медиа submit/status/download (`charge_request`, `authorize_model_retrieve` — `observation.py:431-442`).
- Потолок владельца: `owner_budget_policy`, по умолчанию 100, допустимо до 100000 (`decision_config.py:20-29`); запрос оператора не может превысить (`managed_launch.py:167-168`, `actions_v1.py:393-394`). Значение по умолчанию в UI — 12 (`routers/observations.py:38, 126`).
- Один запуск на (project, workflow-run): `ManagedRunAllowance` (`actions_v1.py:59-84`); закрытие не обнуляет расход.
- Анти-цикл: ≤3 шага модели (`decision_v1.py:21, 222`), запись решения уникальна на инцидент (`decision_state.py:20`), подпись повторного решения без нового свидетельства (`decision_v1.py:434-435`), один билет восстановления на ноду (`actions_recovery.py:50-52, 248-251`), нативный счётчик 3×3 → пауза проекта (`step_failure_policy.py:28-31`).
- Hold: запрещает новые запросы/старт, не отменяет отправленный (`actions_v1.py:486-528`; `docs/ORCHESTRATOR_CURRENT_STATE.md:77`). Аварийный hold при неопределённом результате (`observation.py:423-428, 630-632`).
- Аудит-блокировка: без `scope.gaps`/`failures`/`commit_uncertainty` запросы не уходят (`observation.py:615-616`, `actions_v1.py:540-544`).

### 5.2. Слабые места
**Лимиты стоимости**
- **L1 [Ф] Нет глобального бюджета между проектами**: лимит живёт в `ActionExecutionControl` одного execution; execution на проект — один (`actions_v1.py:429-435`), число проектов не ограничено; суммарного потолка нет (`grep` по `global_budget|per_day|daily_` в `app/` ничего не находит). **Высокая**.
- **L2 [Ф] Нет суточного/оконного потолка** (ни по запросам, ни по деньгам). **Высокая**.
- **L3 [Ф] Лимит — число запросов, не токены/деньги.** Токены ограничены только `max_output_tokens` для Decision и профиля; входные токены не ограничены (в живом прогоне 5464 вх. токена — `STAGE_STATUS_REPORT.md:45`). Стоимость считается ретроспективно из api-tracker (`telemetry.py:135-165`). Docs утверждают «нигде нет денежной модели» (`docs/ARCHITECTURE.md:233-235`), но тарифная таблица и калибровка существуют (`api_tracker_hook.py:23-62`) — [Ф] документ устарел; при этом контракты нод всё равно несут `cost_status:'unknown_tariff'` (`decision_v1.py:279`).
- **L4 [Ф] Медиа-стоимость не учитывается оркестратором**: `record_api_call` вызывается только из `observation._track_api_cost` с `call_type="text"` (`observation.py:550`); видео/аудио/картинки Outsee — не трассируются. Телеметрия «стоимость проекта» поэтому занижена (`telemetry.py:162-165`).
- **L5 [Ф] Рубильник существует, но не подключён**: `api_tracker_hook.check_api_allowed` / `is_killswitch_active` (`api_tracker_hook.py:335-397`) не вызываются нигде в `app/` (поиск по репозиторию находит только определение). Он же «fail-open» при недоступном облаке и отсутствии локальной записи (`:340-372`). Плюс зависит от стороннего облака (Supabase), URL и публикуемый ключ зашиты в исходник по умолчанию (`:309-310`; значения здесь не приводятся). **Высокая**.
- **L6 [Ф/О] Бюджет 12 запросов по умолчанию в UI несовместим с проверенным сценарием**: нативное исчерпание одной ноды = 9 отказов, в проверенном прогоне это 18 POST (`docs/ORCHESTRATOR_CURRENT_STATE.md:183`), а `recover_node` отказывает при `requests_used ≥ request_limit` (`actions_recovery.py:246-247`); живой прогон использовал лимит 40 (`STAGE_STATUS_REPORT.md:124`). [Г] с лимитом 12 нативные повторы могут исчерпать бюджет до того, как супервизор получит возможность действовать. Проверить тестом.
- **L7 [Ф]** В неуправляемом режиме оркестраторного лимита нет вовсе (`observation.py:595` — списание только `if ctx.scope.mode=="managed"`); защита — только нативные политики (`step_failure_policy`, `video_error_policy`).

**Анти-цикл / зависания**
- **W1 [Ф] Нет watchdog зависшего выполнения.** Лиз есть только у решения (120 с, `decision_v1.py:223`) и у билетов восстановления (30/120 с, `actions_recovery.py:478-483`). Для зависшего нативного запроса без `node_error` инцидент не создаётся (правило `uncertain_request` требует `node_error/node_end` или остановленный observer — `diagnosis.py:222-227`); сообщение «requests_without_recorded_end» видно только в `GET …/context` (`observations.py:628-635`). **Высокая**.
- **W2 [Ф] Головная блокировка цикла.** `tick()` вызывает `await self.step(...)` внутри общего `loop()` (`decision_v1.py:583`, `observation.py:359-360`); шаг ждёт ответ модели до `timeout_s` (30–90 с, `decision_config.py:109`). Пока идёт один запрос, останавливаются доставка журнала, диагностика и `reconcile` для **всех** проектов. [О] Средняя–Высокая при росте числа проектов.
- **W3 [Ф]** После `human_review` авто-возобновления нет (`actions_v1.py:493-494`) — это решение безопасности, но означает, что без оператора выполнение стоит неограниченно долго (уведомлений нет в коде ядра; [Г] в Telegram-слой не заходил).

**Рестарт и необратимые блокировки (разд. «fail-closed без возврата»)**
- **E1 [Ф] Деградация наблюдателя односторонняя.** `mark_gap` ставит `observer_state="degraded"` (`observation.py:309-314`); `catch_up` добавляет запись в `ExecutionControl.gaps` (`:335-343`); обратного перехода в `active` в коде нет (поиск присвоений `observer_state="active"` находит только создание — `observation.py:172`, `managed_launch.py:184`). `charge_request` требует `observer_state=='active'` и пустые `gaps` (`actions_v1.py:540-544`). Любая временная ошибка (напр. `database is locked`, о которой пишут docs: `ORCHESTRATOR_COMPLETION_REMAINING_20260930.md:10`) в любой операции цикла фиксируется как «пробел» навсегда. **Критично**.
- **E1b [Ф] Взрыв радиуса.** Исключение в `reconcile()` или `tick()` (они не обёрнуты построчно) ловится общим `except` в `loop` и вызывает `mark_gap` для **всех** активных scope (`observation.py:361-364`). Одна ошибка = permanent gap у всех управляемых проектов.
- **E1c [Ф] Тупик замены.** Новый execution на тот же workflow-run выдать нельзя: `claim_run_allowance` (`actions_v1.py:74-84`) и `prepare` (`managed_launch.py:105-107`) отказывают. [О] нужен «break-glass»: подписанный владельцем перевыпуск execution с переносом израсходованного бюджета.
- **E2 [Ф] Глобальная квота журнала.** `save_body` суммирует **все** `*/*.json` под `body_root` (`observation.py:258-260`), отказывает при `total+len > 32 MiB` (`:264-265`), удаления нет (`RETENTION_SECONDS` определён, не используется — `:146`; комментарий «No automatic deletion», `:256-257`). `record_fact` при таком `gap` вызывает `mark_gap` (`:417-418`) → E1. Лимит общий для всех execution и проектов; два тела на запрос (запрос и ответ), одно тело до 1 МиБ. **Критично/Высокая** (предел достижим за десятки запросов крупных промптов; [Г] точное число запросов не считал). Кроме того, каждое сохранение выполняет `glob+stat` всех тел (O(N) на запись).
- **E3 [Ф] `commit_uncertainty` — только в памяти и не очищается** до рестарта (`observation.py:155, 935`): после неподтверждённого коммита execution блокируется до перезапуска процесса, а после перезапуска пометка исчезает «молча», и инцидент `journal_gap` переводится в `resolved_confirmed` с причиной `condition_not_observed` (`diagnosis.py:355-358`). Т.е. и ложная вечная блокировка, и потеря следа.
- **E4 [Ф]** `_inflight_projects`, `_active_nodes` — in-memory (`actions_recovery.py:21-22`); `_active_nodes` перезагружается из БД (`:37-45`), `_inflight` — нет (на рестарте защитой служат лиз-таймеры и `is_generation_active`). Приёмка жёсткого падения/рестарта для apply/download **не выполнена** (T5 в `ORCHESTRATOR_COMPLETION_REMAINING_20260930.md:65`).
- **E5 [Ф]** Не-веб процесс (`app/worker.py`) не запускает `Observer.loop`; допуск тогда `ordinary_mode` (`actions_v1.py:151-156`), т.е. управляемый проект исполнялся бы без гейтов. Модуль по факту не используется (ссылается только тест `tests/test_step_registry_active_statuses.py:6`) — [Ф] мёртвый код, но потенциально опасный дубль основного цикла `app/main.py`.

**Нагрузочные/производительные слабости (влияют на надёжность)**
- **D1 [Ф]** `Diagnoser.update` каждую секунду читает всю историю каждого execution (в т.ч. давно закрытых): `diagnosis.py:315-319, 372-373`; `catch_up` Observer обходит все `ExecutionControl` когда-либо созданные (`observation.py:320-322`). Рост O(история×исполнения). Телеметрия загружает все инциденты/решения/события проекта в память (`telemetry.py:197-212`) и опрашивается SSE каждые 2 с на каждый открытый клиент (`observations.py:781`).
- **D2 [Ф]** `runtime_config()` читает и валидирует JSON с диска на каждый вызов (`decision_config.py:127-138`) в горячих путях (`observe_attempt` 765, `charge_request` 552, `_context` 146).

### 5.3. Предлагаемые меры (приоритет [О])
1. (Критично) Сделать деградацию **обратимой и локальной**: разделить «неустранимый пробел» (потеря/порча журнала) и «временная ошибка потребителя»; `mark_gap` — только для первого; для второго — счётчик/backoff; обработка ошибок построчно по execution в `loop`, а не глобальный `except`; явное действие оператора «подтвердить и снять пробел» с аудитом.
2. (Критично) Квоты журнала: на execution и на проект; ротация/архивация закрытых execution по `RETENTION_SECONDS`; запись `gap: body_total_limit` не должна блокировать выполнение, если сохранены хеши и метаданные (или должна давать **заметную** причину и кнопку очистки).
3. (Высокая) Глобальный супервайзер расходов: суммарный и суточный потолок (запросы + оценка $ по таблице тарифов + токены), «аварийный стоп всех управляемых execution» (одна кнопка/эндпойнт, ставящий `held` на все контроли и блокирующий `worker_entry_admission`), подключить `check_api_allowed` к реальным точкам отправки или заменить его локальным рубильником без облака.
4. (Высокая) Watchdog: период проверки открытых `model_start` без `model_end/error`, нод `running` без прогресса > порога; создаёт инцидент и (в managed) hold. Для старта-рестарта — «reconcile при запуске» (T5).
5. (Высокая) Персистентная `commit_uncertainty` (в `ExecutionControl.gaps` с типом) и ручное снятие.
6. (Средняя) Вынести вызов модели Decision из общего цикла (отдельная задача с лимитом параллелизма), доставку/диагностику сделать инкрементальной по курсору и пропускать закрытые execution.
7. (Средняя) Согласовать UI-умолчание лимита (12) с реальным потреблением нативных ретраев либо не считать нативные повторы вместе с бюджетом супервизора (разделить счётчики «нативные» и «супервизорные»).

---

## 6. Безопасность: что может повлиять решающая модель, инъекции, allow-list, утечки

### 6.1. Что модель может сделать (поверхность влияния) [Ф]
- Только JSON из 5 полей (`validate_proposal`, `decision_v1.py:44-67`), действие обязано входить в `context.allowed_actions`; аргументы жёстко схематизированы: `read_events` — id событий из `evidence_ids` (≤50), `retry_profile` — `overlay_id` из одобренных или `profile_id` из допустимых, остальные действия — без аргументов. Свободных параметров (модель, токены, промпт, URL) у модели **нет**.
- Allow-list действует в **четыре** слоя: конфиг владельца `allowed_actions` (`decision_config.py:103`), политика запуска (`managed_launch.allowed_actions`, `:234-236`), способности ноды (`decision_v1.py:183`), класс инцидента (`:166`). Затем повтор проверки в исполнителе: `recover_node` (`actions_recovery.py:185-189, 228-229`) и `claim` (`:387-400`). Профиль привязан к хешам базы/графа/каталога/тела (`reservation`, `profiles_v1.py:256-264`).
- Отказ в границах: сеть — маршрут `endpoint` должен точно равняться утверждённому (`check_route`, `decision_config.py:141-147`), HTTPS для внешнего, synthetic — только loopback (`:115-118`); ответ без `status=completed` не принимается (`decision_v1.py:665-666`); `finish` требует независимой проверки (`:464-466`).
- [О] Оценка: **для текущего узкого набора действий дизайн сильный**. Риски появятся с этапом 4/5, когда в схему добавятся действия с текстовыми аргументами.

### 6.2. Prompt-injection из выходов нод в контекст решения
- [Ф] Системный промпт объявляет логи/контракты/результаты инструментов недоверенными (`decision_v1.py:25-41`); ответ — строгая схема.
- [Ф] В контекст идут первые 20 фактов попытки и `incident.details` через `panel_safe` (`decision_v1.py:176-179`). `panel_safe` — **deny-list** ключей (`text, raw, response, event, accumulated_text, ops, characters, scenes, prompt, instructions, input, messages`; `observation.py:992-1004`) + маскировка путей Windows/служебных каталогов. Тела запросов/ответов в payload — только ссылкой на файл (`save_body`), поэтому свободный текст модели/пользователя в контекст в основном не попадает.
- [Ф] Тем не менее в контекст попадают строки, не вычисленные кодом: `incident.details['fact']` собирается из `p.get('reason')` (`diagnosis.py:232`), коды `ERROR_CATALOG`, тексты `NodeRun.error/progress_text` в снапшоте API (`observation.py:1035-1036` → `/context`, `observations.py:590-690`). `read_events` отдаёт payload выбранных событий тем же фильтром.
- [О] Риск сегодня **низкий–средний** (данные в основном структурные), но deny-list хрупок: любое новое поле с текстом нод, добавленное исполнителем, пройдёт в контекст. Предложение: allow-list по реестру событий (разд. 2.3), фиксированные короткие поля, запрет свободных строк длиннее N символов и любых строк из пользовательского/модельного контента (озвучка кадров, промпты, ответы), обрамление данных как JSON-значений, а не текста.

### 6.3. Утечки секретов в журналы и наружу
- [Ф] Редакция: `redact()` — по именам полей (`authorization, cookie, api_key, token, password, secret…`) и regex (`Bearer …`, `key=…`, `sk-…`; `observation.py:77-99`). Применяется к `payload` (`stage`, `:233`), телам (`save_body`, `:249`) и `digest`. Ключи в тела запросов и заголовки не пишутся (`model_start` пишет host/path/модель, без заголовков — `:606-610`). Конфиг рантайма не допускает ключей: неизвестные поля отвергаются (`validate_config`, `decision_config.py:76-79`).
- [Ф] Права на диск: каталог тел 0700/Windows-DACL владельца и SYSTEM (`observation_files.py:6-31`); [Ф] но **содержимое — полные промпты и ответы** (бизнес-данные), попадают в бэкапы/архивы папки данных.
- [Ф] **Утечка в облако**: `_track_api_cost` → `api_tracker_hook.record_api_call` отправляет строку вызова в Supabase (`api_tracker_hook.py:200-223`) вместе с `error_message` — это `f"{type(exc).__name__}: {exc}"` **без `redact()`** (`observation.py:575-577, 648-651`; `decision_v1.py:672`) и с `project_source=str(project_id)`. Тексты исключений HTTP-клиентов могут содержать URL/идентификаторы. Отправка подавляется для `synthetic*`-ключа (`observation.py:546`). [О] **Высокая**: пропускать `error_message` через `redact`/свести к коду каталога; выключаемый флаг облачной отправки.
- [Ф] `GET …/stream` возвращает клиенту `str(exc)` (`observations.py:772`).
- [Ф] `input_revision` хеширует `settings.model_dump()` (`actions_recovery.py:135`); `digest` сначала редактирует по именам полей (`observation.py:102-104`), поэтому секреты в хеш не входят, но в `recovery_revision_parts` пишутся хеши разделов (`:144-145`) — смена несекретной настройки (любой поля `Settings`) аннулирует билеты.

### 6.4. Доступ и авторизация
- [Ф] Аутентификация включается только если заданы `WEB_AUTH_USER` и пароль (`settings.py:403-404`); по умолчанию выключена, CORS `allow_origins=["*"]` (`api.py:260-266`), глобальной проверки нет.
- [Ф] В `observations.py` `AuthDep` стоит только на «управляющих» маршрутах (launch, decisions/control, profiles approve/cancel, actions/*). **Без `AuthDep`**: `POST ""` (включить наблюдение, 235-252), `POST …/stop` (263-269), `POST …/pin` (693-699) и все `GET` (решения, профили, действия, события, контекст, диагностика, телеметрия, stream). Даже при включённой аутентификации чтение журнала/инцидентов открыто.
- [О] В сочетании «аутентификация по умолчанию выключена + `CORS *` + платные действия» любой сайт в браузере оператора может отправить запрос на локальный API (запуск managed, одобрение профиля). **Высокая**. Меры: токен/Origin-проверка для мутаций, `AuthDep` на все маршруты, привязка к 127.0.0.1 по умолчанию, роли.
- [Ф] Идентичность оператора — псевдоним `web_operator:<sha256(token)[:16]>` или `local_web_operator` (`observations.py:272-275`); `human_identity: None` в аудите (`observation.py:177`).

### 6.5. Параллельная неуправляемая плоскость управления
- [Ф] Старый чат-оркестратор допускает `edit_files`, `run_tests`, `git_commit_push` по allowlist `app/`, `tests/`, `prompts/` (`code_autofix.py:13-37`; `db_browser.py:2703-2790`; `docs` пишут об этом как о «Fix bugs»). Блок «managed» применяется только к **этому проекту** (`db_browser.py:2404-2406`).
- [О] Следствие: чат неуправляемого проекта B теоретически может править `app/services/actions_v1.py`/`decision_v1.py`, регулирующие проект A, и `prompts/`, из которых строится `input_revision`. Это самый прямой обход тезиса «серьёзные изменения — только с подтверждением разработчика». **Высокая**. Меры: добавить в `DENIED_PREFIXES` пути ядра оркестратора и `prompts/` либо отключить чат-правку кода в продакшене; вести защищённую от записи контрольную сумму файлов политики (`app/services/{actions_v1,actions_recovery,decision_*,profiles_*,observation}.py`) в `input_revision` и при старте.

### 6.6. Прочее
- [Ф] Журнал «append-only» не защищён от изменения: `ActionRecord.status/verification` обновляются на месте (`actions_recovery.py:335, 586`; `observations.py:473-479`), `Incident.details` перезаписывается (`diagnosis.py:349-352`); хеш-цепочки нет. [О] Для аудита этапа 6 добавить неизменяемый журнал решений (append-only с хеш-цепочкой или `INSERT`-триггеры).
- [Ф] Явный plus: ключ идемпотентности, CAS на лизах, запрет повторных POST после неопределённого результата, привязка билета к хешам входа/графа/конфига.

---

## 7. Панель покрытия в UI: «какая нода что поддерживает»

### 7.1. Данные сегодня (сводка по коду) [Ф]
Типы: 23 в `WORK_NODES` + виртуальный `excel_gpt`. Столбец «Способность» = есть запись в `_CAPABILITIES`; «Наблюдение» = есть хуки в исполнителе/`observe_attempt`.

| Нода | Способность | managed | Действия | Проверка результата | Хуки наблюдения | Заметка |
|---|---|---|---|---|---|---|
| plan, script | да | **да** | read + retry_same/retry_profile/finish | `verify_project_text` (`node_capabilities.py:375-395`) | да (`xlsx_step_runners`) | HITL сохранён; живое восстановление не пройдено |
| excel_gpt (virtual) | да | **да** | то же | общий parse/apply | да | де-факто приёмка по одной группе `script_frames_qc` (H16) |
| split | да | да | read | `observe_split_result` (`:463-478`) | да | восстановление выключено (нативный fallback) |
| image_prompts | да | да | read | `observe_image_prompt_result` (`:258-297`) | да | восстановление выключено; при провале hold |
| images | да | да | read | Pillow/hash/provenance (`artifact_verifier`) | да (Kie route) | только synthetic; Outsee-маршрут не наблюдается |
| videos, audio, assemble | да | нет | read | ffprobe-ветка не принята | нет (шаги без хуков) | `MEDIA_RECOVERY_DECISIONS`: человек-только |
| sfx_plan, sfx_gen | да | нет | read | `observe_sfx_result` | частично (`sfx_plan.py`, `generate_sfx.py`) | |
| publish | да | нет | read (без retry) | dry-run | да (2 вызова) | внешняя публикация заблокирована |
| scene_design, sd_agent, sd_assemble | **нет** (unsupported) | нет | read | `observe_scene_result` вызывается (`observation.py:797-805`), но способность не заведена | частично | |
| animation_prompts | **нет** | нет | read | `observe_animation_result` (`:806-811`) | да | |
| hero, items | **нет** | нет | read | — | нет | `read_contract` частично описывает hero (`decision_v1.py:262-264`) |
| enrich_1…5 (legacy) | **нет** | нет | — | — | — | отображаются как excel_gpt по префиксу (H4) |
| music | **нет** | нет | read | — | нет | |

Итого: **12 из 24** с записью способностей, **6 из 24** `managed`, **3 из 24** с автоповтором.

### 7.2. Дизайн панели «Покрытие нод»
**Источник данных (никаких ручных таблиц в UI):** новый read-only эндпойнт `GET /api/orchestrator/coverage` строится из реестра `NodeSpec` (разд. 2.3) + реестра фактов приёмки. Каждая ячейка = `{level, reason, evidence_ref, since, stale}`.

Строки — типы нод (группировка по семействам: Текст, Кадры, Сцены, Картинки, Видео/Аудио, Сборка/Публикация), опциональный переключатель «по конкретным нодам текущего графа» (используется `effective_node_type`).

Столбцы сгруппированы по 6 этапам плана:
1. **Наблюдение** — хуки ввода/парсинга/применения; трассировка запросов (LLM/медиа); учёт стоимости (текст/медиа).
2. **Диагностика** — какие классы инцидентов эта нода способна породить (из реестра правил), выводится ли причина.
3. **Управление** — managed старт; retry_same; retry_profile; hold/stop; HITL-совместимость; продолжение (`graph`/`excel_chain`/нет).
4. **Настройка** — `profile_compatible`; список `TunableSpec` (параметры, границы); допустимые варианты промпта; смена модели (каталог).
5. **Цикл** — автовход решения (какие классы), тип верификации (`structural/commit/artifact/none`), судья качества (нет/человек).
6. **Лимиты и безопасность** — класс стоимости (`paid_per_image…`), идемпотентность submit, риск двойной оплаты, ограничения бюджета, тест рестарта.

Уровни ячейки: `Нет`, `Только чтение`, `Частично`, `Полностью`, плюс бейдж **уровня приёмки**: `заявлено` / `synthetic` / `реальная (дата)` / `устарело` (код изменился после приёмки — по хешам загруженных модулей, как в `docs/ORCHESTRATOR_CURRENT_STATE.md:148-150`).

UI-элементы:
- Верхняя плашка: «6/24 управляемых, 3/24 с авто-повтором, 0 с реальной приёмкой модифицирующего цикла для медиа»; фильтры по этапу и серьёзности пробела; поиск.
- Клик по строке — боковая панель: контракт ноды (результат `contract.describe`), последние инциденты по типу ноды, ссылки на тесты/документы приёмки, «что нужно, чтобы включить» (чек-лист из conformance-тестов).
- Лента «Пробелы по серьёзности» (экспорт CSV/Markdown) — для планирования.
- Интеграция с запуском: `managed-launch-section` показывает причину недоступности из той же структуры (сейчас — одна строка `observations.py:116`), с переходом на строку покрытия.
- Лёгкость: ответ эндпойнта кэшируется по хешу реестра, обновление — по событию деплоя, а не опросом каждые 2 с (в отличие от текущих секций: `profiles-section.tsx:17`, `decision-section.tsx:19`).

---

## 8. Технический долг (список)

| ID | Что | Доказательство [Ф] | Серьёзность [О] |
|---|---|---|---|
| T1 | Деградация/gap необратимы; общий `except` метит все execution | `observation.py:309-314, 335-343, 361-364` | Критично |
| T2 | Глобальная квота тел 32 МиБ, нет очистки, `RETENTION_SECONDS` не используется, O(N) `glob` на запись | `observation.py:146, 256-265` | Критично |
| T3 | `commit_uncertainty` в памяти; сбрасывается рестартом; закрывается как «не наблюдается» | `observation.py:155, 935`; `diagnosis.py:355-358` | Высокая |
| T4 | Рубильник API не подключён; зашит облачный URL/публикуемый ключ по умолчанию; fail-open | `api_tracker_hook.py:298-397, 309-310` | Высокая |
| T5 | Нет глобального/суточного/денежного лимитов; медиа-стоимость не учитывается | разд. 5, L1–L4 | Высокая |
| T6 | Аутентификация по умолчанию выкл., `CORS *`, мутации без `AuthDep`, чтение журнала без `AuthDep` | `settings.py:403`, `api.py:260`, `observations.py:235, 263, 693` | Высокая |
| T7 | Чат-оркестратор правит код `app/` и `prompts/`, `git push` | `code_autofix.py:13-17`, `db_browser.py:2750-2790` | Высокая |
| T8 | `create_all` вместо миграций: новые колонки в существующих таблицах не появятся (а этапы 4/6 их потребуют) | `observation.py:1017` | Высокая |
| T9 | Утечка `error_message` без `redact` в локальную и облачную БД трекера | `observation.py:575, 648-651`; `api_tracker_hook.py:200-223` | Высокая |
| T10 | `NodeSpec` не несёт 4 поля способности; `_VERIFIERS` не читается; ни одна нода не мигрирована | `node_spec.py:75-97` | Высокая (блокер расширения) |
| T11 | Цепочки `if capability.node_type …` в `observe_attempt` (H5) и в `read_contract` (H8) | `observation.py:790-850`, `decision_v1.py:257-357` | Высокая |
| T12 | `input_revision` хеширует весь `prompts/` + 6 файлов кода + settings — слишком грубо для этапа 4 | `actions_recovery.py:111-146` | Высокая |
| T13 | `graph_version` включает конфигурацию нод → любое изменение ноды аннулирует разрешения | `actions_v1.py:404, 543` | Высокая |
| T14 | Приёмка по типу, а не по контракту (excel_gpt в целом = «managed») | `node_capabilities.py:58-60`, `node_write_contract.py:202-216`, `managed_launch.py:69` | Средняя–Высокая |
| T15 | Динамические атрибуты на `AttemptContext` (`ctx.scene_native_node`, `ctx.split_input_voiceover`, `ctx.image_prompt_*`…) через `getattr/setattr` | `observation.py:797-850`, `node_capabilities.py:226-229, 433-437` | Средняя |
| T16 | Единственный последовательный цикл (ожидание модели блокирует всё); опрос раз в секунду; чтение всей истории | `observation.py:347-365`, `decision_v1.py:583`, `diagnosis.py:315-319` | Средняя |
| T17 | Четыре представления «управляется ли проект» + три таблицы «control» | разд. 1.7 | Средняя |
| T18 | Три реестра шагов (`WORK_NODES`, `step_registry`/`telegram.menu.STEPS`, enum) и ручные «коллизии» | `node_registry.py:145-159`, `step_registry.py` | Средняя |
| T19 | Мёртвый/дублирующий код: `app/worker.py` (альтернативный воркер), `PRODUCTION_CATALOG`, `MEDIA_RECOVERY_DECISIONS`, `RETENTION_SECONDS` | `worker.py`, `profiles_v1.py:23`, `node_capabilities.py:83-133`, `observation.py:146` | Низкая–Средняя |
| T20 | Классификация ошибок по подстрокам русского текста исключений | `step_failure_policy.py:34-71` | Средняя |
| T21 | `runtime_config()` читает файл с диска на каждый вызов | `decision_config.py:127-138` | Низкая |
| T22 | UI: `catalog[0]` вместо выбора профиля; опрос 2 с в каждой секции + SSE 2 с с полной выборкой | `profiles-section.tsx:24, 34`, `telemetry.py:184-225`, `observations.py:781` | Низкая–Средняя |
| T23 | История действий в UI ограничена 100 без пагинации; `list_observations` limit 100 | `observations.py:259, 285` | Низкая |
| T24 | Читаемость: код в стиле «в одну строку через `;`» (`decision_v1.py`, `profiles_v1.py`, `node_capabilities.py` поздняя часть) | например `decision_v1.py:200-216` | Средняя (сопровождение) |
| T25 | Документы расходятся с кодом: «NodeSpec не построен» (`ARCHITECTURE.md:130-134`, `ADDING_NEW_AGENT.md:4-6`), 15 vs 17 таблиц, «нет денежной модели», `ARCHITECTURE_AUDIT.md` устарел | сравнение файлов | Низкая |
| T26 | Тесты: набор `tests/observe_v1` требует особого окружения; в унаследованном наборе остаются падения (94 baseline → 40, `STAGE_STATUS_REPORT.md:29-33`) | docs | Средняя |
| T27 | Нет теста «добавь ноду» и сквозного теста всего конвейера (`ORCHESTRATOR_STATUS_CURRENT.md:43`) | docs | Средняя |
| T28 | Тайм-ауты/таймеры сна проекта (≥ 1 ч до входа супервизора) не согласованы с целью «самовосстановление» | `step_failure_policy.py:28-31` | Средняя |

---

## 9. Рекомендованный порядок работ [О]

1. **Надёжность ядра (1–2 нед):** T1/E1, T2/E2, T3/E3, watchdog W1, аварийный стоп всех (L5), построчная обработка ошибок цикла, вынос вызова модели из общего цикла (W2).
2. **Безопасность доступа (3–5 дн):** T6 (AuthDep + Origin/токен + bind localhost), T9 (redact + флаг облака), T7 (закрыть правку ядра чатом), роли.
3. **Лимиты стоимости (3–5 дн):** глобальный/суточный потолок, учёт медиа-стоимости, разделение счётчиков нативных и супервизорных запросов (L6).
4. **Декларативные ноды (5–8 дн):** NodeSpec целиком, миграция 12 записей, `verifier`/`contract`/`successor`/`executor`, conformance-тесты (разд. 2.3), реальные миграции схемы (T8).
5. **Панель покрытия (3–4 дн)** на основе п. 4 (разд. 7.2).
6. **Этап 4 (2–3 нед после 1–4):** разделение `structure_hash`/`config_revision`, `config_versions` + `change_proposals`, роли, UI (разд. 4). Начинать с T1-параметров и выбора варианта промпта, а не свободного текста.
7. **Расширение классов отказов и нод** — по одной ноде, каждая с conformance-тестом и приёмкой; медиа — только после п. 3 (учёт стоимости/идемпотентности).

---

## 10. Что не проверено / ограничения разбора
- Запуск приложения и тестов не выполнялся (запрещено); утверждения про рантайм-поведение (E1, E2, L6) основаны на чтении кода, помечены там, где требуют проверки.
- `docs/ORCHESTRATOR_CURRENT_STATE.md` прочитан до разд. 8 и выборочно далее (приложения A–C не читались); `auto_advance.py` (2073 строк) — выборочно по местам интеграции с managed (строки 788-819 и упоминания управляемого режима); `git` недоступен («dubious ownership»), поэтому отслеживаемость `prompts/` в VCS не проверена.
- Не разбирались: `gateway`-скрипты, `media_jobs.py` целиком, `artifact_verifier.py`, `scene_contracts.py`, `native_*_contracts.py`, Telegram-слой, `graph/planner.py`.
- Секреты не читались и не приводятся (в источнике обнаружены встроенный облачный URL и публикуемый ключ трекера — значения намеренно не воспроизведены).
