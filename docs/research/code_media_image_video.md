# Медиа-ноды images / videos / hero / items: жизненный цикл, платные submit, безопасное «resume, never resubmit»

Репозиторий: `C:\video-pipeline\video-pipeline-observe-v1`, ветка `feature/orchestrator-observe-v1`, HEAD `35fd0e17` (дерево чистое).
Режим: только чтение кода. `.env` не читался, тесты и приложение не запускались, сеть не использовалась, файлы репозитория не менялись.
Все пути относительно корня репозитория. Номера строк сверены по текущему HEAD. Где вывод основан на чтении без запуска, это помечено «не проверено запуском».

Обозначения: **native** = обычный (человеческий) путь пайплайна. **managed** = синтетический контур оркестратора (`media_jobs.py` + `media_transport.py`).

---

## 0. Главное за одну страницу

1. **Native: `taskId` Kie живёт только в локальной переменной** (`app/bots/kie_http.py:387`). В БД и на диск он не пишется. Возвращается как `GenerationResult.gen_id` (`kie_http.py:398`), но вызывающий код его отбрасывает: `generate_images.py:1981-1985` кладёт в `Artifact.meta` свой локальный `uuid`, а не taskId.
2. **Рестарт процесса в статусе `generating_*` сам приводит к повторной оплате.** Воркер подбирает любой «running»-статус (`app/main.py:287-312`, `running_statuses_list()`). `generate_images.run` снимает lease `img_gen_inflight` (`generate_images.py:698-703`) и ставит в очередь каждый кадр без валидного PNG (`:629-652`), то есть создаёт новый платный submit. `generate_videos.run` делает то же (`generate_videos.py:821-825`, claim `:266-322`).
3. **Ambiguous-ошибки в native ведут к ресабмиту, а не к ожиданию:**
   - `ReadTimeout` на createTask (`kie_post_json`, `kie_kling.py`, timeout=120) попадает в видео-лестнице в `_is_transient_network_error` и классифицируется `media_transient` = `RETRY_SAME` (`outsee_retry.py:1674-1682`, `:1700-1712`, `video_error_policy.py:187-203`). Это до 2 «бесплатных» (не сжигающих попытку) повторных createTask подряд.
   - Таймаут опроса (`kie_http.py:351-355`, код 504) классифицируется `media_failed` = `RETRY_BURN` (`video_error_policy.py:205-218`). Это новый createTask при живом оплаченном задании.
   - Ошибка `_check` при опросе (например code 500/503 в теле JSON) не терпится (`kie_http.py:321`), в отличие от `kie_kling._poll_task`. Это тоже приводит к burn и ресабмиту.
4. **`media_jobs` даёт durable-строки `MediaJob` с уникальностью `(execution_id,node_key,resource_key)` и необратимой резервацией submit, но только для loopback.** Для реального Kie заблокировано минимум по 8 причинам (раздел 3.3). Главные: `validate_media_config` (`media_jobs.py:158`), требование `synthetic` ключа (`:210-212`), `json_request` не передаёт `Authorization` (`media_transport.py:32-33`), нет загрузки референсов (`kie_http.py:541-542, 596-597`), `videos` не `managed` (`node_capabilities.py:63-64`).
5. **Найдены три дополнительных умножителя стоимости вне лестниц:**
   - `finalize_or_retry` → `mark_frames_for_video_regen` **снимает `video_gen_skip`** (`post_step_validate.py:340-399`), статус остаётся running, шаг возвращается без исключения, поэтому счётчик отказов сбрасывается (`main.py:364` `clear_failure_on_success`). Для стабильно проваливающегося клипа это потенциально **неограниченный** цикл «лестница из 7 submit → снять skip → снова». Не проверено запуском.
   - `vision_check_loop` **удаляет** PNG/MP4 (`vision_check_loop.py:281-474`) и запускает платную регенерацию, до 20 раундов (`MAX_VISION_CHECK_ROUNDS`, `:39`). Решение принимает модель.
   - Мёртвая ветка: при `backend == "kie"` ветка `generate_kie_image` в `outsee_retry.py:1046` находится **внутри** `if use_outsee_api:` (`:1024`), а `use_outsee_api` истинно только при `backend == "outsee"` (`:992`). То есть native-путь Kie-картинок через эту обёртку недостижим (фактически уходит в `outsee.generate_image`, `:1116-1121`). Реальный Kie-submit для картинок возможен только в managed (`:979-991`).
6. **Стоимость видео по умолчанию выше, чем в `MEDIA_NODES_READINESS.md`.** `DEFAULTS["video_resolution"]="1080p"` (`generation_options.py` DEFAULTS), `_video_opts` берёт его, если у проекта не задано другое (`generate_videos.py:203-205`). Seedance 2.5 1080p = 114 кр/с × 5 с = 570 кр = **$2.85 за один submit** (в доке считалось 720p = $1.58). Худший случай лестницы Seedance 2.5 1080p: 4×$2.85 + 3×$0.275 ≈ **$12.2 на клип**.
7. **Рекомендация:** двухслойное решение.
   - Слой 1 (native ledger): durable-журнал `reserved → submitted(taskId) → succeeded(urls) → downloaded → applied`, запись **до** POST и сразу после ответа. Без него никакой оркестратор не может безопасно «возобновить».
   - Слой 2: оркестраторное действие `resume_poll` поверх этого журнала / `MediaJob`.
   - Начинать с `images` на дешёвой модели `z-image` (0.8 кр = $0.004).

---

## 1. Полный жизненный цикл одной генерации и где живёт состояние

### 1.1 Images (native)

| Шаг | Что происходит | Где | Состояние |
|---|---|---|---|
| 1 | Worker берёт проект со статусом `generating_images` | `main.py:287+`, `pipeline.py:161` | БД: `Project.status` |
| 2 | Подхват PNG с диска без Artifact | `generate_images.py:551-572` → `artifact_recovery.recover_scene_images_from_disk` (`artifact_recovery.py:169`) | диск → БД (`Artifact`) |
| 3 | Постановка в очередь: источник истины = валидный PNG на диске (≥200 000 байт + magic, `scan_frames.py:34, 59-80`) | `generate_images.py:625-652`; `frame_needs_shot1_image` (`scan_frames.py:97-116`) | диск |
| 4 | Сброс зависших lease | `generate_images.py:698-703` | `Frame.attrs[img_gen_inflight]` |
| 5 | Claim кадра: lease в `Frame.attrs` | `_claim_shot1_batch` (`:1084-1151`), `_claim_shot2_batch` (`:1237`) | БД |
| 6 | `_generate_and_send`: локальный `gen_id=uuid4` и имя `frame_NNN_<uuid8>.png` (**новое имя на каждую попытку**) | `:1763-1771` | память + имя файла |
| 7 | Запись в xlsx `image_gen_id`, `frame_status=image_generating` | `:1805-1812` | xlsx |
| 8 | `await session.commit()` и вызов лестницы | `:1908-1925` → `generate_image_with_retries` (`outsee_retry.py:948`) | — |
| 9 | Submit (native-Kie недостижим, см. п. 0.5; Outsee: API или CDP) | `outsee_retry.py:1024-1121` | — |
| 10 | Kie (в managed или при прямом вызове): `create_task` → `poll_task` → `download` | `kie_http.py:375-398` | **taskId только в памяти** |
| 11 | Файл на диске, Artifact (`uuid4`, meta `gen_id`/`raw_url`/`shot`) | `generate_images.py:1975-1988` (там же `persist_artifact`) | БД + диск |
| 12 | xlsx `image_path/image_url`, HITL-фото | `:1998-2035` | xlsx + HITL |
| 13 | `finalize_or_retry(step="images")` → `require_verified_images` (только managed) + `validate_after_images` | `post_step_validate.py:446-490` | — |

