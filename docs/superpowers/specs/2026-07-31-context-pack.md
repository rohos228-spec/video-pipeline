# Context-pack video-pipeline (machine-usable)

Дата: 2026-07-31
Git HEAD на момент сборки: `a2346e6` (`main`)
Назначение: компактный пакет знаний для автономного агента — владельцы данных, шаги, артефакты, проверки, риски. Не заменяет `docs/AGENT_MAP.md`, а фиксирует рабочий срез для харнеса/аудита.

---

## 1. Источники правды (SoT)

| Домен | SoT | Кэш/указатель | Правило |
|---|---|---|---|
| Контент проекта | `data/videos/<slug>/project.xlsx` + файлы проекта | SQLite `projects` | Не дублировать контент в `meta` |
| Кадры/VO/промпты | xlsx лист `план` R15/R45/R46/R48/R49/R50/R64 | таблица `frames` | Для `anim_pr` правда = xlsx R48/R64, не stale DB |
| Артефакты | файлы на диске | таблица `artifacts` | SQLite хранит pointer/path, не бинарь |
| Оперативная телеметрия | `Project.meta["ops_telemetry"]` | `ops/events.jsonl` | Только последний снимок + append-only события |
| Статусы шагов | `Project.status` | `workflow_runs`/`node_runs` | Ready-статусы ждут действия, running-статусы подхватывает worker |
| UI baked | `web/out/` + `web/STUDIO_VERSION` | `/api/studio-version` | `ui_stale=0` обязателен перед отчётом |

---

## 2. Линейные шаги пайплайна

| # | node_type | step_code | running | ready | Артефакт/проверка |
|---:|---|---|---|---|---|
| 1 | plan | plan | planning | plan_ready | `project.xlsx`, листы валидны |
| 2 | script | script | scripting | script_ready | R49/voiceover чистый |
| 3 | split | split | splitting | frames_ready | `frames` созданы идемпотентно |
| 4 | hero | hero | generating_hero | hero_ready | `characters/*.png`, artifacts HTTP |
| 5 | items | items | generating_items | items_ready | refs/items artifacts |
| 6-10 | enrich_1..5 | enrich_1..5 / excel_gpt | enriching_N | enrich_N_ready | xlsx round-trip fail-closed |
| 11 | image_prompts | img_pr | generating_image_prompts | image_prompts_ready | R45/R46 ↔ `frames.image_prompt` |
| 12 | images | img | generating_images | images_ready | `scenes/*.png`, artifact HTTP |
| 13 | animation_prompts | anim_pr | generating_animation_prompts | animation_prompts_ready | R48/R64 ↔ `frames.animation_prompt` |
| 14 | videos | video | generating_videos | videos_ready | `videos/*.mp4`, artifact HTTP |
| 15 | audio | audio | generating_audio | audio_ready | voice artifact/duration; manual/red-line для автономии |
| 16 | music | music | generating_music | music_ready | music artifact; manual/red-line для автономии |
| 17 | assemble | assemble | assembling | assembled | `final/*.mp4`, preview/stamp |
| 18 | publish | publish | publishing | published | внешняя выгрузка, отдельный контур |

Реестр: `app/orchestrator/node_registry.py`. Canvas side-node: `excel_gpt` (`EXCEL_GPT_STEP_CODE`) работает через slot statuses `enriching_N`/`enrich_N_ready`.

---

## 3. Harness contract

SoT: `app/services/agent_harness.py`, CLI `scripts/night_harness.py`, API `POST /api/projects/{id}/harness/verify`.

Текущие проверки (17):

| check | смысл | repair candidate |
|---|---|---|
| project_xlsx | файл существует | plan |
| scenes_png | требуется только начиная с images_ready+ | img |
| videos_mp4 | требуется только начиная с videos_ready+ | video |
| heroes_png | optional диагностика | — |
| final_mp4 | обязателен для assembled | assemble |
| r48_anim | R48 count == scenes для anim/video+ | anim_pr |
| frames_xlsx_parity | assembled: frames/img_db/anim_db/vo_db/R45/R48/R49 >= scenes | img_pr/anim_pr |
| node_runs_failed | failed node_runs в последнем workflow_run | investigate |
| project_log_clean | ERROR/WARNING/Traceback по проекту в последнем backend log | investigate |
| voiceover_clean | voiceover.txt без `# Лист:`/`vp.check.v1`/ОТЧЁТ | script |
| studio_http | `/api/studio-version` 200, backend_git совпадает | restart backend |
| project_http | `/api/projects/{id}` 200 | investigate |
| frames_http | frames API отражает БД | img_pr/anim_pr/script по ситуации |
| artifacts_http | `/api/artifacts/{uuid}/file` доступен | recover соответствующего шага |
| assets_http | preview/assets доступны | investigate |
| xlsx_http | `/api/projects/{id}/xlsx` 200 и непустой | plan |
| http_scene_parity | HTTP frames ↔ disk scenes | img |

