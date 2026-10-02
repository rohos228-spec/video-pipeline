# Медиа-ноды: готовность к ограниченному восстановлению (подготовка, без live-запусков)

Статус: **все медиа-ноды остаются human-only**. Ни один платный запуск не выполнялся при подготовке,
сеть не использовалась, `.env` не читался. Все пути относительно `video-pipeline-observe-v1/`.
Декларативные решения: `app/services/node_capabilities.py:90` (`MEDIA_RECOVERY_DECISIONS`),
тесты: `tests/test_media_nodes_recovery_decision.py`.

Почему отдельная таблица, а не поле `NodeCapability`: `contract()` строится через `asdict()`
(`node_capabilities.py:35-38`) и уходит в `decision_v1.py:184,274+` и `managed_launch.py:69`; новое поле
изменило бы эти полезные нагрузки. Поэтому данные вынесены в отдельный словарь, который в рантайме никто не читает.

## 1. Что такое «медиа-нода» здесь

Нода из `WORK_NODES` (`app/orchestrator/node_registry.py:36-125`), которая тратит деньги у провайдера
или даёт внешний необратимый эффект: `hero`, `items`, `images`, `videos`, `audio`, `music`, `sfx_gen`,
`assemble` (деньги не тратит, но финальный артефакт), `publish`. Текстовые/структурные ноды
(plan, script, split, scene_*, enrich_*, image_prompts, animation_prompts, sfx_plan) сюда не входят;
`plan`/`script` уже включены отдельно (`node_capabilities.py:44-51`).

## 2. Общие факты (применимы к нескольким нодам)

- Управляемый (managed) медиа-путь существует только как синтетический loopback: `validate_media_config`
  отклоняет `enabled=True` вне режима synthetic и любые не-loopback origin
  (`app/services/media_jobs.py:151-175`, строка 155: «media production transport has no authorized network profile»).
  `assert_entry` дополнительно требует синтетический ключ приложения (`media_jobs.py:188-213`).
  Статус-документ подтверждает: шлюз «не контролирует реальные вызовы провайдера»
  (`docs/ORCHESTRATOR_STATUS_CURRENT.md:32`), медиа-восстановления нет (`:29`).
- Идемпотентность в managed-режиме: таблица `MediaJob` с уникальностью `(execution_id, node_key, resource_key)`
  (`media_jobs.py:23-44`); резервирование submit необратимо, второй submit запрещён
  (`stage_reservation`, `media_jobs.py:339-350`, «duplicate submit denied»); при `submitted/unknown` без
  `provider_job_id` — hold и «never resubmit» (`media_jobs.py:270-271`); `failed/cancelled` не пересабмитятся
  (`:273-274`). Известный job можно только поллить (`kie_http.py:308`). Это защита от двойной оплаты,
  но только в синтетическом контуре.
- Вне managed-режима (реальная работа) durable job id нет: `run_generation` делает create→poll→download
  без записи job в БД (`app/bots/kie_http.py:375-398`); `taskId` существует только в памяти процесса.
  Ключи в `kie_post_json` перебираются при 401 «model unauthorized» (`app/bots/kie_kling.py:221-250`).
- Диагностика медиа-отказов (`app/services/diagnosis.py`): `media_failure` (задача упала или файл не прошёл
  проверку, `:177-186`), `uncertain_request` при `uncertain=True` или `phase=='unknown'`
  с фактом «Новый submit запрещён» (`:179-182`), `media_policy` (`:188-192`), `api_error` и `uncertain_request`
  для `ReadTimeout/ConnectTimeout/CancelledError` (`:206-217`). Во всех случаях `status='human_review'`.
  `uncertain=not bool(provider_job_id)` задаётся в `kie_http.py:441`.
- Критерий восстановления сейчас только текстовый: `RECOVERY_INCIDENTS={'invalid_json','missing_payload'}`
  (`app/services/decision_v1.py:23`); медиа-инциденты в него не входят. Дополнительно `actions_recovery.py:207`
  блокирует восстановление при наличии `uncertain_request`/`journal_gap`/`user_stop`.
