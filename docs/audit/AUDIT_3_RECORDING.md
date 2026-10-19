# АУДИТ 3: Запись и наблюдаемость («всё записывается везде»)

Репозиторий: `C:\video-pipeline\video-pipeline-observe-v1`, HEAD 850b3f47. Режим: только чтение кода (.env не читались, сеть и платные вызовы не использовались, тесты не запускались). Все ссылки вида `файл:строка` проверены чтением. Где вывод сделан по подсчёту grep, а не по полному разбору, это помечено словом «оценка».

Требование заказчика: все логи, все ошибки, все действия, все траты записаны везде, читаются легко и ясно.

## 0. Короткий вывод

1. Журналы есть и сделаны добротно (append-only триггеры, outbox, редактирование секретов в наблюдателе), но они покрывают в основном **управляемые запуски (execution)** и **слой действий**. Весь остальной ручной контур Studio (около 150 мутирующих эндпоинтов роутеров, из них 58 в `project_ops.py`) не оставляет ни одной строки журнала: кто, что и когда изменил в проекте, не восстановить.
2. Деньги пишутся в **четыре независимых хранилища** (`spend_ledger` + `action_layer_spend` + `api-tracker/tracker.db` + `orchestrator_media_jobs/requests`) без общего ключа. Запись нативных платных вызовов медиа сделана «выстрелил и забыл» с глухим `except: pass`: потерянная строка про деньги не оставляет следа.
3. Единой ленты по проекту или запуску нет. Оператор должен ходить по 8+ панелям и таблицам. Предложение единой ленты (раздел 7) строится на существующих таблицах, без новой подсистемы трассировки.
4. Риск диска: `data/backend.log` и `data/backend-<PID>.log` растут без ротации и очистки (создаёт `scripts/run-backend.ps1`), `logs/status.log`, `logs/errors.log` тоже без ротации, таблицы журналов без хранения/очистки. Мониторинга свободного места в приложении нет.
5. Утечки секретов: редактирование есть в наблюдателе и в трекере, но **нет** в слое действий (история, планы), в `spend_ledger.note`, в `errors.log` и нет фильтра на уровне loguru-приёмников. Дополнительно: ключ Supabase и URL зашиты в исходники, а строки о вызовах API по умолчанию уходят в облако.

## 1. Инвентарь: что где записывается

