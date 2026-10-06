# Медиа-ноды: готовность к ограниченному восстановлению (подготовка, без live-запусков)

Статус: **платного автоповтора надзором у медиа-нод нет**. Допуск надзора к `videos`, `items`, `music` есть только как
возобновление по известному task id внутри режима «только возобновление» (ни одного нового платного запроса; раздел «Допуск
медиа» в `docs/NODE_COVERAGE.md`, сервис `app/services/media_admission.py`); они «под управлением только для восстановления».
Оговорка по `items` (и `images`, `hero`): возобновление по task id Kie работает только при строке журнала с таким id, а нативный
шаг картинки при модели Kie отправляет запрос в Outsee, а не в Kie (`outsee_retry.py:995,1027,1123`), так что для `items` это доказано
сухим прогоном через `kie_http.run_generation`, не маршрутом шага; у `videos` и `music` Kie в рабочем маршруте достижим
(`docs/MEDIA_ROUTE_AUDIT.md`).
Уровни остальных: `audio`, `hero`, `assemble`, `sfx_gen` — «под наблюдением» (проверка результата
`app/services/media_verification.py`, сервис возобновления `app/services/media_recovery.py`), `publish` — «только человек»
навсегда; подробности и что не доказано без живого прогона — в `docs/NODE_COVERAGE.md`, разделы «Медиа-ветка» и «Допуск медиа».
Таблица ниже описывает исходное состояние нативного кода до этих правок
(часть утверждений про отсутствие идентификатора задачи устарела: журнал платных задач и охрана «возобновить, а не отправлять
заново» описаны в разделе «Фаза G» ниже). Ни один платный запуск не выполнялся при подготовке,
сеть не использовалась, `.env` не читался. Все пути относительно `video-pipeline-observe-v1/`.
Декларативные решения: `app/services/node_capabilities.py:90` (`MEDIA_RECOVERY_DECISIONS`),
тесты: `tests/test_media_nodes_recovery_decision.py`.
Сводная таблица по всем 24 типам нод (уровень, что проверяется, что запрещено и почему, код причины в данных):
`docs/NODE_COVERAGE.md`; у каждой ноды ниже теперь есть и `CapabilitySpec` с `forbidden_code`/`forbidden_reason`.

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

## Фаза G: безопасность audio, sfx_gen, assemble, publish

Что изменено в нативном коде. Платные вызовы и счётчики `media_spend` не менялись.

- **audio (TTS, `elevenlabs_api.py`).** Повтор POST разрешён только когда состояние известно: ошибка соединения до отправки
  (`ConnectError`, `ConnectTimeout`, `ProxyError`, `PoolTimeout`) и ответ провайдера 429/502/503/504. Таймаут чтения,
  обрыв или ошибка протокола после отправки поднимаются как `ElevenLabsUncertainError` без второго POST, в том числе
  через прямой маршрут при заданном прокси. В каталоге `audio/` остаётся маркер `.tts_uncertain_<хэш>`: пока он есть,
  тот же текст (голос, модель, настройки) повторно не отправляется. Снять его может только человек (удалить файл либо
  положить готовую озвучку, после чего нода идёт по ветке «файл на диске»). Потолок: 2 POST на вызов и
  `AUDIO_MAX_TTS_POSTS_PER_RUN` (по умолчанию 60, 0 = без потолка) на один запуск ноды; при превышении ошибка называет
  настройку. Если рядом уже лежит файл с тем же хэшем текста (`*.mp3.tts.json`), POST не отправляется, файл копируется.
  Запись mp3 идёт через временный `.part` и `os.replace`.
- **sfx_gen.** Откат ElevenLabs на локальный синтез не изменён, но теперь записывается: WARNING, факт `provider_fallback`
  (класс ошибки, `may_have_charged`: `no` при ответе провайдера или отказе соединения, `uncertain` при таймауте или
  обрыве), запись `provider_fallback` у события в чекпоинте, `provider_fallbacks` в чекпоинте и
  `meta.sfx_provider_fallbacks` в результате ноды.
- **assemble.** Итог собирается во временный `final/.<slug>.<id>.part.mp4` и подменяется на `final/<slug>.mp4` через
  `os.replace` только после `verify_artifact(kind="assembly")` со статусом `passed`. Статус `failed` и `unknown`
  считаются отказом ноды: финальный файл не меняется, `approve_final` и сообщение в Telegram не создаются. Запрос
  `approve_final` идемпотентен по паре проект + sha256 выхода (повтор с тем же файлом ничего не добавляет и не
  запускает следующую тему массовой очереди); sha256 хранится в `Artifact.meta` и в `payload`. Автоодобрение
  финала (`auto_mode`, `HITL_AUTO_APPROVE`) в `_apply_approve` требует живой успешной проверки последнего
  `final_video`; без неё проект остаётся в `assembled`. Ручное одобрение человеком не затронуто.