Состояние по носителям:
- **Память (теряется при падении):** taskId, URL результата, счётчики лестницы (`attempt`, `round_idx`), `current_prompt` после GPT-rewrite.
- **БД:** `Frame.status`, `Frame.attrs` (lease, `video_gen_fail_count`, `video_gen_skip`), `Artifact`, HITL, `Project.meta.step_failure` (`step_failure_policy.py:1-9`).
- **Диск:** сам файл (критерий «готово»), xlsx, sidecar `.json` рядом (`generation_storage.write_sidecar`, вызывается **после** успеха: `outsee_retry.py:1061-1079`).
- **Нигде:** момент «submit отправлен, ответ не получен» и сам taskId.

### 1.2 Videos (native)

- Worker: `generate_videos.run` (`generate_videos.py:783`). Подхват клипов с диска (`:797`), снятие lease (`:821-825`), claim shot1 (`:266-322`, пропускает кадры с `video_gen_skip`, `:292-294`), параллельные `_shot1_job` (`:572-684`), затем shot2 (`:325`, `_shot2_job` `:687`).
- Файл `clip_NNN_<uuid8>.mp4` (новое имя каждый раз, `:607`). Старт-кадр обязателен (`_shot1_start_frame`, `:216-250`).
- Лестница: `generate_video_with_retries` (`outsee_retry.py:1347`): 4 primary + 3 fallback.
- Артефакт: `session.add(Artifact(...scene_video...))` (`:653-661`), **без** `persist_artifact` и без верификатора.
- Ошибка: `_note_video_fail_db` (`:530-554`), 5 подряд ошибок или `VideoLadderExhaustedError` → `video_gen_skip` (`:166-175`).
- Финал: `finalize_or_retry(step="video")` → `validate_after_videos` → `mark_frames_for_video_regen` (`post_step_validate.py:128-190, 340-399`).

### 1.3 Hero

- Legacy (`generate_hero.run`, `:475`): одна пара (hero, variation) за прогон. Файл `characters/hero_{i}_v{v}_<uuid8>.png` (`:771-773`). Лестница `generate_image_with_retries` (`:805-819`, **`project_id` не передаётся**). При исчерпании статус откатывается на `frames_ready` и функция возвращается без ошибки (`:834-870`). Artifact `hero_reference` без `persist_artifact` (`:884-893`). Перед этим до 3 GPT-вызовов за промпт.
- Excel/Entity (`_run_excel`, `:971`; `_generate_one_excel_character`, `:1182`): имя **детерминировано** `characters/<id>.png` (`:1234`), `project_id` передаётся (`:1355`), волны до `streams` параллельно (`:1098-1136`). Критерий «готово» = PNG/Artifact (`_excel_ids_with_artifact`). При исчерпании статус `frames_ready` (`:1364`).
- Кнопка «Повторить» (`outsee.regenerate_image`, `:785, 1330`) — CDP Outsee, не Kie.

### 1.4 Items

- `generate_items.run` (`generate_items.py:86`): последовательно, пропускает индексы с существующим Artifact `item_reference` (`:49-69, 118-125`).
- Файл `predmet{idx}_<uuid8>.png` (**случайный суффикс**, `:139-141`). **Всегда** открывается `browser_session()` (`:145`), даже если сгенерировано по HTTP, то есть нужен Chrome CDP.
- `generate_image_with_retries` вызывается **без `project_id`** (`:148-162`). При ошибке статус `hero_ready` + `RuntimeError` (`:163-179`).

---

## 2. Все места платного submit

### 2.1 Лестницы (native)

| Нода | Где | Платных submit на единицу | Примечания |
|---|---|---|---|
| images (Outsee, `backend=="outsee"`) | `outsee_retry.py:1001-1010`: раунд `original` ×3 + раунд `rewritten` ×3 (`gpt_rewrite=True`, `generate_images.py:1912`) | **до 6** | + модерационный fallback `gpt-image-2` → `nano-banana-2`: ещё `max(1,min(2,3))=2` попытки без rewrite (`:1322-1343`, `_image_moderation_fallback_slug` `:108-114`) → **до 8**. «Лимит одновременных» (`:1187-1206`) делает `attempt -= 1`, не считается (до 24 ожиданий). Повторы скачивания до 8 без нового submit (`:1004, 1124-1184`) |
| images (Kie-модели: flux-2-pro, seedream-5-pro, z-image, qwen3-image) | `IMAGE_GENERATORS` (`generation_options.py`), `media_route.py` (`KIE_IMAGE_IDS`) | native: **0 достижимых Kie-submit** через обёртку (мёртвая ветка `:1046`); фактически уходит в `outsee.generate_image` (`:1116`) с Kie-slug; не проверено запуском | managed: ровно 1 submit (`:979-991`), без лестницы и fallback |
| videos | `outsee_retry.py:1347-1795`; константы `generation_options.py:247-255`: `VIDEO_PRIMARY_TOTAL_ATTEMPTS=4`, `VIDEO_FALLBACK_ATTEMPTS=3` | **до 7** «сожжённых» | Fallback = Kling 2.6 (`kling-2-6`, `KIE_KLING_FALLBACK_SLUG`), через `kie_kling.py` (в `kie_catalog` id `kling-2-6` **отсутствует**, есть только `kling-2-6-t2v/-i2v`; `get_model("kling-2-6")` = None → `kie_http.py:522-537` уходит в `kie_kling.generate_video`). Дополнительно «бесплатные» `RETRY_SAME`: до 2 подряд между burn (`:1700-1712`, `transient_streak` сбрасывается только при burn `:1754`) → теоретически до 3 submit на слот лестницы. Concurrency-ожидания до 24 (`:1700-1709`), не платные. `STOP_AUTH` для 401/402/403 (`video_error_policy.py:80-85`). |
| hero | как images | до 6 (+2 на gpt-image-2) на вариацию/персонажа; в волне до `streams`≤4 параллельно | + до 3 GPT-вызовов на промпт (`:651`, `:1282`) |
| items | как images | до 6 (+2) на предмет | + browser_session |

