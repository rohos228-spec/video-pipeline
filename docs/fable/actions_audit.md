<!--
actions-audit-header
source: #342
sha: 3ec7c9579716a53ab119cabb340af562cba76b31
date: 2026-10-17
actions: 102
keep: 80
modify: 22
remove: 0
fs_breach: group.delete:http.delete,montage.delete_frame:http.delete,node.clear_check_agent:http.delete,prompt.delete_master:http.delete,storage.clear_files:http.delete
snapshot_allow: montage.upload_voice,montage.upload_music,history.rollback
-->

# Аудит действий Action Layer (заготовка R0b)

Статус: заготовка WS2 до merge R0b. Код оркестратора в этот PR не входит.
Дата сверки: 2026-10-17.
Источник реестра: PR #342, форк `nikitasolonukha/video-pipeline`, ветка `orchestrator-observe-v1`, sha `3ec7c9579716a53ab119cabb340af562cba76b31`.
База main на момент аудита: `c53a238b`. Тега `pipeline-base-v1` в репозитории нет, R0a (housepc → main) не влит. Rebase #342 не делался.

Граница node_store (какие места читать через `resolve_node`) утверждена куратором Heil: 15 мест, §node_store. Это не черновик. Номера строк этих мест сняты с `r0a-pipeline-base` / #342 и сверяются ещё раз после тега `pipeline-base-v1`. Конфликтная зона rebase против R0a остаётся предварительной до того же тега.

План: `docs/fable/PLAN.md` в ветке `fable-context` (§2.2 этап 0b, §3.6, §6, §9 WS2).

## Как считали

Живой реестр собирает `ensure_builtin_actions()` в `app/services/action_layer/spec.py`: montage → group → wave3 → change → media → wave5 → publish.

В исходниках **103** вызова `ActionSpec` / `S(...)`. `register_wave3()` делает `registry.unregister("node.run_step")` и регистрирует платную версию с заявкой стоимости (`builtin_wave3.py:682`). Ранняя спека в `builtin_group.py:540` в живой реестр не попадает. `register_wave5()` дополнительно снимает и заново вешает `project.resume` и `project.continue_pipeline` (потолок кредитов) и оборачивает часть исполнителей журналом `builtin_wave5_history.wrap_catalog`.

Итог живого реестра: **102** действия. Это совпадает с `docs/ACTION_LAYER.md` на той же голове («сейчас 102»). Расхождения с числом 102 нет. Расхождение с ожидаемыми именами плана — в §расхождения.

Критерий exec-подобности (§2.2.2), как он применён здесь:

- **(а) прямо** — обработчик или его обёртка history сам пишет файл вне `data/reports/` и `data/node_overrides/`.
- **(а) косвенно** — обработчик только вызывает внутренний API (`ActionContext.call`, httpx с `ASGITransport` на своё приложение), а эндпоинт студии пишет файл репозитория (`templates/`, `prompts/`, `node_groups/*.json`).
- **(б)** — в обработчике есть `subprocess` / `eval` / `compile` / `exec` / `os.system`. По всем 102 исполнителям такого вызова нет.
- **(в)** — сетевой вызов обработчика мимо LLM-провайдера и внутреннего API. Прямых нет. Вызовы площадок и провайдеров идут уже внутри шага студии.

Запись проекта, медиа и БД через внутренний API сама по себе не считается exec-инструментом: это штатное управление пайплайном под approval / spend / change_control. Иначе из реестра выпали бы почти все мутации, ради которых слой и существует.

Риск в таблице — шкала плана (`none` / `low` / `medium` / `high`). Рядом — ярус реестра #342 (`read` / `local_safe` / `paid` / `destructive`).

Вердикт:

- **keep** — действие остаётся в whitelist.
- **modify** — в R2 сузить запись (путь или механизм), само действие не выкидывать.
- **remove** — физически убрать из реестра. Таких среди 102 нет.

## Сводка

| | число |
|---|---|
| Живых действий | 102 |
| Регистраций в исходнике | 103 (одна снята: ранний `node.run_step`) |
| keep | 80 |
| modify | 22 |
| remove | 0 |
| Риск none | 21 |
| Риск low | 7 |
| Риск medium | 29 |
| Риск high | 45 |

### remove

Пусто. В реестре нет `edit_files`, `run_tests`, `exec`, `shell`, `write_file`, `fix_bugs` и нет обёрток subprocess. Эти инструменты живут в легаси `db_browser.py` (см. ниже), не в Action Layer.

### modify

1. `prompt.save_master`
2. `prompt.create_master`
3. `prompt.delete_master`
4. `group.save_from_selection`
5. `group.update`
6. `group.delete`
7. `montage.upload_voice`
8. `montage.upload_music`
9. `history.rollback`
10. `montage.delete_child_frame`
11. `montage.delete_frame`
12. `node.clear_check_agent`
13. `storage.clear_files`
14. `node.reset_from_step`
15. `montage.delete_image`
16. `montage.delete_video`
17. `montage.delete_manual_ref`
18. `node.upload_file`
19. `project.reload_xlsx`
20. `node.run_step`
21. `project.upload_xlsx`
22. `hero.replace_image`

`fs_breach` сверяется парами `id:kind`: `group.delete:http.delete`, `montage.delete_frame:http.delete`, `node.clear_check_agent:http.delete`, `prompt.delete_master:http.delete`, `storage.clear_files:http.delete`. Новый kind у уже записанного id — отказ. После детектора HTTP DELETE три keep стали modify: `montage.delete_frame`, `node.clear_check_agent`, `storage.clear_files`. `group.delete` и `prompt.delete_master` уже были modify. `montage.delete_child_frame` переведён в modify отдельно: вызов `POST …/coverage` с `type=coverage_delete`, детектор HTTP DELETE его не видит, физическое удаление шота всё равно не остаётся keep. Счёт: keep 80, modify 22, remove 0. Ещё девять keep переведены в modify: описание говорит об удалении, стирании или перезаписи (`node.reset_from_step`, `montage.delete_image`, `montage.delete_video`, `montage.delete_manual_ref`, `node.upload_file`, `project.reload_xlsx`, `node.run_step`, `project.upload_xlsx`, `hero.replace_image`). Для них путь — пометка, копия в old/ или версия, не физическое стирание. Каталог `data/action_layer_snapshots/` разрешён только трём id из поля `snapshot_allow` (`montage.upload_voice`, `montage.upload_music`, `history.rollback`). Другое действие при записи туда — отказ. Эти три на #342 делают `shutil.copy2` в `_backup_file` и не удаляют файлы снимков. `shutil.rmtree` запрещён на любом пути. `rag.reindex` в реестре 102 нет и в таблицу действий не входит: allowlist теста помечен явно (`_OUTSIDE_REGISTRY_ALLOW`), это не 103-е действие. Для него заранее разрешены только `{settings.data_dir}/orch_rag.db`, `-wal` и `-shm`.

## Таблица действий

