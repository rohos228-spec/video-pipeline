# Система нод оркестратора — полная спецификация

> **Статус:** проектная спецификация (2026-07-31). Доноры: ComfyUI (контракт
> нод) + LangGraph (надёжность исполнения). SoT данных: DB v2 (см.
> [`DB_V2.md`](DB_V2.md)); запись — только `db_apply.apply_ops`.
> Реестр нод в коде: `app/orchestrator/node_registry.py`.

---

## 1. Доноры и что берём

| Донор | GitHub | Что берём |
|---|---|---|
| **ComfyUI** | `comfyanonymous/ComfyUI` | Контракт ноды: typed inputs/outputs, workflow = JSON (воспроизводим 1:1), headless execution, идемпотентность/кэш (входы не изменились → нода не перегенерирует) |
| **LangGraph** | `langchain-ai/langgraph` | Надёжность: checkpoint state на границах шагов (`WorkflowRun`), per-node `retry_policy` + `error_handler`, `interrupt` = HITL-пауза |
| **Temporal** (ориентир) | `temporalio/temporal` | На будущее: crash-proof durable execution, если понадобится переживать падение процесса |

Код доноров не тащим — паттерны реализуем в своём оркестраторе (SQLite,
`ProjectStatus`, `WorkflowRun`, harness).

---

## 2. Жёсткие принципы

1. **DB = источник правды, Excel = экспорт.** Писатель один. Любая запись —
   `db_apply.apply_ops` (fail-closed). TSV `# Лист:`/`@row=` — deprecated.
2. **Harness-гейт обязателен.** Нода не `done`, пока harness не `ok`.
   `ok=false` → soft retry / пауза, продвижение дальше запрещено.
3. **Промт-нода ≠ оператор.** Промт-нода (plan/script/split/img_pr/anim_pr/
   enrich) — модель отдаёт только содержание, код сам мапит в поля по uuid.
   Оператор (excel_gpt) — модель отдаёт `{"ops":[...]}` → `apply_ops`.
4. **Идемпотентность нод.** Повторный запуск = тот же результат; retry без
   wipe (backup в `old/`, `old/scenes/`).
5. **Наружу — человеческие имена** (`закадр`, `промт_картинки`); внутри —
   канон (`voiceover_text`, `image_prompt`); перевод — `FIELD_ALIASES`.

---

## 3. Контракт ноды (шаблон описания)

Каждая нода описывается карточкой:

- **type / step_code** — из `node_registry.WORK_NODES`.
- **Назначение** — что делает.
- **Читает** — контекст из DB/graph (без Excel TSV).
- **Пишет** — поля через apply-ops / артефакты на диск / entities / edges.
- **Harness-гейт** — checks, обязательные для `done`.
- **Retry** — политика (soft retry / attempts / pause).
- **Статусы** — `running → ready` (ProjectStatus).
- **HITL** — есть ли пауза-аппрув.

---

## 4. Каталог нод

### plan (`plan`) — `planning → plan_ready`
- **Назначение:** общий план эпизода.
- **Читает:** `project.topic`, настройки генерации.
- **Пишет:** `общий_план` (target=project → `Project.meta["general_plan"]`).
- **Harness:** общий план осмыслен (`is_meaningful_general_plan`), лист
  «Общий план» после экспорта.
- **Retry:** soft (GPT-вызов); fail-closed по контракту.
- **HITL:** нет.

### script (`script`) — `scripting → script_ready`
- **Назначение:** сценарий/закадровый текст целиком.
- **Читает:** `общий_план`, topic.
- **Пишет:** `voiceover.txt` (артеракт проекта).
- **Harness:** voiceover.txt есть и не загрязнён (`voiceover_clean`).

### split (`split`) — `splitting → frames_ready`
- **Назначение:** разбивка сценария на кадры.
- **Читает:** `voiceover.txt`.
- **Пишет:** кадры (Frame) + `закадр`, `длительность` по каждому uuid.
- **Harness:** frames > 0, паритет DB↔xlsx R49 (`frames_xlsx_parity`),
  `project_log_clean`.

### hero (`hero`) — `generating_hero → hero_ready`
- **Назначение:** референсы персонажей (c01…).
- **Читает:** hero-описания проекта.
- **Пишет:** `entities(type=character, code=cNN)` + файлы `characters/`.
- **Harness:** референсы на диске, entities заполнены. `hero_mode=no_hero`
  — шаг пропускается.