- **Допуск надзорного повтора для assemble (выдан сухим прогоном), audio и sfx_gen (не выдан).**
  `tests/observe_v1/test_media_dry_run_local_nodes.py` доказывает безопасность сборки и сам повтор: один повтор внутри ноды только при
  ошибке `ffmpeg` или проваленной проверке, прежний итог переживает каждую неудачную попытку, `approve_final` один и остаётся `pending`,
  платный запрос недостижим (охрана `resume_only` на всю попытку и разбор импортов). Три пробела закрыты: общий путь повтора не-модельной
  ноды (рабочий процесс под билетом, своя независимая проверка в `reconcile`), сбой типизируется по исходу самой попытки, основа ревизии
  входа записывается. `assemble` под управлением как один локальный повтор; свежий запуск надзором по-прежнему запрещён. Для `audio`
  вердикт `resume_local` (готовый файл) без отдельной операции и независимой проверки, у `sfx_gen` такого вердикта нет: оба остаются под
  наблюдением, код запрета `paid_no_idempotency`. Подробно:
  `docs/NODE_COVERAGE.md`, раздел «Локальные медиа-ноды».
- **publish.** `ok=True` ставится только при найденном признаке успеха (`success_selectors`, `success_url_markers`).
  Селекторы на живом UI не откалиброваны, поэтому сейчас все платформы дают статус `unconfirmed` (`ok=False`). Такие
  платформы пишутся в `meta.publish_unconfirmed`, не попадают в `published_platforms`, пропускаются при повторе (чтобы не
  задвоить пост) и помечаются в сообщении «НЕ ПОДТВЕРЖДЕНО». Проект остаётся в `publishing`. Публикация по-прежнему
  только человеком, новых действий восстановления нет, preview без изменений.

- **Outsee: submit с неизвестным исходом (`outsee_guard`).** Вызовы Outsee API (картинка и видео) идут под записью
  намерения в `provider_task_ledger` (api `outsee`, только строки `intent`, id задач не пишутся, чтобы сверка Kie их не
  трогала). Исход «неизвестен», если POST ушёл, а ответа нет (`ReadTimeout`, `ReadError`, обрыв протокола, curl после
  отправки, ответ без id), либо id получен, а опрос закончился без результата (таймаут, обрыв сети). Тогда повторная
  отправка того же кадра в окне `MEDIA_RESUME_WINDOW_S` запрещена: `OutseeSubmitUnknownError` (`no_resubmit`, лестница
  повторов, rewrite и повтор шага останавливаются), сообщение по-русски называет кадр и срок. Внутренний повтор POST в
  `_post_generate` после потери ответа убран (был второй платный запрос, в том числе через curl). Известные исходы не
  блокируют: отказ провайдера, статус `failed` у задачи, ошибка до отправки, сбой скачивания после готового результата.
  Снять блокировку может только человек после сверки истории и баланса Outsee: `outsee_guard.release_unknown(project_id, key)`
  (как `media_resume.release_unknown` у Kie). В слое действий есть чтение `media.list_unknown_outcomes` и снятие
  `media.release_unknown` (уровень `destructive`, разовое подтверждение, письменная причина, провайдер и ключ; снимается блокировка одного
  провайдера по одному ключу, запись в журнал действий до и после; предпросмотр прямо говорит о возможном втором списании;
  `app/services/media_unknown.py`, `tests/test_media_unknown_release.py`). Отмена пользователем после принятой
  задачи тоже оставляет блокировку. Не затронуто: managed-режим, музыка и ad-hoc вызовы без проекта и ключа кадра.
  Возобновление по id Outsee (опрос той же задачи вместо новой) не сделано.
