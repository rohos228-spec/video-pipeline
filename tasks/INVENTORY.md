# INVENTORY — video-pipeline (Фаза 0, housepc @ d53588ba)

Слой `scene_space` — поверх существующего пайплайна. Промты сцен не переписывать.

## Ноды пайплайна (порядок)

SoT: `app/orchestrator/node_registry.py`, `app/orchestrator/pipeline.py`, `app/orchestrator/steps/`, `app/orchestrator/auto_advance.py`.

| # | node_type | step_code | ready |
|---|-----------|-----------|--------|
| 1 | plan | plan | plan_ready |
| 2 | script | script | script_ready |
| 3 | split | split | frames_ready |
| 4 | sd_agent ×5 → sd_assemble (legacy `scene_design`) | scene_d / scene_asm | scene_design_ready |
| 5 | hero | hero | hero_ready |
| 6 | items | items | items_ready |
| 7 | enrich_1…5 | enrich_* | enrich_*_ready |
| 8 | image_prompts | img_pr | image_prompts_ready |
| 9 | images | img | images_ready |
| 10 | animation_prompts | anim_pr | animation_prompts_ready |
| 11 | videos | video | videos_ready |
| 12 | audio | audio | audio_ready |
| 13 | music | music | music_ready |
| 14 | sfx_plan / sfx_gen | sfx_* | sfx_* |
| 15 | assemble | assemble | assembled |
| 16 | publish | publish | — |

`scene_design` включается флагом `SCENE_DESIGN_ENABLED` / `meta.scene_design_enabled`. Выключен — pass-through. Модуль: `app/services/scene_design/`. **Новую ноду в registry в этом прогоне не добавлять.** Постановка `scene_space` — сервис после разбивки/сборки, вызывается CLI и механизмами правки.

## Схема БД (уже есть)

Файл: `data/state.db` (gitignored). Модели: `app/models.py`. Создание: `Base.metadata.create_all` в `app/main.py::_init_db`. ALTER: `app/services/db_v2.py::migrate_db_v2_schema`. Per-project DB: `app/project_db.py` (тот же migrate). Alembic в зависимостях есть, **папки alembic нет** — миграции как у v2: create_all + явный SQL.

Ключевые таблицы:

| Таблица | Роль |
|---------|------|
| `projects` | проект, meta JSON |
| `frames` | кадр: number, voiceover, meaning, image_prompt, animation_prompt, attrs JSON, uuid, sort_key, scene_id |
| `scenes` | папка кадров: title, place, meaning, scene_type, attrs. **id INTEGER** |
| `frame_texts` / `prompt_versions` / `entities` / `frame_edges` | DB v2 |
| `scene_design_cells` | staging агентов scene_design |

`Frame.attrs` уже держит: `крупность`, `движение`, `набор`, `camera_subdivide`, `accent`, `биты`, `кадры`, `main_action`, scene grammar поля. Крупность в Базе — **русские имена** или легаси VLS/LS, не коды EWS… этого слоя.

Слой ночи: новые таблицы `scenes_space` / `frames_space` (не колонки frames). `frames_space.uuid` = `frames.uuid`. `scenes_space.scene_id` — TEXT, формат в CONTRACTS.

## Промты сцен (не трогать)

| Путь | Зачем |
|------|--------|
| `prompts/scene_design/action.md` | действие |
| `prompts/scene_design/camera.md` | камера агента |
| `prompts/scene_design/camera_sets.md` | наборы |
| `prompts/scene_design/assemble.md` | сборка |
| `prompts/scene_design/characters.md` world.md style.md | прочие агенты |
| `prompts/05_excel_gpt/sd_skeleton.md` | скелет VO |
| `prompts/05_image_prompts/*` | img_pr |
| `prompts/blocks/**` | блоки стиля/негатива |
| `prompts/blocks/forbidden_phrases/ai_cliches_ru.md` | стоп-фразы (валидатор **читает**) |

Код камеры/разбивки (не владение этого прогона, не править без запроса в отчёте): `app/services/scene_design/camera_expand.py`, `app/services/scene_shot_grammar.py`, `app/services/vo_shot_expand.py`, `app/services/apply_ops_batches.py`.

## UI монтажа (не трогать в этом прогоне)

Спека просит CLI SVG/HTML, не Studio.

| Путь | Что |
|------|------|
| `web/src/components/` + montage_* | доска монтажа |
| `app/services/montage_board.py` | доска |
| `app/services/shot_menu.py` | меню съёмки |
| `app/services/shots_report.py` | HTML отчёт script_frames_qc |
| `exports/` | артефакты отчётов |

## Apply-ops (единственный писатель кадров пайплайна)

- Код: `app/services/db_apply.py` (`FIELD_ALIASES`, `apply_ops`)
- HTTP: `POST /api/db/projects/{id}/apply-ops`
- Allowlist нод: `app/services/node_write_contract.py`
- Алиас `shot_size` → **`крупность` (русское поле attrs)**. Не писать коды EWS в apply-ops.
- Пространство: только `app.services.scene_space.store` → таблицы `*_space`.

## Как запускается прогон

| Что | Команда |
|------|--------|
| Studio | `STUDIO.cmd` → [1] → http://127.0.0.1:8765 |
| Приложение | `python -m app.main` |
| Тесты | `python -m pytest tests/ -q` |
| Harness ночи (старый) | `scripts/night_harness.py` — **не** этот слой |
| Миграция v2 | автоматически `_init_db` / web lifespan |

## Команды слоя scene_space (зафиксировано)

Рабочая директория: корень репо. Python из `.venv`.

| Команда | Что |
|---------|-----|
| `python scripts/scene_space_migrate.py --up` | миграция вверх (идемпотентно) |
| `python scripts/scene_space_migrate.py --down` | откат: DROP `frames_space`, `scenes_space` |
| `python scripts/scene_space_plan.py --scene SCENE_ID --out DIR [--from-json PATH]` | SVG/PNG плана сверху; `--from-json` для фикстур |
| `python scripts/scene_space_board.py --scene SCENE_ID --out FILE.html [--from-json PATH]` | монтажная раскладка HTML |
| `python scripts/scene_space_validate.py --fixtures` | валидатор 3 фикстур → `tasks/VALIDATION.md`, код выхода 0/1/2 |
| `python scripts/scene_space_validate.py --scene SCENE_ID` | одна сцена |
| `python scripts/scene_space_rewrite.py --scene SCENE_ID --meaning TEXT` | перезапись смысла |
| `python scripts/scene_space_rebuild.py --scene SCENE_ID` | пересборка разбивки |
| `python scripts/scene_space_e2e.py` | фикстуры + validate + plan + board |

Вывод отображения по умолчанию: `tasks/out/<scene_id>/plan_NN.svg` и `tasks/out/<scene_id>/board.html`.

## Тесты слоя

```
python -m pytest tests/test_scene_space_geom.py tests/test_scene_space_migrate.py tests/test_scene_space_properties.py tests/test_scene_space_validator.py tests/test_scene_space_rewrite.py tests/test_scene_space_e2e.py -q
```

## Вне скоупа (грязное дерево housepc, не коммитить в этот слой)

Локально изменены и не входят в прогон: `app/services/apply_ops_batches.py`, `scene_shot_grammar.py`, `shots_report.py`, `vo_shot_expand.py`, `app/web/routers/projects.py`, куча `prompts/**`. Worktree потоков стартуют от **HEAD housepc**, без этой грязи.