- Нативный счётчик отказов шага (не Decision): 3 попытки на цикл, сон 30 мин, abandon на 9-м отказе
  (`app/services/step_failure_policy.py:1-9, 24-26`). Он остаётся единственным автоматическим повтором медиа-нод.
- Курс: 1 кредит Kie = $0.005 (`app/services/kie_catalog.py:29`). **Стоимость медиа не калибровалась**
  (`docs/KIE_COST_CALIBRATION.md`, раздел «Ограничения»); калибровка относится только к `gpt-5-6-sol`.
  Любые суммы ниже — оценки по статической таблице `kie_catalog` и грубым ставкам трекера
  (`app/services/api_tracker_hook.py:49-64`), помечены «оценка».

## 3. Таблица по нодам

| Нода | Исполнитель (cap. / код) | Как тратятся деньги | Идемпотентность / job id | Нативные повторы / опрос | Классы диагностики | Безопасен ли ограниченный retry оркестратора |
|---|---|---|---|---|---|---|
| `images` | `generate_images.run → kie_http`, managed=True, действия только READ (`node_capabilities.py:61-62`). Реально: `generate_image_with_retries` (`generate_images.py:1885-1913`) | За каждый submit картинки; Kie nano-banana-2 2K 12 кр (`kie_catalog` `nano-banana-2`); провайдер outsee или kie (`outsee_retry.py:978` `image_provider_for`) | managed: `MediaJob` + `provider_job_id`, submit один раз (см. §2). Нативно: нет, `gen_id` только в имени/ID промпта (`generate_images.py:1796`) | До 3 попыток на оригинальный промпт, затем GPT-rewrite и ещё 3 (`:1906-1909`, `outsee_retry.py:991-1000`); скачивание до 8 раз (`outsee_retry.py:1004`); ожидание лимита до 24 раз (`:303`); опрос Kie каждые 4 с до 1200 с (`kie_http.py:37-38`). В managed вся лестница заменена одним job (`outsee_retry.py:978-994`) | `media_failure`, `uncertain_request`, `media_policy`, `api_error` | **Нет.** `failed`/`unknown` терминальны и не пересабмитятся (`media_jobs.py:270-274`); при `uncertain` провайдер мог списать. Нативный путь — до 6 платных submit без job id |
| `videos` | `generate_videos.run → kie_http`, **managed=False** (`node_capabilities.py:63-64`). Реально `generate_video_with_retries` (`outsee_retry.py:1347`) | Самая дорогая единица: Seedance 2.5 720p 63 кр/с (`kie_catalog` `seedance-2-5`), Kling 2.6 5 с 55 кр, Veo 30-370 кр; подпись «видео» | managed: тот же `MediaJob`; нативно job id только в памяти. Ключ повторного `generate_audio=False` в managed (`outsee_retry.py:1393`) | Лестница: 3 rewrite + 1 final primary, затем 3 Kling fallback (`generation_options.py:247-252`); классификатор ошибок и `STOP_AUTH` для 401/402/403 (`video_error_policy.py:37,80-82`); 5 подряд отказов кадра → `video_gen_skip` (`generate_videos.py:140`); пауза 60 с при лимите (`:929`) | те же + `VideoLadderExhaustedError` не отображается в отдельный класс | **Нет.** `managed=False`; до 7 платных submit на клип; статус-док: «not yet wired» (`ORCHESTRATOR_STATUS_CURRENT.md:37`) |
| `hero` | `generate_hero.run` (`generate_hero.py:475`), **нет записи в `_CAPABILITIES`** → `capability_for` даёт «unsupported», только READ-действия | За каждую картинку героя (16:9, relax, `generate_hero.py:73-74`); цена как у `images` | Нет durable job; `open_job` сверяет `result_kind` ноды и всё равно отклонил бы hero («media kind/node scope mismatch», `media_jobs.py:246-247`) | `generate_image_with_retries`: 3 + GPT-rewrite + 3 (`generate_hero.py:794-803`); GPT-промт до 3 раз (`:651`); при исчерпании статус откатывается на `frames_ready` без падения | как у `images` (если бы был в managed; сейчас managed не включён) | **Нет.** Нет ни capability, ни managed-жизненного цикла |
| `items` | `generate_items.run` (`generate_items.py:86`), **нет записи в `_CAPABILITIES`** | За каждый предмет (одна картинка, `item_descriptions`) | Нет durable job; file name с случайным суффиксом `uuid4` (`generate_items.py:139`) → повтор создаёт новый файл и новый платный запрос | 6 попыток (`generate_items.py:152,166`), при отказе статус на `hero_ready` (`:171-173`); открывает `browser_session()` (`:145`) | как у `images` | **Нет.** Тот же класс, что `hero` |
| `audio` | `generate_audio.run → elevenlabs_api`, managed=False (`node_capabilities.py:65-67`). Реально `synthesize_per_frame_audio` (`frame_audio.py:476`, вызов `synthesize_speech` `:516`) | ElevenLabs TTS за символы (весь voiceover одним запросом); прямой API, **не Kie** — таблица `kie_catalog` (6 кр/1000 симв.) к нему не применима; трекер: 0.01 USD/вызов, грубо | Нет ключа идемпотентности; ответ — файл. Перед синтезом удаляются старые `frame_*.mp3` (`frame_audio.py:490`) | 3 попытки на таймаут/сеть/429/502/503/504 с паузой 2-2.5 с×N (`elevenlabs_api.py:192-231`); если озвучка уже лежит на диске, синтез вообще пропускается (`generate_audio.py:251-281`) | `api_error`, `uncertain_request` (ReadTimeout и т. п., `diagnosis.py:206`) | **Нет.** Таймаут после приёма запроса сервером неотличим от неприёма; уже внутри 3 нативных повтора |
| `music` | `generate_music.run` (`generate_music.py:36`), **нет записи в `_CAPABILITIES`** | Один GPT-вызов (текст) + одна генерация Suno V5.5 (12 кр ≈ $0.06, `kie_catalog` `suno-music`) | Нет. В managed `_managed_generation` отклонил бы Suno: «provider family has no approved managed route» (`kie_http.py:407`) | Нативных повторов внутри исполнителя нет; опрос `poll_task` 4 с / 900 с; при `FIRST_SUCCESS` без URL ждёт SUCCESS (`kie_http.py:332-336`); музыка с диска пропускает платный путь (`generate_music.py:43-58`) | `api_error`, `media_failure`; `uncertain` на таймауте | **Нет.** Повтор = новый submit + новый GPT-вызов; Artifact пишется после оплаты (`generate_music.py:154`) |
| `sfx_gen` | `generate_sfx.run → sfx_gen.generate_sfx_files`, managed=False (`node_capabilities.py:74-76`) | ElevenLabs `/v1/sound-effects` по событию (при наличии ключа, `sfx_gen.py:162-186`), иначе бесплатный локальный синтез. Оценка Kie-аналога: 2.5 кр ≈ $0.0125 за событие | Нет idempotency key. Чекпоинт `sfx_files` по индексу события: готовые файлы не пересоздаются (`sfx_gen.py:206-222`) | При любой ошибке вызова — **тихий откат на локальный синтез** (`sfx_gen.py:233-246`) без повтора платного вызова | `contract_rejected` (verify), `api_error` | **Нет.** Исход неудачного платного вызова неизвестен (мог быть списан), а откат маскирует ошибку |
| `assemble` | `assemble.run → assembly.assemble`, managed=False (`node_capabilities.py:68-70`; `assemble.py:114`) | Провайдеру ничего; локальные ffmpeg/ASR (CPU) | Выходной файл в проекте, дедупликации job нет | Нативных повторов нет; `ffmpeg subtitle burn failed` → `RuntimeError` (`assemble.py:550`) | `contract_rejected` | **Деньги не риск, но нет верификатора восстановления** и это финальный артефакт перед `approve_final` (`ORCHESTRATOR_NATIVE_MEDIA_PROGRESS_20260930.md`, строка «Assembly executor») |
| `publish` | `publish.run` (`publish.py:51`) | Денег нет; внешние аккаунты | Нет | managed-публикация отключена: `MediaPolicyError("managed publication is disabled")` (`publish.py:53-57`); `SOCIAL_PUBLISH_ENABLED=false` → пропуск | — | **Нет, необратимо:** повтор может опубликовать дважды. В `_CAPABILITIES` разрешены только `read_events/read_contract/human_review` (`node_capabilities.py:77-79`) |