### items (`items`) — `generating_items → items_ready`
- **Назначение:** предметы/объекты (i01…). Аналог hero: entities(type=prop)
  + файлы `items/`.

### enrich_1..5 (`enrich_N` / `excel_gpt`) — `enriching_N → enrich_N_ready`
- **Назначение:** GPT-дозаполнение блоков кадра (shot_02, смысл, детали).
- **Читает:** graph (кадры, uuid, текущие поля).
- **Пишет:** `промт_картинки_2`, `промт_видео_2`, `смысл` по uuid.
- **Harness:** заполненность слотов, паритет, качество промтов.
- **Retry:** soft; `active_excel_gpt_node_key`/`excel_gpt_completed_keys`
  в `project.meta` — чекпоинт слотов.

### image_prompts (`img_pr`) — `generating_image_prompts → image_prompts_ready`
- **Назначение:** промты картинок по всем кадрам.
- **Читает:** graph (закадр, смысл, entities c01, стиль проекта).
- **Пишет:** `промт_картинки` по каждому uuid (+ активная версия).
- **Harness:** `image_prompts_gpt_quality` (длина, запрещённые маркеры),
  паритет R45.
- **Retry:** soft.

### images (`img`) — `generating_images → images_ready`
- **Назначение:** PNG кадров (`scenes/`).
- **Читает:** `промт_картинки`(_2), референсы entities.
- **Пишет:** артефакты PNG; статусы кадров.
- **Harness:** `scenes_png` (status-aware), `project_log_clean`.
- **Retry:** soft без wipe (backup `old/scenes/<ts>/`); CDP/HTTP provider
  выбирается автоматически (HTTP — без Chrome).

### animation_prompts (`anim_pr`) — `generating_animation_prompts → animation_prompts_ready`
- **Назначение:** промты видео по кадрам.
- **Читает:** graph (закадр, промт_картинки, PNG кадра как референс).
- **Пишет:** `промт_видео`, `промт_видео_2` по uuid.
- **Harness:** `animation_prompts_gpt_quality`, `r48_anim` (status-aware).
- **Retry:** soft; источник правды для пропусков — DB (синк R48).

### videos (`video`) — `generating_videos → videos_ready`
- **Назначение:** MP4 клипы (`videos/`).
- **Читает:** `промт_видео`(_2), PNG кадров.
- **Пишет:** артефакты MP4; статусы кадров.
- **Harness:** `videos_mp4` (status-aware).
- **Retry:** soft без wipe.

### audio (`audio`) — `generating_audio → audio_ready`
- **Назначение:** озвучка кадров (TTS) по `закадр`.
- **Пишет:** mp3/wav дорожки, тайминги (`start_ts/end_ts` → R15 при экспорте).
- **Harness:** аудиофайлы на диске, покрытие кадров.

### music (`music`) — `generating_music → music_ready`
- **Назначение:** фоновая музыка. Промт музыки — из graph/стиля.
- **Пишет:** музыкальный файл.

### assemble (`assemble`) — `assembling → assembled`
- **Назначение:** монтаж финального ролика.
- **Читает:** клипы, аудио, музыку, тайминги.
- **Пишет:** финальный mp4.
- **Harness:** финальный файл, длительность в норме (60–75 с).

### publish (`publish`) — `publishing → published`
- **Назначение:** доставка/публикация результата.
- **Пишет:** маркер доставки, ссылки.

### hitl_* (`hitl_hero/images/videos/final/gate`) — пауза-аппрув
- **Назначение:** interrupt: ждёт аппрув человека (кнопка/Telegram).
  Resume идемпотентен (код до паузы безопасен к повтору).

### config (`topic`, `storage`)
- **Назначение:** тема проекта, путь хранения. Не рабочие шаги.

### shot_menu (`shot_menu`) — меню, не шаг
- **Назначение:** нода-меню на канвасе. Горизонтальная лента: 1 ячейка закадра = 1 блок, шоты камеры внутри. Строки добавляются из полей DB (действие, камера, SET, персонажи, стык, промты). Соседний VO не склеивается.
- **Читает:** кадры DB (`GET /api/db/projects/{id}/shot-menu`).
- **Пишет:** ничего. Не в цепочке auto_advance (side-sink).
- **HITL:** нет.