- **Outsee через браузер (`OutseeBot`, интерфейс сайта).** Методы `generate_image`, `generate_video` и `regenerate_image`
  идут под той же записью намерения (`outsee_guard.run_browser`, ключ API не нужен: защита важна именно там, где API
  не настроен). Бот отмечает в «пробе отправки»: `maybe_sent` прямо перед кликом, который создаёт платную генерацию
  (Generate, «Повторить», каждая механика запуска видео), `done`, когда адрес результата известен, и `rejected`, когда
  сама страница показала сбой (модерация, длина, плашка ошибки). Всё между кликом и одним из двух вердиктов
  (таймаут ожидания результата, падение процесса, отмена, «Generate не запустилась») остаётся неразрешённым намерением:
  следующий клик по тому же кадру в окне `MEDIA_RESUME_WINDOW_S` запрещён (`OutseeSubmitUnknownError`, `no_resubmit`),
  снять блокировку может человек (`media.release_unknown` или `outsee_guard.release_unknown`). Сбой до клика
  блокировки не создаёт; отказ по модерации остаётся известным исходом, лестница по-прежнему переписывает промпт.
  Дополнительно закрыта дыра: `_try_http_image/_try_http_video` при ошибке API уходили на запасной путь через браузер
  (второй платный запрос); теперь, если запрос ушёл или id известен без вердикта, запасной путь запрещён и исход
  фиксируется как неизвестный; если API провалился, ничего не отправив, или провайдер объявил задачу failed, запасной
  путь работает как раньше. Вызовы под `resume_only` браузерный путь тоже отклоняет до клика. Побочный эффект:
  таймаут после клика раньше приводил к новому клику (и второму списанию), теперь требует решения человека.
  Тесты: `tests/test_outsee_browser_guard.py` (поддельные страница, сессия и бот).
- **Циклы перегенерации (`regen_budget`).** Проверка шага (`finalize_or_retry`: видео, картинки, музыка, озвучка) и цикл
  vision-проверки (`vision_check_loop`) списывают бюджет до повторной постановки: `REGEN_MAX_PER_FRAME` (по умолчанию 3 на
  кадр или героя) и `REGEN_MAX_PER_RUN` (по умолчанию 12 раундов на шаг до успеха); 0 отключает потолок. При достижении
  потолка повтор не ставится и результат не проходит дальше: проект уходит в `paused`, причина в `meta.auto_paused_reason`,
  счётчики и остановка в `meta.regen_budget` / `meta.regen_budget_stop`, в журнал пишется факт `runtime_check`
  (`check=regen_budget`, `rejected`), диагностика показывает его как инцидент `contract_rejected` со статусом
  `human_review`. Раньше лимит vision-цикла (20 раундов) молча сбрасывал цикл и пускал цепочку дальше. Счётчики сбрасываются
  при успешном завершении шага, при verdict pass проверки и вручную (`regen_budget.reset`, затем возобновить проект).
  Денежный потолок: `REGEN_MAX_USD_PER_RUN` (по умолчанию 5.0 USD на шаг, 0 отключает). Каждый раунд добавляет
  оценку «число кадров x цена платного запроса» (`REGEN_ESTIMATE_USD_PER_ITEM`, а при 0 берётся
  `spend_guard.MEDIA_SUBMIT_ESTIMATE_USD` = 0.30; для vision-проверок плюс `DEFAULT_REQUEST_ESTIMATE_USD`). Раунд, после
  которого сумма по шагу превысила бы потолок, не ставится; первый раунд шага деньгами не блокируется (иначе шаг
  встал бы навсегда). Остановка такая же, как у счётчиков попыток: `paused`, `auto_paused_reason` («денежный лимит
  перегенераций…»), факт `runtime_check` с `usd_exhausted`, `usd_spent`, `usd_cap`, инцидент `human_review` в диагностике.
  Цена по-прежнему оценка (одна на все шаги, не по модели); фактические траты видны в журнале `spend_ledger`.
- **Подписи источника цены по-русски.** В сводке телеметрии `cost.source` и `cost.sources` теперь русские подписи
  (`telemetry.cost_source_label_ru`), исходные английские строки лежат в `cost.source_raw` и `cost.sources_raw`; в
  отказе по лимиту (`SpendCapExceeded.as_dict`) рядом с кодом `source` добавлено `source_ru`. Интерфейс эти поля не
  выводит, TypeScript-тип дополнен.
- **Остальные ALTER-миграции при старте стали версионными.** `schema_migrations`: `0007_frames_v2_columns` (кадры:
  `uuid`, `sort_key`, `scene_id`), `0008_frames_space_manual` (`frames_space.manual`), `0009_projects_columns`
  (22 колонки настроек `projects`, только основная БД). Схема результата та же. Тесты на старой схеме с данными и повтор:
  `tests/test_schema_migrations_legacy_columns.py`. Не переведено: отдельная база трекера API (`api_calls`) в
  `api_tracker_hook.py`, это локальный файл вне основной БД.

Остаточные риски: при падении процесса между POST и записью маркера повтор возможен; `unknown` у верификатора без
`ffprobe` блокирует сборку; запись `published_platforms` по-прежнему делается после возврата `publish_everywhere`.