## 4. Главные риски (коротко)

1. **Двойная оплата вне managed-режима**: у всех реальных путей нет durable job id и ключа идемпотентности;
   `taskId` Kie живёт только в локальной переменной (`kie_http.py:387-389`). Падение процесса между create и записью
   результата теряет задачу, которую провайдер уже оплатил.
2. **Нативные лестницы уже умножают стоимость**: images/hero/items до 6, videos до 7 платных submit на единицу.
   Любой retry оркестратора поверх — умножение на ещё одну лестницу.
3. **`uncertain_request` после submit = никогда не повторять**: провайдер мог принять и списать
   (`diagnosis.py:179-183`; `actions_recovery.py:207`).
4. **Тихие откаты** (sfx_gen → local_synth; hero/items → смена статуса) скрывают исход платного вызова.
5. **Managed-контур синтетический**: нет ни одной проверки на реальном провайдере; калибровки стоимости медиа нет.
6. Тесты managed-медиа используют фикстуры и локальные файлы; качество результата не проверялось
   (`ORCHESTRATOR_NATIVE_MEDIA_PROGRESS_20260930.md`, «provider quality not tested»).

## 5. Что нужно, чтобы включить каждую ноду позже

Общее для всех (блокирующее):
- A. Решение пользователя по архитектуре: расширять шлюз на реальные вызовы или оставить синтетическим
  (`ORCHESTRATOR_STATUS_CURRENT.md:32`).