Red lines: no wipe, no `reset_step`, no autonomous audio/music repair, no delete готовых PNG/MP4/final.

---

## 4. LLM/Excel contract

SoT: `app/services/llm_contract.py`, `app/services/gpt_api.py`, `app/services/xlsx_text_writeback.py`.

| Элемент | Формат | Обязательность |
|---|---|---|
| Лист Excel | `# Лист: <name>` | Для xlsx writeback |
| Абсолютная строка | `@row=N` | Обязательна для листа `план`; иначе fail-closed |
| Разделитель отчёт→правка | `--- XLSX_WRITEBACK ---` | Для check+writeback сценариев |
| Voiceover | `<<<VOICEOVER>>> ... <<<END>>>` | Для чистого закадрового |
| JSON check | `vp.check.v1` | Только как диалект отчёта, не как запись в план |
| Priority pack | R15/R45/R46/R48/R49/R50/R64 | Должен выживать при truncation |

Норма: вызывающий код собирает accompany через `build_api_accompany`; запрещены локальные третьи диалекты.

---

## 5. Generator envelope (целевой контракт)

```text
provider: outsee | grsai | elevenlabs | local
kind: image | video | music | tts
project_id: int
frame_id/node_key: string
prompt_final: string
input_refs: [path|url]
settings: {generator, resolution, quality, relax, aspect_ratio}
artifact_expected: {kind, path_pattern}
timeout/retry_policy: {timeout_sec, soft_retry, forbidden}
result: {status, artifact_uuid, path, error}
```

Complete только если: accepted by provider → файл на диске → row в `artifacts` → `/api/artifacts/{uuid}/file` 200 → UI/result panel видит тот же artifact.

---

## 6. Verified runtime evidence

| Объект | Evidence | Статус |
|---|---|---|
| #50 assembled | harness API: 17/17 green; artifacts 26/26; assets 40; xlsx 67555 bytes; R45/R48/R49 = 9 | green |
| #51 disposable early E2E | `plan → script → split`; frames=14; harness green после каждого шага | green до media gate |
| backend | `/api/studio-version`: build 376, `ui_stale=0`, `pipeline_ok=1`, backend_git `6aabad1` до test/docs commits | green |
| tests | `test_night_harness_contract.py` 6 passed; ops regression 16 passed; Excel/GPT block 90 passed | green scoped |
| full suite | 1126 passed / 32 failed; изолированные relevant падения проходят; `auto_advance_parity` = pre-existing stub/DB conflict | known debt |

Boundary: медиа-шаг `hero/run` для #51 остановлен auto-review как внешняя генерация; approval retry не показал карточку из-за потери tool-call context. Медиа-полнота подтверждается по #50 + HTTP artifact parity.

---

## 7. Риски/долги

| Риск | Уровень | Что делать |
|---|---|---|
| `auto_advance_parity` tests vs current DB/graph code | medium | отдельный рефактор тестовых stub-сессий; не смешивать с harness |
| Полный media E2E требует подтверждения затрат | high для автономии | запускать только с approval или в выделенном QA-контуре с лимитом сцен |
| Audio/music автономный repair | high | держать forbidden/manual до отдельного сценария |
| Context budget xlsx_to_text 900k | medium | держать priority pack; для слабых моделей ввести профиль меньше |
| UI mutate checks | medium | только disposable project; read-only smoke для продуктовых проектов |

---

## 8. Вопросы к пользователю

1. Оперативные данные: достаточно `meta.ops_telemetry` + `ops/events.jsonl` или нужен отдельный queryable `ops_events`?
2. Excel: какие листы кроме `план`/`Общий план` обязаны входить в GPT context pack?
3. Полный media E2E: разрешаешь disposable QA с 2–3 сценами и жёстким лимитом стоимости, или только #50/static verification?
4. Audio/music: разрешить реальный запуск при живых ключах/сессии или оставить manual-only?
5. UI mutate: можно ли на disposable проекте проверять Disable/Detach/Delete/reset, или только read-only smoke?
6. Контекст: оставить default budget 900k или добавить компактный профиль для слабых моделей?
7. Harness autonomy: может ли ночной режим сам перезапускать backend при зависании или только шаги проекта?