| Что | Хранилище (файл:строка) | Ключи корреляции | Хранение / рост | Читает оператор |
|---|---|---|---|---|
| События запуска (факты) | `observation_events` и `observation_outbox`, `app/services/observation.py:46`, `:62` | project_id, workflow_run_id, execution_id, node_key, attempt_id, request_id | без очистки; очищаются только тела (`cleanup_bodies`, `observation.py:447`, квота 32 МБ на проект, 7 дней) | панель «Наблюдение», `GET .../{execution_id}/events` (`observations.py:641`) |
| Пробелы журнала | `observation_degradations` (`observation.py:79`), `observation_commit_uncertainty` (`:99`) | execution_id, project_id | без очистки | панель контекста, список пробелов |
| Диагноз / инциденты | `observation_diagnosis`, `observation_incidents` (`app/services/diagnosis.py:31`, `:40`), заголовки на русском (`diagnosis.py:~55`, словарь `TITLES`) | execution_id, project_id, node_key, attempt_id | без очистки | панель «Диагноз», `observations.py:784` |
| Решения | `decision_records`, `decision_turns`, `decision_execution_controls`, `decision_runtime_bindings` (`decision_state.py:19`, `:41`) | execution_id, incident_id, decision_id, action_id | без очистки; `payload` реплик JSON без ограничения размера | панель «Решения», `observations.py:234` |
| Действия v1 (operator audit) | `action_v1_records` (`actions_v1.py:43`), `action_execution_controls` (`:24`), `managed_run_allowances` (`:67`) | execution_id, project_id, idempotency_key | без очистки | панель действий, `observations.py:344` |
| Слой действий (чат-план) | `action_layer_plans/steps/approvals/history/spend/plan_context/attachments/macros` (`action_layer/store.py:38`...`:140`) | plan_id; project_id только в `plans` и `plan_context` | вложения имеют срок (`attachments.py:386`); история и spend без очистки | `GET /history` (`action_layer/router.py:288`), `history-list.tsx` |
| Деньги, единый лимитный журнал | `spend_ledger` (`spend_guard.py:63`), триггеры запрета UPDATE/DELETE (`:~75`) | project_id, run_id (`plan:<id>` или execution_id или пусто), source, provider | без очистки, строки малы | `GET /observations/spend` (`observations.py:201`), `spend-section.tsx`; построчно **не** выводится |
| Задачи провайдеров | `provider_task_ledger` (`spend_guard.py:243`) | project_id, key, task_id | без очистки | `GET /observations/provider-tasks` (`observations.py:193`) |
| Расход слоя действий (в кредитах) | `action_layer_spend` (`store.py:97`, `budget.py:57`) | session_key=`plan:<id>`, plan_id | без очистки | только через план/историю |
| Расход LLM и медиа (в USD, по вызовам) | `api-tracker/tracker.db`, таблица `api_calls` (`tracker_schema.py:20`), пишет `api_tracker_hook.record_api_call` (`api_tracker_hook.py:229`); отдельный сервис `api-tracker/` с UI (`API_TRACKER.cmd`) | только `project_source`=str(project_id) (`observation.py:1066`); нет run_id, request_id, attempt_id | без очистки и без VACUUM (`grep retention|DELETE` в `api-tracker/app` пуст) | сервис api-tracker; в Studio только агрегат `telemetry` |
| Медиа-запросы и задачи | `orchestrator_media_jobs/requests` (`media_jobs.py:24`, `:47`) | project_id, execution | нет данных об очистке | панель медиа |
| Телеметрия | `app/services/telemetry.py` (только чтение: агрегаты по существующим таблицам, `telemetry.py:1-10`), `GET /telemetry` (`observations.py:812`), SSE `/stream` (`:881`) | project_id | не пишет, роста нет | `telemetry-section.tsx` |
| Рубильник | `supervisor_kill_switch(+_history)` (`kill_switch.py:42`, `:54`) | глобальный, без проекта | малый | `kill-switch-section.tsx` |
| Контроль изменений конфигурации | `change_proposals`, `config_versions`, `config_pointers` (`change_control/store.py:49`...) | proposal id | малый | роутер change_control |
| Статусы нод | `node_runs` + текстовый файл `logs/status.log` (`node_status_machine.py:29`, `:95`) | project, node | **файл без ротации**, 1.5 МБ уже | только вручную |
| Ошибки ботов | `logs/errors.log` (`bots/outsee.py:35`, `bots/chatgpt.py:29`, `bots/elevenlabs.py:30`) | нет (строка: время, bot, node, kind, текст) | **без ротации** | только вручную |
| Лог приложения Studio | `data/studio-live.log` (`app/web/api.py:196`), DEBUG, ротация 30 МБ, хранить 2 файла | нет | ограничен (~90 МБ) | вручную |
| Лог монитора | `data/monitor/logs/pipeline_*.log/.jsonl`, `events_*.jsonl` (`monitor/log_sink.py:46-61`), DEBUG, 50 МБ, 7 дней | project_id в `emit_event` | текст + jsonl DEBUG × 7 дней, без суммарного лимита; файл событий открывается один раз при `init` (`log_sink.py:68-73`) и не меняется при смене суток | вручную |
| Лог бэкенда (stdout) | `data/backend.log` и `data/backend-<PID>.log` (`scripts/run-backend.ps1:24-26`, `Add-Content`) | нет | **вечный рост, ничего не удаляет** | вручную |
| Telegram | отправка из 15+ мест (`telegram/bot.py:6875 notify_step_done`, `main.py:393-417`, `worker.py:71`, `services/hitl.py:122`, `orchestrator/steps/*`) | нет | не журналируется | чат Telegram |
| Create-задачи Studio (Outsee/Kie вручную) | словарь в памяти `_JOBS` (`services/create_jobs.py:90`) + sidecar json рядом с файлом | job_id, history_id | теряется при перезапуске | вкладка Create |

## 2. Пробелы: платные вызовы и мутации состояния без строки журнала

### 2.1 Платные вызовы без записи расхода

