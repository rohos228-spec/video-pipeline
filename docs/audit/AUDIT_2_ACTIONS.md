# AUDIT 2: действия и кнопки (слой действий, HEAD 850b3f47)

Только чтение кода, тесты не запускались (кроме одного импорта реестра: `ensure_builtin_actions()` даёт 84 действия: read 18, local_safe 31, destructive 34, paid 1). Сеть, платные и LLM-вызовы не использовались, `.env` не читался. Пути относительно `C:\video-pipeline\video-pipeline-observe-v1`, если не указано иное.

## 0. Короткий вывод

1. Каталог (84 действия) полно покрывает панель монтажа, меню съёмки, группы нод и контур изменений. Для остальной Студии (около 60% контролов) действий нет: создание/удаление проекта, запуск любого шага кроме `excel_gpt`, HITL, Outsee/Kie-генерация, GPT-workspace, Baza, fleet, хранилище, герои/предметы, воркфлоу, kill switch.
2. «Каждое действие записано» выполняется только для вызовов через слой. Кнопки интерфейса идут напрямую в эндпоинты, общего аудита (middleware/журнал мутаций) нет. Командная строка слоя живёт только в панели оркестратора, а монтажная панель её не использует.
3. Стоимость в истории всегда оценка: ни одно действие не возвращает `credits_spent`, поэтому `cost_recorded` и `actual_credits` всегда `null`, а резерв остаётся навсегда (в том числе в локальном `spend_ledger`).
4. Исполнители платных и фоновых действий ни разу не запускались против настоящего сервера: тесты либо заглушка `FakeCtx`, либо только preview.
5. Планировщик: последний полный живой прогон текущей фикстуры (351 случай) не выполнялся. Цифры 96% относятся к устаревшей версии кода (260 запросов, 6 окт 12:29); текущий результат по случаям собран из 10 разных прогонов на разных ревизиях кода.

## 1. Сверка контролов Студии (перечислено из кода `web/src`, не только по старой инвентаризации)

Дифф `git diff 3178349c..HEAD -- web/src/components/canvas web/src/components/studio` пуст: контролов монтажа и групп с момента инвентаризации не добавлено. Новые контролы: `components/action-layer/*` (командная строка, карточка плана, вложения, история), `components/change-control/*`, `components/orchestrator/media-recovery-section.tsx`, `node-coverage-section.tsx`, `spend-section.tsx`, расширенный `kill-switch-section.tsx`. Всего в `web/src/components` 124 файла `.tsx`, около 605 `onClick`.

### 1.1 Покрыто действием (по коду, не только по docs/ACTION_LAYER.md)

| Область | Контролы | Действия |
|---|---|---|
| Панель монтажа M01-M54 | все с бэкендом | `montage.*` (см. docs/ACTION_LAYER.md, раздел «Покрытие кнопок») |
| Меню съёмки S01-S03 | | `shot.*` |
| Группы G01-G04, G07 | | `group.*` |
| G05/G06 запуск шага | только нода `excel_gpt` | `node.run_step` (`builtin_group.py:32,109`: `_NODE_RUN_STEP = "excel_gpt"`) |
| G08/G09/G10/G13/G16 | пульт, загрузки, мастер-промты, отключение, пауза/стоп/автопродвижение | `node.*`, `prompt.*`, `project.*` |
| Контур изменений | `components/change-control/*` | `change.*` |
| Восстановление медиа | `media-recovery-section.tsx` | `media.*` |

### 1.2 Контролы без действия (и почему)

