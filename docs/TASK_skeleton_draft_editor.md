# ЗАДАНИЕ: нода скелета «черновик → редактор» (волна 0 scene_design)

> Исполнителю (другой модели): читай целиком перед стартом. Контекст
> системы — `docs/AGENT_MAP.md` (строка scene_design) и
> `docs/superpowers/plans/2026-08-10-scene-design-chrono-dynamics.md`.
> Промпты уже написаны и лежат в репо — не переписывай их, а подключи.

## Цель

Нода `sd_skeleton` должна готовить проект к параллельной работе нод:
смотрит на весь проект разом, пишет скелет (сцены-группы кадров + реестры
персонажей/локаций + нити + биты) в staging-ячейки, и только потом
стартует параллельная волна. Сейчас нода делает один вызов «всё сразу»
с само-критиком внутри того же JSON — это работает некорректно.
Переделать на схему: **черновик целиком → проверки кода → редактор по
адресам → запись в ячейки**.

## Готовые промпты (не трогать содержимое, только подключить)

- `prompts/05_excel_gpt/sd_skeleton.md` — ЧЕРНОВИК (первый вызов).
- `prompts/05_excel_gpt/sd_skeleton_editor.md` — РЕДАКТОР (второй вызов).
- Дубли для git: `templates/excel_gpt_agents/sd_skeleton.md`,
  `templates/excel_gpt_agents/sd_skeleton_editor.md`.
- Нюанс: `prompts/` в .gitignore, `templates/` — в git. При правке промпта
  править ОБЕ копии (есть тест `tests/test_prompts_survive_update.py`).

## Текущее состояние (что уже есть)

- Нода `sd_skeleton` в веере chrono_dyn (`app/services/excel_gpt_node.py`,
  `app/services/node_groups.py`, `app/orchestrator/node_registry.py`).
- В `agents.py` есть `SKELETON` и валидатор `validate_skeleton_one_vo_per_scene`
  (1 VO-ячейка = 1 сцена). **Его заменить** — см. ниже.
- Staging-ячейки: `app/services/scene_design/cells.py` (модель
  `SceneDesignCell` в `app/models.py`), валидация при записи, чанки.
- Чекпоинты агентов: `runner.py` (`save_checkpoint`/`load_checkpoint`,
  файлы `data/.../scene_design/<agent>.json`).
- Волны: `agents.agent_waves()` — chrono_dyn: characters/world/style →
  action → camera. Скелет должен идти ДО волн.
- Шаг: `app/orchestrator/steps/scene_design.py` (`run` = фаза агентов).
- Сброс: `reset_step._wipe_scene_design` чистит ячейки и чекпоинты.

## Что построить

### 1. Поток волны 0 (`app/services/scene_design/skeleton.py`, новый)

```
run_skeleton(project, frames, session):
  1. context = build_shared_context(project, frames)
  2. черновик = GPT(sd_skeleton.md + context)          # вызов 1
  3. разрывы = validate_skeleton(черновик, frames, vo)  # код, без GPT
  4. пока разрывы и кругов < 2:
       ответ = GPT(sd_skeleton_editor.md + context + черновик + разрывы)
       если ответ.error → RuntimeError(текст)          # честный отказ
       черновик = merge_by_id(черновик, ответ)          # замена по id_scene
       разрывы = validate_skeleton(...)
  5. если разрывы остались → RuntimeError(список)       # упасть читаемо
  6. запись ячеек (agent="skeleton") + чекпоинт skeleton.json
```

- Чекпоинт: при повторном запуске (soft retry) готовый скелет не
  пересчитывать — как у остальных агентов.
- Каждая сцена пишется отдельной транзакцией (коммит на сцену), чтобы
  retry одной сцены не трогал остальные.

### 2. Проверки кода (`validate_skeleton`) — сердце задачи

Вход: скелет JSON, кадры, полный закадр. Выход: список разрывов
`{адрес, проблема, как_исправить}` (адрес = id_scene / id сущности).

Обязательные проверки:

1. **Покрытие**: каждый номер кадра ровно в одной сцене; кадры в сцене —
   соседние по порядку; сцены по порядку кадров. ЗАМЕНА текущему
   `validate_skeleton_one_vo_per_scene` (правило «1 VO = 1 сцена» убрать —
   скелет группирует кадры; строгость = покрытие, не 1:1).
2. **Якоря дословно**: `главное.якорь_в_кадрах`, `биты[].якорь`,
   `characters_seed[].якорь`, `locations_seed[].якорь` — непрерывная
   подстрока закадра соответствующих кадров (нормализация пробелов,
   casefold).
3. **Двойники локаций**: пересечение значимых токенов якоря/name новой
   локации с существующими → разрыв «похоже на locNN».
4. **Двойники персонажей**: то же по якорю/имени (кореференция
   «механик»/«Павел»).