| # | Место | Что происходит | Проблема |
|---|---|---|---|
| P1 | `app/web/routers/outsee_create.py:543-562` (`/enhance-prompt`) | прямой POST на `{kie}/api/v1/chat/completions`, платный LLM Kie | ни `spend_ledger`, ни `tracker.db`, ни события. Единственный след: `logger.info` и только при успехе |
| P2 | `app/web/routers/outsee_http.py:~131` (`/generate`, видео) вызывает `oh.generate_video(...)` напрямую | в оркестраторе (`outsee_retry.py:1406`) вызов обёрнут `media_spend.outsee_tracked`, здесь нет | платное видео Outsee из вкладки Create не попадает в `spend_ledger` (image-ветка идёт через `generate_image_with_retries`, она обёрнута) |
| P3 | `app/bots/outsee.py:1996` `await oh.generate_video(**kwargs)` | то же: прямой вызов без `outsee_tracked` | зависит от того, кто вызывает бота; обёртки на самом шве нет |
| P4 | `media_spend.py:155` `_enabled`: под `PYTEST_CURRENT_TEST` запись выключена, а также при отсутствии ключа или ключе `synthetic*` | сознательное решение, но тихое | если ключ прочитан неверно, расход молча не пишется |
| P5 | Текстовые LLM | покрыты низкоуровневыми POST с `@observe_model` (`gpt_api.py:1927`, `:2252`, `:2293`) и пишутся в tracker через `_track_api_cost` (`observation.py:1013`). Вне наблюдаемого контекста `project_id` берётся из contextvar `_cost_project` и может быть `None` | строка расхода без проекта; проверить при подсчёте «траты по проекту» |

Оценка охвата: из ~35 файлов, где есть httpx или POST к внешним сервисам, расход подключён в `kie_http.py`, `elevenlabs_api.py`, `sfx_gen.py`, `outsee_retry.py`, `gpt_api.py`, `observation.py` (`observe_model`), решениях и планировщике. Непокрытыми остаются P1-P3 выше.

### 2.2 Мутации состояния без журнала

Хук наблюдателя `_stage_node_changes` (`observation.py:1421`) пишет только: изменения `NodeRun` и 3 флага `Project.meta` (`user_stop`, `mass_lane_user_stop`, `auto_await_manual_start`), и **только если есть активный scope** (`if observer is None or not observer.scopes: return`, `:1424`). Слой действий пишет в `action_layer_history` только свои шаги (`executor.py:96-366`, также `approval.py`, `service.py`, `planner.py`, `change_control/service.py`, `media_unknown.py`, `db_browser.py`: 1 вызов на 22 мутирующих эндпоинта).

Мутирующие эндпоинты (POST/PUT/PATCH/DELETE) без записи в журнал (подсчёт декораторов и вхождений `append_history|record_fact|ActionRecord` по роутерам, оценка):

| Роутер | Мутаций | Журнальных вызовов | Примеры без следа |
|---|---|---|---|
| `project_ops.py` | 58 | 0 | `/stop` :87, `/steps/{code}/reset` :293, `excel-hero` DELETE :421, `xlsx/upload` :481, `montage-board/frames/{id}` DELETE :901, `montage-board/apply` :1244, `delete-image/video/ref` :1515-1645, `assets/hero/replace` :1909, `storage/.../files` DELETE :2530 |
| `projects.py` | 6 | 0 | создание :245, `DELETE /{id}` :434, `PATCH` :460, `steps/{code}/run` :654 |
| `db_browser.py` | 22 | 1 | прямая правка БД |
| `prompt_studio.py` | 20 | 0 | правка шаблонов и пресетов |
| `fleet.py` | 14 | 0 | |
| `gpt_workspace.py` | 10 | 0 | |
| `workflows.py`, `node_groups.py`, `library.py`, `prompt_files.py`, `config_presets.py`, `gen_assistant.py`, `sidebar_layout.py` и др. | 6+5+6+6+4+7+7 | 0 | |

Итог: удаление проекта, замена героя, сброс шага, правка раскадровки, загрузка файлов из UI оставляют только строки loguru (без project_id в формате) и файл `status.log` для смены статуса ноды.

Кто делал: `require_web_user` возвращает токен, а не имя (`app/web/auth_sessions.py:44-50`), `actor` при мутациях вне слоя действий не определён. При выключенной авторизации (`settings.web_auth_enabled`) оператор неизвестен вообще.

## 3. Проглоченные ошибки

Подсчёт: 124 блока `except ...:` с телом `pass` в `app/` (оценка по grep, включая безобидные `OSError` при удалении файлов). Критичные для записи:

| Место | Строки | Проблема |
|---|---|---|
| `media_spend._write` | `app/services/media_spend.py:~198` | `except Exception: pass`: потеряна строка расхода, ни лога, ни события. Деньги потрачены, а в журнале пусто |
| `media_spend._credit_stop` | `:~210` | то же для записи 402 и включения рубильника по нехватке кредитов |
| `media_spend._write_task` | `:~222` | то же для `provider_task_ledger`: теряется task_id платной задачи, это мешает последующему «возобновить, а не отправлять заново» |
| `media_spend._spawn` | `:~114` | при ошибке планирования `coro.close()` без следа |
| `api_tracker_hook._write` | `app/services/api_tracker_hook.py:346-347` | вся запись в tracker.db и облако под `except Exception: pass`; поток `daemon=True`, при остановке процесса очередь теряется |
| `api_tracker_hook._push_to_supabase` | `:225-226` | возвращает False молча (строка получает `synced=0`, это нормально), но причина не пишется |
| `monitor/log_sink` | 3 `pass` | сбой монитора невидим, допустимо |
| `api_tracker_hook` | ещё 8 блоков `pass` (кэш, killswitch) | `check_api_allowed` при ошибке разрешает вызов (`:453`), это отдельное решение: аварийный рубильник облака fail-open |

Частично корректное поведение: `observation.emit` и `record_fact` при ошибке вызывают `mark_gap` (`observation.py:859`, `:~893`), то есть пробел журнала виден. В `media_spend` и tracker такой механизм не используется. Рекомендация: вместо `pass` вызывать `logger.exception` и записывать «дыру» в `observation_degradations` либо в отдельную строку spend_ledger kind=`event`.

Сквозные `logger.warning("... {}", e)` без `exc_info` есть в обработчиках запуска (`app/web/api.py:~190-230`), это допустимо, но часть ошибок живёт только в потоке loguru.

## 4. События без читаемого русского текста

Метки событий на русском заданы в одном месте: `web/src/lib/observation-view.ts:30-58` (`eventLabel`, ~25 видов). Подсчёт по `record_fact/emit/_queue_event/stage` в `app/` даёт 49 видов событий. **Без русской метки**, то есть в панели показывается сырое имя вида (`return labels[event.kind] ?? event.kind`, `:57`), оценка ~29 видов: `media_request_reserved`, `media_request_input`, `media_response`, `media_wait`, `media_phase`, `media_download`, `media_failure`, `media_policy_denied`, `media_auxiliary_denied`, `media_node_verified`, `model_retrieve_start/end`, `provider_fallback`, `watchdog`, `reconcile`, `reload`, `maintenance`, `action_blocked`, `human_approval_pending`, `local_recovery_guard`, `managed_successor_denied`, `recovery_continuation_deferred/submitted`, `assemble_retry`, `artifact_verified`, `profile_effective_context`, `decision`, `diagnosis`, `consumer`. Часть медиа-событий показывает отдельный компонент `MediaDetails` (`observation-section.tsx:31-44`), поэтому для них пробел менее острый.

Другие журналы:
- `action_layer_history.event` (`planner.py`, `executor.py`: `plan_started`, `step_succeeded`, `budget_block`...) отдаётся как код; русские сообщения собраны в `action_layer/messages_ru.py` (44 строки, один модуль), для истории общего преобразования «код → фраза» нет, `history-list.tsx` решает сам.
- `spend_ledger.kind/source` имеют русские подписи только в `telemetry.py:117-127` и `_source_ru` (`spend_guard.py`), построчно журнал не выводится.
- `status.log` и `errors.log`: смесь русского и английского, формат с табуляциями, не для оператора.
- Заголовки инцидентов (`diagnosis.py` `TITLES`) на русском: образец, к которому надо привести остальное.

## 5. Один факт, записанный несовместимо