| # | Контрол (файл) | Эндпоинт | Причина отсутствия | Важность |
|---|---|---|---|---|
| 1 | Запуск ▶ любой ноды кроме `excel_gpt`: script, img_pr, images, video, audio (TTS), music, sfx, assemble, publish (`studio-workspace.tsx:455`, `node-studio.tsx:384`, `outsee-create-workspace.tsx:1078`) | `POST /projects/{id}/steps/{step}/run` | `node.run_step` отсекает всё кроме `excel_gpt`; для остальных шагов нет оценки стоимости. `project.resume`/`continue_pipeline` нельзя подтвердить (оценка неизвестна, `docs/ACTION_LAYER.md:448`) | Критично: платные шаги конвейера недоступны слою |
| 2 | HITL: принять/отклонить (`hitl/hitl-banner.tsx`, `studio/asset-tray.tsx`, `media-frame-gallery.tsx` `patchFrame`) | `/hitl/{id}/decision`, `PATCH /projects/{id}/frames/{fid}` | не заведено | Высокая: без него автономный конвейер упирается в человека |
| 3 | Создание/удаление проекта, дочерний проект, папки, генерация-очередь (`sidebar/project-sidebar.tsx`, `new-project-wizard.tsx`, `gen-queue-dialog.tsx`) | `POST/DELETE /projects`, `/sidebar-layout/*` | не заведено; удаление проекта планировщик намеренно считает «вне каталога» | Высокая |
| 4 | Настройки проекта: модели, разрешение, тема, пресеты (`inspector/*`, `generation-models-panel.tsx`, `topic-editor.tsx`) | `PATCH /projects/{id}` | есть только `project.set_auto_mode` и `montage.set_assemble_params` | Высокая: платные `apply_queue` и `improve_scene` требуют заявку модели, совпадающую с настройками проекта (`builtin_montage_more.py:235-257`), а менять настройки слой не умеет |
| 5 | Массовая фабрика (`mass-factory-panel.tsx`: `startMassLanes`, парсинг тем) | `/mass-lanes/start` | не заведено | Высокая, платное |
| 6 | Outsee/Kie create-workspace (55 контролов), gen-assistant (30) | `/outsee/generate`, `/kie-create/generate`, `/gen-assistant/*` | не заведено | Средняя, платное, автономная генерация вне проекта |
| 7 | GPT-workspace (`gpt/gpt-workspace.tsx`: ask, save-to-project, save-voiceover), meta-agent compile/save, gpt-verdict run (`gpt-verdict-panel.tsx`) | `/gpt-workspace/*`, `/meta-agent/*`, `/prompt-studio/.../gpt-verdict/run` | не заведено, платный LLM | Средняя |
| 8 | Baza (`baza/baza-workspace.tsx`, 28 контролов): правка кадров, текстов, промтов, сущностей, рёбер, ячеек листа, экспорт xlsx, harness verify | `/db/*` | не заведено (есть частично через `shot.*`) | Средняя |
| 9 | Хранилище ноды, герои/предметы, excel-feed (`storage-panel.tsx`, `hero-config-panel.tsx`, `items-config-panel.tsx`, `excel-feed-panel.tsx`), загрузка/замена xlsx проекта | `/storage/*`, `/excel-hero*`, `/assets/hero/replace`, `/xlsx/upload` | не заведено; есть только `project.reload_xlsx` | Средняя |
| 10 | Воркфлоу и холст: сохранить/дублировать/сбросить, правка рёбер, remap-keys, G12 «открепить», G14 «удалить ноду» (`flow-canvas.tsx`) | `/workflows/*`, `/canvas-edges/*`, `PATCH meta.canvas_graph` | сознательно не заведено (docs/ACTION_LAYER.md, «Не вошло в каталог»); причина верна (переписывание всего графа, гонка с открытым холстом) | Средняя |
| 11 | Аварийный стоп включить/снять (`orchestrator/kill-switch-section.tsx`) | `/observations/kill-switch` | слой только читает флаг. Это разумно (человеческий рубильник), но надо зафиксировать решением | Низкая |
| 12 | Панель оркестратора: наблюдение, управляемый запуск (`managed-launch-section`, `observation-section`), решения, профили (`decision-section`, `profiles-section`), чат оркестратора с подтверждением удаления/git push | `/observations/*`, `/orchestrator/chat` | параллельный слой Actions-v1 со своим журналом, не связан с `action_layer_history` | Средняя: два журнала |
| 13 | Fleet: pull/push/sync (`fleet/*`, `fleet-api.ts`) и `send_to_main_pc` запуск | `/fleet/*` | есть только флаг `montage.set_send_to_main_pc` | Низкая |
| 14 | Глобальный выбор текстовой LLM (`shell/text-llm-picker.tsx`, `/api/text-llm`), баг-репорт, промты шагов (блоки, веса, пресеты: `step-blocks-editor`, `blocks-weight-panel`, `/prompt-studio/*`), библиотека (`/library/*`) | | не заведено | Низкая/средняя |
| 15 | Публикация | | в UI и в роутерах публикации нет вообще: `publish` только метка шага в `node_registry.py:122` и `default_graph.py:41`. Эндпоинта «опубликовать на площадки» в `app/web/routers` нет. Действие создавать не к чему, пока нет функции | см. AUDIT_4_PUBLISH.md |
| 16 | Локальные действия интерфейса (выбор, лайтбокс, свернуть) | | не нужны | нет |

