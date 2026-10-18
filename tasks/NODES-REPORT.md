# Полный отчёт: ноды пайплайна и слой scene_space

Ветка: `housepc` `e7c42b1d`. `main` не трогали. Push не было.  
Дата прогона: 2026-09-17.  
SoT нод: `app/orchestrator/node_registry.py`.

`scene_space` — **не нода канваса**. В `WORK_NODES` / `LINEAR_NODE_TYPES` её нет. Это слой таблиц + сервис, который вешается **после** `sd_assemble`.

---

## 1. Линейка рабочих нод — что было (не менялось)

Порядок исполнения (`LINEAR_NODE_TYPES`). Каждая строка — нода на канвасе / шаг воркера.

| # | node_type | step | running → ready | Что делает | Код шага |
|---|-----------|------|-----------------|------------|----------|
| 1 | `plan` | plan | planning → plan_ready | общий план ролика | `app/orchestrator/steps/make_plan.py` |
| 2 | `script` | script | scripting → script_ready | закадр | `make_script.py` |
| 3 | `split` | split | splitting → frames_ready | 1 VO-ячейка = 1 Frame | `split_frames.py` |
| 4 | **группа scene_design** | см. §2 | | режиссура сцен | `scene_design.py` |
| 5 | `hero` | hero | generating_hero → hero_ready | рефы персонажей | `generate_hero.py` |
| 6 | `items` | items | generating_items → items_ready | предметы | `generate_items.py` |
| 7 | `enrich_1`…`enrich_5` | enrich_* | enriching_N → enrich_N_ready | доп. Excel-слоты | `enrich_xlsx.py` |
| 8 | `image_prompts` | img_pr | generating_image_prompts → image_prompts_ready | промты картинок | `generate_image_prompts.py` |
| 9 | `images` | img | generating_images → images_ready | PNG | `generate_images.py` |
| 10 | `animation_prompts` | anim_pr | generating_animation_prompts → animation_prompts_ready | промты видео | `make_animation_prompts.py` |
| 11 | `videos` | video | generating_videos → videos_ready | клипы | `generate_videos.py` |
| 12 | `audio` | audio | generating_audio → audio_ready | TTS | `generate_audio.py` |
| 13 | `music` | music | generating_music → music_ready | музыка | `generate_music.py` |
| 14 | `sfx_plan` / `sfx_gen` | sfx_* | (в WORK_NODES, не в LINEAR) | звуки | `plan_sfx.py` / `generate_sfx.py` |
| 15 | `assemble` | assemble | assembling → assembled | монтаж ролика | `assemble.py` |
| 16 | `publish` | publish | publishing → published | выгрузка | `publish.py` |

Рядом, не шаги пайплайна:

| node_type | Что |
|-----------|-----|
| `hitl_hero` `hitl_images` `hitl_videos` `hitl_final` `hitl_gate` | паузы человека |
| `topic` `storage` | конфиг |
| `shot_menu` | меню съёмки на канвасе |
| `excel_feed` | прозрачный вход |
| `excel_gpt` | оператор apply-ops (не в LINEAR) |

Промты этих нод (`prompts/scene_design/**`, `prompts/05_image_prompts/**`, `prompts/05_excel_gpt/sd_skeleton.md`) **не менялись**.

---

## 2. Группа нод scene_design — что было

Это одна фаза между `split` и `hero`. На канвасе — веер, не одна кнопка.

| Канвас | step_code | агент | Промт | Код |
|--------|-----------|-------|-------|-----|
| `sd_agent` | `sd_skel` | skeleton | `prompts/05_excel_gpt/sd_skeleton.md` | `app/services/scene_design/skeleton.py` |
| `sd_agent` | `sd_char` | characters | `prompts/scene_design/characters.md` | `agents.py` + `cells.py` |
| `sd_agent` | `sd_world` | world | `prompts/scene_design/world.md` | то же |
| `sd_agent` | `sd_cam` | camera | `prompts/scene_design/camera.md` + `camera_sets.md` | то же |
| `sd_agent` | `sd_act` | action | `prompts/scene_design/action.md` | то же |
| `sd_assemble` | `scene_asm` | сборщик | `prompts/scene_design/assemble.md` | `assembler.py` |

Статус: `scene_designing` → `scene_agents_ready` → `scene_assembling` → `scene_design_ready`.  
Legacy-тип одной ноды `scene_design` ещё в реестре; канон — `sd_agent` + `sd_assemble`.

Что делает сборка **до** нашего слоя (это уже было):

1. `chronology.py` — сцены по границам VO-ячеек (1 ячейка закадра = 1 сцена).
2. `camera_expand.py` — `subdivide_vo_frames_by_camera`: внутри сцены вставляет шоты SET, режет текст ячейки по кадрам.
3. `apply.py` → `db_apply.apply_ops` — пишет `Frame.attrs` (русская `крупность`, действие, персонажи…).

Флаг: `SCENE_DESIGN_ENABLED` / `meta.scene_design_enabled`. Выключен — pass-through.

---

## 3. Что новое — слой scene_space (не нода)

Добавлено поверх §2, после `apply_scene_design` в `app/orchestrator/steps/scene_design.py`.

### 3.1 Таблицы (миграция)