| Факт | Способы записи | Расхождение |
|---|---|---|
| Деньги за вызов | (а) `spend_ledger` USD, (б) `action_layer_spend` кредиты, (в) `tracker.db api_calls.cost_usd`, (г) оценка в `action_layer_plans.cost_low/high`, `action_layer_steps.cost_*`, `action_layer_history.cost_*`, (д) `quote` в `create_jobs` (в памяти) | разные единицы (кредиты и USD), разные ключи (`plan:<id>`, execution_id, пусто, project_source); план: `budget._spend_reserve` пишет в оба (а) и (б) (`budget.py:85-110`), нативные медиа пишут `run_id=""` (`media_spend.py:189`), поэтому не привязаны к запуску или плану; (в) и (а) не связаны ключом, сверка суммы невозможна |
| Смена статуса ноды | `node_runs` + `logs/status.log` + `observation_events.node_change` (внутри scope) + `emit_event` монитора | четыре независимых записи; без scope остаётся только первая и текстовый файл |
| Действие оператора | `action_v1_records` (execution) и `action_layer_history` (plan) и `observation_events(observer_control)` | три журнала действий; `action_layer_history` не содержит execution_id и project_id, `action_v1_records` не содержит plan_id |
| Ошибка бота | `logs/errors.log` (текст) + loguru + `observation_incidents` | без общего id; строка файла не привязана к проекту |
| Рубильник | `supervisor_kill_switch_history` + `ActionRecord` каждого запуска + `action_layer_history.kill_switch_block` | сознательно, но связь только по времени |
| Telegram-уведомление | нигде не фиксируется | нельзя ответить на вопрос «оператору сообщили?» |

## 6. Рост и хранение (диск уже был забит на 100%)

Таблицы без очистки и без архива: `observation_events`, `observation_outbox`, `observation_incidents`, `decision_turns` (JSON-реплики), `action_layer_history` (в `params` и `request_text` до 2000 символов), `action_layer_plans.plan_json`, `spend_ledger`, `provider_task_ledger`, `tracker.db`. Очистка есть только у: тел наблюдения (`observation.py:447`), вложений слоя действий (`attachments.py:386`). Триггеры append-only на `spend_ledger`, `provider_task_ledger`, `action_layer_history`, `action_layer_spend` **запрещают** DELETE, то есть стандартную очистку нельзя сделать без пересоздания триггера: нужен задуманный способ архивации (экспорт и пересоздание таблицы), иначе рост будет неограниченным. Скорость роста мала, по данным окружения `data/state.db` 4.9 МБ (`du`), `tracker.db` 100 КБ. Главная угроза не таблицы, а файлы.

Файлы:

| Файл | Ротация | Риск |
|---|---|---|
| `data/backend.log`, `data/backend-<PID>.log` (`run-backend.ps1:24-52`, `Add-Content`) | нет, ни удаления старых, ни лимита; новый файл на каждый PID | **главный подозреваемый** в переполнении диска при DEBUG-выводе loguru на stderr |
| `logs/status.log` | нет | 1.5 МБ уже, растёт на каждую смену статуса |
| `logs/errors.log` | нет | малый |
| `data/monitor/logs/pipeline_*.log + *.jsonl` | 50 МБ и 7 дней, DEBUG, два дублирующих приёмника | при запуске монитора до 2 × N × 50 МБ в неделю без общего лимита; `enqueue=True` |
| `data/monitor/events/events_<дата>.jsonl` | нет, нет удаления, дата фиксируется при init | в долгоживущем процессе все события за много суток в одном файле |
| `data/studio-live.log` | 30 МБ, 2 файла | ограничено, хорошо |
| `data/observations/bodies` | квота 32 МБ на проект, 7 дней для закрытых | ограничено, хорошо |
| Каталог Create/generation, артефакты | отдельная тема | не журнал |

Мониторинга свободного места в коде нет (`grep disk_usage|free_space|shutil.disk` по `app/`, `scripts/` пуст). При ENOSPC запись `Add-Content` в скрипте проглатывается (`try {...} catch { }`, `run-backend.ps1:33-34`), SQLite падает с ошибкой, наблюдатель получит `mark_gap` только если БД ещё пишется.

## 7. Секреты в журналах

Что сделано хорошо:
- `redact()` (`observation.py:112-134`): по имени поля (`api_key`, `token`, `authorization`, `cookie`, `password`, `secret`, суффиксы `_api_key` и т.д.) и по тексту (`Bearer ...`, `key=...`, `sk-...`, `vk-...`). Применяется при постановке события (`:309`, `:445`), в дайджесте (`:138`), в `_track_api_cost` (`:1065`), в трекере (`api_tracker_hook.py:188-194`, fail-closed для строк), в панельной проекции `panel_safe` (`:1539`), в `db_browser` (`:2432`). Тесты: `tests/observe_v1/test_redaction_trackers.py`, `test_vibecode_redaction.py`, `test_journal.py`.
- В вызовах `logger.*` по приложению не найдено форматирования заголовков, ключей или токенов (grep по `headers|api_key|token|payload|body` в аргументах логгера: совпадений с секретами нет).