Эндпоинты монтажа без кнопки UI: `swap-shots` (заведён как `montage.swap_shots`), `scene-generate-with-images` (`api.ts:1320`, в компонентах не вызывается, действия нет), `finish/images|videos|animation-prompts` (`api.ts:2047-2069`, вызовов в компонентах нет).

### 1.3 Контексты UI не подключены

`orchestrator-panel.tsx:582`: `<ActionLayerSection projectId={projectId} />`. Параметры `sceneId`/`frameId` (`action-layer-section.tsx:14`) нигде не передаются, поэтому фразы «улучшь эту сцену», «удали этот кадр» не получают выбранную сцену и кадр из монтажной панели. Контекст `selected_scene/selected_frame` в планировщике работает только в тестах и eval.

## 2. Проверка каждой группы действий

| Вопрос | Результат |
|---|---|
| Исполнители настоящие? | Да, все 84 вызывают существующие эндпоинты через `ctx.call` (ASGI в процессе). Заглушек `NotImplementedError/TODO` в `app/services/action_layer/` и движке монтажа нет (grep по `montage*.py`, `remount_video.py`). |
| Эндпоинт вызывался на живом сервере? | Нет ни разу. Реальные эндпоинты на временной БД (ASGI, без провайдеров) проверены для read и local_safe и структурных (`test_action_layer_montage_more_real.py`, `..._wave3_catalog.py`, `..._group_shot.py`, `..._montage.py`). |
| Только заглушка `FakeCtx` или preview | `montage.apply_queue`, `improve_scene`, `generate_scene`, `generate_scene_images`, `suggest_scene_variants`, `recover_outsee`, `run_montage`, `align_audio` (`test_action_layer_montage_more_exec_stub.py`; в `..._more_real.py` только preview, строки 57-141); `node.run_step` (`test_action_layer_group_nodes.py`, `FakeCtx`); `montage.link_ref`, `unlink_ref`, `delete_manual_ref` (только `..._more_catalog.py`); `media.resume_poll` (с подменой провайдера). Прогон `e2e_command_line_ui_*` идёт на фейковых `test.*` действиях (`tests/test_e2e_action_layer_http.py` шапка). |
| Тиры | Согласованы с реестром (правила регистрации `docs/ACTION_LAYER.md:30-34`, проверены импортом). Замечания: (а) `montage.set_coverage`, `set_voiceover`, `set_assemble_params`, `shot.edit_*` помечены `local_safe`, но перезаписывают текст без сохранения прежнего значения; в журнал попадают только новые params, откатить по журналу нельзя (`builtin_montage.py:95-104`); (б) `project.set_auto_mode` — `local_safe`, хотя после ручного ▶ воркер сам идёт по платным шагам (в описании сказано); (в) `montage.recover_outsee` — `destructive` без стоимости, но реально платных генераций нет (верно); (г) `change.propose` — `local_safe`: проверить, вызывает ли LLM (не проверено). |
| Запись в историю (кто/что/цена) | Кто: `actor` = `web_operator:<sha256 токена[:16]>` или `local_web_operator` (`approval.py:29-37`). При выключенной авторизации все операторы одинаковы. Что: action_id, params, исход (`executor.py:258-305`). Цена: только оценка, факт всегда `null` (п. 3.1). |
| Фраза планировщика | См. раздел 4. Синонимы есть у всех 84 (по docs), в фикстуре три действия (`link_ref`, `unlink_ref`, `delete_manual_ref`) без случая «план», восемь с единственным случаем: `group.save_from_selection`, `montage.upload_image|music|ref|video|voice`, `node.upload_check_agent`, `node.upload_file`. |

## 3. Подтверждение, хэш, бюджет: реальные дыры