5. **Тип связи против сигналов** (выводит код, сверяет с заявленным):
   - место продолжено (тот же loc id, что у прошлой сцены) + нет маркера
     времени в тексте кадров → ожидается `продолжение`;
   - место то же + маркер времени («спустя», «три ночи», «на рассвете»…
     словарь маркеров в коде) → `сдвиг_времени`;
   - место другое → `новое_место` / `возврат` (возврат = id уже был раньше);
   - у scene_01 только `начало`.
   Несовпадение → разрыв с ожидаемым типом.
6. **Тайминг**: `|Σ len(закадр кадров)/RATE − Σ время_сек| / Σ > 0.15` →
   разрыв. RATE из `settings.scene_design_vo_chars_per_sec` (новый,
   дефолт 14.0).
7. **Биты**: `порядок` возрастает по смещению якорей в закадре; якорь бита
   внутри кадров своей сцены.
8. **Нити**: каждая нить, открытая в `главное.нить` или продолженная,
   должна быть закрыта/исполнена до конца ролика или явно продолжена в
   последней сцене. Висячая нить → разрыв на сцене, где её бросили.
9. **Разовая локация**: место, чей якорь живёт в одном кадре и нет
   возврата, не должно иметь id → разрыв «пиши фон_разовый».
10. **Реестр-ссылки**: `место_id` и `персонажи[].id` существуют в
    locations_seed/characters_seed.

### 3. Ячейки

`agent="skeleton"`, kinds с префиксом, чтобы НЕ пересекаться с ячейками
категорийных агентов в `chronology.group_cells`:
`sk_scene` (поля карточки), `sk_character`, `sk_location`, `sk_bit`,
`sk_thread` (нить: target_key=имя нити, поля: открыта/закрыта, где).

### 4. Встройка в шаг

- `steps/scene_design.py::run`: перед `runner.run_category_agents` вызвать
  `skeleton.run_skeleton(...)`. Флаг: `settings.scene_design_skeleton_enabled`
  (env `SCENE_DESIGN_SKELETON_ENABLED`, дефолт True) с per-project override
  `meta.scene_design_skeleton`. При выключенном scene_design — pass-through
  как сейчас.
- `context_builder.build_shared_context`: если есть ячейки `sk_scene` —
  добавить блок `# СКЕЛЕТ (JSON)` (сцены + реестры) в контекст волны 1.
- Волна 1 (characters/world/style) и action получают скелет: characters
  только детализирует cNN, world — locNN, action пишет фазы внутри сцен
  скелета и обязан покрыть каждый бит фазой (проверка в
  `validate_chrono_dyn_action_scenes` или отдельной функцией: бит без фазы
  → ошибка среза action).
- `reset_step._wipe_scene_design`: удалить и skeleton-чекпоинт
  (`scene_design/skeleton.json`) — ячейки уже чистятся.

### 5. Не сломать

- chrono_dyn-волны, camera_expand, chunked assemble — не трогать.
- `prompt_slot_variants` на канвасе: нода sd_skeleton должна по-прежнему
  позволять выбор варианта промпта (draft — основной; editor вызывается
  только кодом, вариантов не надо).
- Статусы проекта не меняются (`scene_designing → scene_agents_ready`).

## Тесты (`tests/test_scene_design_skeleton.py`, новый)

Мок `gpt_client.gpt_ask_fresh` по маркеру промпта («СКЕЛЕТ — ЧЕРНОВИК» /
«СКЕЛЕТ — РЕДАКТОР»):

1. Чистый черновик → 1 вызов, скелет в ячейках, без редактора.
2. Черновик с двойником loc → редактор чинит → проверки зелёные → 2 вызова.
3. Редактор дважды не чинит → RuntimeError со списком разрывов.
4. Покрытие: пропущенный кадр → разрыв; не-соседние кадры → разрыв.
5. Тип связи: «продолжение» при смене loc → разрыв с ожидаемым типом.
6. Тайминг: текст ~30 с при 10 с кадров → разрыв.
7. Якорь не из закадра → разрыв.
8. Чекпоинт: повторный run_skeleton не дёргает GPT.
9. Регрессия: `pytest tests/test_scene_design_*.py` — всё зелёное.

## Приёмка

- `pytest tests/test_scene_design_skeleton.py tests/test_scene_design_agents.py tests/test_scene_design_step.py -q` — зелёное.
- `ruff check app/services/scene_design app/orchestrator/steps/scene_design.py` — без новых ошибок.
- Ручной прогон на проекте с готовой разбивкой: нода sd_skeleton пишет
  ячейки, волна 1 стартует после, в логе видно «skeleton: draft ok» или
  «skeleton: editor round N fixed [...]».

## Git

Рабочая ветка — из `.env` (`ORCHESTRATOR_GIT_BRANCH`); если пользователь
сказал «в main» — main. Коммит + push сразу, в ответе писать
`git HEAD: <sha> уже в <ветка>`.