Пробелы:
1. **Слой действий не редактирует**: `append_history` (`store.py:190-207`) пишет `request_text`, `params`, `error` как есть; планы (`plan_json`, `request_text`) тоже. Если оператор вставит ключ в чат или параметр содержит токен/URL с подписью, он окажется в БД и в ответе `GET /history`. В `action_layer/*.py` вхождений `redact` нет.
2. `spend_ledger.note` и `provider_task_ledger.note` (`media_spend.py`) формируются из текста ошибок и обрезаются до 200 символов без `redact`. Практически только текст исключений провайдера.
3. `logs/errors.log`, `logs/status.log`: текст ошибок ботов без редактирования (`bots/outsee.py:81-87`).
4. **Нет фильтра на приёмниках loguru** (`monitor/log_sink.py:46`, `web/api.py:196`): исключения с URL (подписанные ссылки, токены в query) и `exc_info=True` попадут в файл в сыром виде. Регулярка `_CREDENTIAL_TEXT` не знает форматов: токен Telegram-бота (`\d+:[A-Za-z0-9_-]{30,}`), ключ Supabase `sb_publishable_`/`sb_secret_`, `AIza...`, `xi-api-key`, ключи Kie/Outsee неизвестного вида (они ловятся только если стоят после `key=`/`Bearer`). Оценка риска: средняя; утечка возможна в исключениях httpx/aiogram.
5. **Облако по умолчанию включено**: `api_tracker_cloud_enabled=True` (`settings.py:377`); каждая строка вызова API (модель, токены, стоимость, `project_source`=id проекта, имя пользователя ОС и имя устройства из `_get_identity`) отправляется на Supabase (`api_tracker_hook.py:206-226`). URL и ключ `sb_publishable_...` зашиты в исходники (`api_tracker_hook.py:210-211`, `:368-369`, `api-tracker/app/config.py:13-14`). Публикуемый (anon) ключ не секрет по замыслу Supabase, но: (а) данные о работе клиента уходят на чужой проект без явного согласия, (б) защита зависит от RLS на стороне Supabase, которую здесь проверить нельзя. Решение за заказчиком, в аудите это пометка «не по умолчанию».
6. `panel_safe` скрывает ключи `text`, `prompt`, `input`, `messages` и т.д. при выдаче в панель, то есть оператору недоступны сами запросы, но в БД и в телах они лежат (локально, по замыслу).

## 8. Сквозная корреляция

Ключи есть, но не связаны между журналами:

| Журнал | project_id | execution_id (run) | plan_id | node_key | attempt_id | request_id |
|---|---|---|---|---|---|---|
| observation_events | есть | есть | нет | есть | есть | есть |
| observation_incidents | есть | есть | нет | есть | есть | через evidence |
| decision_records | есть | есть | нет (action_id) | есть | source_attempt | нет |
| action_v1_records | есть | есть | нет | есть | есть | нет |
| action_layer_history | **нет** (через plan→plan_context) | **нет** | есть | нет | нет | нет |
| spend_ledger | есть (может быть NULL) | как run_id (строка; для нативных медиа пусто) | как `plan:<id>` в run_id | нет | нет | нет |
| provider_task_ledger | есть | нет | нет | нет (`key`) | нет | нет |
| tracker.db api_calls | `project_source` строка | нет | нет | нет | нет | нет |
| loguru | нет (нет `logger.bind/contextualize`, grep пуст) | нет | нет | нет | нет | нет |
| HTTP | нет middleware с request-id | | | | | |

Следствие: цепочку «оператор написал в чат → план → шаг → запрос к Kie → task_id → деньги → файл» собрать нельзя без ручного сопоставления по времени. «Тикета» как сущности нет: ближайшие аналоги `incident_id`, `decision_id`, `proposal id`, `action_id`.

## 9. Экспорт и поиск для оператора

- Единой ленты нет. Есть: события одного запуска (`/events`, пагинация `after/limit`, `observations.py:641`), диагнозы, решения, действия, история слоя действий (`/history`, без фильтра по проекту), траты (только суммы, `/spend`, без построчного списка), задачи провайдеров, телеметрия (агрегаты).
- Поиска по тексту, фильтра по уровню/источнику, экспорта (CSV/JSONL/архив для передачи разработчику) нет ни в API, ни в UI (по `web/src/lib/*` и роутерам).
- `spend_ledger` и `provider_task_ledger` читаются только агрегатами: оператор не видит «за что списали»; `api-tracker` показывает строки вызовов, но без привязки к шагам Studio.
- Журнал Telegram-уведомлений отсутствует.