- B. Калибровка стоимости соответствующей модели по балансу (`GET /api/v1/chat/credit` бесплатен,
  `KIE_COST_CALIBRATION.md`) и замена оценок на измеренные ставки.
- C. Расширить `RECOVERY_INCIDENTS` типизированными медиа-классами и правилом «uncertain → никогда»
  (сейчас только `invalid_json/missing_payload`, `decision_v1.py:23`), с тестами на отказ.
- D. Только после A-C менять `NodeCapability.actions` у конкретной ноды (данные, как у plan/script).

По нодам:
- `images`: уже managed=True и имеет `MediaJob`; нужен реальный сетевой профиль (A), верификатор
  `require_verified_images` на реальных файлах (`media_jobs.py:108`), политика «retry только при
  `failed` до submit», т. е. при `phase=reserved` и отсутствии `provider_job_id`; для `failed` с известным
  job — только человек. Рефы (`reference_images`) в managed запрещены (`kie_http.py:542,597`) — нужен адаптер загрузки.
- `videos`: сначала `managed=True` и worker E2E (`ORCHESTRATOR_STATUS_CURRENT.md:37`), затем то же, что images;
  отдельно — как поверх нативной лестницы лимитировать суммарные submit (бюджет).
- `hero`, `items`: завести `NodeCapability` (result_kind image), путь через `generate_kie_image` с `project_id`,
  чтобы `open_job` их принял; убрать откат статуса как скрытую обработку.
- `audio`: нужен ASR/ffmpeg в окружении (`ORCHESTRATOR_STATUS_CURRENT.md:38`), job-обёртка для ElevenLabs
  с резервированием запроса до отправки и записью результата; повтор только до отправки.
- `music`: managed-маршрут для семейства suno (сейчас запрещён, `kie_http.py:407`), отдельная идемпотентность
  GPT-шага и Suno-шага.
- `sfx_gen`: убрать тихий откат либо записывать провайдера/исход каждого вызова; резервирование перед платным вызовом.
- `assemble`: верификатор вывода и правило продолжения (`recovery_continuation`) с сохранением HITL `approve_final`.
- `publish`: не включать; оставить read-only preview.