Умножители поверх лестницы (все — до сих пор «человеческие» механизмы, оркестратор их не видит):
- `step_failure_policy`: 3 отказа → сон 30 мин → 9 отказов → abandon (`step_failure_policy.py:1-9, 24-26`). Срабатывает только если шаг **бросил исключение**.
- **Тихие выходы без исключения (`finalize_or_retry` → False, hero/items/images «откат статуса + return») не попадают в счётчик** и приводят к повторному запуску воркером (`main.py` цикл; `advance_runner.py` возвращает `new_status=None`). Для videos при этом `mark_frames_for_video_regen` снимает skip (`post_step_validate.py:376-387`). Для images `mark_frames_for_image_regen` → `reset_frames_to_image_prompt_ready`. **Потолка по числу таких циклов в коде не найдено** (поиск по `app/` не дал счётчика; не проверено запуском).
- `generate_videos`: на каждый кадр до 5 запусков лестницы (`VIDEO_FAIL_SKIP_AFTER=5`, `generate_videos.py:140, 171`), кроме `VideoLadderExhaustedError` (сразу skip). Нелестничные исключения (не `OutseeImageError`, не transient) прерывают лестницу и считаются отдельно (`outsee_retry.py:1673-1676`).
- `vision_check_loop`: до 20 раундов, удаляет файлы и ставит `generating_images/videos/hero` (`vision_check_loop.py:39, 560-660`).
- `post_step_validate`/`finalize_or_retry` (см. выше).

### 2.2 Стоимость одного submit (`app/services/kie_catalog.py`, 1 кр = $0.005, `CREDIT_USD` `:29`)

Это **статические** цены каталога, **не калиброванные** (`KIE_COST_CALIBRATION.md`: калибровалась только `gpt-5-6-sol`). Пересчёт: `kie_catalog.estimate_credits` (`:1580`).

Картинки (параметры из `generate_kie_image`, `kie_http.py:578-644`; поле `resolution` передаётся только моделям, у которых оно есть в `fields`):

| Модель | Кредиты | USD | Примечание |
|---|---|---|---|
| z-image | 0.8 | $0.004 | нет полей resolution |
| qwen3-image | 4.8 | $0.024 | |
| flux-2-pro | 1K 5 / 2K 7 | $0.025 / $0.035 | |
| seedream-5-pro | basic 7 / high 14 | $0.035 / $0.07 | поле `resolution` игнорируется (нет в fields), цена определяется `quality` (по умолчанию basic) |
| nano-banana-2 | 1K 8 / 2K 12 / 4K 18 | $0.04 / $0.06 / $0.09 | **маршрутизируется на Outsee**, не на Kie (`media_route.py` `OUTSEE_IMAGE_IDS`); в доке готовности указан как Kie, это неточно |
| gpt-image-2 | 1K 6 / 2K 10 / 4K 16 | — | Outsee |

Видео (`kie_http.generate_kie_video`, `kie_http.py:494-575`; `duration` по умолчанию 5; в `generate_videos` `duration` не передаётся):

| Модель | Цена | 5 с |
|---|---|---|
| seedance-1-5-pro | 480p 1.75 кр/с, 720p 3.5, 1080p 7.5 (без звука) | 8.75 / 17.5 / 37.5 кр = $0.044 / $0.0875 / $0.19 |
| seedance-2-5 | 480p 28, 720p 63, 1080p 114 кр/с | 140 / 315 / 570 кр = $0.70 / $1.575 / **$2.85** |
| kling-3-0 | 14-18 кр/с (std/pro), 67 (4K) | ~70-90 кр = $0.35-0.45 |
| kling-v3-turbo-i2v | 720p 18 кр/с | 90 кр = $0.45 |
| kling-2-6 (fallback, через `kie_kling.py`) | 5 с без звука 55 кр за ролик | $0.275 |
| hailuo-2-3-i2v | 6 с standard 768P 30 кр | $0.15 |

Худший случай на клип: Seedance 2.5 1080p: 4 × $2.85 + 3 × $0.275 ≈ **$12.2** (720p: 4 × $1.575 + 0.825 ≈ $7.1 — это цифра из `MEDIA_NODES_READINESS.md`, она занижена для дефолтного разрешения). Для Outsee-маршрутов (дефолт, если проект не выбрал Kie-модель: `DEFAULTS["video_generator"]="veo_3_fast"`, которого нет в `VIDEO_GENERATORS` → `model_slug=None` → `"veo"` → `settings.video_provider` по умолчанию `outsee`) стоимость определяется тарифом Outsee и здесь не оценена.

Статус-запросы и скачивание: тариф неизвестен (`cost_status: unknown_tariff`, `media_jobs.py` MediaRequest). Баланс `GET /api/v1/chat/credit` бесплатен (`kie_http.get_credits`, `:92`), разрешение 0.01 кр, возможны чужие списания на аккаунте (`KIE_COST_CALIBRATION.md`).

### 2.3 Прочие Kie-submit вне scope (для полноты)
- `generate_music.py:139` (`run_generation` с Suno), `kie_create.py:143` (вкладка «Генерация», in-memory очередь `enqueue_generation`, пользовательская). Не затрагиваются.
- Загрузка референсов/стартовых кадров (`_frame_to_public_url` → `outsee_http.ensure_public_image_url`, `outsee_http.py:935`): в Yandex Object Storage или резервные хосты; без настроенного Yandex при отключённых публичных хостах падает. Платность по Kie не относится.

---

## 3. Crash-unsafe точки, что даёт `media_jobs` и что блокирует реальный Kie

### 3.1 Native: хронология одного submit и что теряется при смерти процесса

| # | Момент | Что платно/известно | Что остаётся после смерти процесса | Итог при рестарте |
|---|---|---|---|---|
| P1 | до POST createTask | ничего не списано | lease `img_gen_inflight` | безопасно, повтор нормален |
| P2 | **POST отправлен, ответ не получен** (`kie_post_json`, timeout=120, `kie_kling.py`; `create_task`, `kie_http.py:150-156`) | возможно создано и оплачено | ничего | **двойная оплата**: в живом процессе ещё и `RETRY_SAME` без burn (п. 0.3) |
| P3 | ответ получен, `taskId` распарсен (`kie_http.py:158-167`), в памяти | оплачено | ничего | **двойная оплата** (самая явная дыра: `:387`) |
| P4 | идёт опрос (`poll_task`, 4 с, до 600/900/1200 с) | оплачено, задача идёт | ничего | **двойная оплата**; при таймауте/исключении taskId лежит только в `context` исключения (`:313, 354`), лестница не читает |
| P5 | SUCCESS получен, URL в памяти, скачивание не завершено | оплачено | ничего или огрызок | **двойная оплата**; срок хранения результата на стороне Kie в коде не зафиксирован (комментарий про `tempfile.redpandaai.co ~3 суток` относится к загрузкам, `kie_http.py:112`) |
| P6 | файл скачан, Artifact не закоммичен | оплачено, результат есть | файл на диске | **безопасно**: `recover_scene_images_from_disk` (`generate_images.py:551`) / `recover_scene_videos_from_disk` (`generate_videos.py:797`) |
| P7 | Artifact есть, HITL/xlsx не отправлены | — | файл + Artifact | безопасно |

