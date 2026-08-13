# Аудит кнопок Studio UI

> Дата: 2026-08-13 · ветка `cursor/pipeline-reliability-2af3` · аудит READ-ONLY (код не менялся).
> Метод: все `onClick` / `<button>` / `<Button>` / `role="button"` в `web/src/**/*.tsx`
> по областям; для каждой кнопки — handler → API (метод + путь + тело) → сверка с
> `app/web/routers/*.py` (+ `app/web/api.py` include_router, `app/web/schemas.py`).
>
> Статусы:
> - **P0** — endpoint не существует / несовпадение полей тела / гарантированный crash.
> - **P1** — деструктив без confirm (wipe/reset/delete); нет защиты от double-click;
>   ошибка не доходит до пользователя (нет catch/toast); optimistic UI без отката.
> - **P2** — нет loading на долгом действии; dead button; устаревшие CDP-ссылки;
>   несоответствие статусов/лейблов.

## Сводка по областям

| Область | Файлы | Кликабельных элементов (примерно)* |
|---|---|---|
| Sidebar (проекты, очередь, папки) | project-sidebar, new-project-wizard, gen-queue-dialog, sidebar-resize-handle | ~54 |
| Канвас: ноды, V-меню, тулбар, RunOverlay, edge-контролы | flow-canvas, pipeline-node, node-v-menu(+excel), node-palette, node-result-*, edge-*, ai-*, gpt-operator-*, excel-feed, storage, hero-config, group-* | ~222 |
| Inspector (проект/нода) | inspector, project-settings, streams, topic-editor, mass-factory, outsee-gen | ~17 |
| «База» (DB v2) | baza-workspace, baza-timeline | ~64 |
| «Монтаж» | assemble-montage-board, audio-align-dialog | ~53 |
| Create (Outsee/Grsai) | outsee-create-workspace | ~48 |
| «Промты» (конструктор блоков) | prompt-builder/* (29 файлов) | ~150 |
| GPT-чат | gpt-workspace | ~28 |
| Оркестратор | orchestrator-panel | ~22 |
| HITL | hitl-banner, media-frame-gallery, visual-hitl-gallery | ~28 |
| Node Studio + панели промтов | node-studio, prompt-files, excel-gpt-settings, node-step-params, asset-tray, gpt-text, gpt-verdict, check-node, frame-prompts, step-blocks, blocks-*, studio-excel-grid | ~142 |
| Header / версия / баги | topbar, studio-version-badge, bug-report-button, text-llm-picker | ~22 |
| Fleet (Сеть) | fleet-panel(-sheet), fleet-files, fleet-project-status, fleet-transfer-banner, montage-handoff | ~21 |
| Прочее | frames-grid, log-panel, prompts/prompt-editor | ~14 |

\* Сырые совпадения `onClick|<button|<Button` на файл; включают навигационные тумблеры
(вкладки, collapse-стрелки), которые проверены и помечены OK группами.

**Итоги: P0 = 2, P1 = 23, P2 = 10.**

---

## 1. Sidebar (список проектов, очередь)

| Кнопка | Файл:строка | Действие / API | Статус | Дефект + доказательство |
|---|---|---|---|---|
| Показать/скрыть панель | `web/src/components/sidebar/project-sidebar.tsx:464` | локальный state | OK | |
| «Новая папка» (иконка + OK) | `project-sidebar.tsx:522`, `:574` | `createFolderMutation` → `POST /api/sidebar-layout/folders` `{name}` → `routers/sidebar_layout.py` `@router.post("/folders")` | OK | disabled + isPending + toast |
| Удалить папку | `project-sidebar.tsx:717` | `confirm()` → `DELETE /api/sidebar-layout/folders/{id}` | OK | confirm с подсчётом проектов |
| Выбор проекта (строка) | `project-sidebar.tsx:917` | `onSelect` | OK | |
| Пин очереди (кружок-позиция) | `project-sidebar.tsx:936` | `toggleGenQueue` → `POST /api/sidebar-layout/gen-queue/toggle` `{project_id}` → `sidebar_layout.py:39,118` (поле `project_id: int` — совпадает) | OK | ошибка → toast; диалог enqueue при добавлении |
| Переименовать проект | `project-sidebar.tsx:1034` | `PATCH /api/projects/{id}` `{title}` → `projects.py:291` | OK | toast onError |
| Дочерний проект | `project-sidebar.tsx:1050` | `POST /api/projects/{id}/child` → `projects.py:220` | OK | `disabled={creatingChild}` |
| **Удалить проект** | `project-sidebar.tsx:1069` | `confirm()` → `deleteMutation` → `DELETE /api/projects/{id}` → `projects.py:278` | **P2** | confirm есть, но кнопка не `disabled` во время `deleteMutation.isPending` — двойной клик по OK → второй DELETE улетает в 404 (ошибка ловится тостом, но мусорный вызов) |
| Wizard «Далее/Создать» | `new-project-wizard.tsx:530` | `createProject` `POST /api/projects` `{title, hero_mode, auto_mode, sidebar_folder_id}` → `schemas.py:92 CreateProjectRequest` — поля совпадают; затем `PATCH /projects/{id}` + опц. `POST /generation-config-presets` | OK | `create.isPending` блокирует |
| **Удалить пресет конфигурации** | `new-project-wizard.tsx:354-363` | `deletePreset.mutate` → `DELETE /api/generation-config-presets/{id}` → `config_presets.py` `@router.delete("/{preset_id}")` | **P1** | удаление пресета одним кликом, **без confirm**; `disabled={deletePreset.isPending}` есть, но защита от случайного клика отсутствует |
| Очередь «Выполнить полностью» / «до ноды» | `gen-queue-dialog.tsx:158`, `:185` | `enqueueGenQueue` → `POST /api/sidebar-layout/gen-queue/enqueue` `{project_id, mode, target_node_key?, target_node_type?}` → `sidebar_layout.py:43,191` — поля совпадают | OK | `enqueue.isPending` + toast |

## 2. Канвас: тулбар, ноды, V-меню, RunOverlay

### 2.1 Тулбар (`flow-canvas.tsx`, WorkflowToolbar)

| Кнопка | Файл:строка | Действие / API | Статус | Дефект |
|---|---|---|---|---|
| «Добавить» (библиотека нод/групп) | `flow-canvas.tsx:1262` → `node-palette.tsx:144` | диалог; insert группы → `POST /api/projects/{id}/canvas/groups/{gid}` → `node_groups.py:125`; создание из выделения → `POST /api/projects/{id}/node-groups/from-selection` `{node_ids,title,description,category}` → `node_groups.py:89` (dict body — совпадает); удаление группы `DELETE /api/node-groups/{id}` с 2-шаговым confirm (`node-palette.tsx:707-738`) | OK | |
| «Сохранить граф» | `flow-canvas.tsx:1267` | `persistWorkflow` → `POST /api/workflows/validate` → `workflows.py` `@router.post("/validate")`, затем `PATCH /api/projects/{id}` `{meta.canvas_graph}` | OK | `saving` state + anti-double (persistInFlightRef) |
| Копировать/Вставить | `flow-canvas.tsx:1271`, `:1281` | localStorage clipboard + опц. `POST /excel-gpt/remap-keys` (catch с toast) | OK | |
| Дублировать граф вниз | `flow-canvas.tsx:1296` | локально + scheduleSave | OK | |
| Нода Excel (mass) | `flow-canvas.tsx:1305` | локальная нода | OK | |
| «WF» (копия workflow) | `flow-canvas.tsx:1311` | `POST /api/workflows/{id}/duplicate` | OK | try/catch→toast |
| **Удалить выделенные ноды** | `flow-canvas.tsx:1322` + `onNodesDelete:933` + клавиши Delete/Backspace | локальное удаление + autosave `canvas-save-workflow` → `PATCH /projects/{id}` | **P1** | удаление нод графа без confirm; autosave делает его сразу персистентным |

### 2.2 RunOverlay (правый верх канваса)

| Кнопка | Файл:строка | Действие / API | Статус | Дефект |
|---|---|---|---|---|
| Тумблер «Автопродвижение» | `project-settings.tsx:81-117` (AutoAdvanceToggle) | `PATCH /api/projects/{id}` `{auto_mode}` | OK | optimistic + откат в onError — образцово |
| «Доделка» ▾ (картинки/видео/промты анимации) | `flow-canvas.tsx:1553-1601` | `POST /api/projects/{id}/finish/images|videos|animation-prompts` → `project_ops.py:115/135/155` | OK | `finishBusy` + тосты |
| «Пауза» | `flow-canvas.tsx:1602` | `POST /api/projects/{id}/pause` → `project_ops.py:43` | OK | `pausing` guard |
| «Продолжить» | `flow-canvas.tsx:1612` | `POST /api/projects/{id}/continue` → `project_ops.py:68` | OK | |
| «⏹ Остановить текущий шаг» | `flow-canvas.tsx:1621` | `POST /api/projects/{id}/stop` → `project_ops.py:87` | OK | не деструктивно (откат running-шага); busy+toast |
| «Отмена run» | `flow-canvas.tsx:1633` | `stop` + `POST /api/runs/{id}/cancel` → `runs.py` | OK | |
| **«Сброс шага»** | `flow-canvas.tsx:1643` (handler `handleResetStep:1494`) | `POST /api/projects/{id}/steps/{code}/reset` → `project_ops.py:293` | **P1** | wipe результатов шага (PNG уходят в `old/scenes/<ts>`, но всё равно wipe) **без confirm**; busy-спиннер есть |
| «Создать Run / Перезапустить ▶» | `flow-canvas.tsx:1652` | `POST /api/runs/from-workflow/{wf}` `{project_id}` → `runs.py:63`; затем `POST /api/projects/{id}/steps/{code}/run?node_key=` → `projects.py:435` | OK | busy + ошибки в toast |

### 2.3 Нода (pipeline-node.tsx) и V-меню (node-v-menu.tsx)

| Кнопка | Файл:строка | Действие / API | Статус | Дефект |
|---|---|---|---|---|
| V-триггер (меню промтов) | `pipeline-node.tsx:167`, `:365` | открывает NodeVMenu | OK | |
| Бейдж результата | `pipeline-node.tsx:156` / `node-result-badge.tsx:46` | открывает NodeResultPanel | OK | |
| **X «Отсоединить вход»** (hover левого края) | `pipeline-node.tsx:588-608` | `canvas-detach-handle` → `flow-canvas.tsx:634` + autosave | **P1** | мгновенное снятие всех входящих связей + автосейв, без confirm |
| Триггер «Монтаж» над нодой assemble | `assemble-montage-board.tsx:2586` | открыть доску | OK | |
| V-меню: слоты промтов / «+ ещё» | `node-v-menu.tsx:295`, `:365` | `onAddPrompt` → `studio-workspace.tsx:396` → `PATCH /projects/{id}` `meta.custom_prompts` + `PUT /api/prompt-files/{step}/{name}` | OK | |
| V-меню: X на кастом-слоте | `node-v-menu.tsx:348-358` | `onRemovePrompt` → `studio-workspace.tsx:439` → `PATCH meta.custom_prompts` | **P2** | удаление слота-промта из схемы без confirm (файл остаётся — потеря только привязки) |
| V-меню: «Запустить шаг» | `node-v-menu.tsx:401` | `onRunNode` → `studio-workspace.tsx:455` → `POST /api/projects/{id}/steps/{code}/run?node_key=` (предварительно `xlsx/reload` + `PATCH excel-gpt`, оба с `.catch`) | OK | disabled-нода → toast |
| V-меню: «Файлы и превью» / «Просмотр Excel» | `node-v-menu.tsx:402-411` | AssetTray / студия | OK | |
| **V-меню: «Открепить связи»** | `node-v-menu.tsx:412` | `onDetachNode` → `studio-workspace.tsx:496` → `canvas-detach-node` (`autoSave:true`) | **P1** | сносит ВСЕ связи ноды с немедленным автосейвом, без confirm |
| V-меню: «Отключить/Включить ноду» | `node-v-menu.tsx:413` | `PATCH meta.disabled_nodes` | OK | обратимо, toast |
| **V-меню: «Удалить ноду»** | `node-v-menu.tsx:418-423` | `onDeleteNode` → `studio-workspace.tsx:487` → `canvas-delete-node` `autoSave:true` → `flow-canvas.tsx:658` | **P1** | удаление ноды из графа проекта без confirm, сразу персистится |
| Пульт оператора GPT (в V-меню и на карточке): Проверка/Чинить/Роль/Формат/emit/«От прошлых»/«Загрузить»/«Снимок»/«Пересверить» | `gpt-operator-menu.tsx:244-799`, `gpt-operator-card-panel.tsx:32` | `PATCH /api/projects/{id}/gpt-operator/{node_key}` / `GET …/resolve` / `POST …/check-agent` (UploadFile) / `DELETE …/check-agent` → `project_ops.py:1450,1462,1672,1699` | OK | все мутации с `patch.isPending` + toast; `Сбросить` агента — мягкий reset к builtin, обратимо |
| Edge: метка «связь/ок/не ок» | `edge-kind-controls.tsx:72-92,127-161` | optimistic set + `PATCH /api/projects/{id}/canvas-edges/{edge_id}` `{kind}` → `project_ops.py:1511` | **P2** | optimistic без отката: при ошибке API метка остаётся сменённой локально (toast об ошибке есть) |
| Edge: кружок ИИ-контроля | `edge-ai-controls.tsx:48-66` | открывает AiControlEdgeDialog | **P2 (dead)** | `EdgeAiControls` **никуда не примонтирован** (`flow-canvas.tsx` рендерит только `EdgeKindControls`, строка 1079) — кнопка недостижима |
| ИИ-проверка ноды (диалог) | `node-ai-review-dialog.tsx` | `PATCH meta.auto_review_kinds`, `GptVerdictPanel` | **P2 (dead)** | диалог примонтирован (`studio-workspace.tsx:728`), но триггер `onOpenAiReview` вызывается только из `node-ai-review-controls.tsx:83`, который не примонтирован; `NodeAiReviewBadge` не используется → весь вход в ИИ-проверку с канваса мёртв |
| Диалоги ИИ-контроля: тумблер «Каждая проверка — новое окно» | `ai-control-edge-dialog.tsx:166-177`, `node-ai-review-dialog.tsx:162-193` | `PATCH meta.ai_new_window_per_check` | **P2** | лейбл «Отдельная CDP-сессия для каждого HITL» — устаревший текст про браузерный стек (флаг живой: читается `app/services/mass_factory.py`); текст вводит в заблуждение |

### 2.4 Хранилище / Excel-feed / Hero на ноде

| Кнопка | Файл:строка | Действие / API | Статус | Дефект |
|---|---|---|---|---|
| Хранилище: «Обновить» | `storage-panel.tsx:183-198` | `POST …/storage/{node_key}/sync` → `project_ops.py:1896` | OK | |
| Хранилище: «Загрузить» | `storage-panel.tsx:199-213` | `POST …/storage/{node_key}/upload` (multipart) → `project_ops.py:1914` | OK | |
| Хранилище: «Скачать всё» | `storage-panel.tsx:215-237` | `GET …/storage/{node_key}/download.zip` → `project_ops.py:1949` | OK | disabled при пустом |
| **Хранилище: «Очистить»** | `storage-panel.tsx:238-248` | `clearStorageFiles` → `DELETE /api/projects/{id}/storage/{node_key}/files` → `project_ops.py:1936` | **P1** | удаляет все файлы хранилища одним кликом, **без confirm**; `disabled` по isPending есть |
| Excel-feed: «Загрузить» topics.xlsx | `excel-feed-panel.tsx:90-103` | `POST /api/projects/{id}/mass-lanes/parse-topics` (multipart) → `project_ops.py:175`; затем `PATCH meta` | OK | isPending + toast |
| Hero: «Загрузить из Excel/Перечитать» | `hero-config-panel.tsx:212-226` | `POST /api/projects/{id}/excel-hero/load` → `project_ops.py:335` | OK | |
| **Hero: «Сброс»** | `hero-config-panel.tsx:227-238` | `clearExcelHero` → `DELETE /api/projects/{id}/excel-hero` → `project_ops.py:411` | **P1** | отвязка персонажей без confirm (impact низкий — только unlink, файлы остаются) |
| Hero: «Сохранить руками» | `hero-config-panel.tsx:321-335` | `PATCH /projects/{id}` `{hero_count, hero_descriptions, hero_variations}` | OK | валидация ≥5 символов |

## 3. Inspector

| Кнопка | Файл:строка | Действие / API | Статус | Дефект |
|---|---|---|---|---|
| «Открыть студию ноды (GPT)» | `inspector.tsx:69-73` | навигация | OK | |
| «Открыть Генерацию» (Outsee) | `outsee-gen-panel.tsx:54-63` | `studio-open-outsee` event | OK | |
| Потоки: 1–4 / 0–4 / 0–10 | `streams-panel.tsx:179,197,215` | `PATCH /api/runtime-streams` `{worker_max_parallel}` → `runtime_streams.py`; `PATCH /projects/{id}` `{meta.img_streams/check_streams}` | OK | isPending + toast + hold отката |
| «Сохранить тему» | `topic-editor.tsx:58-66` | `PATCH /projects/{id}` `{topic}` | OK | |
| Mass: «Excel тем» | `mass-factory-panel.tsx:90-104` | `POST /mass-lanes/parse-topics` | OK | |
| Mass: «Запустить очередь» | `mass-factory-panel.tsx:105-118` | `POST /api/projects/{id}/mass-lanes/start` `{topics?}` → `project_ops.py:247` (dict payload, читает `topics` — совпадает) | OK | disabled пока нет тем |
| **«Отправить на главный ПК» (handoff)** | `montage-handoff-card.tsx:60-73` | `fleetPushToHub` → `POST /api/fleet/local/projects/{id}/push-to-hub` (`fleet-api.ts:75-80`) | **P0** | роута НЕТ в `app/web/routers/fleet.py` (нет `push-to-hub`; grep по `app/` пуст) → всегда 404, юзер видит сырой текст 404 в тосте |

## 4. «База» (baza)

| Кнопка | Файл:строка | Действие / API | Статус | Дефект |
|---|---|---|---|---|
| Закрыть / вкладки / виды (Хронология/Карточки/Зеркало Excel) / листы | `baza-workspace.tsx:341`, `:429-446`, `:471-520`; `baza-timeline.tsx:222-240,268-283,376` | локальный state | OK | |
| «Проверки» (HarnessChip) | `baza-workspace.tsx:639-676` | `POST /api/projects/{id}/harness/verify?include_http=false` → `project_ops.py:2212` (`include_http: bool = Query(True)` — параметр существует) | OK | busy-гвард |
| «Экспорт в Excel» | `baza-workspace.tsx:386-399` | `POST /api/db/projects/{id}/export-xlsx` → `db_browser.py` | OK | try/catch→toast |
| «Обновить» | `baza-workspace.tsx:400-409` | refetch | OK | |
| **«Добавить сцену»** | `baza-workspace.tsx:448-462` | `api.dbAddScene` → `POST /api/db/projects/{id}/scenes` `{title, after_scene_id?}` → `db_browser.py:3018 SceneBody` — совпадает | **P1** | `async onClick` **без try/catch** — при ошибке API молчаливый unhandled rejection; нет pending-гварда (двойной клик = две сцены) |
| **Карточка: «+» (вставить кадр после)** | `baza-workspace.tsx:780-795` | `dbInsertFrame` → `POST /api/db/projects/{id}/frames/insert` `{after_frame_id, scene_id}` → `db_browser.py:2805 InsertFrameBody` | **P1** | без catch → молчаливый фейл |
| Детали кадра: «✓» (статус+длительность) | `baza-workspace.tsx:1056-1072` | `dbPatchFrame` → `PATCH /api/db/frames/{id}` `{status, duration_seconds}` → `db_browser.py:2773 FramePatch` | OK | `save()` с catch |
| Поля DB (attrs) «сохранить» ×N | `baza-workspace.tsx:849-867,882-888` | `PATCH /api/db/frames/{id}` `{attrs}` | OK | через `save()` с catch |
| «Закадр»/«Смысл» — сохранить | `baza-workspace.tsx:1089,1095` (LabeledArea) | `PATCH …/frames/{id}` | OK | |
| Зеркало Excel — коммит ячейки | `baza-workspace.tsx:556-575` (StudioExcelGrid onCellCommit) | `PATCH /api/db/projects/{id}/sheet-cell` `{sheet,row,col,value}` → `db_browser.py:227 SheetCellBody` | OK | try/catch + re-throw в грид (грид держит редактирование) |
| **Тексты: «+» добавить / корзина удалить** | `baza-workspace.tsx:1111-1122` (delete), `:1142-1155` (add) | `POST /api/db/frames/{id}/texts` `{kind,text}` → `db_browser.py:2830`; `DELETE /api/db/texts/{id}` → `db_browser.py` `@router.delete("/texts/{text_id}")` | **P1** | оба без catch; delete ещё и без confirm |
| **Промты: «сделать активной» / «+» новая версия** | `baza-workspace.tsx:1176-1186`, `:1209-1222` | `POST /api/db/prompts/{id}/activate`; `POST /api/db/frames/{id}/prompts` `{kind,text,set_active}` → `db_browser.py:2862 PromptBody` | **P1** | без catch → молчаливый фейл (активация промта влияет на генерацию!) |
| **Связи: корзина / «+»** | `baza-workspace.tsx:1240-1251`, `:1279-1291` | `DELETE /api/db/edges/{id}`; `POST /api/db/frames/{id}/edges` `{to_frame_id, type}` → `db_browser.py:2980 EdgeBody` | **P1** | delete без confirm; оба без catch |
| **Сущности: «+» / корзина** | `baza-workspace.tsx:1415-1436`, `:1486-1496` | `POST /api/db/projects/{id}/entities` `{type,code,name,attrs}` → `db_browser.py:2919 EntityBody`; `DELETE /api/db/entities/{id}` | **P1** | оба без catch; delete без confirm |
| Лента хронологии: тумбы/свёртки/чипы полей | `baza-timeline.tsx` | read-only | OK | |

## 5. «Монтаж» (assemble-montage-board + audio-align)

| Кнопка | Файл:строка | Действие / API | Статус | Дефект |
|---|---|---|---|---|
| «Применить правки (N)» | `assemble-montage-board.tsx:2158-2175` | `POST /api/projects/{id}/montage-board/apply` `{video_trims, pending_ops}` → `project_ops.py:796` (dict body) | OK | `isPending/applyRunning` + прогресс done/total + WS |
| «Галерея Outsee (CDP)» | `assemble-montage-board.tsx:2176-2196` | `POST …/montage-board/recover-outsee` → `project_ops.py:916`; статус `GET …/recover-outsee-status` | OK (роут) / **P2** (лейбл) | роут существует; но текст «CDP-подбор» — ссылка на браузерный стек, который для текста выпилен; пользователю непонятно, нужен ли Chrome |
| «Разбор аудио» + методики + «Запустить» | `audio-align-dialog.tsx:111-126,198-206` | `GET /api/projects/audio-align/methods` → `project_ops.py:1997`; `POST /{id}/audio-align?method&force_asr&run_assemble` → `project_ops.py:2027`; статус `GET …/audio-align/status` | OK | busy + polling + тосты по терминалу |
| «Монтаж» | `assemble-montage-board.tsx:2206-2220` | `POST …/montage-board/montage` → `project_ops.py:881` | OK | `montageActive` + WS/poll |
| «Доп. функции»: голос/музыка с компьютера | `assemble-montage-board.tsx:297-316` | `POST …/montage-board/upload-voice` / `upload-music` (multipart) → `project_ops.py:1153,1182` | OK | try/catch в коллбеках |
| «Обновить» / «Повторить» (при ошибке) | `assemble-montage-board.tsx:2222-2236`, `:2264-2274` | refetch | OK | |
| Действия слота ▾ (Перегенерация/Редактировать промт/ИИзменение/с корректировкой) | `assemble-montage-board.tsx:472-486` | локальная очередь `queueOp` → debounce `POST …/montage-board/queue` `{pending_ops, video_trims?}` → `project_ops.py:724` | OK | |
| **Слот: корзина (удалить картинку/видео)** | `assemble-montage-board.tsx:487-496` → handlers `:1798` (image), `:1843` (video) | `POST …/montage-board/delete-image?frame_number&shot` → `project_ops.py:1055`; `POST …/delete-video` → `project_ops.py:1074` | **P1** ×2 | удаление файла с диска одним кликом по маленькой иконке, **без confirm** (картинка ещё и drag-сосед — промахнуться легко) |
| Слот: загрузить файл | `assemble-montage-board.tsx:497-517` | `POST …/upload-image|upload-video` | OK | |
| Слот: ↔ обмен | `assemble-montage-board.tsx:446-470` → `handleSwapPick:1885` | `POST …/montage-board/swap-slots?kind&a_frame&a_shot&b_frame&b_shot` → `project_ops.py:993` | OK | `swapBusy` + Esc отмена |
| Drag&drop картинки в ячейку | `assemble-montage-board.tsx:610-631` | `POST …/montage-board/move-image?…` → `project_ops.py:1025` | OK | `moveImageBusy` |
| Трим видео (dual slider) | `assemble-montage-board.tsx:798-887` | локально → уходит в `apply` | OK | |
| Промт-модалка «В очередь» | `assemble-montage-board.tsx:210-217` | очередь | OK | disabled без текста |

## 6. Create (outsee-create-workspace)

| Кнопка | Файл:строка | Действие / API | Статус | Дефект |
|---|---|---|---|---|
| Закрыть / ссылка outsee.io | `outsee-create-workspace.tsx:657`, `:683` | навигация | OK | |
| Тип Фото/Видео/Аудио; feed-фильтры | `:1026-1051`, `:787-802` | state | OK | |
| История: job в работе/ожидании → выбор | `:729-748`, `:762-781` | `GET /api/create/queue` → `create_queue.py:15` (поля `max_parallel*` на месте) | OK | |
| Карточки истории → превью | `:835-891` | `GET /api/outsee-create/history?kind&scope&limit` → `outsee_create.py:104` | OK | |
| «→ Старт / → Финиш» из истории | `:946-959` | локальный dataURL из `preview_url` (catch→toast) | OK | |
| Вложения (стартовый/конечный кадр) | `:1099-1167` | локальный FileReader | OK | |
| Модель-чип + picker | `:1192-1227` | state | OK | |
| Чипы aspect/resolution/detail/duration/качество/orientation/sora-size/audio/instrumental | `:1230-1383` | state | OK | |
| «Сохранить» (глобально) | `:1385-1392` | `PUT /api/outsee-create/settings` → `outsee_create.py:90` | OK | isPending |
| «В проект» | `:1393-1401` | `PUT settings` + `PATCH /api/projects/{id}` `{image_generator, aspect_ratio, image_resolution, image_quality, video_generator, video_resolution, …}` | OK | disabled без projectId |
| «Генерировать» | `:1409-1439` | outsee: `POST /api/outsee/generate` `{prompt, media, model, aspect, resolution, duration, generate_audio, project_id, first_frame_url, last_frame_url}` → `outsee_http.py:17 OutseeGenerateBody` — поля совпадают. grsai: `POST /api/grsai/generate` `{prompt, model, aspect, resolution, media, duration, size}` → `grsai.py:32 GrsaiGenerateBody` — совпадает. audio: `POST /api/projects/{id}/steps/{music|audio}/run`. Цена: `GET /api/grsai/quote?media&model&resolution&duration&size&catalog_price` → `grsai.py:120` — параметры совпадают | OK | isPending + явные ошибки про ключи |

## 7. «Промты» (конструктор блоков, prompt-builder)

| Кнопка | Файл:строка | Действие / API | Статус | Дефект |
|---|---|---|---|---|
| «Студия»/«Назад»/«Закрыть» | `prompt-builder-studio.tsx:1774,1802,1924,1952` | навигация | OK | |
| Undo/Redo | `:1520-1539` | локальные стеки + `PATCH /api/prompt-studio/projects/{id}/prompt-config` `{blocks, vars, use_blocks_v2, legacy}` → `prompt_studio.py:70 PromptOverridesPatch` — поля совпадают | OK | autosave 450мс + индикатор |
| «+» → Новый промт / Новый блок | `:1545-1574` | `POST …/step-presets/{step}/presets/{id}` / `POST /api/prompt-studio/blocks/{category}` `{block_id, content, message}` → `prompt_studio.py:113 BlockCreatePayload` | OK | window.prompt + catch→toast |
| Выбор/своп/перенос блоков, удаление слота | `block-editor-menu.tsx`, коллбеки `prompt-builder-studio.tsx:777-832` | autosave patch prompt-config | OK | |
| Контекст-меню блока: Просмотр/Дубликат/Редактировать/Переименовать | `block-context-menu.tsx:105-114` | `POST /blocks/{cat}` (дубликат), `POST /blocks/{cat}/{id}/rename` `{new_block_id}` → `prompt_studio.py:119` | OK | |
| Контекст-меню/бар блока: **Удалить** | `block-context-menu.tsx:125-146`, `block-action-bar.tsx:56-73` | `DELETE /api/prompt-studio/blocks/{cat}/{id}` | OK | **двухшаговый confirm** «Удалить?» + Отмена — образцово |
| Меню промта (⋮): Переименовать | `prompt-context-menu.tsx:64-76` → `prompt-builder-studio.tsx:1304` | `PATCH …/step-presets/{step}/presets/{id}` `{label}` → `prompt_studio.py:124` | OK | |
| **Меню промта (⋮): «Удалить промт»** | `prompt-context-menu.tsx:77-91` → `prompt-structure-graph.tsx:1215-1221` → `prompt-builder-studio.tsx:1317-1338` | `DELETE /api/prompt-studio/step-presets/{step}/presets/{preset_id}` → `prompt_studio.py` `@router.delete("/step-presets/...")` | **P1** | удаление пресета-промта одним кликом, **без confirm** (в отличие от удаления блока рядом — там confirm есть) |
| Превью блока: Редактировать/Сохранить | `prompt-builder-studio.tsx:1673-1705` | `PUT /api/prompt-studio/blocks/{cat}/{id}` `{content, message}` / `PUT /api/library/items/{id}` | OK | isPending |
| Превью: Скачать | `:1706-1712` | `GET /api/library/items/{id}/download` | OK | |
| Превью: История → версия | `:1723-1741` | `POST /api/library/items/{id}/restore/{version}` → `library.py` | **P2** | восстановление версии одним кликом без confirm (перезаписывает текущий текст блока; старая версия остаётся в истории — обратимо) |
| Применить пресет-промт (плитки) | `prompt-structure-graph.tsx:828` → `selectPromptVariant` | patch prompt-config | OK | undo есть |
| Назначение/снятие блока в пресете (рейл) | `prompt-builder-studio.tsx:1218-1285` | `PATCH …/presets/{id}` `{blocks:{kind:id|null}}` | OK | catch + refetch |
| Журнал блоков (BlockActivityPanel) | `block-activity-panel.tsx` | `GET /api/prompt-studio/block-activity`, `POST /blocks/sync` | OK | read-only + фоновой sync |
| Файлы промтов (PromptFilesPanel): Загрузить/Имя/Скачать/Сохранить/Сделать активным/История/↩ восстановить/Удалить | `prompt-files-panel.tsx:350-558`, `:639-755` | `GET/PUT/DELETE /api/prompt-files/{step}/{name}`, `POST …/upload`, `PATCH …/rename` `{new_name}`, `GET/POST …/history/{vid}[/restore]`, `PATCH …/history/{vid}` `{label}` → `prompt_files.py:48-390` | OK | confirm на удалении файла и на восстановлении версии; default защищён |
| Шаблон шага (StepBlocksEditor) | `step-blocks-editor.tsx:83-96` | `GET/PUT /api/prompt-studio/step-template/{step_id}` `{blocks:[{number,title,body}]}` → `prompt_studio.py:94` | OK | dirty-guard |
| BlocksV2Toggle / BlocksWeightPanel | `blocks-v2-toggle.tsx:62-71`, `blocks-weight-panel.tsx:87-95` | `PATCH …/prompt-config` | OK | |

### Мёртвый код в prompt-builder (P2, кластер)
`variant-aaa.tsx`, `variant-stack.tsx`, `variant-slots.tsx`, `variant-match.tsx`,
`neural-variant-picker.tsx`, `aaa-window.tsx`, `excel-impact-panel.tsx`
**не импортируются** живой цепочкой (`prompt-builder-studio.tsx`/`node-studio.tsx`/`studio-workspace.tsx`) —
старые экраны вариантов AAA/матрицы. Кнопки внутри недостижимы. Не ломают UI, но мусорят бандл.

## 8. GPT-чат (gpt-workspace)

| Кнопка | Файл:строка | Действие / API | Статус | Дефект |
|---|---|---|---|---|
| «Новый чат» | `gpt-workspace.tsx:458-466` | `POST /api/gpt-workspace/sessions` `{title}` → `gpt_workspace.py:20` | OK | |
| Выбор сессии | `:493-501` | `GET …/sessions/{id}` | OK | |
| **Удалить сессию** | `:503-510` | `gptDeleteSession` → `DELETE …/sessions/{id}` → `gpt_workspace.py` `@router.delete("/sessions/{session_id}")` | **P1** | удаление чата с историей/файлами без confirm |
| Отправить (➤ / Enter) | `:742-755`, `:732-737` | `POST …/sessions/{id}/ask` `{message, with_attachments}` → `gpt_workspace.py:28 AskBody` — совпадает | OK | busy + таймер |
| Прикрепить (скрепка/drag&drop) | `:705-717`, `:697-702` | `POST …/sessions/{id}/attachments` (multipart `file`/`files`) | OK | |
| Вложение: «В Результаты» | `:588-603` | `POST …/attachments/{name}/to-outputs` | OK | catch→toast |
| **Вложение: X (удалить)** | `:604-612` | `DELETE …/attachments/{name}` | **P1** | `void api.gptDeleteAttachment(...).then(...)` — **без `.catch`**: при ошибке молчаливо |
| «Скачать всё (.zip)» / скачать файл | `:675-683`, `FileChip` | `GET …/outputs.zip` | OK | |

## 9. Оркестратор (orchestrator-panel)

| Кнопка | Файл:строка | Действие / API | Статус | Дефект |
|---|---|---|---|---|
| Отправить / Enter | `orchestrator-panel.tsx:711-721`, `:695` | `POST /api/db/projects/{id}/orchestrator/chat` `{message, history, fix_bugs}` → `db_browser.py:2176 OrchestratorChatBody` — совпадает | OK | busy + abort |
| «Отмена» (прервать GPT) | `:490-504` | AbortController | OK | |
| «Фикс багов» тумблер | `:506-528` | state | OK | |
| Подтвердить удаление нод / проектов / push (+Отмена) | `:596-613`, `:631-648`, `:665-681` | `POST …/orchestrator/confirm-remove` `{node_key,node_type,only}` / `confirm-delete-projects` `{ids}` / `confirm-git-push` `{message, files}` → `db_browser.py:2695,2719,2740` — поля совпадают | OK | явный 2-кнопочный confirm, busy |
| Свернуть/развернуть панель | `:530-554` | state | OK | |

## 10. HITL

| Кнопка | Файл:строка | Действие / API | Статус | Дефект |
|---|---|---|---|---|
| Баннер «Одобрение: …» | `hitl-banner.tsx:80-92` | открыть модалку | OK | |
| Модалка: Одобрить/Перегенерировать/Отклонить/Изменить промт→Применить | `hitl-banner.tsx:222-292` | `POST /api/hitl/{id}/decision` `{decision, edited_prompt?}` → `hitl.py:55` + `schemas.py:245 HITLDecisionRequest` — совпадает; decision ∈ approve/regenerate/reject/edit_prompt | OK | `submit.isPending`; Enter=approve задокументирован |
| Галерея: «Отклонить все»/«Перегенерировать все»/«Одобрить все» | `visual-hitl-gallery.tsx:88-120` | bulk по pending (последовательные decision) | OK | `bulk.isPending` |
| Кадр: Отклонить/Одобрить | `media-frame-gallery.tsx:166-211` | per-frame decision (cherry-pick pending HITL кадра) | OK | disabled после решения |
| Кадр: ✎ редактировать промт → «Сохранить промт» | `media-frame-gallery.tsx:138-153`, `:222-233` | `PATCH /api/projects/{id}/frames/{frame_id}` `{image_prompt|animation_prompt}` → `frames.py` | OK | |

## 11. Node Studio и панели промтов

| Кнопка | Файл:строка | Действие / API | Статус | Дефект |
|---|---|---|---|---|
| «Запустить шаг» в шапке студии | `node-studio.tsx:580-599` | `POST /api/projects/{id}/steps/{code}/run?node_key=` | OK | `runStep.isPending` + disabled при nodeDisabled |
| Вкладки Настройки/Промты/Excel/Результаты + «Сопровод. текст» | `node-studio.tsx:603-639` | state | OK | |
| Excel: Скачать/Загрузить/Перечитать | `node-studio.tsx:664-703` | `GET …/xlsx`, `POST …/xlsx/upload` (multipart, `project_ops.py:471`), `POST …/xlsx/reload` (`:456`) | OK | isPending |
| Excel-нода настроек (роль/формат/emit/проверка/агенты/формат отчёта) | `excel-gpt-settings-panel.tsx:189-556` | `PATCH …/gpt-operator/{node_key}`, `GET …/check-prompt-preview`, `GET …/source-prompt`, `GET/POST/DELETE …/check-agent` | OK | toasts везде |
| GptTextPanel: Сохранить / Сохранить как шаблон / **Сбросить override** | `gpt-text-panel.tsx:161-196` | `PUT …/gpt-text/{step}` `{text}` / `POST …/gpt-text/{step}/save-template` `{name,text}` / `DELETE …/gpt-text/{step}` → `prompt_studio.py:79,83` | OK / **P2** | «Сбросить override» — DELETE без confirm, но включена только при `is_override` и восстанавливает автотекст (обратимо переписыванием) |
| GptVerdictPanel: Сохранить/Сохранить как/Удалить шаблон/Запустить проверку | `gpt-verdict-panel.tsx:367-529` | `POST …/gpt-verdict/{step}/save-template` `{name,content}` / `DELETE …/templates/{name}` / `POST …/gpt-verdict/{step}/run` `{prompt}` → `prompt_studio.py:664,668` | OK | удаление с `window.confirm`; run с isPending |
| CheckNodePromptPanel: просмотр промта/источников | `check-node-prompt-panel.tsx:69-80,125-140` | GET preview/source-prompt | OK | |
| NodeStepParamsPanel (длина/голос/сборка FFmpeg) | `node-step-params-panel.tsx:113-126,175-187,223-247,307-419,477-492` | `PATCH /projects/{id}` `{meta: withNodeStepParams(...)}` | OK | `saving` + toast |
| AssetTray: ←/→, редактировать/сохранить, «Одобрить» | `asset-tray.tsx:219-311` | `PATCH frames/{id}`; HITL approve через `listProjectHitl`+`submitHitlDecision` | OK | |
| **AssetTray: «Удалить»** | `asset-tray.tsx:312-320` | `onClick={() => toast.message("Удаление файла — через папку проекта")}` | **P2** | dead button by design — выглядит как действие, но лишь показывает тост; лучше `disabled` + title |
| FramesGrid: Save/Reset на кадр | `frames-grid.tsx:159-188` | `PATCH frames/{id}` `{voiceover_text, image_prompt, animation_prompt}` | OK | dirty-guard + isPending |
| LogPanel: Пауза/Копировать/Очистить | `log-panel.tsx:266-314` | локально (WS /ws/global) | OK | |

## 12. Header / версия / health

| Кнопка | Файл:строка | Действие / API | Статус | Дефект |
|---|---|---|---|---|
| «Генерация»/«Чат»/«Промты»/«База»/«Сеть»/«Логи» | `topbar.tsx:64-126` | window events / Sheet | OK | |
| «API» | `topbar.tsx:128-132` | `GET /api/docs` (FastAPI `docs_url="/api/docs"` — `api.py:220`) | OK | |
| «Баг» → «Сохранить и скачать» | `bug-report-button.tsx:101-110`, `:199-212` | `GET /api/bug-reports/preview?minutes` → `bug_reports.py:27`; `POST /api/bug-reports` `{description, minutes, projectId, projectSlug, studioVersion}` → `bug_reports.py:19 BugReportCreate` — **camelCase поля совпадают** | OK | disabled без описания + isPending |
| Версия-бейдж | `studio-version-badge.tsx:33` | `GET /api/studio-version` → `api.py:343` + `studio_version.py:79` (поля `build/sha/label/backend_git/ui_stale/pipeline_ok/text_llm_*` — все отдаются) | OK | опрос каждые 30с + на focus; amber при `ui_stale`/`pipeline_ok=false`; `catch(()=>undefined)` уместен для бейджа |
| **TextLlmPicker (селект модели)** | `text-llm-picker.tsx:40-55`, `:71-74` | `GET /api/text-llm` / `PUT /api/text-llm` `{provider, model_id}` → `text_llm.py:20,25` | **P2** | при `!r.ok` — молча (`setStatus` не вызывается, тоста/ошибки нет): юзер думает, что переключил, а значение не применилось |
| Health | — | `GET /api/health` → `api.py:339` — существует; UI не дёргает напрямую | OK | |

## 13. Fleet («Сеть»)

| Кнопка | Файл:строка | Действие / API | Статус | Дефект |
|---|---|---|---|---|
| Вход (форма) | `fleet-panel.tsx:151-183` | `POST /api/auth/login` `{username, password}` → `auth.py` `@router.post("/login")` | OK | ошибка в `loginError` |
| Обновить | `fleet-panel.tsx:196-198` | `GET /api/fleet/config` + `/api/fleet/nodes` → `fleet.py:959,141` | OK | loadError показывается |
| Выбор станции / «Перейти» | `fleet-panel.tsx:217-246`, `fleet-project-status.tsx:90-96` | навигация / `window.open(base_url)` | OK | |
| **«На монтаж»** (pull проекта на hub) | `fleet-panel.tsx:279-284` → `fleet-project-status.tsx:98-104` | `fleetPullProject` → `POST /api/fleet/nodes/{nid}/projects/{pid}/pull-to-main` `{run_assemble:true}` → `fleet.py:406` (роут есть) | **P1** | `void fleetPullProject(...).then(...)` — **без `.catch`**: сбой сети/500 → unhandled rejection, юзер не узнает |
| **«Sync»** | `fleet-panel.tsx:289-297` | `fleetSyncNode` → `POST /api/fleet/nodes/{id}/sync` → `fleet.py:207` | **P1** | без `.catch`; нет busy-индикации |
| Файлы станции (навигация/превью) | `fleet-files-panel.tsx:59-103` | `GET …/nodes/{id}/files?path`, `…/files/content` → `fleet.py:261,299` | OK | catch в `load` |
| Баннер передачи: X (остановить/закрыть) | `fleet-transfer-banner.tsx:77-91,105-118` | `onCancelTransfer` → `api.stopProject` (`page.tsx:137-143`) | OK | `.catch`→toast в баннере |
| **Баннер: «Отправить на главный ПК»** | `fleet-transfer-banner.tsx:154-170` ← `page.tsx:117-134` | `fleetPushToHub` → `POST /api/fleet/local/projects/{id}/push-to-hub` | **P0** | тот же несуществующий роут (см. §3) |
| **Polling активных передач** | `use-fleet-transfer.ts:131-157` | `fetch GET /api/fleet/transfers/active` (каждые 1–3 с) | **P0** | роута НЕТ в `fleet.py` (нет `transfers`) → вечный 404 в фоне; молча проглатывается (`catch {}`), на UI не видно, но это мёртвый сетевой цикл + шум в логах бэкенда |

## 14. Legacy / мёртвый код (P2-кластер)

| Элемент | Файл | Статус | Доказательство |
|---|---|---|---|
| PromptEditor (legacy «Мастер-промты» Sheet) | `prompts/prompt-editor.tsx` | **P2** | не импортируется нигде в `web/src` (grep `PromptEditor` — только self + комментарий в studio-workspace.tsx:223) |
| GptVerdictDialog | `studio/gpt-verdict-dialog.tsx` | **P2** | не импортируется (используется `GptVerdictPanel` напрямую) |
| EdgeAiControls / NodeAiReviewControls / NodeAiReviewBadge | `canvas/edge-ai-controls.tsx`, `node-ai-review-controls.tsx`, `node-ai-review-badge.tsx` | **P2** | не примонтированы; см. §2.3 |
| variant-aaa / variant-stack / variant-slots / variant-match / neural-variant-picker / aaa-window / excel-impact-panel | `prompt-builder/*` | **P2** | не в живой цепочке импортов (§7) |

---

## Топ-10 дефектов по критичности (НЕ фиксить — только предложения)

1. **P0 — «Отправить на главный ПК» всегда 404.**
   UI: `web/src/components/fleet/montage-handoff-card.tsx:60-73`, `fleet-transfer-banner.tsx:154-170`, `page.tsx:117-134` → `web/src/lib/fleet-api.ts:75-80` `POST /api/fleet/local/projects/{id}/push-to-hub`; в `app/web/routers/fleet.py` такого роута нет.
   Фикс: добавить роут `push-to-hub` в fleet.py (по образцу `mark-montage-ready`: собрать bundle + отправить на hub) ИЛИ убрать кнопку/`fleetPushToHub` до появления бэкенда.

2. **P0 — polling `GET /api/fleet/transfers/active` 404 вечно.**
   `web/src/hooks/use-fleet-transfer.ts:135` дёргается каждые 1–3 с; роута нет.
   Фикс: добавить эндпоинт (список активных transfer-джоб из in-memory реестра) или удалить poll-ветку (WS `fleet_transfer` уже покрывает обновления).

3. **P1 — «Сброс шага» без подтверждения.**
   `flow-canvas.tsx:1643-1651` → `POST /steps/{code}/reset` (`project_ops.py:293`) — wipe результатов шага.
   Фикс: `confirm(\`Сбросить шаг «…»? Результаты уйдут в backup old/scenes/…\`)` перед `resetProjectStep`.

4. **P1 — «Удалить ноду» / «Открепить связи» / «X на входе» без подтверждения и с немедленным autosave.**
   `node-v-menu.tsx:412,418-423` → `studio-workspace.tsx:487-501` → `flow-canvas.tsx:621-671`; тулбар `flow-canvas.tsx:1322` + Delete-клавиша.
   Фикс: confirm для V-меню «Удалить ноду» (нода + её связи сразу в `meta.canvas_graph`); для Delete-клавиши можно оставить как есть (Undo нет — оценить).

5. **P1 — «База»: все write-кнопки молчат при ошибке API.**
   `baza-workspace.tsx:448-462` (сцена), `:780-795` (кадр), `:1111-1122`/`:1142-1155` (тексты), `:1176-1186`/`:1209-1222` (промты, влияют на генерацию!), `:1240-1251`/`:1279-1291` (связи), `:1415-1436`/`:1486-1496` (сущности) — `async onClick` без try/catch; delete'ы ещё и без confirm.
   Фикс: обернуть в общий хелпер `runDbOp(fn, okMsg)` с toast.error; для трёх корзин — confirm.

6. **P1 — «Монтаж»: корзина удаляет файл с диска без confirm.**
   `assemble-montage-board.tsx:487-496` → `handleDeleteImage:1798` / `handleDeleteVideo:1843`.
   Фикс: `confirm(\`Удалить ${kind} кадра #N shot S с диска?\`)` в обоих handlers.

7. **P1 — «Удалить промт» в конструкторе без confirm (рядом блоки удаляются с confirm).**
   `prompt-context-menu.tsx:77-91` → `prompt-structure-graph.tsx:1215-1221` → `prompt-builder-studio.tsx:1317`.
   Фикс: добавить в `PromptContextMenu` двухшаговый confirm как в `BlockContextMenu` (`confirmDelete` state).

8. **P1 — Fleet «На монтаж» и «Sync» без обработки ошибок.**
   `fleet-panel.tsx:279-284`, `:289-297` — `void promise.then()` без `.catch`.
   Фикс: `.catch((e) => toast.error(...))` + busy-state на кнопке.

9. **P1 — Хранилище «Очистить» без confirm.**
   `storage-panel.tsx:238-248` → `DELETE …/storage/{node_key}/files` (`project_ops.py:1936`) — сносит все файлы ноды.
   Фикс: `confirm(\`Удалить ${allFiles.length} файлов хранилища?\`)`.

10. **P2 — TextLlmPicker молчит при неудаче переключения.**
    `text-llm-picker.tsx:48-51`: при `!r.ok` ничего не происходит.
    Фикс: `else toast.error(...)` + revert `value` на предыдущую модель.

### Почётные упоминания (P1/P2 вне топ-10)
- `new-project-wizard.tsx:354-363` — удаление пресета конфигурации без confirm (P1).
- `gpt-workspace.tsx:503-510` — удаление чата без confirm (P1); `:604-612` — удаление вложения без `.catch` (P1).
- `hero-config-panel.tsx:227-238` — «Сброс» Excel-персонажей без confirm (P1, низкий impact).
- `project-sidebar.tsx:1069-1079` — кнопка удаления проекта без `disabled` на isPending (P2).
- `edge-kind-controls.tsx:199-223` — optimistic смена типа связи без отката при ошибке (P2).
- ИИ-проверка на канвасе недостижима: `edge-ai-controls.tsx`, `node-ai-review-controls.tsx` не примонтированы (P2 dead code).
- CDP-лейблы: `ai-control-edge-dialog.tsx:166-177`, `node-ai-review-dialog.tsx:162-193`, кнопка «Галерея Outsee (CDP)» `assemble-montage-board.tsx:2195` — тексты про браузерный/CDP-стек устарели (P2).

## Что проверено и НЕ подтвердилось как дефект
- Все вызовы `api.*` из `web/src/lib/api.ts` (≈90 методов) имеют существующие роуты в `app/web/routers/*` — кроме двух fleet-эндпоинтов выше.
- Поля тел запросов сверены с pydantic-моделями: CreateProjectRequest, HITLDecisionRequest, GrsaiGenerateBody/GrsaiQuoteBody, OutseeGenerateBody, BugReportCreate (camelCase!), OrchestratorChatBody, confirm-*, FramePatch/InsertFrameBody/TextBody/PromptBody/EntityBody/EdgeBody/SceneBody/SheetCellBody, PromptOverridesPatch/ComposeRequest, StepPresetPatch, BlockCreatePayload/BlockRenamePayload, GenQueueToggle/GenQueueEnqueue — несовпадений не найдено.
- `/api/docs`, `/api/health`, `/api/studio-version`, `/api/files?path=`, `/ws/{channel}` — на месте.
- Старых CDP-роутов (`/api/browser|cdp|chrome|chatgpt|elevenlabs`) фронтенд не вызывает — только текстовые лейблы (см. выше).