### 3.1 Факт трат не фиксируется, резерв не освобождается (средняя-высокая)
- `executor.py:330,337` читает `credits_spent`, но `grep credits_spent app/services/action_layer/*.py` находит только это чтение, определение поля `spec.py:71` и docs. Ни одно действие и ни один `poll` не возвращают сумму.
- Следствия: `cost_recorded`, `cost_actual`, `actual_credits` всегда пусты; `budget.settle` (`budget.py:97-111`) выходит сразу; резерв по плану остаётся.
- Резерв пишется и в общий `spend_ledger` (`budget.py:85-94`, `source="action_layer"`, `track=False`) и не закрывается. Потолки: проект 10 USD «за всё время», сутки 25 USD (`app/settings.py:393-395`, `docs/SPEND_LIMITS.md`). 1 кредит 0.005 USD, значит около 2000 кредитов неснятых резервов блокируют платные пути проекта навсегда, остальные проекты в течение суток. Это самоблокировка на доступность, а не утечка денег.
- Резервы слоя и реальные списания провайдерских путей (media_spend/supervisor) считаются в одном ledger независимо: двойной учёт одной генерации (консервативно). Подтверждено по чтению; пересечение путей не измерено.

### 3.2 Таймаут и сбой опроса не останавливают платную фоновую задачу (средняя-высокая)
- `executor.py:274-277`: при `asyncio.TimeoutError` (900 с по умолчанию, `settings.py:411`) шаг закрывается `unknown`, но `_call_stop` не вызывается. То же при исключении в `poll` (`executor.py:288-291`, `ctx.call` 5xx).
- Серверная задача `apply` (платная генерация Outsee/Kie) продолжается без наблюдения, пока план уже `failed`; `project_busy` смотрит только статус плана (`service.py:133-139`), поэтому на проекте можно запустить второй план. Пример: большая очередь видео легко идёт дольше 15 минут.
- Рекомендация: при timeout и исключении `poll` вызывать `spec.stop` либо переводить шаг в «ждём завершения» и держать проект занятым.

### 3.3 Гонка записи очереди с интерфейсом (средняя)
`_queue_ops`, `_cancel_queue_ops`, `_set_video_trim`, `_queue_and_apply` (`builtin_montage.py:112-124`, `builtin_montage_more.py:321-334,389-412,626-642`) делают read-modify-write всей `pending_ops` (GET доски, слияние, POST /queue). Интерфейс сохраняет всю очередь с debounce 400 мс. Серверная защита `should_accept_queue_save` отвергает только более короткий список во время apply (из инвентаризации §5.4). Параллельная правка человека и слоя перезапишет чужие операции без ошибки. Нужен либо общий замок/версия очереди, либо точечный `add/remove` эндпоинт.

### 3.4 `project_busy` и approve (низкая-средняя)
- `service.py:133-139`: проверка «другой план идёт» и перевод своего плана в `running` идут в разных транзакциях, окно гонки для двух планов одного проекта; второй от дублирования спасают предусловия шага (`no_apply_running`) и атомарный захват шага (`executor.py:250-257`), но не гарантируют для всех действий.
- Одноразовое подтверждение расходуется атомарно (`approval.py:89-95`), одновременный запуск одного платного плана безопасен. Для плана без подтверждения два параллельных `run` не создают двух задач (`executor.launch` проверяет `is_live`).

### 3.5 Подтверждение процедурное, не криптографическое (информация)
`approve` и `run` доступны любому, кто может вызвать эндпоинт: без авторизации любой локальный процесс получает `local_web_operator` (`approval.py:34-37`, `LocalOnlyDep` ограничивает только не-localhost). ИИ-агент с HTTP-доступом может сам вызвать `approve` и `run`. Отдельного канала подтверждения человеком (код в интерфейсе, второй фактор) нет. Включение `WEB_AUTH` улучшает атрибуцию, но не разделяет «план составил агент» и «подтвердил человек»: оператор подтверждения не сверяется с автором плана (`create_approval` проверяет только запрещённые префиксы).

### 3.6 Файлы загрузки не привязаны к хэшу (низкая)
В план попадает `source_path = attachments/...` без sha256 (`planner_uploads.py`), файл читается при запуске (`context.py:201-207`). Таблица вложений хранит `sha256`, но при `run` она не сверяется. Подмена файла между подтверждением и запуском возможна для любого, кто пишет в `DATA_DIR`.

### 3.7 Идентификаторы идемпотентности не используются сервером (низкая)
Заголовок `Idempotency-Key` ставится в `ctx.call` (`context.py:294-296`), но во всём `app/` его никто не читает (grep). Заголовок оператора `X-Action-Layer-Operator` читает единственный эндпоинт, `media_recovery.py:69`, и значение берётся из заголовка без проверки. Следовательно: (а) повтор шага после рестарта защищён только флагом `idempotent` и статусом `unknown`; (б) события проекта (`project_updated`) и логи эндпоинтов не знают, что действие пришло от слоя и от кого.