Параллельные потоки (`img_streams` до 4) умножают P2-P5 на число одновременных кадров. В `_download` в native (`kie_http.py:364-372`) файл пишется напрямую (`write_bytes`), без `.part`/fsync: оборванная запись оставляет «битый» PNG; он отсекается проверкой `is_valid_scene_image` (≥200 КБ + magic), но для видео проверка слабее (`≥32 байт`, `kie_http.py:366`; `kie_kling._download` ≥1024 байт).

### 3.2 Managed: что уже есть в `media_jobs.py`

- **Таблица `MediaJob`** (`:23-44`): `job_id`, `execution_id`, `node_key`, `resource_key`, `input_hash`, `config_hash`, `phase`, `provider_job_id`, `path`, `verification`, `error`; уникальность `(execution_id,node_key,resource_key)`.
- **`MediaRequest`** (`:46-56`): по строке на каждый submit/status/download/upload (категории `generation_submit|status|download|upload`, `:285`), `cost_status="unknown_tariff"`.
- **Резервация submit необратима**: `reserve("generation_submit")` (`:284-303`) → `charge_request` (`actions_v1.py:530-571`) в **одной транзакции** с увеличением общего счётчика запросов и переводом `reserved → submitted` (`stage_reservation`, `:345-350`); повтор → «duplicate submit denied».
- **`open_job`** (`:235-275`): стабильный `resource_key` (`_resource`, `:226-232`) вычёркивает `<uuid8>` из имени, поэтому новое случайное имя файла на повторе даёт тот же ключ. Изменение входа/конфига/модели → отказ «no new submit» (`:267-268`). `submitted|unknown` без `provider_job_id` → hold + «never resubmit» (`:269-271`). `failed|cancelled` → hold, терминально (`:272-274`).
- **`_managed_generation`** (`kie_http.py:401-443`): если `provider_job_id` уже есть, createTask пропускается и сразу опрос (`:415-419`). То есть **«resume polling» для phase `queued|running|completed|unknown` уже реализован** и покрыт тестами (`tests/observe_v1/test_media_transport.py:118-146`, `test_managed_restart_safety.py:360`, `test_native_video_routing.py:221`).
- Любое исключение → phase `unknown` (если не терминальна) + `media_failure` + `_hold` (`:435-443`); отмена локального ожидания с известным job → просто факт `media_wait`, провайдер не отменяется (`:426-430`).

### 3.3 Что именно блокирует `media_jobs` против реального Kie

1. `validate_media_config`: `enabled=True` вне `mode == "synthetic"` отклоняется (`media_jobs.py:158`); `origin` и `download_origins` обязаны быть `http://127.0.0.1|::1:порт` (`:160-166`); `check_url` повторяет это (`:177-185`). У реального Kie `https://api.kie.ai` или VPS-relay (`kie_kling.kie_api_base_url`).
2. `assert_entry` (`:188-213`): `cfg["mode"] != "synthetic"` → отказ; `settings.gpt_api_key` обязан начинаться с `synthetic` (`:210-212`).
3. **`media_transport.json_request` не добавляет заголовок `Authorization`** (клиент создаётся без headers, `media_transport.py:32-33`) и не использует `kie_post_json` (перебор ключей при 401 «model unauthorized», `X-VP-Relay-Token` для VPS). В реальном контуре это 401 при первом же createTask.
4. Whitelist маршрутов: только `POST /api/v1/jobs/createTask` и `GET /api/v1/jobs/recordInfo` (`media_transport.py:17-18`). Не поддержаны `veo/suno/runway` (`kie_http.py:406`) и **Kling-fallback**, который идёт отдельным клиентом `kie_kling.py`.
5. **Референсы/старт-кадры запрещены** (`kie_http.py:541-542` для видео, `:596-597` для картинок: «managed reference upload has no authorized adapter»). Все клипы пайплайна имеют `start_frame`; картинки shot2/K2/K3 и персонажные листы передают `refs` (`generate_images.py:1822-1860`). То есть **managed-видео невозможно без адаптера загрузки**, а managed-картинки работают только для кадров без референсов.
6. `videos`: `managed=False` (`node_capabilities.py:63-64`); `generate_videos.py` не вызывает `persist_artifact`, нет верификатора на применении, `media_view` показывает «не принято» (`media_view.py:152-153`).
7. `hero`/`items`: нет записи в `_CAPABILITIES` → `capability_for` даёт `unsupported` → `open_job` падает «media kind/node scope mismatch» (`media_jobs.py:246-247`); имена файлов `characters/<id>.png`, `hero_*`, `predmet*` не проходят `_resource` (допустимы только `frame_*`, `clip_*`, `voice_full`, `:228-231`); `project_id` не передаётся (`generate_hero.py:805-819`, `generate_items.py:148-162`).
8. Бюджет считает **запросы**, а не деньги: `request_limit ≤ owner_policy['max_requests']` (`managed_launch.py:167`), каждая проверка статуса тратит единицу. Тариф `unknown_tariff`, денежного потолка нет (`ORCHESTRATOR_NATIVE_MEDIA_PROGRESS_20260930.md`, последний абзац: «Unknown prices/credits are not a monetary cap»).

Пробелы внутри самого resume (даже в synthetic):
- **Нет возобновления из `downloading`/`verifying`.** `stage_reservation` разрешает `status` только из `{queued,running,completed,unknown}` (`media_jobs.py:352`), а URL результата в `MediaJob` не хранится (колонок нет). Падение посреди скачивания → следующий вход упирается в «status requires known unfinished provider job», phase → `unknown`, hold. Нужны колонка `result_urls` (или запись в `verification`) и/или допуск `status` из `downloading|verifying`.
- Окно между `create_task` (ответ получен, `kie_http.py:417`) и `job.update(phase="queued", provider_job_id=...)` (`:418`) — отдельная транзакция. Падение здесь = «submit received без committed id»; тест гарантирует hold и ноль повторных POST (`test_managed_restart_safety.py:319`). Это правильно (fail-safe), но оплаченное задание теряется для автоматики. Устранить без lookup на стороне Kie нельзя (см. п. 7, неопределённость U1).
- Провайдерский `state=fail` → phase `failed` терминально (`kie_http.py:325-331`, `media_jobs.py:313-316`); новый submit для того же `(execution,node,resource)` невозможен. Это сознательно, но означает: «исправить промпт и повторить» в managed = новый execution или человек.
- Хук снятия hold для возобновления (`ActionSupervisor.set_held(False)`) в коде есть (`media_jobs.py:220-223`), но отдельного действия «resume media job» в `node_capabilities` нет: все медиа-ноды имеют только `READ_ACTIONS` (`:5, 61-76`), решение `human_only` (`:90-128`).

