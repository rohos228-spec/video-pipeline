# Agent map — video-pipeline

Компактный указатель для Cursor-агентов. **Pointers + короткие cheats.**
Большие доки не дублировать — открывать по ссылке.

Операторская шпаргалка для человека: [`OPERATOR_BIBLE.md`](OPERATOR_BIBLE.md).
Поиск по знаниям: `python scripts/build_knowledge_index.py` →
`GET http://127.0.0.1:8765/api/knowledge/search?q=…`.

---

## 1. Read first (always-on)

| Файл | Зачем |
|------|--------|
| [`../ai-pack/START_HERE.md`](../ai-pack/START_HERE.md) | **Пакет для ИИ** (копии гайдов + как чинить/промпты). Пересборка: `python scripts/build_ai_pack.py` |
| [`AGENTS.md`](../AGENTS.md) | Cloud/dev: git→main, providers, GPT API, UI version |
| [`.cursor/rules/video-pipeline-ops.mdc`](../.cursor/rules/video-pipeline-ops.mdc) | Studio, anim_pr/R48, soft-retry, diagnose |
| [`.cursor/rules/video-pipeline-map.mdc`](../.cursor/rules/video-pipeline-map.mdc) | Это правило: сначала карта |
| [`NODE_SYSTEM.md`](NODE_SYSTEM.md) | Система нод оркестратора: доноры, контракты, harness-гейты, каталог нод/действий |
| [`DB_V2.md`](DB_V2.md) | DB v2 = SoT: apply-ops контракт, алиасы, экспорт в Excel, руководство оператора |
| [`PROMPT_CONTRACT.md`](PROMPT_CONTRACT.md) | Контракт промптов GPT↔DB: apply-ops / artifact / staging; стоп-лист Excel/TSV |
| [`NODE_MODELS.md`](NODE_MODELS.md) | Пикер модели на ноде: vibecode vs kie vs Grsai/Outsee, какие ключи |

---

## 2. Git & delivery

- Ветки ПК: **`housepc` / `tompc` / `strangepc` / `workpc`** (`ORCHESTRATOR_GIT_BRANCH` в `.env`).
- После фикса: commit + **`git push origin <ветка>`** + сообщить `git HEAD: <sha> уже в <ветка>`.
- Details: `AGENTS.md` + ops rule (не повторять здесь).

---

## 3. Run / update / diagnose

- Windows: `STUDIO.cmd` → **[1]** Studio (`http://127.0.0.1:8765`), **[2]** update from `origin/main`.
- Stop: `stop-backend.cmd` / `scripts/stop-backend.ps1`.
- Логи: `data/backend-*.log`, `data/studio-live.log`.
- Не использовать `scripts/legacy/`.
- Меню в разных md может расходиться — **SoT: `STUDIO.cmd` + ops rule**.

---

## 4. Architecture map (dirs)

```
app/
  main.py                 # entry: bot + worker + web
  orchestrator/           # pipeline steps, auto_advance, graph
  services/               # gpt_*, prompt_composer, mass_factory, xlsx_*
  bots/                   # outsee_http, chatgpt (CDP attach), grsai, elevenlabs
  web/routers/            # FastAPI REST
  storage/                # plan_sheet_v8 и т.п.
prompts/                  # локальные промты (не в git; не тянутся при update)
web/src/                  # Studio Next UI → baked web/out/
data/                     # state.db, gpt_workspace/, logs (gitignored)
templates/                # project_template_v8.xlsx, series templates
docs/                     # human/agent docs
```

---

## 5. Pipeline steps & statuses

**SoT:** [`app/orchestrator/node_registry.py`](../app/orchestrator/node_registry.py),
[`pipeline.py`](../app/orchestrator/pipeline.py),
[`steps/`](../app/orchestrator/steps/),
[`auto_advance.py`](../app/orchestrator/auto_advance.py).

| node_type | step_code | смысл |
|-----------|-----------|--------|
| plan | plan | план ролика |
| script | script | сценарий / VO |
| split | split | разбивка кадров |
| scene_design | scene_d | мульти-агентный дизайн сцен: 5 GPT-агентов параллельно → staging-ячейки `scene_design_cells` (валидация при записи, `cells.py`) → хронология по закадру + привязка кадров (`chronology.py`) → сборщик → валидация → apply-ops. В боевые таблицы пишет только финальная сборка. Флаг `SCENE_DESIGN_ENABLED` / `meta.scene_design_enabled`; выключен — pass-through. Модуль: [`app/services/scene_design/`](../app/services/scene_design/), промпты `prompts/scene_design/*.md` |
| hero | hero | hero-кадр |
| items | items | предметы/рефы |
| enrich_1…5 | enrich_* | доп. Excel-слоты |
| image_prompts | img_pr | промты картинок |
| images | img | генерация PNG |
| animation_prompts | anim_pr | промты анимации → **R48/R64** |
| videos | video | клипы |
| audio | audio | TTS |
| music | music | музыка |
| assemble | assemble | монтаж |
| publish | publish | выгрузка |