## 6. План live-проверки и потолки стоимости (ОЦЕНКИ, не измерения)

Предусловие: явное разрешение пользователя на каждый платный шаг; перед и после каждой пробы читать баланс
бесплатным `GET /api/v1/chat/credit`. Суммы — по статической таблице `kie_catalog` (1 кр = $0.005), не по калибровке;
после первой пробы заменить измеренными. Предлагаемый потолок на весь план: **$2** (оценка запаса, не калиброванный лимит).

| Шаг | Что запускается | Единица | Оценка за 1 submit | Худший случай нативной лестницы | Потолок шага (оценка) |
|---|---|---|---|---|---|
| 0 | Только чтение баланса | — | $0 | — | $0 |
| 1 | `images`, 1 кадр, дешёвая модель (z-image 0.8 кр ≈ $0.004) | 1 картинка | ≈$0.004 | 6 × $0.004 ≈ $0.024 | $0.05 |
| 2 | `images`, 1 кадр целевой модели (nano-banana-2 2K 12 кр ≈ $0.06; flux-2-pro 2K 7 кр ≈ $0.035) | 1 картинка | ≈$0.035-0.06 | 6 × $0.06 ≈ $0.36 | $0.40 |
| 3 | `hero`/`items`, 1 штука (как шаг 2) | 1 картинка | ≈$0.06 | ≈$0.36 | $0.40 |
| 4 | `videos`, 1 клип 5 с, самый дешёвый i2v (Kling 2.6 55 кр ≈ $0.275) | 1 клип | ≈$0.275 | 3 fallback × $0.275 = $0.825 при уже упавшем primary | $1.00 |
| 5 | `videos`, Seedance 2.5 720p, 5 с (63 кр/с × 5 = 315 кр ≈ $1.58) | 1 клип | ≈$1.58 | 4 × $1.58 + 3 × $0.275 ≈ $7.1 | **не запускать без отдельного одобрения** |
| 6 | `music` (Suno 12 кр ≈ $0.06 + один GPT-вызов, GPT калиброван: порядка $0.004-0.008 за вызов) | 1 трек | ≈$0.07 | без нативных повторов ≈ $0.07 | $0.10 |
| 7 | `sfx_gen` с ключом ElevenLabs, 2-3 события (аналог Kie 2.5 кр ≈ $0.0125 каждое) | 3 события | ≈$0.04 | без повторов ≈ $0.04 | $0.05 |
| 8 | `audio` ElevenLabs TTS, короткий текст до 500 симв. (аналог Kie 6 кр/1000 симв. ≈ $0.03/1000) | 1 запрос | ≈$0.015 | 3 нативных попытки ≈ $0.045 | $0.10 |

Сумма потолков шагов 1-4 и 6-8 ≈ $2.1 (оценка); шаг 5 исключён. Ограничения оценок: цены `kie_catalog` не
сверялись с балансом; для ElevenLabs прямой API тариф другой, чем у Kie-прокси; нативные лестницы считались
в худшем случае, когда каждая попытка оплачена (на практике провайдер может не списывать за отклонённый
модерацией запрос — это не проверялось). Для `uncertain`-проб нужна отдельная инъекция сбоя на synthetic-контуре, а не live.

Критерии успеха live-проб: ровно один `generation_submit` на единицу в журнале, `provider_job_id` записан
до первого опроса, при инъектированном обрыве после submit — hold и ноль повторных submit, разница баланса
в пределах оценки.

## 7. Тесты (без сети и БД)

`tests/test_media_nodes_recovery_decision.py`: (1) каждая нода из `WORK_NODES` либо имеет медиа-решение,
либо явно перечислена как не-медиа — добавление нового типа без решения роняет тест; (2) нода со статусом
`generating_*` не может молча оказаться не-медиа; (3) у каждой медиа-ноды нет `retry_same/retry_profile/finish`,
`profile_compatible=False`, решение `human_only`, непустая осмысленная причина; (4) причины платных нод называют
риск двойной оплаты/отсутствие маршрута; (5) форма `contract()` не изменилась.