---

## 4. Callback или polling

**Только polling.** Для `api="jobs"` в тело `createTask` `callBackUrl` **не добавляется** (`kie_http.py:136-167`; `kie_catalog.build_payload` для jobs возвращает `{"model":..., "input":...}`, `:1680-1746`). Единственное место с `callBackUrl` — заглушка `https://localhost/kie-callback` для `suno`/`runway` (`kie_http.py:40, 147-149`) исключительно потому, что те API требуют поле; результат всё равно забирается опросом. В репозитории нет HTTP-обработчика Kie-колбэка.
- Опрос: `GET /api/v1/jobs/recordInfo?taskId=` (`_POLL_PATHS`, `kie_http.py:45-50`), интервал 4 с (`:37`), общий дедлайн 1200 с по умолчанию (`:38`), для картинок 600 с (`generate_kie_image` timeout), для видео 900-1200 с.
- Нормализация состояния: `jobs`: `state` ∈ `success|fail`, иначе pending (`_task_state`, `:250-257`); в managed детальные фазы `waiting|queuing|pending|generating` (`:326-328`).
- Результат: `resultJson` → `resultUrls` (`_result_media_urls`, `:229-247`); берётся первый URL.
- Вывод: **«resume polling» технически достаточно одного `taskId`** (+ модель/api-семейство и путь для сохранения). Колбэк как отдельный механизм доставки для «возобновления» не нужен и не используется.
- Неопределённость U1: умеет ли Kie отдавать список недавних задач аккаунта (чтобы найти «осиротевший» taskId по времени/промпту) — в коде не используется; требует проверки по документации Kie, не проверено. Если такого эндпоинта нет, P2 (ответ потерян) по определению разрешается только человеком + сверкой баланса.

---

## 5. Дизайн «resume, never resubmit»

### 5.1 Принципы
1. **Запись ДО POST.** Перед любым платным submit durable-запись `reserved` (fsync) с ключом и хэшем входа. Нет записи — нет POST.
2. **Запись СРАЗУ ПОСЛЕ ответа** с `taskId` (до первого опроса).
3. **Неоднозначный исход = стоп.** `reserved` без `taskId` после падения процесса, `ReadTimeout` на createTask, обрыв соединения после отправки, ответ без `taskId` при HTTP 200 → состояние `unknown`: автоматических POST нет, нужен человек (или проверка баланса).
4. **Только определённые отказы разрешают новый submit:** HTTP/JSON-код, однозначно означающий «не принято» (401/402/403/422/429/433…), или провайдерское `state=fail` для известного `taskId`. Каждый новый submit по тому же ключу — не больше `max_submits` (по умолчанию 1 для оркестратора).
5. **Таймаут ожидания известного задания ≠ отказ.** Это `pending`: остановить попытку, оставить запись `submitted`, не жечь лестницу, продолжить опрос при следующем входе.
6. **Ключ = намерение, а не файл.** Имя файла содержит случайный `uuid8`; ключ должен быть стабильным: `images`: `frame_NNN[_s2]`, `videos`: `clip_NNN[_s2]`, `hero`: `<excel_id>` или `hero_i_vV`, `items`: `predmet<idx>`. Для сознательной регенерации (HITL «regenerate», vision-regen, `finalize_or_retry`) добавляется `intent` (для картинок уже есть `attempt_number = len(approve_images HITL)+1`, `generate_images.py:1759-1761`; vision-раунд `META_ROUND`), иначе регенерация будет ошибочно «возобновляться» к старому результату.
7. **Денежный потолок в самом журнале.** Перед `reserved` считать `kie_catalog.estimate_credits(spec, values)` (`kie_catalog.py:1580`), накапливать сумму по execution/проекту и отказывать при превышении (закрывает `unknown_tariff` для submit; статусы/скачивания остаются неучтёнными).

### 5.2 Конечный автомат записи (одна строка на ключ)

```
reserved --POST ok, taskId--> submitted --poll success--> succeeded(urls)
   |                              |                           |
   |--POST rejected (4xx код)-->rejected   |--state=fail-->failed        --download+verify--> downloaded --apply--> applied
   |--timeout/обрыв/нет id----->unknown    |--poll timeout--> submitted (pending, не ошибка)
```
- Допустимый новый submit: из `rejected` и `failed` (в пределах `max_submits`), никогда из `reserved/unknown/submitted/succeeded/downloaded`.
- Возобновление: `submitted` → опрос того же `taskId`; `succeeded` → скачивание по сохранённым URL без опроса; `downloaded` → верификация/apply.
- `reserved` без `taskId` на старте процесса = `unknown` (процесс умер между P1 и P3).

### 5.3 Где хранить
Два совместимых варианта.
- **A (рекомендуется для native, не зависит от оркестратора):** файловый журнал рядом с проектом `project.data_dir/kie_ledger/<key>.json`, запись атомарно (`tmp` → `fsync` → `os.replace`). Плюсы: работает для hero/items/videos без миграции БД и без master; одинаково видят человек и оркестратор. В репозитории уже есть похожий приём — sidecar с полями `status`/`job_id` и режимом `require_file=False` «для pending» (`generation_storage.write_sidecar`, `:146-197`), но **нигде не используется для pending** и не атомарен; ключ у него — случайное имя файла.
- **B (для оркестратора):** строки `MediaJob`/`MediaRequest` (уже есть) + колонка `result_urls`. Требует решения по архитектуре (пункт A в `MEDIA_NODES_READINESS.md §5`: расширять шлюз на реальные вызовы или нет).

Оркестратор читает журнал A через новый read-only инструмент и/или проецирует его в `MediaJob`. Это даёт «resume» без изменения политики шлюза.

### 5.4 Что должно быть изменено в классификации ошибок (иначе журнал не поможет)
- Новые типизированные исключения (наследники `OutseeImageError`, чтобы не ломать `except OutseeImageError`): `KieSubmitUnknownError` (POST ушёл, исход неизвестен, `context={"uncertain":True,"no_retry":True}`) и `KieTaskPending(task_id)` (таймаут ожидания/опроса известного задания).
- `video_error_policy.classify_video_error`: новая ветка **до** строки 196 (`media_transient`) и 205 (`media_failed`): `uncertain`/`task_id`+timeout → новое действие `HOLD_UNKNOWN`/`RESUME_LATER` (не `RETRY_SAME`, не `RETRY_BURN`).
- В лестницах (`outsee_retry.py:1185` для images и `:1664-1682` для videos) эти исключения **пробрасываются**, не вызывая GPT-rewrite и новую попытку.

### 5.5 Порядок внедрения по нодам