| # | Действие | Файл / обработчик | Что делает | Риск | Реестр | Exec §2.2.2 | Вердикт | Почему |
|---|---|---|---|---|---|---|---|---|
| 1 | `change.list` | app/services/action_layer/builtin_change.py:_list (спека L136) | Предложения изменений настроек и промптов: статус, уровень, цель, diff. | none | read | нет | keep | Только чтение через внутренний API. Прямых (а)(б)(в) нет. |
| 2 | `change.get` | app/services/action_layer/builtin_change.py:_get (спека L143) | По proposal_id: предложение целиком (diff, digest, статус, текущая версия цели). | none | read | нет | keep | Только чтение через внутренний API. Прямых (а)(б)(в) нет. |
| 3 | `change.propose` | app/services/action_layer/builtin_change.py:_propose (спека L151) | Записывает предложение: T0 совет, T1 параметр или выбор готового варианта из явного списка, T2 новый текст промпта проекта (новый вариант, общие файлы не трогаются), T3 запись для разработчика (код не применяется никогда). | medium | local_safe | нет | keep | Локальная мутация проекта через внутренний API. (а)(б)(в) в обработчике нет. |
| 4 | `change.apply` | app/services/action_layer/builtin_change.py:_apply (спека L168) | Перепроверяет уровень, allow-list, digest и базовую версию, пишет версию и значение, читает обратно; при расхождении возвращает прежнее. | high | destructive | нет | keep | Инфраструктура change_control #342. Propose обещает не трогать общие файлы. subprocess нет. Действие остаётся; если apply всё же пишет git-шаблон, в R2 цель промта уводится в node_store, id не удаляется. |
| 5 | `change.rollback` | app/services/action_layer/builtin_change.py:_rollback (спека L188) | Возвращает прежнюю версию значения цели новой строкой версии; история не удаляется. | high | destructive | нет | keep | Откат change_control той же инфраструктурой, что apply. subprocess нет. id остаётся. |
| 6 | `project.stop` | app/services/action_layer/builtin_group.py:_stop_project (спека L366) | Глобальный стоп проекта: прерывает активный шаг, генерацию и задачи монтажа. | low | local_safe | нет | keep | Флаг управления проектом через внутренний API. Записи вне allowlist и исполнения кода нет. |
| 7 | `shot.get_menu` | app/services/action_layer/builtin_group.py:_shot_get_menu (спека L374) | Ячейки закадра и шоты. | none | read | нет | keep | Только чтение через внутренний API. Прямых (а)(б)(в) нет. |
| 8 | `shot.edit_cell_voiceover` | app/services/action_layer/builtin_group.py:_shot_edit_cell (спека L380); живой executor — обёртка builtin_wave5_history._wrap_executor | Записывает текст закадра ячейки в БД. | medium | local_safe | нет | keep | Локальная мутация проекта через внутренний API. (а)(б)(в) в обработчике нет. |
| 9 | `shot.add_cell` | app/services/action_layer/builtin_group.py:_shot_add_cell (спека L389) | Новая пустая ячейка перед ячейкой с индексом before_index (без индекса в конец), дробный sort_key. | medium | local_safe | нет | keep | Локальная мутация проекта через внутренний API. (а)(б)(в) в обработчике нет. |
| 10 | `shot.edit_field` | app/services/action_layer/builtin_group.py:_shot_edit_field (спека L399); живой executor — обёртка builtin_wave5_history._wrap_executor | Действие, персонажи, стык, сцена, площадка, крупность, движение или промты шота: запись в БД без запуска генерации. | medium | local_safe | нет | keep | Локальная мутация проекта через внутренний API. (а)(б)(в) в обработчике нет. |
| 11 | `group.list` | app/services/action_layer/builtin_group.py:_group_list (спека L411) | Встроенные и пользовательские группы нод. | none | read | нет | keep | Только чтение через внутренний API. Прямых (а)(б)(в) нет. |
| 12 | `group.get` | app/services/action_layer/builtin_group.py:_group_get (спека L416) | Полный spec группы: ноды, связи, промты. | none | read | нет | keep | Только чтение через внутренний API. Прямых (а)(б)(в) нет. |
| 13 | `group.shots_report` | app/services/action_layer/builtin_group.py:_group_shots_report (спека L422) | Читает готовый HTML-отчёт ноды fw_report (первые 3000 символов). | none | read | нет | keep | Только чтение через внутренний API. Прямых (а)(б)(в) нет. |
| 14 | `group.insert` | app/services/action_layer/builtin_group.py:_group_insert (спека L428) | Добавляет ноды группы, связи, промт-варианты и флаги в канвас проекта. | medium | local_safe | нет | keep | Локальная мутация проекта через внутренний API. (а)(б)(в) в обработчике нет. |
| 15 | `group.save_from_selection` | app/services/action_layer/builtin_group.py:_group_from_selection (спека L439) | Записывает выбранные ноды канваса как новую пользовательскую группу (файл в node_groups/). | medium | local_safe | (а) косвенно: POST /node-groups/from-selection пишет node_groups/<id>.json в корень репозитория | modify | Пользовательская группа коммитится файлом в git. Оркестратор не должен писать этот каталог; хранение — вне git либо отдельным решением куратора. |
| 16 | `group.update` | app/services/action_layer/builtin_group.py:_group_update (спека L455) | Название, описание или категория пользовательской группы. | medium | local_safe | (а) косвенно: PUT /node-groups/{id} переписывает JSON группы в репозитории | modify | Та же запись файла репо, что у group.save_from_selection. До решения по каталогу групп — не в whitelist как есть. |
| 17 | `group.delete` | app/services/action_layer/builtin_group.py:_group_delete (спека L468) | Удаляет файл пользовательской группы. | high | destructive | (а) косвенно: DELETE /node-groups/{id} удаляет файл группы из репозитория | modify | только через подтверждение и пометку (soft delete), без физического удаления, с восстановлением |
| 18 | `node.get_config` | app/services/action_layer/builtin_group.py:_node_get_config (спека L480) | Роль, режим выхода, виды выхода, режим проверки и признак принудительного Vibecode. | none | read | нет | keep | Только чтение через внутренний API. Прямых (а)(б)(в) нет. |
| 19 | `node.get_llm_route` | app/services/action_layer/builtin_group.py:_node_get_llm_route (спека L486) | Для нод script_frames_qc выбор модели игнорируется: всегда Vibecode «GPT 5.6 Sol». | none | read | нет | keep | Только чтение через внутренний API. Прямых (а)(б)(в) нет. |
| 20 | `node.set_config` | app/services/action_layer/builtin_group.py:_node_set_config (спека L492) | Роль, режим и виды выхода, «брать со стрелок», снимок и режим проверки. | medium | local_safe | нет | keep | Локальная мутация проекта через внутренний API. (а)(б)(в) в обработчике нет. |
| 21 | `node.set_prompt_variant` | app/services/action_layer/builtin_group.py:_node_set_prompt_variant (спека L517) | Привязывает файл промта к слоту ноды в этом проекте (meta). | medium | local_safe | нет | keep | Локальная мутация проекта через внутренний API. (а)(б)(в) в обработчике нет. |
| 22 | `node.set_disabled` | app/services/action_layer/builtin_group.py:_node_set_disabled (спека L530) | Пишет ноду в meta.disabled_nodes проекта или убирает оттуда. | low | local_safe | нет | keep | Флаг управления проектом через внутренний API. Записи вне allowlist и исполнения кода нет. |
| 23 | `node.reset_from_step` | app/services/action_layer/builtin_group.py:_node_reset (спека L553) | Стирает результаты шага и всех шагов ниже по конвейеру и пересчитывает статус проекта. | high | destructive | нет | modify | Результаты шага не стирать физически: версия или копия в old/, откат из снимка. |
| 24 | `media.get_recovery_assessment` | app/services/action_layer/builtin_media_recovery.py:_assessment (спека L125) | Читает журнал платных задач и файлы: есть ли открытая задача Kie для докачивания, готовый файл, провалившаяся задача или неизвестный исход отправки, и что из этого допускает надзор. | none | read | нет | keep | Только чтение через внутренний API. Прямых (а)(б)(в) нет. |
| 25 | `media.resume_poll` | app/services/action_layer/builtin_media_recovery.py:_resume (спека L132) | Спрашивает Kie про уже отправленную задачу (клипы, предметы, музыка) и скачивает готовый результат. | medium | local_safe | нет в обработчике; сеть провайдера идёт внутренним API студии (не отдельный HTTP действия) | keep | Локальная мутация проекта через внутренний API. (а)(б)(в) в обработчике нет. |
| 26 | `media.list_unknown_outcomes` | app/services/action_layer/builtin_media_recovery.py:_unknown_list (спека L143) | Показывает ключи проекта (клипы, картинки, предметы, музыка), у которых прошлая платная отправка в Kie или Outsee не имеет известного исхода и поэтому блокирует повтор: провайдер, возраст блокировки и окно, на которое она действует. | none | read | нет | keep | Только чтение через внутренний API. Прямых (а)(б)(в) нет. |
| 27 | `media.release_unknown` | app/services/action_layer/builtin_media_recovery.py:_release (спека L152) | Решение человека после сверки кабинета провайдера (Kie или Outsee): снимает блокировку повторной платной отправки по одному ключу этого провайдера. | high | destructive | нет | keep | Разрушительная мутация данных проекта через внутренний API, не shell и не файл git-шаблона. Оставляем под approval. |
| 28 | `montage.get_board` | app/services/action_layer/builtin_montage.py:_get_board (спека L181) | Кадры, закадр, очередь правок и устаревшие видео. | none | read | нет | keep | Только чтение через внутренний API. Прямых (а)(б)(в) нет. |
| 29 | `montage.apply_status` | app/services/action_layer/builtin_montage.py:_apply_status (спека L187) | Состояние фоновой задачи apply: идёт ли, сколько операций сделано. | none | read | нет | keep | Только чтение через внутренний API. Прямых (а)(б)(в) нет. |
| 30 | `montage.montage_status` | app/services/action_layer/builtin_montage.py:_montage_status (спека L192) | Состояние фоновой задачи пересборки видео (озвучка и сборка). | none | read | нет | keep | Только чтение через внутренний API. Прямых (а)(б)(в) нет. |
| 31 | `montage.recover_status` | app/services/action_layer/builtin_montage.py:_recover_status (спека L197) | Состояние фоновой задачи подбора готовых карточек из галереи. | none | read | нет | keep | Только чтение через внутренний API. Прямых (а)(б)(в) нет. |
| 32 | `montage.set_voiceover` | app/services/action_layer/builtin_montage.py:_set_voiceover (спека L202); живой executor — обёртка builtin_wave5_history._wrap_executor | Записывает текст закадра кадра в БД. | medium | local_safe | нет | keep | Локальная мутация проекта через внутренний API. (а)(б)(в) в обработчике нет. |
| 33 | `montage.set_coverage` | app/services/action_layer/builtin_montage.py:_set_coverage (спека L214); живой executor — обёртка builtin_wave5_history._wrap_executor | Роль, крупность, ракурс, движение, стык, свет или действие кадра: запись в БД без запуска генерации (картинка в очередь автоматически не ставится). | medium | local_safe | нет | keep | Локальная мутация проекта через внутренний API. (а)(б)(в) в обработчике нет. |
| 34 | `montage.queue_ops` | app/services/action_layer/builtin_montage.py:_queue_ops (спека L230) | Дописывает операции перегенерации в очередь доски (одноимённые заменяются). | medium | local_safe | нет | keep | Локальная мутация проекта через внутренний API. (а)(б)(в) в обработчике нет. |
| 35 | `montage.swap_shots` | app/services/action_layer/builtin_montage.py:_swap_shots_default (спека L250) | Меняет картинки и/или видео и промты шотов кадра. | medium | local_safe | нет | keep | Локальная мутация проекта через внутренний API. (а)(б)(в) в обработчике нет. |
| 36 | `montage.cancel_queue_ops` | app/services/action_layer/builtin_montage_more.py:_cancel_queue_ops (спека L736) | Снимает выбранные операции с очереди доски или очищает всю очередь. Файлы кадров не трогает. | low | local_safe | нет | keep | Флаг управления проектом через внутренний API. Записи вне allowlist и исполнения кода нет. |
| 37 | `montage.apply_queue` | app/services/action_layer/builtin_montage_more.py:_apply_queue (спека L751) | Применить правки очереди (генерация) | high | destructive+paid | нет в обработчике; сеть провайдера идёт внутренним API студии (не отдельный HTTP действия) | keep | Платный шаг пайплайна через внутренний API. subprocess/eval нет. Оставляем под approval, spend и paid-retry. |
| 38 | `montage.improve_scene` | app/services/action_layer/builtin_montage_more.py:_improve_scene (спека L776) | Улучшить сцену (LLM + картинки кадров) | high | destructive+paid | нет в обработчике; LLM провайдера идёт внутренним API (исключение §2.2.2в) | keep | Платный шаг пайплайна через внутренний API. subprocess/eval нет. Оставляем под approval, spend и paid-retry. |
| 39 | `montage.generate_scene_images` | app/services/action_layer/builtin_montage_more.py:_generate_scene_images (спека L799) | Сгенерировать картинки сцены | high | destructive+paid | нет в обработчике; сеть провайдера идёт внутренним API студии (не отдельный HTTP действия) | keep | Платный шаг пайплайна через внутренний API. subprocess/eval нет. Оставляем под approval, spend и paid-retry. |
| 40 | `montage.generate_scene` | app/services/action_layer/builtin_montage_more.py:_generate_scene (спека L817) | Один вызов LLM пишет сцены ячейки по заказу оператора; replace_ns пересобирает только выбранные куски цепочки. | high | destructive+paid | нет в обработчике; LLM провайдера идёт внутренним API (исключение §2.2.2в) | keep | Платный шаг пайплайна через внутренний API. subprocess/eval нет. Оставляем под approval, spend и paid-retry. |
| 41 | `montage.suggest_scene_variants` | app/services/action_layer/builtin_montage_more.py:_suggest_variants (спека L834) | Один вызов LLM возвращает варианты действия, формата сцены или якорей. | high | paid | нет в обработчике; LLM провайдера идёт внутренним API (исключение §2.2.2в) | keep | Платный шаг пайплайна через внутренний API. subprocess/eval нет. Оставляем под approval, spend и paid-retry. |
| 42 | `montage.set_scene_chain` | app/services/action_layer/builtin_montage_more.py:_set_scene_chain (спека L844); живой executor — обёртка builtin_wave5_history._wrap_executor | Режет цепочку «действие1 → действие2 → ...» на шоты ячейки без LLM. | high | destructive | нет | keep | Разрушительная мутация данных проекта через внутренний API, не shell и не файл git-шаблона. Оставляем под approval. |
| 43 | `montage.set_anchors` | app/services/action_layer/builtin_montage_more.py:_set_anchors (спека L857); живой executor — обёртка builtin_wave5_history._wrap_executor | Якоря режут закадр ячейки по кадрам. | high | destructive | нет | keep | Разрушительная мутация данных проекта через внутренний API, не shell и не файл git-шаблона. Оставляем под approval. |
| 44 | `montage.set_voiceover_span` | app/services/action_layer/builtin_montage_more.py:_set_vo_span (спека L871) | Закрепляет за сценой фрагмент закадра или снимает закрепление (clear). | medium | local_safe | нет | keep | Локальная мутация проекта через внутренний API. (а)(б)(в) в обработчике нет. |
| 45 | `montage.delete_child_frame` | app/services/action_layer/builtin_montage_more.py:_delete_child (спека L882) | Удаляет дочерний шот (не голову сцены). | high | destructive | нет | modify | только через подтверждение и пометку (soft delete), без физического удаления, с восстановлением |
| 46 | `montage.insert_frame` | app/services/action_layer/builtin_montage_more.py:_insert_frame (спека L893) | kind=parent: новая ячейка закадра после кадра (или в начало); kind=child: шот в ту же сцену. | medium | local_safe | нет | keep | Локальная мутация проекта через внутренний API. (а)(б)(в) в обработчике нет. |
| 47 | `montage.merge_scenes` | app/services/action_layer/builtin_montage_more.py:_merge_scenes (спека L904) | Склеивает две соседние ячейки закадра в одну сцену. | high | destructive | нет | keep | Разрушительная мутация данных проекта через внутренний API, не shell и не файл git-шаблона. Оставляем под approval. |
| 48 | `montage.split_scene` | app/services/action_layer/builtin_montage_more.py:_split_scene (спека L915) | Кадр и все правее становятся новой сценой (первый кадр сцены отделить нельзя). | high | destructive | нет | keep | Разрушительная мутация данных проекта через внутренний API, не shell и не файл git-шаблона. Оставляем под approval. |
| 49 | `montage.explode_scene` | app/services/action_layer/builtin_montage_more.py:_explode_scene (спека L925) | Каждый видимый кадр ячейки становится отдельной ячейкой закадра. | high | destructive | нет | keep | Разрушительная мутация данных проекта через внутренний API, не shell и не файл git-шаблона. Оставляем под approval. |
| 50 | `montage.delete_frame` | app/services/action_layer/builtin_montage_more.py:_delete_frame (спека L935) | Удаляет кадр; у головы сцены первый дочерний кадр повышается. | high | destructive | (а) косвенно: `ctx.call("DELETE", …/frames/{id})` | modify | только через подтверждение и пометку (soft delete), без физического удаления, с восстановлением |
| 51 | `montage.swap_slots` | app/services/action_layer/builtin_montage_more.py:_swap_slots (спека L946) | Обмен слотов картинок или видео между любыми кадрами; слоты помечаются устаревшими. | medium | local_safe | нет | keep | Локальная мутация проекта через внутренний API. (а)(б)(в) в обработчике нет. |
| 52 | `montage.move_image` | app/services/action_layer/builtin_montage_more.py:_move_image (спека L957) | Перенос в пустой слот или обмен, если цель занята; промты едут с файлами. | medium | local_safe | нет | keep | Локальная мутация проекта через внутренний API. (а)(б)(в) в обработчике нет. |
| 53 | `montage.delete_image` | app/services/action_layer/builtin_montage_more.py:_delete_image (спека L966) | Файл уходит в old/scenes, запись картинки удаляется, видео кадра помечается устаревшим. | high | destructive | нет | modify | Файл уже в old/. Запись картинки не удалять: пометить и хранить версию. |
| 54 | `montage.delete_video` | app/services/action_layer/builtin_montage_more.py:_delete_video (спека L979) | Файл клипа уходит в old/videos, запись видео удаляется. | high | destructive | нет | modify | Файл уже в old/. Запись видео не удалять: пометить и хранить версию. |
| 55 | `montage.set_video_trim` | app/services/action_layer/builtin_montage_more.py:_set_video_trim (спека L989) | Пишет start/end (секунды) обрезки слота видео в настройки доски; влияет на следующую сборку. | medium | local_safe | нет | keep | Локальная мутация проекта через внутренний API. (а)(б)(в) в обработчике нет. |
| 56 | `montage.list_ref_assets` | app/services/action_layer/builtin_montage_more.py:_list_ref_assets (спека L1000) | Персонажи и предметы проекта, которые можно приложить кадру. | none | read | нет | keep | Только чтение через внутренний API. Прямых (а)(б)(в) нет. |
| 57 | `montage.link_ref` | app/services/action_layer/builtin_montage_more.py:_link_ref (спека L1004) | Привязывает файл из ref-assets к кадру; файл не копируется. | medium | local_safe | нет | keep | Локальная мутация проекта через внутренний API. (а)(б)(в) в обработчике нет. |
| 58 | `montage.unlink_ref` | app/services/action_layer/builtin_montage_more.py:_unlink_ref (спека L1013) | Скрывает персонажа/предмет из рефов ячейки. | medium | local_safe | нет | keep | Локальная мутация проекта через внутренний API. (а)(б)(в) в обработчике нет. |
| 59 | `montage.delete_manual_ref` | app/services/action_layer/builtin_montage_more.py:_delete_manual_ref (спека L1021) | Убирает ручной реф кадра; если файл был загружен для этого кадра, он удаляется с диска (приложенный готовый ассет остаётся). | high | destructive | нет | modify | Файл рефа не удалять с диска: копия в old/ и пометка, готовый ассет остаётся. |
| 60 | `montage.recover_outsee` | app/services/action_layer/builtin_montage_more.py:_recover_outsee (спека L1034) | Забрать правки из галереи Outsee | high | destructive | нет в обработчике; сеть провайдера идёт внутренним API студии (не отдельный HTTP действия) | keep | Разрушительная мутация данных проекта через внутренний API, не shell и не файл git-шаблона. Оставляем под approval. |
| 61 | `montage.run_montage` | app/services/action_layer/builtin_montage_more.py:_run_montage (спека L1045) | Смонтировать видео (озвучка + сборка) | high | destructive | нет | keep | Разрушительная мутация данных проекта через внутренний API, не shell и не файл git-шаблона. Оставляем под approval. |
| 62 | `montage.align_audio` | app/services/action_layer/builtin_montage_more.py:_align_audio (спека L1058) | Разбор аудио (таймкоды кадров) | medium | local_safe | нет | keep | Локальная мутация проекта через внутренний API. (а)(б)(в) в обработчике нет. |
| 63 | `montage.set_assemble_params` | app/services/action_layer/builtin_montage_more.py:_set_assemble_params (спека L1068) | Хвост после озвучки, громкость музыки, субтитры, пропуск первых секунд. | medium | local_safe | нет | keep | Локальная мутация проекта через внутренний API. (а)(б)(в) в обработчике нет. |
| 64 | `montage.get_cost_basis` | app/services/action_layer/builtin_montage_more.py:_get_cost_basis (спека L1080) | Текущие генераторы и разрешения, как их берёт регенерация панели, их цена по каталогу Kie (или признак «цена неизвестна») и число операций в очереди. | none | read | нет | keep | Только чтение через внутренний API. Прямых (а)(б)(в) нет. |
| 65 | `montage.upload_voice` | app/services/action_layer/builtin_wave3.py:_upload_voice (спека L470); живой executor — обёртка builtin_wave5_history._wrap_executor | Сохраняет файл как audio/voice_full.<расширение> и запоминает путь в настройках проекта. | high | destructive | (а) прямо: обёртка history (_backup_file) копирует прежний файл в data/action_layer_snapshots/. Каталог разрешён только этому id, upload_music и history.rollback | modify | Сам аплоад идёт внутренним API. Снимок не удаляется. Вердикт modify оставлен: путь вне data/reports/ и data/node_overrides/, но каталог именной. |
| 66 | `montage.upload_music` | app/services/action_layer/builtin_wave3.py:_upload_music (спека L483); живой executor — обёртка builtin_wave5_history._wrap_executor | Сохраняет файл как music/bgm.<расширение> и запоминает путь в настройках проекта. | high | destructive | (а) прямо: та же обёртка _backup_file пишет копию музыки в data/action_layer_snapshots/ (allowlist только этого id и двух соседних) | modify | Как montage.upload_voice: загрузка через внутренний API, снимок не удаляется, вердикт modify сохранён. |
| 67 | `montage.upload_image` | app/services/action_layer/builtin_wave3.py:_upload_image (спека L493) | Кладёт PNG в слот кадра (shot 1/2, slot=parent для still родителя): прежняя картинка слота архивируется в old/, видео кадра помечается устаревшим. | high | destructive | нет | keep | Разрушительная мутация данных проекта через внутренний API, не shell и не файл git-шаблона. Оставляем под approval. |
| 68 | `montage.upload_video` | app/services/action_layer/builtin_wave3.py:_upload_video (спека L504) | Кладёт MP4 в слот видео кадра: прежний клип архивируется в old/, пометка «устарело» снимается. | high | destructive | нет | keep | Разрушительная мутация данных проекта через внутренний API, не shell и не файл git-шаблона. Оставляем под approval. |
| 69 | `montage.upload_ref` | app/services/action_layer/builtin_wave3.py:_upload_ref (спека L515) | Новый ручной референс кадра (картинка и имя): новый файл, прежние рефы не затрагиваются. | medium | local_safe | нет | keep | Локальная мутация проекта через внутренний API. (а)(б)(в) в обработчике нет. |
| 70 | `node.upload_file` | app/services/action_layer/builtin_wave3.py:_upload_node_file (спека L527) | Подменяет вход ноды: .xlsx заменяет прежние xlsx ноды (они удаляются) и выключает «брать со стрелок»; в режиме проверки .txt/.md становится агентом проверки. | high | destructive | нет | modify | Прежние xlsx не удалять: копия в old/ и версия, затем подмена входа. |
| 71 | `node.upload_check_agent` | app/services/action_layer/builtin_wave3.py:_upload_check_agent (спека L539) | Сохраняет .txt/.md агента проверки ноды, включает режим проверки и источник «агент». | high | destructive | нет | keep | Разрушительная мутация данных проекта через внутренний API, не shell и не файл git-шаблона. Оставляем под approval. |
| 72 | `node.clear_check_agent` | app/services/action_layer/builtin_wave3.py:_clear_check_agent (спека L550) | Удаляет загруженные файлы агента проверки ноды: дальше берётся встроенный агент. | high | destructive | (а) косвенно: `ctx.call("DELETE", …/check-agent)` | modify | Детектор видит HTTP DELETE. Файл агента проверки удаляется через API. В R2 это не инструмент оркестратора. |
| 73 | `project.pause` | app/services/action_layer/builtin_wave3.py:_pause (спека L559) | Останавливает выполняющуюся ноду и ставит статус paused (прежний статус запоминается). | low | local_safe | нет | keep | Флаг управления проектом через внутренний API. Записи вне allowlist и исполнения кода нет. |
| 74 | `project.resume` | app/services/action_layer/builtin_wave3.py:_resume (спека L565) | Возвращает статус, с которого проект ушёл в паузу. | high | destructive+paid | нет | keep | Платный шаг пайплайна через внутренний API. subprocess/eval нет. Оставляем под approval, spend и paid-retry. |
| 75 | `project.continue_pipeline` | app/services/action_layer/builtin_wave3.py:_continue (спека L578) | Снимает ручной стоп и паузу и продвигает проект на следующий шаг (*_ready -> running), если шаг готов к запуску. | high | destructive+paid | нет | keep | Платный шаг пайплайна через внутренний API. subprocess/eval нет. Оставляем под approval, spend и paid-retry. |
| 76 | `project.set_auto_mode` | app/services/action_layer/builtin_wave3.py:_set_auto_mode (спека L590) | Переключатель «Автопродвижение» (project.auto_mode). | low | local_safe | нет | keep | Флаг управления проектом через внутренний API. Записи вне allowlist и исполнения кода нет. |
| 77 | `project.reload_xlsx` | app/services/action_layer/builtin_wave3.py:_reload_xlsx (спека L604) | Импортирует project.xlsx в БД: поля проекта, закадр, промты и атрибуты кадров перезаписываются значениями из файла, недостающие кадры создаются. | high | destructive | нет | modify | Перезапись полей — новой версией. Кадры не удалять и не терять шоты. |
| 78 | `montage.set_send_to_main_pc` | app/services/action_layer/builtin_wave3.py:_set_send_to_main_pc (спека L614) | Включает или выключает флаг assemble.send_to_main_pc. | low | local_safe | нет | keep | Флаг управления проектом через внутренний API. Записи вне allowlist и исполнения кода нет. |
| 79 | `prompt.get_master` | app/services/action_layer/builtin_wave3.py:_get_master (спека L626) | Текст общего промта шага (первые 20000 символов), длина и sha256. | none | read | нет | keep | Только чтение через внутренний API. Прямых (а)(б)(в) нет. |
| 80 | `prompt.save_master` | app/services/action_layer/builtin_wave3.py:_save_master (спека L633); живой executor — обёртка builtin_wave5_history._wrap_executor | Пишет общий файл промта шага: правка действует на ВСЕ проекты. | high | destructive | (а) косвенно: PUT внутреннего API перезаписывает файл мастер-промта в репозитории (templates/node_groups или prompts/), сразу для всех проектов | modify | D3: git-шаблоны read-only. Запись промта ноды — в node_store (БД), не в файл репо. |
| 81 | `prompt.create_master` | app/services/action_layer/builtin_wave3.py:_create_master (спека L651) | Новый файл варианта промта шага (общий для всех проектов). | medium | destructive | (а) косвенно: создаёт новый файл варианта промта в репозитории, общий для всех проектов | modify | Новый вариант промта не должен появляться файлом в git. В R2 — версия в node_store. Общий файл для всех проектов — ярус destructive, как у prompt.save_master. |
| 82 | `prompt.delete_master` | app/services/action_layer/builtin_wave3.py:_delete_master (спека L662) | Удаляет файл варианта промта шага (общий для всех проектов, default удалять нельзя). | high | destructive | (а) косвенно: DELETE внутреннего API удаляет файл промта из репозитория для всех проектов | modify | только через подтверждение и пометку (soft delete), без физического удаления, с восстановлением |
| 83 | `node.get_run_cost_basis` | app/services/action_layer/builtin_wave3.py:_get_run_cost_basis (спека L674) | Для нод Каркас, Площадка, Шоты, QC: пачки, худшее число запросов к модели и токены на запрос по реальным данным проекта и файлам промтов. | none | read | нет | keep | Только чтение через внутренний API. Прямых (а)(б)(в) нет. |
| 84 | `node.run_step` | app/services/action_layer/builtin_wave3.py:_run_node_step (спека L682) | Явный старт шага ноды (режим resume): убивает активную генерацию проекта и перезаписывает в БД выход ноды по всем ячейкам. | high | destructive+paid | нет в обработчике; LLM провайдера идёт внутренним API (исключение §2.2.2в) | modify | Выход ноды перезаписывать новой версией, прежний текст ячеек не стирать. |
| 85 | `step.get_run_cost_basis` | app/services/action_layer/builtin_wave5.py:_get_step_cost_basis (спека L355) | Для шагов картинок, видео, озвучки, музыки, сборки и LLM-промтов: сколько генераций по доске, какие значения заявить в step.run_step и верхняя оценка в кредитах. | none | read | нет | keep | Только чтение через внутренний API. Прямых (а)(б)(в) нет. |
| 86 | `step.run_step` | app/services/action_layer/builtin_wave5.py:_run_media_step (спека L364) | Ручной запуск шага (режим resume): картинки, видео, озвучка, музыка, финальная сборка, промты картинок и анимации. | high | destructive+paid | нет в обработчике; сеть провайдера идёт внутренним API студии (не отдельный HTTP действия) | keep | Платный шаг пайплайна через внутренний API. subprocess/eval нет. Оставляем под approval, spend и paid-retry. |
| 87 | `hitl.list_pending` | app/services/action_layer/builtin_wave5.py:_list_pending (спека L383) | Запросы подтверждения проекта (HITL), по которым решение ещё не принято: id, вид и кадр. | none | read | нет | keep | Только чтение через внутренний API. Прямых (а)(б)(в) нет. |
| 88 | `hitl.approve` | app/services/action_layer/builtin_wave5.py:_decide('approve') (спека L388) | Принимает результат: кадр получает статус одобренного. | high | destructive | нет | keep | Разрушительная мутация данных проекта через внутренний API, не shell и не файл git-шаблона. Оставляем под approval. |
| 89 | `hitl.reject` | app/services/action_layer/builtin_wave5.py:_decide('reject') (спека L399) | Отклоняет результат: кадр получает статус failed, проект может уйти на паузу. | high | destructive | нет | keep | Разрушительная мутация данных проекта через внутренний API, не shell и не файл git-шаблона. Оставляем под approval. |
| 90 | `hitl.regenerate` | app/services/action_layer/builtin_wave5.py:_decide('regenerate') (спека L408) | Возвращает кадр на перегенерацию: воркер заново сгенерирует картинку или клип и потратит деньги. | high | destructive+paid | нет | keep | Платный шаг пайплайна через внутренний API. subprocess/eval нет. Оставляем под approval, spend и paid-retry. |
| 91 | `project.create` | app/services/action_layer/builtin_wave5.py:_create_project (спека L423) | Новый проект с названием, темой и режимом героя. | medium | local_safe | нет | keep | Локальная мутация проекта через внутренний API. (а)(б)(в) в обработчике нет. |
| 92 | `project.create_child` | app/services/action_layer/builtin_wave5.py:_create_child (спека L435) | Дочерний проект: настройки, промты и тексты родителя, без озвучки, Excel и результатов. | medium | local_safe | нет | keep | Локальная мутация проекта через внутренний API. (а)(б)(в) в обработчике нет. |
| 93 | `project.patch_settings` | app/services/action_layer/builtin_wave5.py:_patch_settings (спека L442) | Название, тема, режим героя, модель и разрешение картинок и видео, формат кадра. | medium | local_safe | нет | keep | Локальная мутация проекта через внутренний API. (а)(б)(в) в обработчике нет. |
| 94 | `massfactory.start` | app/services/action_layer/builtin_wave5.py:_mass_start (спека L459) | Очередь тем: первый дочерний проект стартует с плана, остальные идут по очереди сами. | high | destructive+paid | нет | keep | Платный шаг пайплайна через внутренний API. subprocess/eval нет. Оставляем под approval, spend и paid-retry. |
| 95 | `kill_switch.engage` | app/services/action_layer/builtin_wave5.py:_kill_engage (спека L476) | Включает глобальный аварийный стоп: платные и разрушающие шаги слоя перестают выполняться. | low | local_safe | нет | keep | Флаг управления проектом через внутренний API. Записи вне allowlist и исполнения кода нет. |
| 96 | `project.upload_xlsx` | app/services/action_layer/builtin_wave5.py:_upload_xlsx (спека L484) | Заменяет project.xlsx файлом .xlsx (проверяется сервером) и перечитывает его в базу: поля проекта, закадр и кадры перезаписываются значениями из файла. | high | destructive | нет | modify | Прежний xlsx — в old/. Кадры и закадр перезаписывать версией, не удаляя строки. |
| 97 | `hero.replace_image` | app/services/action_layer/builtin_wave5.py:_replace_hero (спека L496) | Кладёт картинку (png, jpg, webp) в папку characters проекта под её именем; файл с таким же именем перезаписывается без архива. | high | destructive | нет | modify | Персонажа не удалять. Прежний файл — копия в old/, запись атомарно и с версией. |
| 98 | `storage.upload_file` | app/services/action_layer/builtin_wave5.py:_storage_upload (спека L505) | Добавляет файл в хранилище ноды проекта под новым именем с отметкой времени; существующие файлы не затрагиваются. | medium | local_safe | нет | keep | Локальная мутация проекта через внутренний API. (а)(б)(в) в обработчике нет. |
| 99 | `storage.clear_files` | app/services/action_layer/builtin_wave5.py:_storage_clear (спека L512) | Удаляет все файлы из хранилища ноды проекта. | high | destructive | (а) косвенно: `ctx.call("DELETE", …/storage/{node_key}/files)` | modify | Детектор видит HTTP DELETE. Хранилище ноды очищается через API. В R2 это не инструмент оркестратора. |
| 100 | `history.list_snapshots` | app/services/action_layer/builtin_wave5_history.py:_list_snapshots (спека L428) | Список снимков, которые слой сохранил перед правками закадра, покрытия, полей шота, файлов озвучки и музыки, мастер-промтов: что было, что стало и можно ли откатить. | none | read | нет | keep | Только чтение через внутренний API. Прямых (а)(б)(в) нет. |
| 101 | `history.rollback` | app/services/action_layer/builtin_wave5_history.py:_rollback (спека L437) | Возвращает прежнее значение из снимка тем же эндпоинтом, которым шла правка. | high | destructive | (а) прямо: при откате file_voice/file_music снова вызывает _backup_file в data/action_layer_snapshots/ (тот же именной allowlist) | modify | Откат нужен. Копия остаётся в каталоге снимков, файлы снимков не удаляются. Вердикт modify сохранён. |
| 102 | `publish.send` | app/services/action_layer/publish_action.py:_publish_send (спека L194) | Публикует готовый ролик проекта на площадки через профиль MoreLogin с подписью из темы проекта. | high | destructive | (в) косвенно: шаг publish ходит на площадки через MoreLogin; обработчик — только POST внутреннего API | keep | Штатная публикация студии под destructive и подтверждением человека. Обработчик не открывает сокет сам. Вопрос куратору — оставлять ли в whitelist оркестратора. |


