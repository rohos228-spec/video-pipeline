# Автономный системный аудит video-pipeline: план, нормы, форматы

Дата: 2026-07-31
Цель: к 09:00 иметь проверенную программу, единый контракт обмена LLM/генераторов, безопасную запись оперативных данных и харнес, который может сам наблюдать, проверять и мягко перезапускать ошибки.

Связанная уже существующая спецификация: `2026-07-30-night-harness-llm-excel.md`. Этот документ шире: полный аудит программы, UI, нод, Excel, GPT, генераторов и автономии.

---

## 0. Текущий baseline (зафиксировано)

- Ветка: `main`, HEAD `c6274c8` (`feat(night): LLM contract, Excel priority pack, agent harness`).
- Есть незакоммиченные правки: `app/services/llm_contract.py`, `docs/superpowers/specs/2026-07-30-night-harness-llm-excel.md`, `scripts/night_harness.py`, плюс QA-скрипты `scripts/_qa_sequential_verify50.py`, `scripts/_qa_storage_http50.py`.
- Backend запущен напрямую через `scripts/run-backend.ps1`; health `/api/studio-version`: build 376, backend_git `c6274c8`, `ui_stale=0`, `pipeline_ok=1`.
- Harness CLI/API по проекту #50 зелёный: `project.xlsx`, 9 scenes, 9 videos, final, R48=9/9, failed node_runs=0, voiceover clean.
- QA HTTP #50 зелёный: artifacts 26/26 OK, assets 40 preview OK, xlsx download OK, disk parity OK, recent #50 ERROR/WARN=0.
- Harness расширен HTTP-слоем: CLI `--http` и API verify дают 15 проверок (disk/R48/node_runs/voiceover + studio/project/frames/artifacts/assets/xlsx HTTP).
- Dependency drift исправлен: `pypdf` был в `pyproject.toml`, но отсутствовал в `.venv`; после установки PDF context test зелёный, `pip check` чистый.
- Тесты: `tests/test_night_harness_contract.py` 5 passed; ops regression `test_chatgpt_attachment_guard.py + test_animation_prompt_gpt.py` 16 passed; широкий Excel/GPT блок 90 passed.

---

## 1. Словарь простым языком

**Durable данные** — то, что нельзя потерять: `project.xlsx`, `scenes/*.png`, `videos/*.mp4`, `characters/*.png`, `final/*.mp4`, голос/музыка, БД `state.db`.

**Оперативные данные** — служебная память проверок: «когда проверяли», «что упало», «какой следующий шаг». Не является контентом ролика.

**Контекст LLM** — текст, который реально уходит в модель. Модель не видит бинарный Excel; она видит TSV-экспорт.

**Priority pack** — правило: при нехватке места сначала отдаём важные строки плана: R15, R45, R46, R48, R49, R50, R64.

**Fail-closed** — если ответ модели опасен для Excel, лучше отказать в записи, чем молча сдвинуть строки.

**Soft-retry** — повторный запуск шага без удаления уже готовых файлов.

**Harness** — сторожок: verify → журнал → optional soft repair → снова verify.

**SoT** — source of truth, единственный источник правды. Для anim_pr это `project.xlsx` R48/R64, не stale DB.

---

## 2. Главная системная проблема

У программы три правды:

1. Диск и живой `project.xlsx`.
2. SQLite: проекты, frames, artifacts, node_runs, workflow_runs.
3. Canvas graph: ноды, edges, side `excel_gpt`, storage, HITL.

Ошибки возникают, когда один слой смотрит в другой как в источник правды. Примеры уже зафиксированы в `QA-RUN-LIVE.md`: stale NodeRun, rewind после slot collision, R48 из xlsx vs БД, final preview при unknown node status, spam auto_advance после assembled.

Норма: для каждого типа данных должен быть явный владелец.

---

## 3. Норма хранения данных

| Тип данных | Владелец/SoT | Куда писать | Чего не делать |
|---|---|---|---|
| Контент проекта | `project.xlsx` + файлы проекта | `data/videos/<slug>/` | Не дублировать контент в meta |
| Кадры/промпты для пайплайна | xlsx + `frames` как кэш | Синк xlsx ↔ frames через существующие сервисы | Не считать БД правдой для R48 |
| Артефакты | файлы на диске + `artifacts` как указатель | `artifacts` только pointer/path | Не хранить бинарь в SQLite |
| Оперативная телеметрия | `project.meta["ops_telemetry"]` | последний снимок проверки | Не делать из него историю |
| История проверок | `ops/events.jsonl` | append-only события | Не писать туда Excel/видео |
| LLM ответы | storage/snapshots + result ноды | текст/файлы результата | Не смешивать HITL verdict с API ответом |

---

## 4. Единый LLM contract

SoT: `app/services/llm_contract.py`.

Обязательные маркеры:

| Маркер | Назначение |
|---|---|
| `# Лист:` | начало TSV-блока листа Excel |
| `@row=N` | абсолютная строка Excel; обязательна для безопасной записи в лист «план» |
| `--- XLSX_WRITEBACK ---` | разделитель: отчёт проверки → правка Excel |
| `<<<VOICEOVER>>> … <<<END>>>` | чистый закадровый текст |
| `# ОТЧЁТ ПРОВЕРКИ` | текстовый отчёт проверки |
| `vp.check.v1` | JSON-диалект отчёта проверки |

Норма: любой код, который просит GPT вернуть Excel/voiceover/check, обязан собирать accompany через `build_api_accompany` и/или использовать те же маркеры. Запрещено плодить локальные «третьи диалекты».