Canvas HITL/config nodes (`hitl_*`, `topic`, `storage`) — см. тот же registry.

---

## 6. project.xlsx short v8 cheat

**SoT:** [`app/services/xlsx_v8_import.py`](../app/services/xlsx_v8_import.py) (`ROW_*`),
[`app/storage/plan_sheet_v8.py`](../app/storage/plan_sheet_v8.py),
[`templates/project_template_v8.xlsx`](../templates/project_template_v8.xlsx).

Лист **«план»**, строки (1-based в комментариях кода):

| Row | Константа / смысл |
|-----|-------------------|
| 15 | `ROW_TIMECODE_V8` — таймкоды |
| 45 | `ROW_IMAGE_PROMPT_V8` — промт картинки shot_01 |
| 46 | `ROW_IMAGE_PROMPT_2_V8` — shot_02 |
| **48** | anim_pr shot_01 (в UI/коде часто «промт для видео»; **источник правды для anim_pr**, не stale DB) |
| 49 | `ROW_VOICEOVER_V8` — закадровый текст |
| 50 | `ROW_DURATION_V8` — время на кадр |
| **64** | anim / video prompt shot_02 |

Ops: при сбоях anim_pr смотреть **R48 в xlsx**, soft-retry без wipe.

> **DB v2 (2026-07-31):** параллельная гибкая модель (uuid карточек,
> дробный `sort_key`, `frame_edges`, `prompt_versions`, `entities`) +
> кнопка «База» в Studio. Excel-адресация пока работает, но для вставки
> кадров/версий промтов/связей — читать [`docs/DB_V2.md`](DB_V2.md).

Series workbook — отдельный трек: `docs/SERIES_XLSX_WORKBOOK.md`.

---

## 7. Prompts: blocks v2 vs legacy

**Не переписывать — читать:**

- [`docs/PROMPT_CONTRACT.md`](PROMPT_CONTRACT.md) — GPT↔DB IO (apply-ops); Excel/TSV стоп-лист
- [`docs/PROMPTS_BLOCKS.md`](PROMPTS_BLOCKS.md) — контракт blocks/steps/vars/weights
- [`app/services/prompt_composer.py`](../app/services/prompt_composer.py)
- Диск: `prompts/blocks/`, `prompts/steps/`, `prompts/step-presets/`, legacy `prompts/0*_*/`
- Studio UI: вкладка «Промты» / Prompt Builder; REST `app/web/routers/prompts.py`, `prompt_studio.py`, `prompt_files.py`

Флаг v2: `Project.prompt_overrides["use_blocks_v2"]` / blocks map.

---

## 8. GPT: три разных пути

| Путь | Назначение | SoT |
|------|------------|-----|
| Pipeline text | plan/script/checks/anim_pr/hero-prompt… | `gpt_client.py` → `gpt_api.py` (`GPT_API_KEY`) |
| **gpt_workspace** | Свободный Studio-чат, файлы в «Готовые файлы» | `gpt_workspace.py`, `app/web/routers/gpt_workspace.py`, `data/gpt_workspace/` |
| ChatGPT CDP | **Не** для текста; attach/CDP остаётся для media/TTS кейсов | `app/bots/chatgpt.py` (см. ops attach rules) |

Правила workspace (кратко): сообщение с `?`/`？` в конце → ответ в чате, не pack файла;
картинки «пришли» → поиск/URL, не 1×1 stub; договор-shaped reply → авто pack.

**Не доверять** `HANDOVER.md` про «текст через ChatGPT web».

---

## 9. Media providers / Create

| Что | SoT |
|-----|-----|
| Outsee HTTP API | `app/bots/outsee_http.py`, `app/web/routers/outsee_http.py` |
| Outsee CDP fallback | `OUTSEE_HTTP_FALLBACK_CDP`, `app/bots/outsee.py` |
| Grsai | `app/bots/grsai.py`, `app/web/routers/grsai.py` |
| Studio Create UI | `web/src/components/outsee/*`, settings `data/outsee_create_settings.json` |
| Create REST | `app/web/routers/outsee_create.py` — `GET/PUT /api/outsee-create/settings`, generate |
| Очередь Create | `app/web/routers/create_queue.py` |
| Env | `IMAGE_PROVIDER` / `VIDEO_PROVIDER` = `outsee` \| `grsai` — см. `AGENTS.md` |