## Инструменты правки кода и исполнения

В реестре 102 действий их нет. Проверены точные id: `edit_files`, `run_tests`, `exec`, `shell`, `write_file`, `fix_bugs`, а также вызовы `subprocess`, `eval`, `compile`, `exec`, `os.system` в телах исполнителей `app/services/action_layer/builtin_*.py` и `publish_action.py`.

Запрет оркестратору на правку кода студии (правило 8 плана) этим реестром не нарушается. Дыра — старый чат, не Action Layer.

Обёртка history (`shutil.copy2` в `_backup_file`) — это копирование файла данных, не запуск процесса. Она попала в modify по признаку (а), не по (б).

## §node_store — закрытый перечень (утверждён Heil)

**Граница закрыта.** Состав 15 мест ниже — решение куратора, не черновик. Патч R2 заменяет чтение на `node_store.resolve_node()`, не больше 20 строк на место (вызов + import). Патчи загрузчиков пайплайна (строки 1–3 и 9–15) ревьюит WS1. Строки 4–8 — код Action Layer #342, на R0a этих файлов нет. Любой другой файл `app/services/` не трогать.

Номера строк — с `r0a-pipeline-base` `a2409eef` и #342 `3ec7c957` (на `r0a-merge-fable` `72cdf3ee` те же). После тега `pipeline-base-v1` строки сверить заново; состав перечня от тега не зависит. Колонка «инв.» — номер строки в инвентаризации чтений ниже.