## 10. Предложение: единая лента проекта («журнал регистратора»)

Принцип: не новая подсистема трассировки и не дублирование записи. Читающее представление поверх существующих таблиц плюс одна небольшая таблица для того, что сейчас не пишется вообще.

### 10.1 Общая форма строки ленты

`ts, project_id, run_id, plan_id, node_key, attempt_id, request_id, source, kind, severity, usd, credits, actor, text_ru, ref_table, ref_id`

Источники (SQL `UNION ALL` в представлении `v_project_timeline` либо построение в Python по курсору `(ts, source, id)`):

| source | Таблица | Что берётся |
|---|---|---|
| observe | `observation_events` | kind, node_key, attempt_id, request_id, payload (только панельно-безопасный) |
| incident | `observation_incidents` | `TITLES[type]`, severity, status |
| gap | `observation_degradations`, `observation_commit_uncertainty` | stage, error_type |
| decision | `decision_records`, `decision_turns` | status, proposal |
| action | `action_v1_records` | action_kind, initiator, rationale |
| plan | `action_layer_history` + соединение с `action_layer_plans/plan_context` (project_id) | event, actor, outcome, error, cost |
| approval | `action_layer_approvals` | кто и когда подтвердил |
| spend | `spend_ledger` | kind, provider, usd, credits, note, run_id |
| task | `provider_task_ledger` | event, status, task_id |
| killswitch | `supervisor_kill_switch_history` | включил и снял, причина |
| change | `change_proposals` | предложение, статус |
| api | `tracker.db api_calls` через `ATTACH` или отдельный запрос по `project_source` | модель, токены, usd, status_code, error |
| operator (новая) | `operator_audit` | ручные мутации (раздел 10.3) |
| notify (новая, опционально) | `operator_audit` с kind=`telegram` | что и кому отправлено |

### 10.2 Текст на русском

Один модуль `app/services/timeline_ru.py`: функция `text_ru(source, kind, payload) -> str`. Брать готовые тексты: `TITLES` (диагноз), `messages_ru.py` (слой действий), `observation-view.ts` (портировать `eventLabel` на сервер, чтобы Telegram, экспорт и UI говорили одно и то же), `telemetry.spend_source_label_ru`. Для неизвестного kind: «Событие <kind> (подробности в журнале)» плюс запись в отчёт о покрытии, чтобы тест на покрытие падал, когда в коде появляется новый вид события без фразы (тест: множество видов из `record_fact/emit` ⊆ ключи словаря).

### 10.3 Новая таблица только для незаписанного

`operator_audit` (append-only, по образцу `spend_ledger`): `id, ts, request_id, actor, method, path, project_id, status_code, summary_ru, detail_json(redacted)`. Заполняется middleware FastAPI для всех POST/PUT/PATCH/DELETE: project_id из пути, actor из сессии (имя, не токен), summary_ru из таблицы «маршрут → фраза» (дополняемой). Закрывает ~150 мутирующих эндпоинтов одним местом вместо правки каждого. Тот же middleware создаёт `request_id` и делает `logger.contextualize(request_id=..., project_id=...)`.

### 10.4 Сквозные ключи

- В loguru: формат со `{extra[request_id]}`/`{extra[project_id]}`, `contextualize` в middleware, в воркере и на входе execution.
- `spend_ledger`: заполнять `run_id` для нативных медиа (из contextvar текущего execution/plan); добавить `request_id` и `ref` (task_id или plan step) отдельной миграцией (новые колонки, триггеры append-only сохраняются).
- `action_layer_history`: добавить `project_id` и `execution_id` (nullable) при записи.
- `tracker.db.api_calls.metadata_json`: класть `run_id, request_id, plan_id` (колонку менять не надо, метаданные уже есть).
- `provider_task_ledger`: `run_id`.

### 10.5 API и UI

- `GET /api/projects/{id}/timeline?after=<cursor>&limit=&source=&severity=&run_id=&plan_id=&q=&since=` (курсор `ts|source|id`), `GET .../timeline/export?format=jsonl|csv|md` (редактирование через `redact` и `panel_safe`). SSE-версия по образцу `/stream`.
- Вкладка «Журнал» в Studio: одна лента с фильтрами (деньги, ошибки, действия оператора, запуск), подсветка ошибок, переход к исходной панели по `ref_table/ref_id`, кнопка «скачать для разработчика».
- Итоги сверху: потрачено сегодня/проект/запуск (из `spend_ledger`), число ошибок, открытые пробелы журнала, свободное место на диске.