**Images (первыми).** Причины: самая низкая цена submit, самая маленькая лестница, детерминированный критерий готовности (PNG на диске), `persist_artifact`/верификатор уже есть.
- Ключ: `frame_{n:03d}` / `frame_{n:03d}_s2`; `intent` = `attempt_number`.
- Referensы (`refs`) в ключ/хэш не входят в managed, но в native работают; хэш входа считать от `payload` (как `digest(payload)` в `open_job`) + `intent`.
- Блокировать claim кадра с журнальным `unknown` (`generate_images.py:1120-1148`, `1256-1272`), чтобы `requeue_failed_frames_without_png` (`:629`) и `reset_frames_to_image_prompt_ready` не создавали submit.
- Починить мёртвую ветку Kie (`outsee_retry.py:1046`) **только после** включения журнала и под флагом (иначе открывается новый платный путь без защиты).
- Артефакт: писать `provider_task_id` в `Artifact.meta` (`generate_images.py:1981-1985`, из `result.gen_id`).

**Videos.**
- Нужен журнал в **двух** клиентах: `kie_http.create_task`/`poll_task` и `kie_kling._create_task`/`_poll_task` (Kling-fallback идёт мимо `kie_http`). Единый контекст через `contextvars`, как `current_job` в `media_transport.py:16`.
- Дедлайны 900-1200 с: `KieTaskPending` особенно вероятен; без него лестница сожжёт попытку на живом задании ($0.275-$2.85).
- Фиксировать `post_step_validate.mark_frames_for_video_regen` (`:376-387`): не снимать `video_gen_skip` и не удалять клип, если журнал не в терминале, и добавить потолок числа регенераций на кадр.
- Артефакт и верификатор: `persist_artifact`-аналог для `scene_video` (`generate_videos.py:440-448, 507-515, 653-661, 758-766`) + `verify_artifact(kind="video")`. Для managed — сначала адаптер загрузки референсов (п. 3.3.5).
- Старт-кадр: `last_frame_image` в Kie-ветку не передаётся (`_dispatch`, `outsee_retry.py:1453-1497` и сигнатура `generate_kie_video`): конечные stills (`freeze_stills.video_end_still`, `freeze_stills.py:139-154`) для Kie тихо игнорируются. Это функциональный пробел, не связанный с оплатой, фиксирую для полноты.

**Hero.**
- Excel-режим: ключ = `ch.id`, путь уже детерминирован (`generate_hero.py:1234`), критерий готовности уже есть (`_excel_ids_with_artifact`). Журнал встраивается так же, как для images.
- Legacy: ключ `hero_{i}_v{v}` + `intent` (`is_regen`, `_is_regen_for_pair`, `:597`).
- Для managed понадобятся: `NodeCapability('hero', result_kind='image')`, расширение `_resource` (`media_jobs.py:228`), передача `project_id` (`:805-819`).
- GPT-шаг промпта (до 3 вызовов) отдельно от Kie-шага; его результат кэшируется в `meta.hero_prompt` (`:883`), повтор не должен перегенерировать промпт при возобновлении.

**Items.**
- Ключ `predmet{idx}` + хэш описания. Убрать зависимость от `browser_session()` (`generate_items.py:145`) при HTTP-пути. Передавать `project_id`.
- Критерий готовности уже есть (Artifact `item_reference` по `item_index`, `:49-69`). Лестница и откат на `hero_ready` (`:163-179`) заменяются на «unknown/pending → стоп, показать человеку».

### 5.6 Оркестраторное действие `resume_poll` (слой 2)
- Предусловия (все проверяются в коде, не моделью): запись журнала/`MediaJob` существует, `provider_job_id` не пуст, phase ∈ {`submitted`/`queued`/`running`/`completed`/`unknown`+id, `succeeded`, `downloading`}; НЕ `failed/cancelled/rejected`; `generation_submit` по ключу ровно 1; нет `user_stop`/`journal_gap`/`uncertain_request` без id; бюджет статусов/скачиваний не исчерпан.
- Эффект: снять hold (`ActionSupervisor.set_held`, `media_jobs.py:220-223`) и один раз перезапустить **оригинальный** исполнитель в том же `execution_id` (в managed `open_job` вернёт ту же строку, `_managed_generation` пропустит createTask, `kie_http.py:415-419`).
- Пост-условие (`verify`): число `MediaRequest(category="generation_submit")` для `job_id` не изменилось; артефакт прошёл `verify_artifact`; иначе → `human_review`.
- Добавить `'resume_poll'` в `actions` только для `images` (`node_capabilities.py:61-62`); остальным нодам после соответствующего принятия. Запрещено включать `retry_same`/`retry_profile` для медиа.
- Не путать с `recover_node` (`actions_recovery.py:149+`): он требует `native_json_retry` и текстовую семантику, блокируется `uncertain_request`.

### 5.7 Что может и чего не может доказать верификация

Реализация: `artifact_verifier.verify_artifact` (`artifact_verifier.py:59-122`), вызывается из `media_transport.verify` (`:93-107`) только в managed.

| Доказывает | Не доказывает |
|---|---|
| Файл существует, внутри каталога проекта (`contained_path`, `:15-19`), размер >0 и ≤256 МБ (`:63-64`), sha256 (`:65`) | Что картинка соответствует промпту, стилю, персонажу; нет артефактов, лишнего текста, NSFW |
| Картинка: Pillow открывает, формат ∈ PNG/JPEG/WEBP/GIF, пиксели ≤40 млн, `verify()` и `load()` без исключений (`:67-77`) | Что это не пустое/однотонное изображение (проверка энтропии нет) |
| Видео: `ffprobe` без stderr, есть видеопоток, `duration>0` и конечна, известный контейнер, ширина/высота >0 (`:80-112`); опциональные `expected` width/height/min/max duration (`:115-119`) | Что в видео есть движение (нет `freezedetect`), нет чёрных/зависших кадров, соответствует старт-кадру и промпту; что длительность равна запрошенной (если `expected` не передан) |
| Целостность между apply и финальным гейтом (хэш совпадает, `media_jobs.py:75-76, 138-142`) | Что провайдер списал именно столько и один раз (это даёт только баланс) |

Нюансы:
- В managed `verify(job)` вызывается **без `expected`** (`kie_http.py:424`), поэтому соответствие ожидаемому соотношению сторон не проверяется. Размеры вывода Kie зависят от модели; добавлять `expected` нужно с калибровкой (U3).
- `if process.returncode or ... or stderr.strip()` (`artifact_verifier.py:93`): любое предупреждение ffprobe в stderr даёт `failed`. Возможны ложные отказы на валидных MP4; не проверено на реальных файлах Kie.
- `ffprobe` на хосте найден (`ffmpeg 8.1.2`, WinGet), но в sandbox-образе приёмки его не было до 30.09 (`ORCHESTRATOR_NATIVE_MEDIA_PROGRESS_20260930.md`, «Approved diagnostic dependency»). Зависит от PATH процесса приложения.
- Native-проверки слабее: `is_valid_scene_image` (≥200 000 байт + magic, `scan_frames.py:34, 59-75`), для видео ≥32/≥1024 байт и `postprocess`/`_assert_video_not_mush` только для Outsee (`outsee_http.py:611`).
- Семантическое качество не проверяется нигде: `semantic_quality_verified: False` во всех контрактах (`node_capabilities.py:35-38`, `media_jobs.py:144-145`). Единственный семантический контур — `vision_check_loop` (GPT-оценка), и он сам **тратит деньги** и удаляет файлы.