| # | инв. | Источник | Файл | Функция | Строка R0a | Строка #342 |
|---|---|---|---|---|---|---|
| 1 | 1 | оба | `app/services/prompt_library.py` | `read_prompt` | 587 | 514 |
| 2 | 2 | оба | `app/services/montage_action_gpt.py` | `load_action_prompt` | 173 | 168 |
| 3 | 3 | оба | `app/services/montage_scene_improve.py` | `load_group_prompt` | 216 | 196 |
| 4 | 4 | #342 | `app/services/change_control/targets.py` | `PromptHandler._effective` | — | 115 |
| 5 | 5 | #342 | `app/services/change_control/targets.py` | `PromptHandler.describe` | — | 128 |
| 6 | 6 | #342 | `app/services/change_control/targets.py` | `PromptHandler._variant_text` | — | 170 |
| 7 | 7 | #342 | `app/services/revisions.py` | `_file_entry` | — | 250 |
| 8 | 8 | #342 | `app/services/action_layer/builtin_wave3.py` | `_load_basis` | — | 355 |
| 9 | 9 | R0a | `app/services/frame_style.py` | `load_style_text` | 115 | — |
| 10 | 15 | оба | `app/services/scene_design/agents.py` | `load_prompt` | 257, 261, 266, 275 | 256, 260, 265, 274 |
| 11 | 26 | R0a | `app/services/scene_boundaries.py` | `load_prompt` | 53 | — |
| 12 | 27 | R0a | `app/services/scene_skeleton_batches.py` | `run_registry_prepass` | 451 | — |
| 13 | 28 | R0a | `app/services/nii67_agent.py` | `load_template` | 24 | — |
| 14 | 29 | R0a | `app/services/nii67_scenario.py` | `_read` | 72 | — |
| 15 | 30 | R0a | `app/services/style_agent/agents.py` | `load_prompt` | 53 | — |

Строка 10 — один патч функции `load_prompt`. Вызывающие (`scene_design/runner.py`, `skeleton.py`) файл сами не открывают и в перечень не входят.

В перечень не вошли строки инвентаризации **10, 11–14, 16–25, 31**. Строки **15 и 26–30** в перечне есть: это шесть загрузчиков в LLM (строки границы 10–15). Сопоставление номера инвентаризации с файлом:

| инв. | граница | файл | функция |
|---|---|---|---|
| 1 | да, №1 | `app/services/prompt_library.py` | `read_prompt` (ветка excel / node_groups) |
| 2 | да, №2 | `app/services/montage_action_gpt.py` | `load_action_prompt` |
| 3 | да, №3 | `app/services/montage_scene_improve.py` | `load_group_prompt` |
| 4 | да, №4 | `app/services/change_control/targets.py` | `PromptHandler._effective` |
| 5 | да, №5 | `app/services/change_control/targets.py` | `PromptHandler.describe` |
| 6 | да, №6 | `app/services/change_control/targets.py` | `PromptHandler._variant_text` |
| 7 | да, №7 | `app/services/revisions.py` | `_file_entry` |
| 8 | да, №8 | `app/services/action_layer/builtin_wave3.py` | `_load_basis` |
| 9 | да, №9 | `app/services/frame_style.py` | `load_style_text` |
| 10 | нет | `app/services/style_agent/apply.py` | `_write` (бэкап `shutil.copyfile`) |
| 11 | нет | `app/services/prompt_library.py` | `read_prompt` (ветка не-excel, `prompts/<шаг>`) |
| 12 | нет | `app/services/prompt_library.py` | `_excel_gpt_local_is_stale` |
| 13 | нет | `app/services/prompt_library.py` | `_coerce_hero_sheet_variant` |
| 14 | нет | `app/services/prompt_library.py` | `_hero_style_file_ok` |
| 15 | да, №10 | `app/services/scene_design/agents.py` | `load_prompt` |
| 16 | нет | `app/services/shot_menu.py` | `_set_catalog` |
| 17 | нет | `app/services/shot_templates.py` | `load_shot_templates` |
| 18 | нет | `app/services/node_groups.py` | `load_custom_groups` |
| 19 | нет | `app/services/node_groups.py` | `update_custom_group` |
| 20 | нет | `app/services/prompt_composer.py` | `list_block_catalog` |
| 21 | нет | `app/services/prompt_composer.py` | `write_step_template_blocks` |
| 22 | нет | `app/services/prompt_composer.py` | `list_style_presets` |
| 23 | нет | `app/services/prompt_composer.py` | `load_style_preset` |
| 24 | нет | `app/services/prompt_composer.py` | `_read_block` |
| 25 | нет | `app/services/prompt_composer.py` | `_read_template` |
| 26 | да, №11 | `app/services/scene_boundaries.py` | `load_prompt` |
| 27 | да, №12 | `app/services/scene_skeleton_batches.py` | `run_registry_prepass` |
| 28 | да, №13 | `app/services/nii67_agent.py` | `load_template` |
| 29 | да, №14 | `app/services/nii67_scenario.py` | `_read` |
| 30 | да, №15 | `app/services/style_agent/agents.py` | `load_prompt` |
| 31 | нет | `app/services/revisions.py` | `_tree_hash` |

### Дельта закрытого перечня против PLAN

PLAN §2.2.3 и §3.6 оценивали **2–4** call-site (`apply_ops_batches.py`, `scene_skeleton_batches.py`, общий prompt-loader). Утверждённый перечень — **15** мест. Это расширение исключения из freeze: не «2–4 файла», а 15 патчей, каждый не длиннее 20 строк. `apply_ops_batches.py` шаблоны не читает и в перечень не входит.

Номера строк — с пробного переноса PR #353 (ветка `cursor/ws2-r0b-dryrun-af2a`, tip `4cad50d2`). В тексте описания #353 таблицы строк нет; промежуточный коммит в том описании был `bf8e1d7a`. Ниже строки def и чтения `read_text` / `read_bytes` на tip пробного переноса.

| # | Файл | Функция | def | чтение | патч |
|---|---|---|---|---|---|
| 1 | `app/services/prompt_library.py` | `read_prompt` | 573 | 587, 599, 601 | ≤ 20 |
| 2 | `app/services/montage_action_gpt.py` | `load_action_prompt` | 169 | 173 | ≤ 20 |
| 3 | `app/services/montage_scene_improve.py` | `load_group_prompt` | 212 | 216 | ≤ 20 |
| 4 | `app/services/change_control/targets.py` | `PromptHandler._effective` | 112 | 115 | ≤ 20 |
| 5 | `app/services/change_control/targets.py` | `PromptHandler.describe` | 123 | 128 | ≤ 20 |
| 6 | `app/services/change_control/targets.py` | `PromptHandler._variant_text` | 159 | 170 | ≤ 20 |
| 7 | `app/services/revisions.py` | `_file_entry` | 246 | 250 | ≤ 20 |
| 8 | `app/services/action_layer/builtin_wave3.py` | `_load_basis` | 336 | 355 | ≤ 20 |
| 9 | `app/services/frame_style.py` | `load_style_text` | 111 | 115 | ≤ 20 |
| 10 | `app/services/scene_design/agents.py` | `load_prompt` | 243 | 257, 261, 266, 275 | ≤ 20 |
| 11 | `app/services/scene_boundaries.py` | `load_prompt` | 51 | 53 | ≤ 20 |
| 12 | `app/services/scene_skeleton_batches.py` | `run_registry_prepass` | 448 | 451 | ≤ 20 |
| 13 | `app/services/nii67_agent.py` | `load_template` | 23 | 24 | ≤ 20 |
| 14 | `app/services/nii67_scenario.py` | `_read` | 71 | 72 | ≤ 20 |
| 15 | `app/services/style_agent/agents.py` | `load_prompt` | 49 | 53 | ≤ 20 |

У `read_prompt` на пробном переносе к чтению R0a (`:587`) добавлены запасные чтения #342 (`:599`, `:601`). Один патч на функцию, не три.

### Снимок против main (история, не граница)

Искали в `app/services/` на main (`c53a238b`) и на #342 (`3ec7c957`) чтения `templates/node_groups/**` и `prompts/templates`. На main каталога `templates/node_groups` нет, `scene_skeleton_batches.py` нет, строки `prompts/templates` нет. `apply_ops_batches.py` на обоих деревьях шаблоны нод не читает. Ожидание плана («2–4 call-site») на main не выполнялось. На R0a загрузчики скелета, границ и `prompts/templates/nii67` уже есть — они вошли в утверждённую таблицу выше (строки 11–14).

### `prompts/templates` на main и #342

Строки `prompts/templates` нет ни на main, ни на #342. На R0a каталог есть, и его чтения входят в границу (строки 13–14 таблицы выше).

### `templates/node_groups/**` на main

Каталога нет. В `app/services/` нет ни одной ссылки на этот путь. `scene_skeleton_batches.py` нет ни на main, ни на #342. `apply_ops_batches.py` на обоих деревьях есть и **не читает** шаблоны нод: он разбирает ops.

Ожидание плана («2–4 call-site: `apply_ops_batches.py`, `scene_skeleton_batches.py`, общий prompt-loader») на main не выполняется: первых двух чтений нет, общий загрузчик читает другие деревья (см. смежные).

### Восемь чтений на #342 до решения куратора

Историческая таблица. Действующая граница — 15 строк в начале §node_store, не этот список.

Общий резолвер на #342: `prompt_library.node_group_prompts_dir` (`app/services/prompt_library.py:148`) возвращает `корень/templates/node_groups/<group_id>`. `resolve_script_frames_qc_prompt_path` (`:239`) для имён группы берёт этот каталог, а для `scene_skeleton_agent` и `action` — не его, а `prompts/scene_design/` (словарь `SCRIPT_FRAMES_QC_SHARED_PROMPTS`, `:130`).

| # | Файл | Функция | Строка | Как читается |
|---|---|---|---|---|
| 1 | `app/services/prompt_library.py` | `read_prompt` | 514 | `p.read_text`; `p` из `resolve_excel_gpt_prompt_path`. Для имён script_frames_qc кроме двух shared — файл в `templates/node_groups/script_frames_qc/`. Единая точка для `read_resolved_project_prompt` (`:884`) и `get_project_prompt` (`:972`) |
| 2 | `app/services/montage_action_gpt.py` | `load_action_prompt` | 168 | `resolve_excel_gpt_prompt_path("main_action_from_bits_ru", group_id="script_frames_qc").read_text` |
| 3 | `app/services/montage_scene_improve.py` | `load_group_prompt` | 196 | `resolve_script_frames_qc_prompt_path(name).read_text`. Вызовы: `:477` (`script_writer_ru`), `:1637` (`shots_qc_ru`) |
| 4 | `app/services/change_control/targets.py` | `PromptHandler._effective` | 115 | `path.read_text` после `resolve_excel_gpt_prompt_path` (`:105`) |
| 5 | `app/services/change_control/targets.py` | `PromptHandler.describe` | 128 | то же чтение текста промта |
| 6 | `app/services/change_control/targets.py` | `PromptHandler._variant_text` | 170 | `path.read_text` варианта |
| 7 | `app/services/revisions.py` | `_file_entry` | 250 | `path.read_bytes` (sha256). Зовёт `resolved_prompt_inputs` (`:282`), путь резолвится на `:279` |
| 8 | `app/services/action_layer/builtin_wave3.py` | `_load_basis` | 355 | `resolve_script_frames_qc_prompt_path(expected).read_text` — длина промта для `node.get_run_cost_basis`, не текст в LLM |