---

## 5. Карта действий (любое действие)

### Пользовательские
| Действие | Триггер | Эффект | Harness |
|---|---|---|---|
| Запустить шаг | `POST /api/projects/{id}/steps/{code}/run` | running-статус, нода выполняется | после шага — обязательный |
| Правка в «Базе» | UI | DB + авто-экспорт в xlsx | при следующем verify |
| apply-ops | `POST /api/db/projects/{id}/apply-ops` | fail-closed запись + экспорт | — |
| Экспорт в Excel | кнопка / `POST …/export-xlsx` | DB → project.xlsx + backup | — |
| Вставка кадра | «+» на карточке | дробный sort_key, перекидывание next | — |
| Аппрув HITL | кнопка/TG | resume interrupt, продвижение | следующего шага |
| Смена активной версии промта | карточка кадра | активная версия → Frame.image_prompt/animation_prompt + экспорт | — |

### Системные
| Действие | Триггер | Эффект |
|---|---|---|
| auto_advance | после `ok` harness | запуск следующей ноды (auto_mode) |
| soft retry | CDP/сетевой сбой img/video/anim_pr | повтор без wipe (≤ политики), backup артефактов |
| pause после ошибок | серия фейлов шага | пауза 30 мин; снятие — ручной запуск шага/кнопка |
| reset_step | ручной сброс | backup артефактов в `old/`, не wipe |
| backfill DB v2 | старт backend / открытие graph | uuid/sort_key/тексты/версии/edges из legacy |
| write-through export | после apply-ops/правок «Базы» | DB → project.xlsx автоматически |
| harness verify | после шага / по кнопке | гейт `ok` → done или soft retry |

---

## 6. Harness-гейты (матрица)

## 6. Harness-гейты (матрица)

**Центральный гейт (2026-08-01):** перед продвижением из любого `*_ready`
статуса `auto_advance` прогоняет `run_harness_verify` и **не продвигает**
проект при плохих проверках данных (status-aware). Блок — только данные;
наблюдаемость (`project_log_clean`, `node_runs_failed`) — телеметрия, не
блок. 3 фейла подряд на одном статусе → `paused` (снятие — ручной ▶ после
фикса). Выключение: `HARNESS_GATE_DISABLED=true`. anim_pr имеет свой гейт
внутри шага (донорный паттерн); центральный покрывает остальные ноды.



| Нода | Обязательные checks для done |
|---|---|
| plan | общий план осмыслен |
| script | voiceover_clean |
| split | frames>0, frames_xlsx_parity (R49), project_log_clean |
| hero/items | референсы/entities на диске |
| enrich_N | заполненность слотов, parity |
| img_pr | image_prompts_gpt_quality, parity R45 |
| img | scenes_png (status-aware), project_log_clean |
| anim_pr | animation_prompts_gpt_quality, r48_anim (status-aware) |
| video | videos_mp4 (status-aware) |
| audio | аудиофайлы, покрытие |
| music | музыкальный файл |
| assemble | финальный mp4, длительность 60–75 с |
| publish | маркер доставки |

`ok=false` → шаг не done: soft retry (img/video/anim_pr) или пауза с
уведомлением; продвижение по графу блокируется.

---

## 7. State machine и checkpoint

- Статусы нод — `node_registry.WorkNodeSpec` (`running → ready`); линейный
  порядок — `LINEAR_NODE_TYPES` (+ excel_gpt слоты).
- **Checkpoint** = `WorkflowRun.nodes_snapshot/edges_snapshot` + статусы
  проекта + `project.meta` (excel_gpt ключи). Resume = идемпотентный
  перезапуск текущей ноды (паттерн LangGraph: код до interrupt безопасен).
- **retry_policy** (по ноде, из донора): img/video/anim_pr — soft retry;
  текстовые ноды — 1 форматный retry; медиа-пауза 30 мин после серии фейлов.
- **error_handler**: фиксация в лог (`project_log_clean`), уведомление
  (TG/Studio), pause, не wipe.

---

## 7.5. Как обучать систему (оркестратор)

«Обучение» оркестратора — это три слоя, каждый правится в своём месте:

### Слой 1. Правила поведения — системный промт
**Файл:** `app/web/routers/db_browser.py`, константа `_ORCHESTRATOR_SYSTEM`.
Что он должен/не должен, формат ответа (JSON/текст), глоссарий типов нод,
запреты («не открывай окна генерации», «лишние = duplicates»). Ошибка в
диалоге → правило дописывается сюда. Это самый быстрый способ «научить».

### Слой 2. Знания — контекстные сборщики
**Файл:** тот же, функции `_pipeline_state_lines`, `_checks_context`,
`_diagnostics_context`, `_settings_context`, `_canvas_nodes_context`,
`_orchestrator_context`. Оркестратор не должен ничего «вспоминать» — всё,
о чём его спрашивают, обязано быть в контексте. Не знает чего-то → добавь
сборщик (или расширь существующий), не надейся на модель.

### Слой 3. Умения — действия (actions)
**Файл:** тот же, цикл `for act in ops_data.get("actions")` + обработчики
`_apply_*` / `_resolve_*`. Новое умение = новый обработчик по шаблону:
валидация входа (fail-closed, русские ошибки) → мутация через существующие
сервисы (не дублировать логику кнопок — вызывать их код) → запись в
`actions_run` → тест в `tests/test_db_v2_api.py`. Деструктивное — только
через `pending_confirm` (кнопка подтверждения).

### Качество промтов — библиотека, не оркестратор
Содержание промтов шагов живёт в `prompts/` (варианты `.md`, blocks v2).
Правится через Studio (студия ноды / «Промты») или файлами — оркестратор
лишь переключает варианты (`set_prompt`).

### Цикл обучения на ошибке (как делали всю сессию)
1. Кейс из диалога (скопировать реплику) **или из журнала**:
   каждый диалог оркестратора пишется в `data/videos/<slug>/ops/events.jsonl`
   (`type=orchestrator_chat`: сообщение, ответ, действия, ошибка); хвост —
   `GET /api/db/projects/{id}/orchestrator/feedback`. Пользователь кейсы
   не поставляет — журнал разбирается агентом пачками.
2. Правило → `_ORCHESTRATOR_SYSTEM` или новый обработчик/контекст.
3. Тест, воспроизводящий кейс (фейковый GPT отвечает JSON действия).
4. `pytest` + `ruff` → commit → push в main.
5. У пользователя: [2] → перезапуск backend → Ctrl+F5.

**Режим работы:** агент делает автономно пачками → в main → пользователю
понятный отчёт (что изменилось, как проверить) → пользователь проверяет
или комментирует → комментарии = новые кейсы.

**Запрещено при обучении:** авто-применение изменений без подтверждения
для деструктива; действия без валидации по каталогам/реестрам; «молчаливые»
падения (ошибка должна попадать в ответ чата).

---

## 8. Roadmap подключения

1. ~~**anim_pr эталоном**~~ — **сделано** (2026-08-01): пары GPT →
   `build_apply_ops_from_pairs` → `db_apply.apply_ops` (uuid, версии промтов,
   авто-экспорт R48/R64), после шага — обязательный harness-гейт
   (`run_harness_verify`; в гейт не входят наблюдаемость-чеки
   `project_log_clean`/`node_runs_failed`, иначе ошибка в логе блокировала
   бы повтор навсегда). Backfill uuid перед записью.
2. ~~img_pr, split, plan~~ — **сделано** (2026-08-01): plan пишет `общий_план`
   через apply-ops (target=project), split — закадр/длительность по uuid,
   img_pr — промт_картинки + версии; у всех harness-гейт после шага через
   общий `agent_harness.harness_gate_or_raise` (anim_pr переведён на него же).
3. ~~excel_gpt — оператор-режим~~ — **сделано** (2026-08-01): ответ модели
   с `{"ops":[...]}` детектируется в `gpt_operator_client` (TSV-writeback
   пропускается), применяется в `enrich_xlsx` через `db_apply.apply_ops`
   (export сам); в контекст ноды подмешивается uuid-мап кадров + контракт.
   Legacy TSV — fallback, работает как раньше.
4. retry_policy/error_handler — декларативно в `node_registry` (как у донора).
5. HITL-ноды как interrupt в графе (явные паузы/resume).