---

## 6. Конкретные шаги реализации (file:line), тесты со стабами, план live-запуска

### 6.1 Шаги

Порядок важен: пока нет шагов 1-4, шаги 7-8 включать нельзя.

1. **Новый модуль `app/services/kie_task_ledger.py`** (новый файл).
   - API: `reserve(project_dir, key, input_hash, est_credits, max_submits)`, `record_task(key, task_id, api, model)`, `record_result(key, urls)`, `mark_downloaded(key, path, sha256)`, `mark_applied(key)`, `mark_unknown(key, reason)`, `find(key)`.
   - Атомарная запись `tmp+fsync+replace`, блокировка файла, счётчик кредитов за проект.
   - Контекст `current_ledger` (`contextvars`), по образцу `current_job` (`media_transport.py:16`).
2. **`app/bots/kie_http.py`**
   - `run_generation` (`:375-398`): принимает `ledger_key`/`intent`; до `create_task` (`:387`) `reserve`; если запись уже `submitted` → пропуск `create_task`, сразу `poll_task`; `succeeded` → сразу `download`; `reserved/unknown` → `KieSubmitUnknownError`.
   - `create_task` (`:136-167`): обернуть `kie_post_json` (`:156`): `httpx.TimeoutException/TransportError` → `KieSubmitUnknownError` (не `KieHttpError`); сразу после парсинга (`:159-166`) `record_task`. Определённые отказы (`_check` code≠200 на create) → `ledger.mark_rejected`.
   - `poll_task` (`:283-356`): терпеть `_check`-ошибки 5xx по образцу `kie_kling._poll_task` (8 подряд); таймаут (`:351-355`) → `KieTaskPending(task_id)`; при success → `record_result(urls)` до возврата.
   - `download` (`:359-372`): `.part` + `fsync` + `os.replace` + порог размера, затем `mark_downloaded`.
   - `generate_kie_image` (`:578-644`) и `generate_kie_video` (`:494-575`): принять и прокинуть `ledger_key`.
3. **`app/bots/kie_kling.py`**: `_create_task` и `_poll_task` — то же (`ledger` через контекст). Fallback Kling иначе остаётся вне защиты.
4. **`app/services/video_error_policy.py`**: новые исключения/действие и ветки в `classify_video_error` до `:187` и `:205`. Обновить `outsee_retry.py:1185` (images) и `:1664-1682` (videos): пробрасывать `KieSubmitUnknownError` и `KieTaskPending`.
5. **`app/orchestrator/steps/generate_images.py`**
   - `_generate_and_send` (`:1763-1771`): вычислить `ledger_key` от `frame.number`/`shot`, передать в оба вызова `generate_image_with_retries` (`:1889`, `:1909`); при возобновлении использовать `out_path` из записи, а не новый случайный.
   - `Artifact.meta` (`:1981-1985`): добавить `provider_task_id`.
   - `_claim_shot1_batch` (`:1120-1148`), `_claim_shot2_batch` (`:1256-1272`): пропускать кадры с `unknown` в журнале и показывать их оператору. `run` (`:629`): `requeue_failed_frames_without_png` не должен ставить такие кадры.
6. **`app/services/post_step_validate.py:340-399, 402-409`**: учитывать журнал; потолок числа регенераций на кадр. Так же ограничить выход без исключения из `generate_hero.py:834-870`, `:1364` и `generate_items.py:163-179`.
7. **`app/orchestrator/steps/generate_videos.py`**: ключ `clip_NNN[_s2]` в `:416/:420`, `:483/:487`, `:607/:611`, `:712/:716`; не считать `KieTaskPending`/`KieSubmitUnknownError` ошибкой кадра (`:632-647`, `:737-751`); claim (`:266-322`) уважает журнал.
8. **Hero/items**: `generate_hero.py:771-773, 805-819, 1234, 1341-1356`; `generate_items.py:139-162`: ключи, `project_id`, убрать `browser_session()`.
9. **Исправление мёртвой ветки** `outsee_retry.py:1046` (вынести из `if use_outsee_api:`) — отдельный коммит, под флагом, после шагов 1-6.
10. **Слой оркестратора** (после 1-6 и решения по архитектуре):
    - `node_capabilities.py:61-62`: `'resume_poll'` в `actions` для `images`.
    - Реализация в `actions_v1.py` (рядом с `charge_request`, `:530`).
    - Колонка `result_urls` и допуск `status` из `downloading|verifying` в `media_jobs.py:352`.
    - Отдельно: адаптер загрузки референсов и добавление `Authorization` в `media_transport.json_request` (`:32-33`) под реальный профиль.
    - Обновить `media_view.py:148-163`, чтобы показывать запись журнала.

### 6.2 Тесты (только стабы, без сети и платных вызовов)

Шаблон: `tests/observe_v1/test_native_video_routing.py:76-137` (патч `httpx.AsyncClient.send` + `httpx.MockTransport`, синтетические ключи, запрет внешних хостов) и `tests/test_media_polling_retry.py` (стаб ответов 502/504). Хелперы сети в тестах не вызываются: все POST/GET идут в `MockTransport`. Тест `test_native_video_routing.py` требует ffmpeg (жёсткий отказ без него, `ORCHESTRATOR_STATUS_CURRENT.md`).