На main этих восьми мест нет: файлы `montage_action_gpt.py`, `montage_scene_improve.py`, `change_control/targets.py`, `node_rules.py` и каталог `action_layer/` отсутствуют, а `read_prompt` (`app/services/prompt_library.py:354`) и `resolved`-обёртка (`:589`) читают `prompts/<шаг>/` и при устаревшей локальной копии — `templates/excel_gpt_agents/` (`excel_gpt_template_dir`, `:123`). Ветки в `templates/node_groups` у main нет.

Рядом, но **не** в перечне (байты `templates/node_groups` не читают):

- `prompt_library.node_group_prompts_dir` (`:148`) и `script_frames_qc_prompt_relpath` (`:236`) — только собирают путь.
- `prompt_library.list_group_owned_prompts` (`:225`) — `is_file()`, без чтения текста.
- `prompt_library.write_prompt` (`:531–537`) — **пишет** `templates/node_groups` для имён группы. Это не чтение. В границу чтений запись не входит; сохранение из UI при версии в БД обходит файл (раздел «Контракт NodeDef»).
- `node_rules.PROMPT_DIR` (`app/services/node_rules.py:9`) и описание группы в `node_groups.py:441` — строки в метаданных, файл не открывается.
- `gpt_operator.collect_source_prompts` (`app/services/gpt_operator.py:247`) — зовёт `read_resolved_project_prompt`. Патчить надо `read_prompt`, не этот вызов.

### Смежные загрузчики на main и #342

Снимок до R0a. `load_prompt` из этой таблицы теперь в утверждённой границе (строка 10 §node_store). `prompts_dir` и `excel_gpt_template_dir` байты не читают. `prompt_composer` в границу не входит.

| Дерево | Файл | Функция | Строка | Откуда читает |
|---|---|---|---|---|
| main и #342 | `app/services/scene_design/agents.py` | `prompts_dir` | 195 | `prompts/scene_design` (`PROMPTS_SUBDIR`, `:13`) |
| main и #342 | `app/services/scene_design/agents.py` | `load_prompt` | 241 | сначала `step_dir("excel_gpt")/*.md` (`read_text` на `:255` и `:259`), иначе `prompts/scene_design/<agent>.md` (`:264`), плюс include (`:274`) |
| main и #342 | `app/services/scene_design/runner.py` | вызовы `load_prompt` | main: 364, 851; #342: 452, 949 | через `load_prompt` |
| main и #342 | `app/services/scene_design/skeleton.py` | вызов `load_prompt` | 1569 | скелет, `prompts/scene_design` или excel_gpt-вариант |
| main и #342 | `app/services/prompt_library.py` | `excel_gpt_template_dir` | 123 | `templates/excel_gpt_agents` |
| main и #342 | `app/services/prompt_composer.py` | корни | 33–36 | `prompts/blocks`, `prompts/steps`, `prompts/styles` — не `prompts/templates` |

`load_prompt` на #342 отличается от main на одну строку (diff `agents.py` — `1 insertion`). Путь чтения тот же.

## §db_browser — легаси чата (удаление в R2 / R6)

Файл на main: `app/web/routers/db_browser.py` (целиком владение WS2). На #342 тот же файл отличается на +97/−8 строк; режимы `fix_bugs` / `diagnose_only` и ключи `edit_files` / `run_tests` есть уже на main. Ниже — строки main.

Режим **`fix_bugs`** (удалить в R2 вместе с `edit_files` / `run_tests`):

- флаг запроса `fix_bugs: bool = False` — около `:2386`;
- детектор `_is_fix_bugs_mode` — `:819` (флаг API или маркер «режим фикс багов» в тексте);
- при включении в промпт уходит требование чинить через `edit_files` + `run_tests` — `:2454`;
- разбор JSON-действий модели — цикл с `:2523`.

Ключи кода, которые этот цикл исполняет (`_CODE_ACT_KEYS`, `:2517`):

| Ключ | Строки | Куда уходит |
|---|---|---|
| `read_file` | 2671–2698 | `app.services.code_autofix.read_file` |
| `edit_files` | 2699–2718 | `code_autofix.apply_edits` — запись файлов репозитория |
| `run_tests` | 2720–2745 | `code_autofix.run_tests` |
| `git_commit_push` | 2746–2790 | `app.services.git_ops.commit_and_push` |

`code_autofix.run_tests` (`app/services/code_autofix.py:191`) вызывает `subprocess.run` (`:203`). `code_autofix.py:5` импортирует `subprocess`. Это и есть запрещённая оркестратору обёртка исполнения. В реестр Action Layer она не входит. В R2 убирается вместе с `fix_bugs`, иначе дыра останется рядом с whitelist.

Режим **`diagnose_only`** (пометить deprecated в R2, удалить в R6):

- детектор `_is_diagnose_only_question` — `:827`. Если сообщение похоже на «почини код» или включён `fix_bugs`, режим не включается;
- при diagnose-only ключи из `_CODE_ACT_KEYS` пропускаются (`:2525`), в ответ не должны попадать правки кода;
- пост-фильтр пузыря — `:2825`.

Рядом в том же цикле остаются действия чата, не названные планом, но тоже опасные для оркестратора: `git_commit_push`, `delete_projects`, `remove_node`. Их судьбу надо подтвердить куратору (вопрос ниже). План явно требует убрать `fix_bugs` + `edit_files` / `run_tests` и позже весь `diagnose_only`.

## Конфликтная зона будущего rebase #342 на main

Снимок против main `c53a238b`. Предварительная цифра против `r0a-pipeline-base` — **23 файла**, см. раздел ниже. Этот раздел не переписывать задним числом: он фиксирует, с чем сравнивали до R0a.

Сравнение `git diff c53a238b 3ec7c957 -- app/orchestrator`. Реестр, approvals, spend и change_control лежат в `app/services/action_layer/` и `app/services/change_control/`, **не** в `app/orchestrator/`. В `app/orchestrator/` инфраструктура Action Layer почти не пересекается с main: новый файл один.

Правило §2.2.1: инфраструктура Action Layer — в пользу #342; логика пайплайна и вызовы сервисов — в пользу housepc/main (после R0a — в пользу уже влитого main). Каждый файл ниже всё равно смотрит человек.