### 3.8 Оценка стоимости
- Платные действия на Outsee-генераторах без тарифа в репозитории возвращают «неизвестно» и блокируют подтверждение (`_kie_unit`, `builtin_montage_more.py:185-186`, тест `test_unpriced_outsee_model_blocks_approval_fail_closed`). Проект на Outsee-пути (в инвентаризации картинки и видео идут через Outsee) нельзя применить через слой. Нужен тариф Outsee или явный лимит операций.
- Оценки LLM (`LLM_SCENE_CALL_HIGH`, 20 000/4 000 токенов), число платных попыток (3 для картинки, 4 для видео) и `_LLM_USD_*` допущены, не замерены (`builtin_montage_more.py:32-50`). Реальные вызовы групп `script_frames_qc` идут через Vibecode, а оценка считается по ставкам Kie (`llm_override`).
- Лимит `max_image_ops/max_video_ops` защищает от разрастания очереди, но не от смены типа операции между превью и запуском: `_apply_queue` проверяет число (`builtin_montage_more.py:343-351,362-363`), а не состав очереди.

### 3.9 Мутации вне слоя
Любая кнопка интерфейса и любой скрипт меняют состояние напрямую. Общего журнала мутаций нет: нет middleware (`app/web/api.py` содержит только CORS), нет журнала «кто нажал». История слоя `action_layer_history` содержит только вызовы слоя. Для требования заказчика «любая кнопка записана» необходимо одно из двух: (а) прогнать все кнопки через слой (интерфейс вызывает `preview/run`), (б) сделать журнал мутирующих HTTP-вызовов на уровне приложения (метод, путь, оператор, проект, тело-дайджест, исход) и привязать к нему `actor`.

### 3.10 Прочее
- Kill switch блокирует в том числе read и `project.stop` в плане (`executor.py:199-205`): план остановки при включённом рубильнике не выполнится. Допустимо, но неочевидно.
- В режиме Actions-v1 `/steps/{step}/run` отвечает 409 (`projects.py:~680`), `node.run_step` имеет проверку (тест `test_run_step_refused_when_managed`), а `project.pause/resume/continue` отказывают так же. Пути пока двойные: слой и панель оркестратора.
- `project.resume` / `continue_pipeline` с неизвестной ценой по построению не подтверждаются (`docs/ACTION_LAYER.md:448`): кнопки ▶ работают только в интерфейсе.

## 4. Качество планировщика

Источник: `C:\video-pipeline\_evalout\eval1.json ... eval13.json`, фикстура `tests/fixtures/action_planner_eval.json`.

- Фикстура: 351 случай (278 plan, 49 clarify, 24 refuse), 3 многошаговых, 23 с контекстом выбора. Медиана 3 случая на действие. Случаев на макросы нет (макросы идут мимо планировщика).
- Динамика полной выборки: eval1 (94 случая) accuracy 0.298; eval2 0.574; eval3 (253) 0.893; eval4 (260 запросов) 0.958 (`plan_ids_exact` 0.96, `params_ok` 0.945, 12:29 6 окт). После этого сделаны правки `planner.py` (19:48), `planner_rules.py` (02:57 7 окт), `planner_catalog.py` (02:43), `builtin_wave3.py`, `macros.py`, правка фикстуры (02:55). Повторного полного прогона не было.
- Актуальное состояние по случаям (последний прогон каждого id): eval4 даёт 150, eval7 110, eval9 50, остальное 41 случай из eval5,6,8,10-13. То есть результат собран из прогонов разных ревизий, а не измерение текущего кода.
- Падают в последних прогонах 11 случаев: `im_b`, `gs_c` (лишний `generate_scene_images`), `mu_b` и `queue_ai` (`transport_error`, не качество модели), `link_ref`, `chg_back`, `swap_slots`, `mr_h` (уточнение вместо плана), `cost_1`, `gin_c`, `mr_a` (лишний `get_recovery_assessment`). Часть уже могла быть исправлена после прогона: подтвердить нельзя.
- Измерялось на сидированном проекте (`scripts/eval_seed.py`, 30 кадров, фейковые вложения). Реальных проектов и реальных фраз заказчика в наборе нет. Правки правил делались под падающие случаи этой же фикстуры (рост 30% до 96%): оценка завышена за счёт подгонки, отложенной выборки нет.
- Не измерено вовсе:
  1. Фразы по действиям, которых нет в фикстуре как «план»: `montage.link_ref`, `unlink_ref`, `delete_manual_ref`, из-за отсутствия id рефов в контексте их планировщик не может построить.
  2. Одиночный случай на действие: 8 действий загрузок и `group.save_from_selection`.
  3. Многошаговые фразы («разбей сцену 3, улучши её и собери»): всего 3 случая; макросы `smart.*` не пробуются через текст.
  4. Контекст «этот кадр/эта сцена» из интерфейса: в UI не подключён (п. 1.3).
  5. Устойчивость каталога из 24 действий на 84: проверена офлайн (`test_action_planner_eval_offline.py`), живой отбор на реальных запросах не измерен.
  6. Реальная стоимость и задержки планировщика на живом проекте с большим числом кадров (окно кадров >80 и размер контекста).
  7. Воспроизводимость: нет повторов одной и той же фразы, температура и модель в фикстуре не фиксируются; `transport_error` не разделён с ошибкой качества в summary.
  8. Синонимичные формулировки ярлыков кнопок самой Студии («Применить правки», «ИИзменение», «Галерея Outsee (CDP)»): в фикстуре формулировки авторские, не взятые с кнопок.