| # | Сценарий | Проверка |
|---|---|---|
| T1 | create ok, на первом GET `recordInfo` читаем журнал | `taskId` уже записан **до** первого опроса |
| T2 | процесс «умирает» на первом опросе (исключение-имитация `ProcessDied`), новый вызов с тем же ключом | ровно 1 POST createTask; повторный опрос того же `taskId`; 1 скачивание |
| T3 | POST createTask → `httpx.ReadTimeout` (через images- и video-лестницы) | ровно 1 POST; `KieSubmitUnknownError`; повторный вход по ключу — 0 новых POST |
| T4 | createTask 402/401/422 | запись `rejected`; в лестнице поведение прежнее (`test_native_video_routing.py:230` уже требует остановку при 402) |
| T5 | опрос превышает дедлайн | `KieTaskPending`, лестница не сжигает попытку; следующий вход опрашивает тот же `taskId` |
| T6 | `recordInfo` отдаёт 5xx/JSON code 500 трижды, затем success | native-опрос терпит, ресабмита нет |
| T7 | провайдер `state=fail` | запись `failed`, ресабмит только в пределах `max_submits`; сверка числа POST с ожидаемым (images: ≤ лимита ключа; videos: 4+1 как в `:240-249` уже существующего теста) |
| T8 | обрыв посреди скачивания | `.part` не принимается как итог; повтор скачивает по сохранённым URL без опроса и без POST |
| T9 | два потока с разными ключами | журналы не пересекаются, суммарный счётчик кредитов верен |
| T10 | БД-фикстура как в `tests/test_generate_images_loop_guard.py:30` | `_claim_shot1_batch` не отдаёт кадр с `unknown`; `requeue_failed_frames_without_png` его не ставит |
| T11 | `finalize_or_retry(step="video")` при незавершённом журнале | `mark_frames_for_video_regen` не снимает skip, счётчик регенераций ограничен |
| T12 | потолок кредитов исчерпан | 0 POST, понятная ошибка |
| T13 | регрессия на мёртвую ветку | при включённом флаге native Kie-картинка делает ровно один createTask (сейчас красный тест) |
| T14 | managed: phase `downloading`, затем рестарт | status допустим, URL берётся из `result_urls`, POST не повторяется |
| T15 | верификатор | обрезанный PNG → `failed`; нулевой файл → `failed`; MP4 с ffprobe (skip без него); ложный отказ на benign-stderr зафиксировать как known issue |

Существующие тесты, которые должны остаться зелёными: `tests/observe_v1/test_media_jobs.py`, `test_media_transport.py`, `test_managed_restart_safety.py:315-360`, `tests/test_outsee_video_fallback.py`, `tests/test_video_error_policy.py`, `tests/test_generate_videos_skip.py`. Известные уже красные в `ORCHESTRATOR_STATUS_CURRENT.md` («Known pre-existing…», `test_outsee_video_fallback.py` — 3 кейса, `test_media_polling_retry.py::test_outsee_poll_recovers_from_transient_502_streak`) не связывать с новыми изменениями.

### 6.3 План live-запуска и потолок стоимости на шаг

Предусловия: явное разрешение пользователя **на каждый платный шаг**; перед и после шага читать баланс `GET /api/v1/chat/credit` (бесплатно); ограничитель `max_submits=1` на ключ и суммарный потолок кредитов в журнале (`estimate_credits`). Цены ниже — **оценки по каталогу**, не измерения; после первой пробы заменить измеренными (шаг калибровки). Баланс может меняться от чужих списаний (`KIE_COST_CALIBRATION.md`), поэтому вывод по одной пробе делать по двум повторам.

| Шаг | Что | Оценка за submit | Потолок шага |
|---|---|---|---|
| 0 | Только стабы (раздел 6.2). Баланс не трогается | $0 | $0 |
| 1 | Скрипт-проба вне пайплайна: `z-image`, 1 картинка, процесс принудительно завершается сразу после `record_task` (до первого опроса), затем перезапуск → ожидается ровно 1 списание по балансу | 0.8 кр = $0.004 | $0.02 |
| 2 | Проба «неизвестного submit»: короткий read-timeout на createTask (connect нормальный) для `z-image`; смотрим, создалось ли и списалось ли задание-сирота, и подтверждаем, что код стоит в `unknown` без повторов | 0.8 кр | $0.02 |
| 3 | `images` через узел: 1 кадр, `flux-2-pro` 2K (после включения журнала и под флагом по п. 6.1.9) | 7 кр = $0.035 | $0.10 |
| 4 | `images`: 3 кадра, тот же режим; проверка параллелизма `img_streams` | ≈ $0.11 | $0.20 |
| 5 | `hero` (excel-режим, 1 персонаж) и `items` (1 предмет) | ≈ $0.035 каждый | $0.10 |
| 6 | `videos`: 1 клип, самый дешёвый i2v: `seedance-1-5-pro` 480p 5 с (1.75 кр/с) | 8.75 кр = $0.044 | $0.15 |
| 7 | `videos`: Kling 2.6 (fallback-клиент), 1 клип | 55 кр = $0.275 | $0.40 |
| 8 | `videos`: Seedance 2.5 720p | 315 кр = $1.58 | **не запускать без отдельного одобрения**; 1080p (дефолт проекта) = $2.85, поэтому предварительно зафиксировать `video_resolution=720p` |

Сумма потолков шагов 1-7 ≈ $1.0. Критерии успеха каждого шага: ровно один `generation_submit` на ключ (журнал), `taskId` записан до первого опроса, при принудительном обрыве — ноль повторных POST и один итоговый файл, расход по балансу в пределах оценки ±разрешение 0.01 кр.

Замечание для шага 6: старт-кадр загружается через `ensure_public_image_url` (`outsee_http.py:935`), для которого нужен настроенный Yandex Object Storage (публичные хосты отключены, `:944-947`); если его нет, i2v-проба упадёт ещё до оплаты. Альтернатива — `kie_http.upload_file` (`:109-133`), но в видео-пути она не используется; платность загрузки в коде не описана (U4).

---

## 7. Неопределённости и что не проверено

- U1. Есть ли у Kie эндпоинт списка/поиска недавних задач (чтобы разрешать P2 автоматически) и сколько хранятся результаты по `taskId` / URL (важно для отложенного resume). В коде нет, документацию Kie не открывал.
- U2. Неограниченность цикла `finalize_or_retry` → повторные лестницы (п. 0.5, 2.1): выведено чтением (`post_step_validate.py:376-387`, `advance_runner.py`, `main.py`), запуском не проверено; возможно ограничение вне просмотренных файлов (UI-пауза, `gen_queue`).
- U3. Размеры/длительность реальных выходов Kie для `expected` в верификаторе, поведение ffprobe-stderr на реальных MP4.
- U4. Платность загрузки файлов Kie и срок жизни загруженных URL (в коде комментарий «~3 суток», `kie_http.py:112`).
- U5. Что делает native-путь с Kie-слагом в `outsee.generate_image` (CDP) на практике: ожидаю `OutseeImageError` (нет модели на Outsee) или `AttributeError`, если `outsee is None`; запуском не проверено.
- U6. Точная привязка hold-release к действию оператора: `set_held(False)` есть, но отдельного пути «снять hold для resume» в списке действий нет; потребуется проверка `actions_v1.py` целиком (я читал только `charge_request` и `check_managed_start`).
- U7. Маршрут `nano-banana-2`: по `media_route.py` — Outsee; в `MEDIA_NODES_READINESS.md` указан как Kie. Верить коду, цену Outsee не оценивал.
- U8. Статус-документы называют routing-дефект Seedance «исправленным сегодня» (`ORCHESTRATOR_STATUS_CURRENT.md`, строка Videos); в прочитанном коде `generate_kie_video`/лестница выглядят корректно, регрессионные тесты — `tests/observe_v1/test_native_video_routing.py:179-258`. Сам фикс (коммит) не искал.
- Цены в разделе 2.2 — статический каталог на 20.08.2026 (`kie_catalog.py` шапка), не сверены с балансом.