| Файл | Кто добавил отличие | Куда решать |
|---|---|---|
| `app/orchestrator/node_spec.py` | только #342 (407 строк), на main файла нет | **#342**. Контракт ноды: `allowed_actions`, approval, managed. Это инфраструктура наблюдения/Action Layer, не логика шага. Конфликта содержимого с main нет, файл берётся целиком |
| `app/orchestrator/auto_advance.py` | оба; на #342 вставка `actions_v1.managed_project_enabled` / `check_managed_start` около `:795` | **смешанный**. Тело продвижения пайплайна — main/housepc. Хуки managed-режима — из #342, вручную, не затирать слепо |
| `app/orchestrator/pipeline.py` | оба; #342 открывает окно `balance_adapters` вокруг шага | **main/housepc** для хода шагов. Вставку учёта баланса сверить отдельно: это не реестр действий |
| `app/orchestrator/graph/planner.py` | оба; развилка excel_gpt / overflow | **main/housepc**. Это граф канваса, не planner Action Layer (`app/services/action_layer/planner.py` на main отсутствует и при rebase приезжает целиком из #342) |
| `app/orchestrator/steps/enrich_xlsx.py` | оба, крупнейший diff; на #342 вызов `actions_v1.continue_managed_image` около `:971` | **смешанный**: логика enrich — main/housepc, вызов managed — кусок #342 |
| `app/orchestrator/steps/publish.py` | оба; на #342 `actions_v1.get_permission` около `:359` | **смешанный**: сборка публикации — main/housepc, проверка permission — #342 |
| `app/orchestrator/steps/assemble.py` | оба | **main/housepc** |
| `app/orchestrator/steps/generate_audio.py` | оба | **main/housepc** |
| `app/orchestrator/steps/generate_hero.py` | оба | **main/housepc** |
| `app/orchestrator/steps/generate_image_prompts.py` | оба | **main/housepc** |
| `app/orchestrator/steps/generate_images.py` | оба | **main/housepc** |
| `app/orchestrator/steps/generate_items.py` | оба | **main/housepc** |
| `app/orchestrator/steps/generate_music.py` | оба | **main/housepc** |
| `app/orchestrator/steps/generate_sfx.py` | оба | **main/housepc** |
| `app/orchestrator/steps/generate_videos.py` | оба | **main/housepc** |
| `app/orchestrator/steps/make_animation_prompts.py` | оба | **main/housepc** |
| `app/orchestrator/steps/make_script.py` | оба | **main/housepc** |
| `app/orchestrator/steps/plan_sfx.py` | оба | **main/housepc** |
| `app/orchestrator/steps/scene_design.py` | оба | **main/housepc** |
| `app/orchestrator/steps/split_frames.py` | оба | **main/housepc** |

Файлы `app/orchestrator/`, одинаковые на обеих головах (в diff не вошли, конфликта нет): `__init__.py`, `default_graph.py`, `node_registry.py`, `graph/__init__.py`, `graph/validate.py`, `steps/__init__.py`, `steps/make_plan.py`. Уникальных файлов только на main в этом каталоге нет.

До rebase R0a в main ещё нет housepc-правок `app/orchestrator/*`. Когда R0a вольётся, конфликт надо пересчитать против тега `pipeline-base-v1`, не против сегодняшнего `c53a238b`: housepc мог разойтись с этим main в тех же step-файлах.

`app/services/**` в этот аудит как конфликт rebase не разбирался построчно: freeze сервисов — зона WS1 до конца R0a. Исключение R2 — 15 мест в §node_store.

## Расхождения и вопросы куратору

1. Число действий **102**, не «около». Источник расхождения «103 в исходнике / 102 в реестре» — снятый `node.run_step` из `builtin_group.py`. В таблицу он не включён.
2. План §6.1 пишет, что в 102 уже есть `get_project_state`, `list_nodes` / `get_node`, `get_scenes` / `get_shots`, `get_run_logs`, `get_spend_report`. **Их нет.** Ближайшие чтения: `node.get_config`, `node.get_llm_route`, `group.list`, `group.get`, `shot.get_menu`, `group.shots_report`, `montage.get_board`, `node.get_run_cost_basis`, `step.get_run_cost_basis`, `montage.get_cost_basis`, `history.list_snapshots`. Нужны ли отдельные действия под именами плана, или достаточно этих?
3. План §6.2 говорит, что `run_node` уже есть в реестре #342 (`POST .../steps/excel_gpt/run?node_key=&only_node=true&mode=resume`). Имени `run_node` нет. Роль выполняют `node.run_step` (excel_gpt, платно, destructive) и `step.run_step` (шаг конвейера). `stop_node` / `resume_node` тоже нет: стоп ближе к `project.stop` и `kill_switch.engage`, продолжение — к `project.resume` / `project.continue_pipeline` / `node.run_step` с resume. Подтвердить, что 11 новых действий плана садятся рядом, а не переименовывают эти id.
4. Закрыто Heil. В границу вошли строки инвентаризации 1–9 и загрузчики в LLM: `scene_design/agents.load_prompt` (один патч), `scene_boundaries.load_prompt`, `scene_skeleton_batches.run_registry_prepass`, `nii67_agent.load_template`, `nii67_scenario._read`, `style_agent/agents.load_prompt`. Не вошли composer, `shot_templates`, `shot_menu`, пользовательские `node_groups/*.json`, бэкап стиля, зонд hero и `_tree_hash`. `apply_ops_batches.py` по-прежнему не читает шаблоны.
5. Закрыто вместе с контрактом UI. `write_prompt` в перечень чтений не входит. Если у ноды есть версия в БД, сохранение промта из студии не зовёт `write_prompt`, а пишет новую версию через `node_store/writer.py` (раздел «Контракт NodeDef»). Пока версии в БД нет, файл git остаётся базой только для чтения.
6. Закрыто. `data/action_layer_snapshots/` — allowlist только для `montage.upload_voice`, `montage.upload_music`, `history.rollback` (поле шапки `snapshot_allow`). Другие действия туда не пишут. Эти три копируют файл и не удаляют снимки. Вердикт modify у них сохранён: запись вне `data/reports/` и `data/node_overrides/`, каталог именной. `shutil.rmtree` запрещён на любом пути.
7. `publish.send` оставлен keep: обработчик не ходит в сеть сам, но шаг публикует ролик на площадки. Оставлять в whitelist оркестратора?
8. `media.release_unknown` оставлен keep (признаков exec нет), но снимает блокировку неизвестного исхода платной отправки. Не забрать ли его у оркестратора отдельно от критерия §2.2.2?
9. В `db_browser.py` рядом с `edit_files` / `run_tests` исполняются `git_commit_push`, `delete_projects`, `remove_node`. План называет только `fix_bugs` и два ключа. Удалять ли весь набор кодовых ключей одним коммитом R2?
10. Rebase #342 на текущий main затронет 19 общих файлов `app/orchestrator/` плюс новый `node_spec.py`. Предварительный пересчёт против `r0a-pipeline-base` (`a2409eef`), а не против main: **23 файла** (22 изменённых + `node_spec.py` только на #342). Три файла, одинаковые с main, с R0a уже расходятся: `default_graph.py`, `node_registry.py`, `steps/make_plan.py`. Финальная цифра — после тега `pipeline-base-v1`. Инфраструктура слоя (`app/services/action_layer/**`, change_control, spend) на main и на R0a отсутствует и приезжает из #342 без пары — это не конфликт содержимого, пока housepc туда не писал.

## Инвентаризация чтений по `r0a-pipeline-base`

Состав границы node_store утверждён (таблица §node_store). Предварительными остаются только номера строк (сверить после тега `pipeline-base-v1`) и конфликтная зона rebase ниже. До тега патчи `app/services/` не ставятся: строки могут сдвинуться, когда R0a войдёт в main.

Деревья сверки (2026-10-17):

- R0a: `origin/r0a-pipeline-base`, sha `a2409eef1e1b12645d109b4d949e5dbf311d9c80`.
- `origin/r0a-merge-fable`, sha `72cdf3ee0c316b5095d8e50de3d90ece6646bb57`. Это не merge-коммит: единственный родитель — `a2409eef`. В `app/orchestrator/` коммит ничего не меняет. В `app/services/` меняется только `run_sync.py`. Номера строк чтений шаблонов и список конфликтов оркестратора на обеих ветках совпадают.
- #342: `3ec7c9579716a53ab119cabb340af562cba76b31`.
- Rebase #342 не делался.

В таблицах ниже — чтения **байт** в `app/services/` (вызов `read_text` / `read_bytes` / `shutil.copyfile` исходника). Конструкторы пути, `is_file()` и чистая запись в перечень не входят.

Утверждённая граница — не вся эта таблица. В неё входят строки **1–9, 15, 26, 27, 28, 29, 30**. Исключены: **10, 11–14, 16–25, 31**.

### Call-sites

| # | Источник | Класс | Файл | Функция | Строка R0a | Строка #342 | Как читается |
|---|---|---|---|---|---|---|---|
| 1 | оба | node_groups | `app/services/prompt_library.py` | `read_prompt` | 587 | 514 | `p.read_text` после `resolve_excel_gpt_prompt_path`. Имена группы кроме shared — `templates/node_groups/script_frames_qc/<имя>.md`. На R0a тот же вызов читает и `styles/style_*.md` (ветка резолвера `:288–290`). Shared уходят в `prompts/scene_design/`: на #342 это `scene_skeleton_agent` и `action`, на R0a к ним добавлен `scene_boundaries_agent` (`SCRIPT_FRAMES_QC_SHARED_PROMPTS`, R0a `:135`) |
| 2 | оба | node_groups | `app/services/montage_action_gpt.py` | `load_action_prompt` | 173 | 168 | `resolve_excel_gpt_prompt_path("main_action_from_bits_ru", group script_frames_qc).read_text`. Вызов: R0a `:217`, на #342 рядом с определением |
| 3 | оба | node_groups | `app/services/montage_scene_improve.py` | `load_group_prompt` | 216 | 196 | `resolve_script_frames_qc_prompt_path(name).read_text`. Вызовы R0a: `:506` (`script_writer_ru`), `:1699` (`shots_qc_ru`). На #342 те же вызовы: `:477` и `:1637` |
| 4 | #342 | node_groups | `app/services/change_control/targets.py` | `PromptHandler._effective` | — | 115 | `path.read_text` после `resolve_excel_gpt_prompt_path` (`:105`). Каталога `change_control/` на R0a нет |
| 5 | #342 | node_groups | `app/services/change_control/targets.py` | `PromptHandler.describe` | — | 128 | то же чтение текста промта |
| 6 | #342 | node_groups | `app/services/change_control/targets.py` | `PromptHandler._variant_text` | — | 170 | `path.read_text` варианта |
| 7 | #342 | node_groups | `app/services/revisions.py` | `_file_entry` | — | 250 | `path.read_bytes` (sha256). Зовёт `resolved_prompt_inputs` (`:282`), путь резолвится на `:279`. Если резолвер отдал файл группы — хешируется `templates/node_groups`. Файла `revisions.py` на R0a нет |
| 8 | #342 | node_groups | `app/services/action_layer/builtin_wave3.py` | `_load_basis` | — | 355 | `resolve_script_frames_qc_prompt_path(expected).read_text` — длина для `node.get_run_cost_basis`, не текст в LLM. Каталога `action_layer/` на R0a нет |
| 9 | R0a | node_groups | `app/services/frame_style.py` | `load_style_text` | 115 | — | `styles_dir()` (`:64`) = `templates/node_groups/script_frames_qc/styles/`, затем `p.read_text`. `list_style_names` (`:88`) только `glob`/`is_file`, в перечень не входит. Файла на #342 нет |
| 10 | R0a | node_groups | `app/services/style_agent/apply.py` | `_write` | 61 | — | `shutil.copyfile` существующего файла в `.bak` перед записью (`:63` — уже запись). Корень стилей по умолчанию — `frame_style.styles_dir()` (`MaterializeRoots.default`, `:50`). Не загрузчик LLM |
| 11 | оба | другой | `app/services/prompt_library.py` | `read_prompt` | 591 | 528 | ветка не-excel: `prompts/<шаг>/<имя>.md`. На #342 при отсутствии `default` есть запасное чтение `:526` |
| 12 | оба | другой | `app/services/prompt_library.py` | `_excel_gpt_local_is_stale` | 344 | 296 | первые 1200 символов локальной копии `prompts/05_excel_gpt`, чтобы выбрать git-шаблон `templates/excel_gpt_agents`. Это не файл `templates/node_groups` |
| 13 | оба | другой | `app/services/prompt_library.py` | `_coerce_hero_sheet_variant` (`_ok`) | 755 | 675 | `prompts/hero/<вариант>.md` — проверка, что текст похож на лист, не загрузка в LLM |
| 14 | оба | другой | `app/services/prompt_library.py` | `_hero_style_file_ok` | 785 | 705 | `prompts/hero_style/<вариант>.md`, та же проверка |
| 15 | оба | другой | `app/services/scene_design/agents.py` | `load_prompt` | 257, 261, 266, 275 | 256, 260, 265, 274 | вариант excel_gpt, затем `sd_<agent>.md` в каталоге шага excel_gpt, затем `prompts/scene_design/<agent>.md` (`PROMPTS_SUBDIR`, `:13`), плюс include (`camera` → `camera_sets.md`) |
| 16 | оба | другой | `app/services/shot_menu.py` | `_set_catalog` | 238 | 238 | `prompts/scene_design/camera_sets.md` |
| 17 | оба | другой | `app/services/shot_templates.py` | `load_shot_templates` | 46 | 40 | `templates/shot_templates/shot_templates.json` (`_REL`: R0a `:27`, #342 `:21`) |
| 18 | оба | другой | `app/services/node_groups.py` | `load_custom_groups` | 719 | 640 | `node_groups/*.json` в корне репо (`custom_groups_dir`: R0a `:566`). Это пользовательские группы, не `templates/node_groups` |
| 19 | оба | другой | `app/services/node_groups.py` | `update_custom_group` | 773 | 694 | тот же JSON, чтение перед перезаписью |
| 20 | оба | другой | `app/services/prompt_composer.py` | `list_block_catalog` | 249 | 251 | `prompts/blocks/<категория>/<имя>.md` |
| 21 | оба | другой | `app/services/prompt_composer.py` | `write_step_template_blocks` | 310 | 312 | читает первую строку существующего `prompts/steps/<id>/template.md` (функция ещё и пишет файл) |
| 22 | оба | другой | `app/services/prompt_composer.py` | `list_style_presets` | 352 | 354 | `prompts/styles/*.json` |
| 23 | оба | другой | `app/services/prompt_composer.py` | `load_style_preset` | 364 | 366 | `prompts/styles/<id>.json` |
| 24 | оба | другой | `app/services/prompt_composer.py` | `_read_block` | 373 | 375 | `prompts/blocks` |
| 25 | оба | другой | `app/services/prompt_composer.py` | `_read_template` | 380 | 382 | `prompts/steps/<id>/template.md`. Обёртка `read_step_template` сама файл не открывает |
| 26 | R0a | другой | `app/services/scene_boundaries.py` | `load_prompt` | 53 | — | `prompts/scene_design/scene_boundaries_agent.md` (`PROMPT_REL` `:30`, путь `:45`). Файла на #342 нет |
| 27 | R0a | другой | `app/services/scene_skeleton_batches.py` | `run_registry_prepass` | 451 | — | `prompts/scene_design/scene_skeleton_registry_prepass.md` (`PREPASS_REL` `:398`). Мастер скелета приходит аргументом в `run_skeleton_batches` (`:523`) и здесь с диска не читается. `:151` и `:160` — флаг и трасса в `data/`, не шаблон. Вызов модуля — `app/orchestrator/steps/enrich_xlsx.py` (вне `app/services/`). Файла на #342 нет |
| 28 | R0a | другой | `app/services/nii67_agent.py` | `load_template` | 24 | — | `prompts/templates/nii67/v1/<имя>.json` (`TEMPLATE_DIR` `:17`). Это и есть дерево `prompts/templates`, которого не было на main и на #342 |
| 29 | R0a | другой | `app/services/nii67_scenario.py` | `_read` | 72 | — | `prompts/templates/nii67/v2/<имя>` (`TEMPLATE_DIR` `:24`). Зовёт `load_v2` (`:76`) |
| 30 | R0a | другой | `app/services/style_agent/agents.py` | `load_prompt` | 53 | — | `prompts/style_agent/<role>.md`. Корень `PROMPTS_DIR` (`:32`) — путь от cwd, не `find_project_root` |
| 31 | #342 | другой | `app/services/revisions.py` | `_tree_hash` | — | 242 | sha256 всех `*.md`/`*.json`/`*.txt` каталога. Вызовы: `prompts/{blocks,steps,styles,step-presets}` (`:288`), `PROMPTS_ROOT` и `excel_gpt_template_dir()` → `templates/excel_gpt_agents` (`:295–296`). Сам `templates/node_groups` эта функция не обходит |

Итого **31** чтение байт: класс `node_groups` — 10 (из них на обоих деревьях 3, только #342 — 5, только R0a — 2), класс `другой` — 21.

Не вошло в таблицу (байты шаблона не читают):

- `prompt_library.node_group_prompts_dir` (R0a `:154`, #342 `:148`), `script_frames_qc_prompt_relpath` (R0a `:261`, #342 `:229`), `excel_gpt_template_dir` (оба `:123`) — только путь.
- `list_group_owned_prompts` — `is_file()` (R0a `:255`, #342 `:225`).
- `prompt_library.write_prompt` — запись, не чтение. На R0a `:599` пишет стиль, `:604` пишет промт группы; на #342 запись группы — `:537`. В freeze-чтения не входит. Обход при сохранении из UI — раздел «Контракт NodeDef».
- `node_rules.PROMPT_DIR` и строки описания в `node_groups.py` — метаданные, файл не открывается.
- `apply_ops_batches.py` есть на обоих деревьях и шаблоны нод не читает.
- `scene_design/runner.py` и `skeleton.py` зовут `load_prompt`, сами файл не открывают. Патчить надо `load_prompt`.

### Конфликты rebase #342 против `r0a-pipeline-base`

Сравнение `git diff a2409eef 3ec7c957 -- app/orchestrator`.

**Конфликтных файлов: 23.** Из них 22 с разным содержимым на обеих головах и 1 файл только на #342 (`node_spec.py`, 407 строк). Файлов только на R0a в этом каталоге нет. Одинаковые (в diff не вошли): `app/orchestrator/__init__.py`, `graph/__init__.py`, `graph/validate.py`, `steps/__init__.py`. Сводка diff: 23 файла, +2662 / −1260 строк в направлении R0a → #342.

Против main (`c53a238b`) в diff было 20 путей (19 общих + `node_spec.py`). Три файла, которые с main совпадали, с R0a уже расходятся, потому что сами разошлись main и R0a (`git diff c53a238b a2409eef`: `default_graph.py` 2 строки, `node_registry.py` 13, `steps/make_plan.py` 23). Коммит `72cdf3ee` этот набор не меняет.

Правило §2.2.1 то же: инфраструктура Action Layer — в пользу #342; логика пайплайна и вызовы сервисов — в пользу дерева R0a (после тега это будет main). Реестр по-прежнему живёт в `app/services/action_layer/`, на R0a его нет, поэтому почти весь `app/orchestrator/` — пайплайн.

| Файл | Откуда отличие против R0a | Куда решать |
|---|---|---|
| `app/orchestrator/node_spec.py` | только #342 | **#342**. Контракт ноды (`allowed_actions`, approval, managed). Конфликта содержимого нет |
| `app/orchestrator/default_graph.py` | новое против R0a; против main файл был одинаковый. Подпись музыки: R0a «GPT + Suno», #342 «GPT + Suno (Outsee)» | **R0a**. Это подпись шага, не инфраструктура слоя |
| `app/orchestrator/node_registry.py` | новое против R0a; против main был одинаковый. R0a держит `hitl_style`, `STYLE_AGENT_NODE_TYPES` (`style_extract`, `hitl_style`) и `is_style_agent_node_type`. #342 это снимает и добавляет `sfx_plan` / `sfx_gen` в `LINEAR_NODE_TYPES`. Спеки SFX в реестре R0a уже есть; пропадает именно членство в линейном списке | **смешанный**. Слепой выбор R0a убирает SFX из линейного списка. Слепой выбор #342 убирает типы агента стиля. Сводить руками |
| `app/orchestrator/steps/make_plan.py` | новое против R0a; против main был одинаковый. #342 переносит `write_general` выше снимка xlsx и пишет статус константой `plan_ready` до `flush`; R0a пишет лист после установки `project.status` | **R0a**. Логика шага плана, не Action Layer |
| `app/orchestrator/auto_advance.py` | оба; на #342 вставка `actions_v1.managed_project_enabled` / `check_managed_start` | **смешанный**. Ход пайплайна — R0a. Хуки managed — из #342, вручную |
| `app/orchestrator/pipeline.py` | оба; #342 открывает окно `balance_adapters` вокруг шага | **R0a** для хода шагов. Вставку учёта баланса сверить отдельно |
| `app/orchestrator/graph/planner.py` | оба | **R0a**. Граф канваса, не planner Action Layer |
| `app/orchestrator/steps/enrich_xlsx.py` | оба, крупнейший diff (в направлении R0a → #342 файл теряет сотни строк логики R0a; на #342 около `:971` вызов `actions_v1.continue_managed_image`) | **смешанный**. Тело enrich — R0a, вызов managed — кусок #342. Победа файла #342 целиком сотрёт пайплайн R0a в этом шаге |
| `app/orchestrator/steps/publish.py` | оба; на #342 `actions_v1.get_permission` | **смешанный**. Сборка публикации — R0a, проверка permission — #342 |
| `app/orchestrator/steps/assemble.py` | оба | **R0a** |
| `app/orchestrator/steps/generate_audio.py` | оба | **R0a** |
| `app/orchestrator/steps/generate_hero.py` | оба | **R0a** |
| `app/orchestrator/steps/generate_image_prompts.py` | оба | **R0a** |
| `app/orchestrator/steps/generate_images.py` | оба | **R0a** |
| `app/orchestrator/steps/generate_items.py` | оба | **R0a** |
| `app/orchestrator/steps/generate_music.py` | оба | **R0a** |
| `app/orchestrator/steps/generate_sfx.py` | оба | **R0a** |
| `app/orchestrator/steps/generate_videos.py` | оба | **R0a** |
| `app/orchestrator/steps/make_animation_prompts.py` | оба | **R0a** |
| `app/orchestrator/steps/make_script.py` | оба | **R0a** |
| `app/orchestrator/steps/plan_sfx.py` | оба | **R0a** |
| `app/orchestrator/steps/scene_design.py` | оба | **R0a** |
| `app/orchestrator/steps/split_frames.py` | оба | **R0a** |

`app/services/**` построчно как зона rebase здесь не разбирался. Исключение R2 — 15 мест §node_store. Номера строк сверить после тега, состав мест уже закрыт.

## Контракт NodeDef и правки из UI

Решение куратора, реализация в R2. Код в этом PR не пишется.

`resolve_node` и действие `get_node` возвращают один `NodeDef`. Набор полей фиксирует WS3 в `app/orchestrator/contracts.py` (PR #346):

| Поле | Смысл |
|---|---|
| `source` | `"git"` или `"db"` |
| `db_version` | номер версии в БД |
| `git_sha256` | sha256 базового git-файла, от которого собрана версия |
| `stale_base` | git-файл изменился после создания версии в БД |

Полей `overridden_in_db` и `active_version` нет. Короткий набросок плана (`source` + `version` в §3.6) этим набором заменён.

Если у ноды есть версия в БД (`source = "db"`), эндпоинт сохранения промта из UI студии не пишет файл. Он создаёт новую версию через `node_store/writer.py` и возвращает номер версии. UI показывает «сохранено как vN+1». Git-шаблон остаётся базой только для чтения.

### Эндпоинты, которые WS2 переводит на writer

Пишут git-файл промта через `write_prompt_with_history` → `write_prompt` (`prompt_library.py`: R0a `:594`, #342 `:531`). Префикс роутеров — `/api`. Одинаковы на `r0a-pipeline-base` и на #342, если строка не помечена.

| Эндпоинт | Роутер / функция | Строка R0a | Строка #342 | Кто зовёт из UI |
|---|---|---|---|---|
| `PUT /api/prompt-files/{step}/{name}` | `prompt_files.save_prompt_file` | 274 | 254 | `PromptFilesPanel`: R0a `:199` и `:368`, #342 `:195`. `node-prompt-block.tsx` только на R0a, `:60`. `studio-workspace.tsx` создаёт пустой файл слота: R0a `:428`, #342 `:412` |
| `POST /api/prompt-files/{step}/upload` | `prompt_files.upload_prompt_file` | 436 | 416 | `PromptFilesPanel` (`api.uploadPromptFile`): R0a `:312`, #342 `:308` |
| `POST /api/prompt-files/{step}/{name}/history/{version_id}/restore` | `prompt_files.restore_prompt_file_history` → `prompt_history.restore_prompt_version` (`:307`, внутри `write_prompt_with_history` `:309`) | 387 | 367 | `PromptFilesPanel` (`api.restorePromptFileVersion`): R0a `:256`, #342 `:252` |
| `POST /api/prompt-studio/projects/{id}/gpt-text/{step}/save-template` | `prompt_studio.save_gpt_text_as_template` (вызов `write_prompt_with_history` `:245`) | 211 | 211 | `gpt-text-panel.tsx` (`api.saveGptTextAsTemplate`), оба дерева, `:56` |

На #342 тот же `write_prompt` зовёт ещё `change_control/targets.py:259` (применение предложения, не клик студии). Это не UI-эндпоинт; в R2 цель промта уходит в node_store вместе с `change.apply`.

Три панели из задачи сами `write_prompt` не вызывают.

`node-step-params-panel.tsx` сохраняет параметры шага в `meta` проекта, не файл промта:

- оба дерева: `api.patchProject` → `PATCH /api/projects/{id}` → `projects.patch_project` (R0a `:461`, #342 `:480`). Вызов панели: R0a `:583`, #342 `:674`. В meta кладётся `withNodeStepParams` (план, сценарий, split, audio, assemble).
- только R0a: `StreamsPicker` в том же файле, `:666`, пишет `meta.img_streams` / `meta.outsee_streams` тем же `patch_project`.

`excel-gpt-settings-panel.tsx`:

- оба дерева: `api.patchGptOperator` → `PATCH /api/projects/{id}/gpt-operator/{node_key}` → `project_ops.gpt_operator_patch` (R0a `:2115`, #342 `:2146`). Вызов панели: R0a `:43`, #342 `:56`. Пишет конфиг оператора в `meta`, не git-шаблон.
- оба дерева: `POST` / `DELETE /api/projects/{id}/gpt-operator/{node_key}/check-agent` → `upload_check_agent_file` / `delete_check_agent_file` (R0a `:2333` / `:2360`, #342 `:2360` / `:2391`). Это загруженный файл агента проверки проекта, не шаблон репозитория.
- только #342: `api.patchProject` на `:143` и `:161` — имя варианта слота (`prompt_slot_variants`), не тело промта.

`node-result-panel.tsx` на обоих деревьях API не вызывает. Он рисует `NodeResultViewBody` из `node-result-views.tsx`. Тело пишет поля проекта, не шаблон ноды: `PATCH /api/projects/{id}` с `script_text` (R0a `:326`, #342 `:497`) и на #342 ещё `general_plan` (`:287`), плюс загрузка xlsx и замена картинки героя. На writer это решение их не переводит.

Рядом в студии пишутся другие промт-файлы. Этим решением они на writer не переводятся (их чтения в границу не вошли):

- `PUT /api/prompt-studio/step-template/{step_id}` → `prompt_studio.save_step_template` (`:521` на обоих) — `prompts/steps/<id>/template.md`, зовёт `step-blocks-editor.tsx`.
- `POST /api/prompt-studio/projects/{id}/gpt-verdict/{step}/save-template` → `save_gpt_verdict_as_template` (`:752`) — `prompts/check_<step>/`, зовёт `gpt-verdict-panel.tsx`.
- `PUT /api/prompt-studio/projects/{id}/gpt-text/{step}` → `save_project_gpt_text` (`:185`) — override сопроводительного текста в проекте, git-файл не пишет. Зовёт `gpt-text-panel.tsx`.
- `PATCH /api/prompt-studio/projects/{id}/prompt-config` → `patch_project_prompt_config` (`:621`) — overrides блоков проекта. Зовут `blocks-v2-toggle.tsx` и `blocks-weight-panel.tsx`.

### Новые действия R2

К списку плана §6.3 добавляется действие куратора. Остальные новые id плана этот аудит не переписывает.

#### `revert_node_to_git` (risk: medium, tier: destructive)

Действие вне реестра 102. История версий не удаляется, git-файл не пишется. Ярус destructive: активная версия ноды в БД деактивируется.

```json
{"params": {
   "node_key": {"type": "string", "required": true},
   "reason": {"type": "string", "required": true}},
 "tier": "destructive",
 "effects": [
   "change_control: деактивирует версию ноды в БД",
   "история версий не удаляется",
   "git-файл не пишется и не откатывается"
 ],
 "semantics": "следующий resolve_node / get_node возвращает source=\"git\". db_version не указывает на активную версию. git_sha256 и stale_base считаются от текущего базового файла.",
 "rollback": "change_control: снова активировать ту же версию в БД"}
```

## Что этот документ не делает

Rebase #342, перенос кода в main, патчи `app/services/`, smoke на :8766 и тег `action-layer-v1` — это сам R0b после R0a. Здесь таблица действий, закрытый перечень из 15 мест, инвентаризация чтений, контракт NodeDef и тест-заготовка `tests/actions/test_whitelist_no_exec.py`. Пока реестра в main нет, тест реестра пропускается; детектор exec проверяется на локальных заглушках и падать не должен.

## Прогон детектора по всем 102 действиям

Снято с дерева #342 `3ec7c957` анализатором `tests/actions/test_whitelist_no_exec.py` (`reachable_write_hits`) после трёх блокеров: ссылка на опасную функцию не в позиции вызова, недоказанные HTTP/SQL, пары `fs_breach`. Повторный прогон всех 102 исполнителей не добавил denied-пар. В `fs_breach` по-прежнему только пять `http.delete`.

Сменился вердикт у одного действия: `montage.delete_child_frame` keep → modify. Детектор его не видит (`POST …/coverage` с `type=coverage_delete`, не HTTP DELETE). В modify оно из-за физического удаления шота. Ещё девять действий переведены в modify по столбцу «Что делает» (удаление, стирание, перезапись файла или записи): детектор HTTP DELETE их не видит, вердикт всё равно modify.

Ложные срабатывания, которые детектор считает denied, а живой реестр ими не задет: `http.unknown` (метод `call`/`request` не сворачивается в константу), `sql.unknown` (SQL из переменной, f-строки с подстановкой или конкатенации не из двух констант), `executemany` с таким SQL, ссылка на `os.remove`/`shutil.rmtree` и остальные опасные функции аргументом, присваиванием или return, `getattr` на `subprocess`/`os`/`shutil`/`pty`, `os.open` с `O_WRONLY`/`O_RDWR`/`O_CREAT`/`O_TRUNC`/`O_APPEND` или с недоказанными флагами вне allowlist. Разрешены доказуемые GET, HEAD, OPTIONS, POST, PUT, PATCH и доказуемый читающий SQL (`SELECT`/`WITH`/`EXPLAIN`/`PRAGMA`/`SHOW` без слов записи). Склейка двух строковых констант сворачивается и классифицируется. Ячейка «нет» значит, что этих попаданий у исполнителя нет.

| Действие | Вердикт | Найденные вызовы и пути | Причина |
|---|---|---|---|
| `change.apply` | keep | нет | Детектор не нашёл запись, удаление, SQL DELETE/DROP/TRUNCATE и HTTP DELETE. |
| `change.get` | keep | нет | Детектор не нашёл запись, удаление, SQL DELETE/DROP/TRUNCATE и HTTP DELETE. |
| `change.list` | keep | нет | Детектор не нашёл запись, удаление, SQL DELETE/DROP/TRUNCATE и HTTP DELETE. |
| `change.propose` | keep | нет | Детектор не нашёл запись, удаление, SQL DELETE/DROP/TRUNCATE и HTTP DELETE. |
| `change.rollback` | keep | нет | Детектор не нашёл запись, удаление, SQL DELETE/DROP/TRUNCATE и HTTP DELETE. |
| `group.delete` | modify | http.delete путь не доказан (denied) | только через подтверждение и пометку (soft delete), без физического удаления, с восстановлением |
| `group.get` | keep | нет | Детектор не нашёл запись, удаление, SQL DELETE/DROP/TRUNCATE и HTTP DELETE. |
| `group.insert` | keep | нет | Детектор не нашёл запись, удаление, SQL DELETE/DROP/TRUNCATE и HTTP DELETE. |
| `group.list` | keep | нет | Детектор не нашёл запись, удаление, SQL DELETE/DROP/TRUNCATE и HTTP DELETE. |
| `group.save_from_selection` | modify | нет | Вердикт прежний: POST пишет node_groups в git. Детектор HTTP DELETE этого вызова не видит. |
| `group.shots_report` | keep | нет | Детектор не нашёл запись, удаление, SQL DELETE/DROP/TRUNCATE и HTTP DELETE. |
| `group.update` | modify | нет | Вердикт прежний: PUT переписывает JSON группы в git. Нового запрета детектора нет. |
| `hero.replace_image` | modify | нет | Описание: файл перезаписывается без архива. Вердикт modify: копия в old/ и версия. Детектор HTTP DELETE не видит. |
| `history.list_snapshots` | keep | нет | Детектор не нашёл запись, удаление, SQL DELETE/DROP/TRUNCATE и HTTP DELETE. |
| `history.rollback` | modify | Path.mkdir {settings.data_dir}/action_layer_snapshots/dyn (allowed); shutil.copy2 {settings.data_dir}/action_layer_snapshots/dyn/dyn (allowed) | Запись внутри allowlist или каталога снимков для разрешённого id. |
| `hitl.approve` | keep | нет | Детектор не нашёл запись, удаление, SQL DELETE/DROP/TRUNCATE и HTTP DELETE. |
| `hitl.list_pending` | keep | нет | Детектор не нашёл запись, удаление, SQL DELETE/DROP/TRUNCATE и HTTP DELETE. |
| `hitl.regenerate` | keep | нет | Детектор не нашёл запись, удаление, SQL DELETE/DROP/TRUNCATE и HTTP DELETE. |
| `hitl.reject` | keep | нет | Детектор не нашёл запись, удаление, SQL DELETE/DROP/TRUNCATE и HTTP DELETE. |
| `kill_switch.engage` | keep | нет | Детектор не нашёл запись, удаление, SQL DELETE/DROP/TRUNCATE и HTTP DELETE. |
| `massfactory.start` | keep | нет | Детектор не нашёл запись, удаление, SQL DELETE/DROP/TRUNCATE и HTTP DELETE. |
| `media.get_recovery_assessment` | keep | нет | Детектор не нашёл запись, удаление, SQL DELETE/DROP/TRUNCATE и HTTP DELETE. |
| `media.list_unknown_outcomes` | keep | нет | Детектор не нашёл запись, удаление, SQL DELETE/DROP/TRUNCATE и HTTP DELETE. |
| `media.release_unknown` | keep | нет | Детектор не нашёл запись, удаление, SQL DELETE/DROP/TRUNCATE и HTTP DELETE. |
| `media.resume_poll` | keep | нет | Детектор не нашёл запись, удаление, SQL DELETE/DROP/TRUNCATE и HTTP DELETE. |
| `montage.align_audio` | keep | нет | Детектор не нашёл запись, удаление, SQL DELETE/DROP/TRUNCATE и HTTP DELETE. |
| `montage.apply_queue` | keep | нет | Детектор не нашёл запись, удаление, SQL DELETE/DROP/TRUNCATE и HTTP DELETE. |
| `montage.apply_status` | keep | нет | Детектор не нашёл запись, удаление, SQL DELETE/DROP/TRUNCATE и HTTP DELETE. |
| `montage.cancel_queue_ops` | keep | нет | Детектор не нашёл запись, удаление, SQL DELETE/DROP/TRUNCATE и HTTP DELETE. |
| `montage.delete_child_frame` | modify | нет | только через подтверждение и пометку (soft delete), без физического удаления, с восстановлением |
| `montage.delete_frame` | modify | http.delete путь не доказан (denied) | только через подтверждение и пометку (soft delete), без физического удаления, с восстановлением |
| `montage.delete_image` | modify | нет | Описание: запись картинки удаляется. Вердикт modify: пометка и версия, файл уже в old/. |
| `montage.delete_manual_ref` | modify | нет | Описание: файл удаляется с диска. Вердикт modify: копия в old/ и пометка. |
| `montage.delete_video` | modify | нет | Описание: запись видео удаляется. Вердикт modify: пометка и версия, файл уже в old/. |
| `montage.explode_scene` | keep | нет | Детектор не нашёл запись, удаление, SQL DELETE/DROP/TRUNCATE и HTTP DELETE. |
| `montage.generate_scene` | keep | нет | Детектор не нашёл запись, удаление, SQL DELETE/DROP/TRUNCATE и HTTP DELETE. |
| `montage.generate_scene_images` | keep | нет | Детектор не нашёл запись, удаление, SQL DELETE/DROP/TRUNCATE и HTTP DELETE. |
| `montage.get_board` | keep | нет | Детектор не нашёл запись, удаление, SQL DELETE/DROP/TRUNCATE и HTTP DELETE. |
| `montage.get_cost_basis` | keep | нет | Детектор не нашёл запись, удаление, SQL DELETE/DROP/TRUNCATE и HTTP DELETE. |
| `montage.improve_scene` | keep | нет | Детектор не нашёл запись, удаление, SQL DELETE/DROP/TRUNCATE и HTTP DELETE. |
| `montage.insert_frame` | keep | нет | Детектор не нашёл запись, удаление, SQL DELETE/DROP/TRUNCATE и HTTP DELETE. |
| `montage.link_ref` | keep | нет | Детектор не нашёл запись, удаление, SQL DELETE/DROP/TRUNCATE и HTTP DELETE. |
| `montage.list_ref_assets` | keep | нет | Детектор не нашёл запись, удаление, SQL DELETE/DROP/TRUNCATE и HTTP DELETE. |
| `montage.merge_scenes` | keep | нет | Детектор не нашёл запись, удаление, SQL DELETE/DROP/TRUNCATE и HTTP DELETE. |
| `montage.montage_status` | keep | нет | Детектор не нашёл запись, удаление, SQL DELETE/DROP/TRUNCATE и HTTP DELETE. |
| `montage.move_image` | keep | нет | Детектор не нашёл запись, удаление, SQL DELETE/DROP/TRUNCATE и HTTP DELETE. |
| `montage.queue_ops` | keep | нет | Детектор не нашёл запись, удаление, SQL DELETE/DROP/TRUNCATE и HTTP DELETE. |
| `montage.recover_outsee` | keep | нет | Детектор не нашёл запись, удаление, SQL DELETE/DROP/TRUNCATE и HTTP DELETE. |
| `montage.recover_status` | keep | нет | Детектор не нашёл запись, удаление, SQL DELETE/DROP/TRUNCATE и HTTP DELETE. |
| `montage.run_montage` | keep | нет | Детектор не нашёл запись, удаление, SQL DELETE/DROP/TRUNCATE и HTTP DELETE. |
| `montage.set_anchors` | keep | нет | Детектор не нашёл запись, удаление, SQL DELETE/DROP/TRUNCATE и HTTP DELETE. |
| `montage.set_assemble_params` | keep | нет | Детектор не нашёл запись, удаление, SQL DELETE/DROP/TRUNCATE и HTTP DELETE. |
| `montage.set_coverage` | keep | нет | Детектор не нашёл запись, удаление, SQL DELETE/DROP/TRUNCATE и HTTP DELETE. |
| `montage.set_scene_chain` | keep | нет | Детектор не нашёл запись, удаление, SQL DELETE/DROP/TRUNCATE и HTTP DELETE. |
| `montage.set_send_to_main_pc` | keep | нет | Детектор не нашёл запись, удаление, SQL DELETE/DROP/TRUNCATE и HTTP DELETE. |
| `montage.set_video_trim` | keep | нет | Детектор не нашёл запись, удаление, SQL DELETE/DROP/TRUNCATE и HTTP DELETE. |
| `montage.set_voiceover` | keep | нет | Детектор не нашёл запись, удаление, SQL DELETE/DROP/TRUNCATE и HTTP DELETE. |
| `montage.set_voiceover_span` | keep | нет | Детектор не нашёл запись, удаление, SQL DELETE/DROP/TRUNCATE и HTTP DELETE. |
| `montage.split_scene` | keep | нет | Детектор не нашёл запись, удаление, SQL DELETE/DROP/TRUNCATE и HTTP DELETE. |
| `montage.suggest_scene_variants` | keep | нет | Детектор не нашёл запись, удаление, SQL DELETE/DROP/TRUNCATE и HTTP DELETE. |
| `montage.swap_shots` | keep | нет | Детектор не нашёл запись, удаление, SQL DELETE/DROP/TRUNCATE и HTTP DELETE. |
| `montage.swap_slots` | keep | нет | Детектор не нашёл запись, удаление, SQL DELETE/DROP/TRUNCATE и HTTP DELETE. |
| `montage.unlink_ref` | keep | нет | Детектор не нашёл запись, удаление, SQL DELETE/DROP/TRUNCATE и HTTP DELETE. |
| `montage.upload_image` | keep | нет | Детектор не нашёл запись, удаление, SQL DELETE/DROP/TRUNCATE и HTTP DELETE. |
| `montage.upload_music` | modify | Path.mkdir {settings.data_dir}/action_layer_snapshots/dyn (allowed); shutil.copy2 {settings.data_dir}/action_layer_snapshots/dyn/dyn (allowed) | Запись внутри allowlist или каталога снимков для разрешённого id. |
| `montage.upload_ref` | keep | нет | Детектор не нашёл запись, удаление, SQL DELETE/DROP/TRUNCATE и HTTP DELETE. |
| `montage.upload_video` | keep | нет | Детектор не нашёл запись, удаление, SQL DELETE/DROP/TRUNCATE и HTTP DELETE. |
| `montage.upload_voice` | modify | Path.mkdir {settings.data_dir}/action_layer_snapshots/dyn (allowed); shutil.copy2 {settings.data_dir}/action_layer_snapshots/dyn/dyn (allowed) | Запись внутри allowlist или каталога снимков для разрешённого id. |
| `node.clear_check_agent` | modify | http.delete путь не доказан (denied) | HTTP DELETE: удаление через внутренний API. Вердикт modify, id в fs_breach. |
| `node.get_config` | keep | нет | Детектор не нашёл запись, удаление, SQL DELETE/DROP/TRUNCATE и HTTP DELETE. |
| `node.get_llm_route` | keep | нет | Детектор не нашёл запись, удаление, SQL DELETE/DROP/TRUNCATE и HTTP DELETE. |
| `node.get_run_cost_basis` | keep | нет | Детектор не нашёл запись, удаление, SQL DELETE/DROP/TRUNCATE и HTTP DELETE. |
| `node.reset_from_step` | modify | нет | Описание: стирает результаты шага. Вердикт modify: версия или копия в old/, не физическое стирание. |
| `node.run_step` | modify | нет | Описание: перезаписывает выход ноды. Вердикт modify: новая версия, прежний текст ячеек не стирать. |
| `node.set_config` | keep | нет | Детектор не нашёл запись, удаление, SQL DELETE/DROP/TRUNCATE и HTTP DELETE. |
| `node.set_disabled` | keep | нет | Детектор не нашёл запись, удаление, SQL DELETE/DROP/TRUNCATE и HTTP DELETE. |
| `node.set_prompt_variant` | keep | нет | Детектор не нашёл запись, удаление, SQL DELETE/DROP/TRUNCATE и HTTP DELETE. |
| `node.upload_check_agent` | keep | нет | Детектор не нашёл запись, удаление, SQL DELETE/DROP/TRUNCATE и HTTP DELETE. |
| `node.upload_file` | modify | нет | Описание: прежние xlsx удаляются. Вердикт modify: копия в old/ и версия. |
| `project.continue_pipeline` | keep | нет | Детектор не нашёл запись, удаление, SQL DELETE/DROP/TRUNCATE и HTTP DELETE. |
| `project.create` | keep | нет | Детектор не нашёл запись, удаление, SQL DELETE/DROP/TRUNCATE и HTTP DELETE. |
| `project.create_child` | keep | нет | Детектор не нашёл запись, удаление, SQL DELETE/DROP/TRUNCATE и HTTP DELETE. |
| `project.patch_settings` | keep | нет | Детектор не нашёл запись, удаление, SQL DELETE/DROP/TRUNCATE и HTTP DELETE. |
| `project.pause` | keep | нет | Детектор не нашёл запись, удаление, SQL DELETE/DROP/TRUNCATE и HTTP DELETE. |
| `project.reload_xlsx` | modify | нет | Описание: поля перезаписываются из файла. Вердикт modify: новая версия, кадры не удалять. |
| `project.resume` | keep | нет | Детектор не нашёл запись, удаление, SQL DELETE/DROP/TRUNCATE и HTTP DELETE. |
| `project.set_auto_mode` | keep | нет | Детектор не нашёл запись, удаление, SQL DELETE/DROP/TRUNCATE и HTTP DELETE. |
| `project.stop` | keep | нет | Детектор не нашёл запись, удаление, SQL DELETE/DROP/TRUNCATE и HTTP DELETE. |
| `project.upload_xlsx` | modify | нет | Описание: кадры перезаписываются из файла. Вердикт modify: прежний xlsx в old/, запись версией. |
| `prompt.create_master` | modify | нет | Вердикт прежний: новый файл варианта промта в git. Детектор DELETE этого PUT не видит. |
| `prompt.delete_master` | modify | http.delete путь не доказан (denied) | только через подтверждение и пометку (soft delete), без физического удаления, с восстановлением |
| `prompt.get_master` | keep | нет | Детектор не нашёл запись, удаление, SQL DELETE/DROP/TRUNCATE и HTTP DELETE. |
| `prompt.save_master` | modify | нет | Вердикт прежний: PUT мастер-промта в git. Снимок пишет `_wrap_executor` (`builtin_wave5_history`, строки обёртки save_master). Откат сверяет текущий текст с `after` и отвечает 409, если после снимка текст уже меняли. DELETE нет. |
| `publish.send` | keep | нет | Детектор не нашёл запись, удаление, SQL DELETE/DROP/TRUNCATE и HTTP DELETE. |
| `shot.add_cell` | keep | нет | Детектор не нашёл запись, удаление, SQL DELETE/DROP/TRUNCATE и HTTP DELETE. |
| `shot.edit_cell_voiceover` | keep | нет | Детектор не нашёл запись, удаление, SQL DELETE/DROP/TRUNCATE и HTTP DELETE. |
| `shot.edit_field` | keep | нет | Детектор не нашёл запись, удаление, SQL DELETE/DROP/TRUNCATE и HTTP DELETE. |
| `shot.get_menu` | keep | нет | Детектор не нашёл запись, удаление, SQL DELETE/DROP/TRUNCATE и HTTP DELETE. |
| `step.get_run_cost_basis` | keep | нет | Детектор не нашёл запись, удаление, SQL DELETE/DROP/TRUNCATE и HTTP DELETE. |
| `step.run_step` | keep | нет | Детектор не нашёл запись, удаление, SQL DELETE/DROP/TRUNCATE и HTTP DELETE. |
| `storage.clear_files` | modify | http.delete путь не доказан (denied) | HTTP DELETE: удаление через внутренний API. Вердикт modify, id в fs_breach. |
| `storage.upload_file` | keep | нет | Детектор не нашёл запись, удаление, SQL DELETE/DROP/TRUNCATE и HTTP DELETE. |