## 5. Движок монтажа: заглушки

Файлы `app/services/montage*.py`, `remount_video.py`, `audio_align_*.py`, `app/web/routers/project_ops.py`: заглушек и `NotImplementedError` нет, упоминания «stub» относятся к бизнес-логике (заглушки каталога действий кадров: `montage_coverage_ops.py:617-661`; порог валидации Outsee: `montage_board_apply.py:62`).

| Функция | Действие | Реальный исполнитель | Замечание |
|---|---|---|---|
| assemble / монтаж | `montage.run_montage` | `POST /montage-board/montage` → `remount_video` | destructive верно (стирает final.mp4). Исполнитель проверен только с заглушкой. Результат `final_video` в `poll` приходит, путь проверки файла нет |
| align audio | `montage.align_audio` | `POST /projects/{id}/audio-align` | `run_assemble` зашит `False` (`builtin_montage_more.py:693`); «Разобрать и сразу собрать» делается макросом. Результат разбора возвращается как `result`, но разбор не сверяется с покадровыми таймкодами |
| regenerate | `montage.queue_ops` + `apply_queue` | `/queue`, `/apply` | настоящие. Видео-only очередь доступна слою (UI-кнопка её не включает). Не проверено на сервере |
| scene edits | `generate_scene`, `improve_scene`, `generate_scene_images`, `set_scene_chain`, `set_anchors`, `set_voiceover_span`, структурные | эндпоинты `project_ops.py` | `improve_scene` после LLM сам ставит очередь картинок и запускает apply (одно действие тратит деньги дважды: LLM и картинки; оценка это учитывает, `_improve_estimate`) |
| refs | `list_ref_assets`, `link_ref`, `unlink_ref`, `delete_manual_ref`, `upload_ref` | `/ref-assets`, `/link-ref`, `/delete-ref`, `/refs` | `link_ref`/`unlink_ref` без фразы планировщика; `unlink_ref` вызывает тот же `/delete-ref`, что и удаление файла (по docs: для персонажей/предметов отвязка). Один и тот же эндпоинт стоит за `local_safe` (`unlink_ref`) и `destructive` (`delete_manual_ref`): проверить, что `unlink_ref` с kind=manual не удаляет файл |
| Авто-очередь картинки при визуальной правке покрытия | нет | UI (`BOARD:2462-2478`) | при вызове `montage.set_coverage` авто-`image_ai_change` не создаётся. Задокументировано, но расходится с поведением кнопки: фраза «смени ракурс» меняет БД без обновления картинки |
| Видео-часть apply | `apply_queue` | `/apply` | оценка видео использует `VIDEO_BILLED_ATTEMPTS=4` и `duration=5` допущением (`builtin_montage_more.py:36,40`) |

## 6. Тонкие обёртки, не проверенные на реальном сервере