---

## 5. Норма генераторов

Нужен одинаковый envelope для image/video/music/TTS запросов:

```text
provider: outsee | grsai | elevenlabs | local
kind: image | video | music | tts
project_id, frame_id/node_key
prompt_final
input_refs: [files/urls]
artifact_expected: path/kind
timeout/retry_policy
result: artifact_uuid/path/status/error
```

Проверка генератора считается complete только когда:
1. HTTP/queue принял задачу;
2. файл появился на диске;
3. artifact записан в БД;
4. файл доступен через `/api/artifacts/{uuid}/file`;
5. UI/result panel видит тот же artifact.

---

## 6. План работ

### Phase A — context-pack и карта
- Собрать отчёты под-агентов: orchestrator, Excel, LLM, generators, runtime/UI/harness.
- Свести в один machine-usable context-pack: файл с владельцами данных, шагами, артефактами, рисками.

### Phase B — безопасный runtime
- Backend up, health, harness CLI/API, QA scripts — уже baseline green.
- UI smoke через browser agent без mutate/delete/reset/audio/music.
- Проверить storage HTTP, artifacts HTTP, xlsx download, logs.

### Phase C — полный путь создания видео
- Не ломать assembled #50.
- Вариант 1: использовать #50 как эталон assembled и прогонять проверки шагов.
- Вариант 2: создать disposable QA-проект/клон и пройти plan→script→split→hero→img_pr→img→anim_pr→video→audio→music→assemble с ограниченным числом сцен.
- Для каждого шага фиксировать: input, SoT, output, artifact, API status, UI status, retry semantics.

### Phase D — системные исправления
- Убрать расхождения SoT.
- Ужать writeback fail-closed там, где ещё возможен сдвиг строк.
- Расширить harness: HTTP artifacts/assets, frames DB↔xlsx, логи проекта, UI smoke hooks, repair policy.
- Не вносить wipe/audio/music в автономный контур без явного сценария.

### Phase E — верификация
- Тесты: harness/xlsx/gpt/anim_pr/chatgpt attach + релевантные regressions.
- API: studio-version, project, frames, artifacts, assets, xlsx, harness.
- UI: checklist browser agent.
- Evidence: записать команды, вывод, HEAD, оставшиеся риски.

---

## 7. Матрица проверки каждого шага

| Шаг | Input/SoT | Output | Проверка | Repair |
|---|---|---|---|---|
| plan | topic/prompt | general plan + xlsx | project.xlsx есть, листы валидны | soft run plan |
| script | plan | script + voiceover | R49 заполнен, voiceover clean | soft run script |
| split | script | frames | frames count > 0, idempotent | soft run split |
| hero | refs | characters/*.png | artifacts HTTP OK | soft run hero |
| img_pr | frames | R45/R46 + frame.image_prompt | xlsx↔DB parity | soft run img_pr |
| img | image prompts | scenes/*.png | disk+artifact HTTP | soft run img |
| anim_pr | scenes + R48 | R48/R64 + frame.animation_prompt | R48 count == scenes | soft run anim_pr |
| video | anim prompts | videos/*.mp4 | disk+artifact HTTP | soft run video |
| audio | voiceover | voice files | artifact HTTP, duration | manual/ограниченно |
| music | mood/prompt | music file | artifact HTTP | manual/ограниченно |
| assemble | videos+audio+music | final/*.mp4 | final exists, stamp, preview | soft run assemble |

---

## 8. Вопросы к пользователю (подготовлены, задать после первого полного прохода)

1. Оперативные данные: оставляем только `meta.ops_telemetry` + `ops/events.jsonl` или нужен отдельный SQLite `ops_events` для запросов?
2. Excel: какие листы кроме «план» и «Общий план» считаются критичными для GPT context pack?
3. Для полного E2E: можно ли создать disposable QA-проект и тратить API на 2–3 сцены, или проверять только на #50?
4. Audio/music: разрешить реальный ElevenLabs/Suno/Outsee music при живых ключах/сессии или держать их в manual-only контуре?
5. UI mutate checks: можно ли в disposable проекте проверять Disable/Detach/Delete/reset на безопасных нодах, или эти кнопки только read-only smoke?
6. Объём контекста: какой жёсткий бюджет символов считать нормой для xlsx_to_text по умолчанию — текущий 900k или нужен меньший профиль для слабых моделей?
7. Harness autonomy: должен ли ночной режим сам перезапускать backend при подозрении на зависание, или только шаги проекта?

---

## 9. Красные линии автономии

- Никакого `reset_step` wipe в харнесе.
- Не удалять готовые PNG/MP4/final для починки.
- Не считать truncated TSV отсутствием `project.xlsx`.
- Не писать в лист «план» без `@row=`/label fallback.
- Не запускать audio/music автономно, если это не согласовано отдельно.
- Не коммитить секреты `.env` / credentials.
- После код-фиксов: тесты → commit → push `origin main` → сообщить новый HEAD.

---

## 10. Текущий статус

Baseline green. HTTP-слой харнеса уже сделан и проверен CLI+API. Полный test suite: 1126 passed / 32 failed; релевантные Excel/voiceover/storage падения изолированно проходят, а `auto_advance_parity` — отдельный pre-existing конфликт тестовых stub-сессий с текущим graph/DB-кодом (мои правки эту зону не трогали). Идут фоновые карты по orchestrator/Excel/LLM/generators/runtime и безопасный UI smoke. Следующий технический шаг: свести карты в context-pack, добавить log-scan по проекту в харнес и решить вопрос disposable E2E.