`scenes_space` — план комнаты JSON (метры, оси, запертая сторона).  
`frames_space` — на каждый `frames.uuid`: крупность EWS…ECU, сторона оси A/B, `screen_pos`, `screen_dir`, ракурс, роль бита, дельты.

`scene_id` живой сцены: `p{project.id}:s{scenes.id}` (пример `#50` сцена `main` = `p50:s3`).  
Фикстуры: `fix:dialogue` `fix:cross` `fix:turn`.

Коды: `app/services/scene_space/migrate.py`, хуки в `app/main.py`, `app/web/api.py`, `app/project_db.py`.

### 3.2 Модули

| Файл | Задача |
|------|--------|
| `geom.py` | ось, сторона (косое произведение), дельты, facing |
| `store.py` | CRUD `*_space` |
| `shot_size.py` | русский «общий» → WS и таблица стыков 2.1 |
| `rules.py` / `blocking.py` | постановка: крупности, 30°, сторона, служебные кадры |
| `validate.py` | правила §6, exit 0/1/2 |
| `render_plan.py` / `render_board.py` | SVG сверху + HTML раскладка |
| `rewrite.py` | перезапись смысла / rebuild, `manual=1` не трогает |
| `pipeline.py` | **новое:** ingest живого `Scene`/`Frame` в `*_space` |

### 3.3 Когда слой запускается

| Когда | Что делает | Вставляет Frame? |
|-------|------------|------------------|
| CLI `scene_space_sync.py --project N` | overlay: читает кадры, пишет `*_space` | нет |
| После `sd_assemble` (новый прогон) | ingest + `rewrite`/`blocking` | да, служебные WS/POV если правило требует |
| CLI `rewrite`/`rebuild` на `pN:sM` | живая БД | да |
| CLI `plan`/`board`/`validate` `--fixtures` | три учебные сцены | нет |

Существующие ролики (`#50` и т.д.): только overlay, кадры не двигали — так ты выбрал.

---

## 4. Два разных отчёта валидатора (оба сырые)

Команда фикстур (эталон слоя):

```
python scripts/scene_space_validate.py --fixtures
```

Файл: `tasks/VALIDATION.md` — **exit=0**

| scene_id | errors | warnings |
|---|---:|---:|
| fix:cross | 0 | 0 |
| fix:dialogue | 0 | 0 |
| fix:turn | 0 | 0 |

Раскладки (e2e, `violations: 0`):

- `tasks/out/fix-dialogue/board.html`
- `tasks/out/fix-cross/board.html`
- `tasks/out/fix-turn/board.html`

Команда живого `#50 nicshe-60-sekund`:

```
python scripts/scene_space_sync.py --project 50
python scripts/scene_space_validate.py --scene p50:s3 --out tasks/out/p50-s3/VALIDATION.md
```

Файл, который у тебя открыт: `tasks/out/p50-s3/VALIDATION.md` — **exit=2**, 52 error / 5 warning.

Почему красный (данные кадров, не «слой сломан»):

- в `Frame.attrs` нет `крупность`/`план` → ingest ставит MS на все 18 стыков → `size_same`
- камера в overlay одна и та же → `angle_30`
- `animation_prompt` без маркеров `PROMPT:` / `NEGATIVE PROMPT` → `prompt_style`
- 2.9: одна крупность, нет turning_point / reaction

Раскладка того же прогона: `tasks/out/p50-s3/board.html` (18 кадров, нарушения подсвечены).

`#57 scene-design-30` то же самое: `tasks/out/p57-s10/VALIDATION.md` exit=2.

---

## 5. Проверки кода слоя

```
python -m pytest tests/test_scene_space_geom.py tests/test_scene_space_migrate.py tests/test_scene_space_properties.py tests/test_scene_space_validator.py tests/test_scene_space_rewrite.py tests/test_scene_space_e2e.py tests/test_scene_space_pipeline.py -q
```

**70 passed** (на HEAD `e7c42b1d`).

Property §7: 10 тестов, geom по 200 прогонов.

---

## 6. Чего в группе нод нет и не появится, пока не скажешь

- Ноды `scene_space` в registry / на канвасе — нет.
- Studio-кнопки «план сверху» — нет (только CLI HTML/SVG).
- `img_pr` не читает `frames_space.shot_size` (EWS не пишется в русскую `крупность`).
- `camera_expand.py` не переписывали.
- На `#50` служебные кадры не вставляли.

---

## 7. Команды

| Команда | Результат |
|---------|-----------|
| `python scripts/scene_space_validate.py --fixtures` | `tasks/VALIDATION.md` |
| `python scripts/scene_space_sync.py --project 50` | overlay `p50:s3` |
| `python scripts/scene_space_validate.py --scene p50:s3 --out tasks/out/p50-s3/VALIDATION.md` | живой отчёт |
| `python scripts/scene_space_board.py --scene p50:s3 --out tasks/out/p50-s3/board.html` | живая раскладка |
| `python scripts/scene_space_plan.py --scene fix:dialogue --out tasks/out/fix-dialogue --from-json tests/fixtures/scene_space/dialogue.json` | план сверху фикстуры |

Карта файлов: `tasks/INVENTORY.md`. Контракты имён: `tasks/CONTRACTS.md`. Канон съёмки: `tasks/CANON.md`.