Исполнитель = одна-две `ctx.call` без собственной логики, проверен только на временной БД или заглушке:
`montage.set_voiceover`, `set_voiceover_span`, `set_scene_chain`, `set_anchors`, `insert_frame`, `merge_scenes`, `split_scene`, `explode_scene`, `delete_frame`, `delete_child_frame`, `swap_slots`, `swap_shots`, `move_image`, `delete_image`, `delete_video`, `upload_*`, `link_ref`, `delete_manual_ref`, `recover_outsee`, `run_montage`, `align_audio`, `set_assemble_params`, `group.*`, `shot.*`, `node.set_config`, `node.set_disabled`, `node.set_prompt_variant`, `prompt.*`, `project.pause/resume/continue_pipeline/reload_xlsx/set_auto_mode`.
Из них никогда не запускались на живом приложении (даже на временной БД с реальными эндпоинтами): `recover_outsee`, `run_montage`, `align_audio`, `link_ref`, `unlink_ref`, `delete_manual_ref`, `project.resume`, `project.continue_pipeline`, `node.run_step`, `node.reset_from_step`.

## 7. Конкретный список работ (по приоритету)

P0 (деньги и целостность)
1. Возвращать факт трат: `credits_spent` из `apply_queue`/`improve_scene`/`node.run_step` (из `results` apply job и api-tracker по `run_id`/`plan:<id>`), закрывать резерв через `budget.settle`; показать факт в истории и в `GET /plans/{id}`.
2. При таймауте шага и исключении `poll` вызывать `spec.stop` или оставлять проект занятым до конца серверной задачи; тест на таймаут платного фонового шага.
3. Замок/версия очереди `pending_ops` для действий слоя (точечные `add/remove`), тест на гонку с сохранением из интерфейса.
4. Единый журнал мутаций на уровне приложения или перенос кнопок через слой; провести `X-Action-Layer-Operator` и `Idempotency-Key` в события проекта.
5. Тариф Outsee (или режим «лимит операций без цены») для `apply_queue`/`generate_scene_images`, иначе слой не может применить правки на Outsee.

P1 (покрытие)
6. Действия: запуск любого шага с оценкой (`step.run_step` с оценкой по модели/числу кадров: images, video, audio TTS, music, sfx, assemble), HITL-решение (`hitl.decide`), `project.create/delete/duplicate`, `project.patch_settings` (модель, разрешение, тема), `massfactory.start`, `storage.*`, `hero.*`, xlsx upload, `workflow.save/reset`, `canvas.disconnect/delete_node` через точечный эндпоинт.
7. Подключить `selected_scene/selected_frame` из монтажной панели в `ActionLayerSection`; добавить командную строку и кнопку «выполнить через слой» в сам Studio-интерфейс монтажа.
8. Записывать в историю прежние значения для перезаписей текста (`set_voiceover`, `set_coverage`, `set_anchors`, `set_scene_chain`, `shot.edit_*`) и сделать действия «откат» по журналу.
9. Привязать вложения к хэшу плана: сверка `sha256` при `run`.
10. Разделить автора плана и подтверждающего: либо код подтверждения в UI, либо запрет на `approve` для не-UI клиента (заголовок/сессия), либо отдельное «второе лицо» для destructive+paid.

P2 (измерение)
11. Полный живой прогон 351 случая на текущем коде, один раз (`scripts/eval_action_planner.py --live`), с отдельным учётом `transport_error`; зафиксировать revision и модель в отчёте.
12. Отложенная выборка (30-50 случаев) с реальными фразами заказчика и формулировками с кнопок; многошаговые (10+), «этот кадр» с контекстом, id рефов в контексте для `link_ref/unlink_ref/delete_manual_ref`, по 3 случая на загрузки.
13. Живой прогон на реальном сервере с копией проекта и малым лимитом: `apply_queue` (1 картинка), `improve_scene` (1 сцена), `generate_scene`, `align_audio`, `run_montage`, `recover_outsee`, `node.run_step` (Каркас), со сверкой факта трат и записей в историю.
14. Проверить `unlink_ref` против `delete_manual_ref` (общий эндпоинт), `change.propose` на LLM-вызов, влияние `coverage_kind` (снятие still) и согласовать tier.

## 8. Что не проверялось

Реальный запуск сервера и браузера; фактические цены Outsee/Vibecode; пересечение резерва слоя с резервами провайдерских путей в `spend_ledger`; поведение `send_to_main_pc` и fleet; тесты не запускались (подсчёты по статическому чтению и импорту реестра).