---

## 10. Mass: Studio factory ≠ Telegram `/mass`

| Система | SoT |
|---------|-----|
| **Studio «Фабрика видео»** | `app/services/mass_factory.py`, `web/src/components/inspector/mass-factory-panel.tsx`, inspector Project settings |
| **Telegram `/mass`** | [`docs/MASS_CREATION.md`](MASS_CREATION.md), `app/services/batches.py`, `app/telegram/mass_menu.py` |

Одинаковое слово — **разные** entry points. Mass Creation doc описывает Telegram-партию; Studio factory — Excel тем → lanes в UI.

Пресеты генерации (мастер нового проекта): `app/services/generation_config_presets.py`,
`data/generation_config_presets.json`, `web/.../new-project-wizard.tsx`.

---

## 11. ASR & montage

| Что | SoT |
|-----|-----|
| Сборка ролика | `app/orchestrator/steps/assemble.py` |
| Таймлайн / sync | `app/services/frame_timeline_sync.py`, `mapper.py` |
| Whisper / ASR | `app/services/whisper.py`; NVIDIA: `ASR_BACKEND=nvidia`, `.[nvidia]`, `scripts/download_nvidia_asr.py` |
| Word-level в БД | таблица `asr_words` (`app/models.py` AsrWord), запись `app/services/asr_words_store.py`; API `GET /api/projects/{id}/asr-words` |
| VO в xlsx | R49 (`ROW_VOICEOVER_V8`) + таймкоды R15 |
| **Regen на доске** | `montage_board_regen.py` → тот же API, что img/video (`outsee_retry`); промты из БД (`prompt_versions`/Frame), Excel fallback. CDP не нужен при `IMAGE/VIDEO_PROVIDER=outsee\|grsai`. См. `docs/DB_V2.md` §8 |

---

## 12. Studio UI version bump

Перед коммитом с правками `web/src/**`:

```bash
python scripts/bump_studio_version.py
```

- Counter: `web/STUDIO_VERSION`
- Baked UI: `web/out/` **коммитится**
- Badge / stale: `/api/studio-version`

---

## 13. Series track

Отдельный длинный пайплайн (не shorts v8):

- `docs/SERIES_PRODUCTION_PLAN.md`, `SERIES_OPERATOR_CHEATSHEET.md`, `SERIES_XLSX_WORKBOOK.md`, `SERIES_CAMERA_ANGLES.md`
- `prompts/steps/series/README.md`
- `templates/project_template_series_v1.xlsx`

---

## 14. Stale — do not trust

| Документ | Почему |
|----------|--------|
| `HANDOVER.md` | Старые ветки + «текст через ChatGPT web» |
| Старые QA/audit reports | Исторические срезы |
| Части `README.md` | Могут расходиться с xlsx+API reality |

SoT по тексту GPT: `AGENTS.md` + этот map §8.

---

## 15. Quick «where is X»

| Ищу | Открыть |
|-----|---------|
| **DB v2 / кнопка «База»** | [`DB_V2.md`](DB_V2.md), `app/services/db_v2.py` |
| Монтаж regen img/video | `montage_board_regen.py` (API = ноды img/video; промты из БД) |
| step_code / status | `node_registry.py` |
| R48 anim | `plan_sheet_v8.py`, ops rule |
| Prompt contract (GPT↔DB) | `PROMPT_CONTRACT.md` |
| Prompt blocks | `PROMPTS_BLOCKS.md` |
| Free GPT chat files | `gpt_workspace.py` |
| Create settings | `outsee_create.py` + Create UI |
| Mass Studio | `mass_factory.py` |
| Mass Telegram | `MASS_CREATION.md` |
| Soft retry steps | `step_failure_policy.py` |
| Image style Cursor skills | `.cursor/skills/README.md` (opt-in, не пайплайн) |
| Knowledge search | `scripts/build_knowledge_index.py`, `/api/knowledge/search` |
| **GPT VPS-relay (только прокладка)** | [`../deploy/gpt-relay/README.md`](../deploy/gpt-relay/README.md) |
| **Группы нод («+ Группа»)** | `app/services/node_groups.py` (каталог+вставка), `app/web/routers/node_groups.py`, веер scene_design = группа `scene_design_fanout` |