## 11. План работ (по шагам, с приоритетом)

Приоритет A: деньги и диск (риск потерь).
1. A1. `media_spend`: заменить `except: pass` в `_write`, `_credit_stop`, `_write_task`, `_spawn` на `logger.exception` плюс запись события-пробела (`spend_ledger` kind=`event`, note=`write_failed`); тест на отказ БД.
2. A2. Обернуть P1-P3 (`outsee_create /enhance-prompt` Kie chat, `routers/outsee_http.py /generate` видео, `bots/outsee.py:1996`) в `media_spend`/`_track_api_cost`; тест «каждый платный шов даёт строку» (перебор модулей с httpx POST, список исключений явный).
3. A3. Ротация `run-backend.ps1` (лимит размера, удаление `backend-<PID>.log` старше N дней, максимум суммарно); перевести `status.log` и `errors.log` на loguru-приёмники с `rotation`/`retention` либо `RotatingFileHandler`; `events_*.jsonl` ротировать по суткам фактически; общий лимит для `data/monitor`.
4. A4. Проверка свободного места на старте и раз в N минут: порог в `settings`, событие «мало места» в журнал и Telegram, остановка новых платных запусков ниже критического порога.
5. A5. Политика хранения таблиц: сводка, архивация в файл (jsonl.gz) и пересоздание append-only таблиц без нарушения триггеров (отдельная команда `archive_journals --older-than`), для `observation_events/outbox`, `decision_turns`, `action_layer_history`, `tracker.db` (+VACUUM).

Приоритет B: секреты.
6. B1. Применить `redact` в `append_history`, при сохранении планов (`request_text`, `params`, `plan_json`), в `spend_ledger.note`, `provider_task_ledger.note`, в записях `errors.log`.
7. B2. Глобальный `filter` для всех приёмников loguru (редактирование строки и исключения). Расширить `_CREDENTIAL_TEXT`: токен Telegram-бота, `sb_*`, `AIza`, `xi-api-key`, query-параметры `token=`/`signature=`/`X-Amz-Signature`. Тесты на каждый формат.
8. B3. Облако трекера: по умолчанию выключено (`API_TRACKER_CLOUD_ENABLED=false`) либо явное согласие в UI; убрать зашитые URL и ключ (вынести в настройки), решить с заказчиком.

Приоритет C: корреляция и лента.
9. C1. Middleware: `request_id`, `actor`, `logger.contextualize`; таблица `operator_audit`; маршрутная таблица фраз.
10. C2. Колонки корреляции: `run_id` в нативных медиа-записях, `project_id`/`execution_id` в `action_layer_history`, метаданные tracker.
11. C3. `timeline_ru.py` + тест покрытия видов событий + перенос `eventLabel` (49 видов, ~29 без текста).
12. C4. `GET /timeline`, `/timeline/export`, SSE; вкладка «Журнал» в Studio.
13. C5. Построчный вывод `spend_ledger` и `provider_task_ledger` в Studio («за что списали»), сверка сумм: `spend_ledger` против `tracker.db` с флагом расхождения.

Приоритет D: прочее.
14. D1. Журнал Telegram: единая функция `notify()` вместо 15 разрозненных `send_message`, запись в `operator_audit` (kind=`telegram`), общая обработка ошибок отправки.
15. D2. Единый вид записи смены статуса ноды: `status.log` оставить как производный, источник правды `node_runs` + событие.
16. D3. Create-задачи (`_JOBS`): сохранять в БД (потеря при перезапуске).
17. D4. Документ `docs/RECORDING.md`: карта журналов (таблица раздела 1) для оператора и разработчика.

## 12. Что не проверялось

- Содержимое `.env` и реальные значения ключей: не читались по правилу.
- Политики RLS Supabase и содержимое облачной таблицы: недоступны без сети.
- Фактический размер `data/backend*.log` на рабочей машине заказчика: в этой копии `data/backend*.log` отсутствуют; вывод про рост сделан по коду `run-backend.ps1`.
- Полнота списка непокрытых платных швов по `app/bots/outsee.py` (очень большой файл) и по ботам браузера: проверены вызовы `oh.generate_*`, остальное оценочно.
- Тесты не запускались; утверждения о тестах основаны на чтении файлов.
