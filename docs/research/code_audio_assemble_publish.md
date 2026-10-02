# Аудит нод audio / music / sfx_plan / sfx_gen / assemble / publish: что оркестратор может делать безопасно

Репозиторий: `C:\video-pipeline\video-pipeline-observe-v1`. Режим: только чтение кода. Сеть, платные вызовы, запуск приложения и тестов не выполнялись. `.env` не открывался, значения ключей не читались.
Все пути относительны корня репозитория. Формат ссылок: `файл:строка`.
Метка **[НЕ ПРОВЕРЕНО]** означает вывод по чтению кода, который без запуска или сети подтвердить нельзя.

Прочитано: `docs/MEDIA_NODES_READINESS.md`, `docs/ORCHESTRATOR_NATIVE_MEDIA_PROGRESS_20260930.md`, `docs/ORCHESTRATOR_COMPLETION_REMAINING_20260930.md`, `docs/ORCHESTRATOR_STATUS_CURRENT.md` (таблица статусов), `app/services/node_capabilities.py` и все файлы из задачи. Дополнительно: `kie_kling.py` (`kie_post_json`), `frame_audio.py`, `ai_result_io.py`, `post_step_validate.py`, `artifact_verifier.py`, `step_failure_policy.py`, `media_jobs.py`, `decision_v1.py`, `actions_recovery.py`, `auto_advance.py`, `publishers.py`.

---

## 0. Итоговая таблица рекомендаций

| Нода | Рекомендация сейчас | Максимум после доработок | Главная причина |
|---|---|---|---|
| `audio` (ElevenLabs TTS) | **diagnose+escalate only** | **resume-only**: перезапуск только когда `voice_full_*` уже на диске и прошёл верификацию. Это путь без TTS. Платный повтор TTS не разрешать никогда. | Нет ключа идемпотентности. Нет journal-факта об отправке. Нативно до 27 POST на весь голос. Таймаут ElevenLabs не помечается как uncertain. |
| `music` (GPT + Suno через Kie) | **diagnose+escalate only** | **resume-only**: (а) файл на диске, (б) опрос известного `taskId` после его сохранения. Новый submit не разрешать. | `taskId` живёт только в памяти (`kie_http.py:387-389`). Повтор = новый GPT-вызов и новая оплата Suno. Для ноды нет записи в `_CAPABILITIES`. |
| `sfx_plan` (GPT, текст) | **diagnose+escalate only** | **bounded retry ×1** (retry_same). Самая безопасная из шести: только дешёвый текстовый вызов. | Чекпоинт `ai_jobs["sfx_plan"]` не пересматривается: повтор детерминированно упадёт, если не сбросить его. Нет ограничения на общее число событий. |
| `sfx_gen` (ElevenLabs SFX / локальный синтез) | **diagnose+escalate only** | **bounded retry по событиям**: только недостающие или откатившиеся на `local_synth` события, максимум 1 попытка на событие, жёсткий потолок вызовов. | Есть чекпоинт по `idx`, детерминированные пути и `verify_audio_file`. Но нет ключа идемпотентности, а любая ошибка тихо превращается в локальный синтез. |
| `assemble` (ffmpeg) | **diagnose+escalate only** | **bounded retry ×1** при выполнении всех условий из §6.8. Для безопасности нужны верификатор выхода и атомарная запись. | Денег нет, но итог неатомарно перезаписывает `final/<slug>.mp4`. Каждый успех создаёт новый HITL `approve_final` и сообщение в Telegram. Верификатора на выходе нет. |
| `publish` | **human-only forever** для записи/публикации. Разрешён только read-only preview (`publish.py:20-48`). | Без изменений. | Необратимый внешний эффект. Запись о публикации делается только после возврата всей `publish_everywhere`. `ok=True` ставится по `sleep(8)`, не по факту публикации. |

Уровни, в смысле этой таблицы:
- `diagnose+escalate` — оркестратор читает журнал, файлы и БД, формулирует инцидент и передаёт человеку. Никаких запусков.
- `resume-only` — разрешён только повтор, который по построению не делает нового платного submit. Пример: узел, который идёт по ветке «файл уже на диске».
- `bounded retry` — один повтор с жёстким потолком стоимости и предусловиями.

---

## 1. Сквозные факты (важны для всех шести нод)

1. **Журнал не видит платных вызовов ни у одной из этих нод.**
   - Обёртка `observe_model` навешана только на GPT-POST: `observation.py:560`, применение `gpt_api.py:1905,2213,2254`.
   - ElevenLabs (`elevenlabs_api.py:179-187`), Suno через Kie (`kie_http.py:387`) и `_elevenlabs_sfx` (`sfx_gen.py:162-184`) не пишут `record_fact`.
   - Фактов `media_request_reserved` / `media_failure` / `media_phase` нет, они создаются только в managed-контуре `MediaJob` (`media_jobs.py:299,326`, `kie_http.py:439`).
   - Расходы ElevenLabs/Suno/SFX нигде не учитываются: из `elevenlabs_api`, `sfx_gen`, `kie_http` нет вызовов `api_tracker_hook.record_api_call`. Единственный вызывающий — `observation.py:548-550`, и он только для GPT-текста.
   - Следствие: по журналу нельзя узнать, отправлялся ли платный запрос.

2. **Информативность `node_error` минимальна.** Пишется только `{"error_type": type(exc).__name__}` (`observation.py:853`).
   - `diagnosis.py:206` считает запрос неопределённым (uncertain) только для `CancelledError`, `ReadTimeout`, `ConnectTimeout`.
   - `ElevenLabsApiError` оборачивает любой таймаут и сетевую ошибку (`elevenlabs_api.py:210-219`).
   - `KieHttpError` оборачивает таймаут опроса и сбой задачи (`kie_http.py:311-314,343-355`).
   - Итог: самые опасные случаи («запрос мог быть принят») классифицируются как `contract_rejected` («причина неизвестна», `diagnosis.py:219-221`), а не как `uncertain_request`.
   - Исключение: «сырой» `httpx.ReadTimeout` из `kie_post_json` при создании задачи Suno (`kie_http.py:156`, таймаут 120 с) пройдёт как uncertain.

3. **Результат верификации SFX не создаёт инцидентов.** `observe_sfx_result` пишет `node_result_verification` (`sfx_plan.py:498`), но в `diagnosis.py:176-221` нет ветки для этого вида факта. Подтверждено поиском по `app/`: единственный потребитель — `scene_contracts.py`.
   - Провал верификации SFX остаётся фактом без инцидента.

4. **Managed-режим для этих нод невозможен.** Все шесть имеют `managed=False` или отсутствуют в `_CAPABILITIES` (`node_capabilities.py:65-79`; `music` вообще без записи, поэтому `capability_for` вернёт "unsupported", `:136-141`).
   - При `scope.mode=='managed'` старт такой ноды ставит hold и бросает ошибку (`observation.py:777-783`).
   - Путь `retry_same`/`retry_profile` недоступен структурно:
     - `RECOVERY_INCIDENTS={'invalid_json','missing_payload'}` (`decision_v1.py:23`);
     - для прочих инцидентов действия retry вычитаются (`decision_v1.py:166`);
     - `actions_recovery.py:233-240` требует `model_start` и `model_end` в попытке, а у TTS, Suno, ffmpeg таких фактов нет.

5. **Нативный повтор есть и дорог.**
   - `step_failure_policy.py:27-31`: 3 отказа за цикл, сон 30 минут, 3 цикла, на 9-м отказе abandon.
   - `_soft_retry_without_wipe` (`:144-229`) подхватывает файлы с диска для `audio`, `music`, `assemble` и больше ничего не чистит.
   - Для audio и music повтор «бесплатен» только если платный результат уже лёг на диск.
   - Отказ с 402 / "credits insufficient" ставит проект на паузу без soft-retry (`step_failure_policy.py:313-345`, `is_credits_failure` в `scene_design/agent_chunks.py:49-57`). ElevenLabs 402 попадает сюда, потому что текст ошибки содержит "HTTP 402" (`elevenlabs_api.py:239-260`).

6. **`verify_artifact` существует, но не подключён к этим нодам.**
   - Он умеет `audio` (есть аудиопоток), `video`, `assembly` (видео+аудио), `subtitles`, с проверкой `min_duration`/`max_duration` и размеров (`artifact_verifier.py:63-125`).
   - Тесты: `tests/observe_v1/test_media_contract.py:702-724`.
   - Используется только в `publish.preview_publication` (`publish.py:40`), managed-пути `media_jobs.py:70`/`media_transport.py:94` и веб-роутере `artifacts.py:125-141`.
   - Ни `generate_audio`, ни `generate_music`, ни `assemble` его не вызывают. Валидаторы шага `validate_after_audio` / `validate_after_music` проверяют только существование файла (`post_step_validate.py:245-337`).
   - Он требует `ffprobe`; без него возвращает `unknown` с причиной `ffprobe_unavailable`. Он также строг: непустой stderr ffprobe означает отказ (`artifact_verifier.py:93`).

7. **Человеческие барьеры можно обойти конфигурацией.**
   - Переходы `audio_ready → generating_music`, `music_ready → sfx_planning`, `sfx_ready → assembling` помечены `HITLKind.approve_videos` (`auto_advance.py:177-198`).
   - `assembled → publishing` — `approve_final` (`:199-202`).
   - Всё это действует только при `auto_mode`/`HITL_AUTO_APPROVE` выключенных (`auto_advance.py:1368-1372`, `settings.py:349`; по умолчанию False).
   - При включённом auto_mode vision-проверка для final не выполняется: docstring `auto_review.py`, функция `review_image` (около стр. 303), говорит «auto-approve без vision-чека».
   - Для оркестратора `approve_final` — барьер, который он не имеет права ни закрывать, ни создавать. Включение `auto_mode` и `HITL_AUTO_APPROVE` ему запрещено.

---

## 2. Нода `audio`

### 2.1 Поток исполнения (`app/orchestrator/steps/generate_audio.py`)
1. Старт только при `status is generating_audio` (`:195`).
2. Восстановление артефактов с диска: видео сцен, аудио, whisper (`:199-207`).
3. Читает кадры из БД (`:209-215`), ищет готовую озвучку: `find_voice_full_on_disk` (`:220-227`, `frame_audio.py:72-98`).
   - В `audio/` подходят файлы `voice_full*`, `voice`, `voiceover`, `ozvuchka`, `voice_montage` (`frame_audio.py:27-49`).
   - Файл `voice_full_<uuid>.mp3`, который сам пишет TTS, подходит под эти же правила.
4. **Ветка A, «файл на диске»** (`:251-293`): TTS нет, только `align_existing_voice_full` (ASR + раскладка по кадрам) и `_persist_audio_results` с `source="disk_whisper"`.
5. **Ветка B, платный TTS** (`:295-334`):
   - условие `use_11labs`: флаг `AUDIO_USE_ELEVENLABS_FALLBACK` **или** env **или** наличие `elevenlabs_api_key` (`:301-306`). Наличия ключа достаточно, чтобы тратить деньги, даже при `AUDIO_USE_ELEVENLABS_FALLBACK=False` (по умолчанию он False, `settings.py:266`);
   - `synthesize_per_frame_audio` (`frame_audio.py:476-549`):
     - `delete_frame_audio_files` стирает `frame_NNN.mp3` (`:490`);
     - **один** POST на весь `voiceover.txt` (`:492-521`);
     - `voice_full_<8hex>.mp3` пишется сразу после успешного ответа (`:504`, `elevenlabs_api.py:274`);
     - затем `probe_duration` и ASR (`:531-536`), раскладка по кадрам, нарезка `frame_NNN.mp3` (`:539-547`).
6. `_persist_audio_results` (`:61-165`): обновляет тайминги кадров, создаёт `Artifact(audio)` с метаданными `clips` (с текстом и таймингами), `Artifact(whisper_words)`, пишет words JSON.
7. `finalize_or_retry` (`:168-185`): проверка существования файла (см. §1.6).

### 2.2 Внешний вызов и стоимость
- `POST https://api.elevenlabs.io/v1/text-to-speech/{voice_id}` с заголовком `xi-api-key` (`elevenlabs_api.py:154-159`). Тело: `text`, `model_id` (по умолчанию `eleven_multilingual_v2`), `voice_settings` (`:160-167`).
- Тарификация: по символам. Фактическая цена вне кода; `kie_catalog` («6 кр/1000 симв.») к прямому API не применима, как верно отмечено в `MEDIA_NODES_READINESS.md`. Плоская ставка 0.01 USD в `api_tracker_hook.py:61` — приблизительная и по коду не вызывается для TTS (§1.1). **[НЕ ПРОВЕРЕНО]**
- Стоимость одного вызова пропорциональна длине всего сценария, а не одного кадра: `resolve_full_voiceover_text` (`frame_audio.py:371-380`).

### 2.3 Идемпотентность и повторы
- Ключа идемпотентности нет. Заголовок или поле запроса, по которому провайдер вернул бы тот же результат, не используется.
- Нативный цикл `elevenlabs_api.py:191-234`: 3 попытки на таймаут, сетевую ошибку, 429, 502, 503, 504 (задержки 2·n и 2.5·n с).
- **Скрытое удвоение:** при настроенном `ELEVENLABS_PROXY_URL` каждая сетевая ошибка внутри попытки сразу порождает второй POST напрямую без прокси (`:197-212`). Худший случай — 6 POST на один вызов `synthesize_speech`.
- Поверх этого шаговая политика даёт до 9 запусков ноды (`step_failure_policy.py:27-31`). Теоретический потолок — 27 полных озвучек всего сценария (54 с прокси). Он реален, только если ни разу не лёг файл `voice_full_*`.
- Заранее заданный `tts_timeout = max(180·кадров, 600) с` (`frame_audio.py:505`); дефолт функции 240 с (`elevenlabs_api.py:125`).
- **Спасительный механизм:** если TTS отработал, а упал позже (ASR, ffmpeg-нарезка, БД), на диске остаётся `voice_full_<uuid>.mp3` (префикс `voice_full_` защищён и подхватывается: `frame_audio.py:47-49,72-98`). Следующий запуск пойдёт по ветке A без нового TTS. Именно этот случай задокументирован как блокер: нет ASR-весов (`ORCHESTRATOR_NATIVE_MEDIA_PROGRESS_20260930.md`, строки про faster-whisper).

### 2.4 Состояние, которое остаётся
- Файлы: `audio/voice_full_*.mp3`, `audio/frame_NNN.mp3`, `audio/words_*.json`. Запись `write_bytes` неатомарна (`elevenlabs_api.py:274`), порог «битого» файла — 1000 байт (`:269`).
- БД: `Artifact(audio)`, `Artifact(whisper_words)`, `Frame.start_ts/end_ts/duration_seconds` (`generate_audio.py:73-77`). Коммит выполняет внешний воркер. Если процесс упадёт до коммита, останутся только файлы на диске.
- Нет привязки озвучки к тексту: ни хэша текста, ни `voice_id`, ни `model_id` в метаданных артефакта (`generate_audio.py:99-110`).
- **Риск устаревшей озвучки:** если сценарий поменяли после первой озвучки, перезапуск возьмёт старый `voice_full_*` (ветка A) и выровняет его по новому тексту. Дополнительная проверка есть только у words JSON (`whisper_words_fresh_for_audio`, `whisper.py:190-205`, сравнение mtime), текст не сверяется.

### 2.5 Классы отказов (как их увидит `diagnosis.py`)
| Ситуация | `error_type` | Класс инцидента |
|---|---|---|
| Нет ключа, неверный ключ 401, 429 после 3 попыток | `ElevenLabsApiError` | `contract_rejected` («причина неизвестна», `diagnosis.py:219-221`) |
| 402 «нет кредитов» | `ElevenLabsApiError` | шаговая политика ставит паузу (§1.5); инцидент как выше |
| Таймаут или обрыв после 3 попыток | `ElevenLabsApiError` | **не** `uncertain_request`, хотя запрос мог быть принят (§1.2) |
| Файл < 1000 байт | `ElevenLabsApiError` | `contract_rejected` |
| Нет ASR | `RuntimeError` | `contract_rejected` |
| `CancelledError` / сырой `ReadTimeout` | — | `uncertain_request` (`diagnosis.py:206`) |

Платный вызов виден в журнале только косвенно: `node_start` / `node_error` / `node_end`.

### 2.6 Что безопасно оркестратору
- **Всегда:** чтение журнала, инцидентов, файлов в `audio/`, артефактов; запуск `ffprobe` по существующим файлам (read-only).
- **Resume-only** (после доработок §2.9): перезапуск ноды разрешён, только если одновременно
  1. в `audio/` лежит `voice_full_*.mp3|wav`, прошедший `verify_artifact(kind="audio")` и проверку тишины (`audio_rms_db`, `ai_result_io.py:165`);
  2. хэш текста сценария совпадает с сохранённым хэшем озвучки (нужна доработка, §2.9);
  3. ASR-бэкенд доступен (иначе повтор упадёт так же, но бесплатно; это нужно проверять до запуска, чтобы не плодить отказы);
  4. запуск идёт с флагом «TTS запрещён» (`allow_tts=False`), чтобы код физически не мог дойти до `generate_audio.py:315`.
- **Платный повтор TTS: никогда** для оркестратора: нет pre-submit резервации, нет ключа идемпотентности, нет фактов в журнале.

### 2.7 Верификаторы (что есть, чего не хватает)
Есть:
- Существование `voice_full` или `frame_NNN.mp3` (`post_step_validate.py:272-337`).
- Размер ≥ 1000 байт (`elevenlabs_api.py:269`).
- Предупреждение (не ошибка) при расхождении длительности более 0.15 с (`generate_audio.py:79-87`).
- `verify_artifact(kind="audio")` (но не вызывается).

Нужны (все выполнимы read-only):
- **A1.** `verify_artifact(kind="audio")`: аудиопоток, длительность > 0, формат известен.
- **A2.** Тишина: `audio_rms_db > −60` (порог из `ai_result_io.verify_audio_file`, `:182-211`).
- **A3. Длительность против длины текста.** `chars_per_sec = len(normalize_text_for_tts(text)) / duration`. Речь на русском с `eleven_multilingual_v2` порядка 12-18 симв./с **[НЕ ПРОВЕРЕНО]**. Порог откалибровать офлайн по уже существующим артефактам: в `Artifact.meta["clips"]` лежат текст и длительность каждого кадра (`generate_audio.py:89-110`). Предлагаемая полоса: отклонение от медианы по проекту больше чем в ±50% = инцидент `suspect_duration`, длительность < 1 с или < 0.3×ожидаемой = отказ. Числа — стартовые, их надо подтвердить на данных.
- **A4.** Привязка к тексту: `text_sha256`, `voice_id`, `model_id` в `Artifact.meta`.
- **A5.** Покрытие ASR: доля слов и «крошечные» кадры (`timings_have_crumb_durations`, `frame_audio.py:229-279`). Fallback на пропорциональную раскладку делается молча.

### 2.8 HITL и необратимость
- HITL после audio формально `approve_videos` (`auto_advance.py:177-182`), но в auto_mode проходит без человека (§1.7).
- Необратимо: потраченные деньги и стирание `frame_NNN.mp3` перед синтезом (`frame_audio.py:490`). Обратимо: сам артефакт можно пересоздать. `voice_full_*` защищён от удаления (`is_protected_voice_or_music_file`, `frame_audio.py:52-69`).

### 2.9 Изменения кода (если решат двигаться к resume-only)
| # | Файл:строка | Изменение |
|---|---|---|
| 1 | `generate_audio.py:188-194` | Добавить `allow_tts: bool = True` (по образцу `force_full_asr`). При `allow_tts=False` и отсутствии `voice_path` на `:295` бросать `RuntimeError("tts_forbidden")` до проверки ключа. |
| 2 | `generate_audio.py:99-110` | Писать в `Artifact.meta`: `text_sha256`, `voice_id`, `model_id`, `tts_chars`, `provider`. |
| 3 | `elevenlabs_api.py:179-187,221-232` | Если есть `_attempt`-контекст: `record_fact` до POST (`provider_request_reserved`: voice_id, model, `text_sha256`, `text_len`, номер попытки, `via_proxy`) и после ответа (status_code, bytes). Не менять поведение запроса. |
| 4 | `elevenlabs_api.py:210-219` | Нести тип исключения: сохранять `context={"error_kind": "timeout"/"network", "request_may_be_accepted": True}` (по образцу чтения `context` в `observation.py:636-642`). Тогда `diagnosis.py:206` сможет пометить uncertain без хрупкого сравнения имён классов. |
| 5 | `elevenlabs_api.py:197-212` | Не отправлять повторный POST «без прокси», если первый запрос мог дойти до сервера (ReadTimeout после отправки). Доработка нативного кода, обсуждать с владельцем. |
| 6 | `diagnosis.py:206` | Расширить условие uncertain: `p.get('error_kind') in {'timeout','network'}` и `p.get('request_may_be_accepted')`; для этого `observation.py:853` должен писать эти поля из `exc.context`. |
| 7 | `node_capabilities.py:65-67` | Оставить `READ_ACTIONS`; добавить явную фазу `verifying` в `contract()` как данные, без новых действий. Новые действия включать только после пунктов 1-4. |
| 8 | `post_step_validate.py:272-337` | Добавить `verify_artifact` и `text_sha256` в `validate_after_audio`. |

### 2.10 Тесты без сети (заглушки)
- `synthesize_speech` с подставленным `httpx.AsyncClient` (по образцу `tests/test_elevenlabs_api.py`): счётчик POST. Ожидается ровно 1 POST на 200, 3 на ReadTimeout без прокси и 6 при заданном прокси (фиксирует текущее удвоение).
- Ветка A без TTS: при подставленных `synthesize_speech` (как `AsyncMock`, счётчик вызовов 0) и `find_voice_full_on_disk` с `tmp_path` перезапуск не вызывает `synthesize_speech`. Параметризовать по `allow_tts=False`.
- Устаревшая озвучка: два запуска с разным текстом, один `voice_full_*` — тест должен падать на текущем коде и проходить после п.2/п.8.
- Классификация: подать в `derive_incidents` синтетические события `node_error{error_type:'ElevenLabsApiError'}` и (после п.4/6) с `error_kind:'timeout'`: ожидается `contract_rejected` сейчас и `uncertain_request` после.
- Полоса A3: чистая функция по списку (текст, длительность) с калиброванной медианой; тест на выбросы.
- Для `ffprobe`-тестов: `pytest.skip`, если `shutil.which("ffprobe")` пусто (как `tests/observe_v1/test_media_contract.py:712-714`).

---

## 3. Нода `music`

### 3.1 Поток (`generate_music.py`)
1. Старт при `generating_music` (`:37`). Создаёт `music/`.
2. **Короткое замыкание:** `_find_music_on_disk` берёт самый новый `music_*.mp3|wav`, `*.mp3`, `*.wav` размером > 1000 байт (`:23-33,43-72`). Создаёт `Artifact(music)`, `finalize_or_retry`, статус `music_ready`. GPT и Suno не вызываются.
3. Иначе: читает `voiceover.txt` (нужен непустой, `:74-79`), требует `project.topic` (`:81-83`).
4. **GPT-вызов** `ask_with_files(...)` (`:94-104`, таймаут 900 с) даёт промпт для Suno; < 20 символов считается ошибкой (`:106-107`).
5. Файл назначения `music/music_<8hex>.mp3` с новым uuid при каждом запуске (`:114-116`).
6. **Suno через Kie** при `kie_configured()` (`:120-139`): `run_generation(spec, payload, music_path, timeout_s=900)`, `V5_5`, `instrumental=True`, промпт обрезан до 500 символов. Иначе браузерный Outsee (`:140-151`).
7. `Artifact(music)` пишется **после** оплаты (`:153-161`). Затем `finalize_or_retry` и статус `music_ready` (`:163-175`). Далее fleet-хуки (`:178-185`).

### 3.2 Внешние вызовы и стоимость
- GPT: платный токенный вызов (калиброван только для `gpt-5-6-sol`, `docs/KIE_COST_CALIBRATION.md`).
- Kie Suno: `POST {kie_api_base}/api/v1/generate` (`create_task`, `kie_http.py:136-167`), опрос `GET /api/v1/generate/record-info?taskId=` (`:45-49,283-356`). Цена по каталогу — 12 кр за трек (≈$0.06; `kie_catalog.py:1166`), не калибровалась.
- Один submit у Suno может вернуть несколько клипов (`_suno_clip_audio_urls` выбирает самый длинный, `kie_http.py:201-226`). Скачивается один. **[НЕ ПРОВЕРЕНО]**: оплата идёт за задачу, а не за скачанный клип.
- Браузерный путь Outsee: отдельный, платный по подписке, вне охвата аудита.

### 3.3 Идемпотентность
- `taskId` существует только в локальной переменной `run_generation` (`kie_http.py:387-389`) и в логе (`:388`). При падении (опрос, сеть, скачивание) `taskId` остаётся лишь в `KieHttpError.context` (`:311-314,343-355`), которое никто не сохраняет, а `node_error` пишет только имя класса.
- `kie_post_json` повторяет POST только при 401 «not authorized to use this model» (ротация ключей, `kie_kling.py:221-250`); на таймаут не повторяет.
- Опрос: каждые 4 с до 900 с; 8 подряд сетевых ошибок = отказ (`kie_http.py:306-315`). Нужно отличать: сбой во время опроса (задача **уже оплачена и идёт**) и сбой создания (задача, возможно, создана).
- Managed-путь для Suno закрыт явно: `"provider family has no approved managed route"` (`kie_http.py:406-407`). Кроме того, `media_jobs._resource` не распознаёт имена `music_*` (`media_jobs.py:~230-237`).
- Повтор целой ноды = новый GPT-вызов + новый `taskId` + новая оплата. Единственная естественная защита: файл `music_*.mp3` на диске (шаг 2 выше).

### 3.4 Состояние
- Файл `music/music_*.mp3`, `Artifact(music)`. Всё остальное (промпт GPT, `taskId`) не сохраняется.
- `kie_http.download` пишет без атомарности и принимает файл от 32 байт (`:366`); последующий `_find_music_on_disk` отсекает файлы меньше 1000 байт, поэтому «огрызок» от 32 до 999 байт не будет подхвачен, а больший будет.

### 3.5 Классы отказов
- Нет voiceover / topic / короткий промпт GPT / сбой Suno: `RuntimeError` / `KieHttpError`. Диагностика: `contract_rejected` или `api_error` (`diagnosis.py:198-221`).
- Сырой `httpx.ReadTimeout` при создании задачи даёт `uncertain_request`. Таймаут ожидания результата (`KieHttpError`, `kind=timeout`) не даёт.
- Ошибки Suno от провайдера (`GENERATE_AUDIO_FAILED` и т. п., `kie_http.py:270-271`) идут как `KieHttpError`.

### 3.6 Что безопасно
- **Всегда:** диагностика, `ffprobe` существующего файла, чтение журнала.
- **Resume-only (а), без доработок кода:** файл `music_*.mp3` на диске, прошёл `verify_artifact(kind="audio")` и тест на тишину → перезапуск ноды ветка «диск» (`generate_music.py:43-72`) стоит ноль (ни GPT, ни Suno). Оговорка: `_find_music_on_disk` берёт самый новый любой `*.mp3` в `music/`, в том числе пользовательский BGM; перед перезапуском нужно убедиться, что это файл этой ноды (имя `music_*` + mtime позже старта попытки).
- **Resume-only (б), требует доработки:** опрос известного `taskId` (read-only GET, `poll_task`) и скачивание. Условия: `taskId` сохранён и связан с хэшем промпта, файла нет, срок хранения результата у провайдера не истёк (**[НЕ ПРОВЕРЕНО]**, неизвестен), не более одной сессии опроса на попытку.
- **Новый submit — нет.**

### 3.7 Верификаторы
Есть: существование файла (`post_step_validate.py:245-269`) и размер > 1000 байт при подхвате с диска.
Нужны:
- **M1.** `verify_artifact(kind="audio")`.
- **M2.** Не тишина (`audio_rms_db`).
- **M3.** Длительность не короче, например, 20 с (BGM зацикливается через `stream_loop`, `assembly.py`/`sfx_mix.py`; поэтому жёстко длительность не критична, **[НЕ ПРОВЕРЕНО]** какой порог желателен продуктово).
- **M4.** Для Suno-файла: `instrumental=True` не проверить автоматически; пропустить.

### 3.8 HITL и необратимость
- Отдельного HITL у music нет; общий переход `music_ready → sfx_planning` (`auto_advance.py:184-188`).
- Необратимо: оплата Suno (≈$0.06 по каталогу, не калибровано) и GPT-вызов. Файл можно пересоздать.
- Скрытая цепочка повтора: если `assemble` упал, статус откатывается в `audio_ready` (`assemble.py:356-364`). Тогда в auto_mode конвейер снова пройдёт `generating_music`. Платежа не будет, только если `music/*.mp3` на месте (проверено: `generate_music.py:43-72`). **[НЕ ПРОВЕРЕНО]**: как именно текущий граф канваса продвигается из `audio_ready` (`_graph_next_running`).

### 3.9 Изменения кода
| # | Файл:строка | Изменение |
|---|---|---|
| 1 | `node_capabilities.py:~65-80` | Добавить `_CAPABILITIES['music']` (результат `audio`, фазы `reserved/submitted/queued/running/downloading/verifying/verified/failed/unknown`, `READ_ACTIONS`). Исправить расхождение: сейчас ноды в таблице нет, а в `MEDIA_RECOVERY_DECISIONS` есть (`:112`). Оставить `managed=False`. |
| 2 | `kie_http.py:375-398` | В `run_generation` добавить необязательный `on_task_created(task_id)`; вызывать сразу после `create_task` (`:387`). По умолчанию `None`, поведение не меняется. |
| 3 | `generate_music.py:118-139` | Передать колбэк: сохранить `{task_id, prompt_sha256, music_path, created_at}` в `project.meta["ai_jobs"]["music_suno_task"]` **и закоммитить сразу** (сейчас сессия коммитится только воркером после возврата, поэтому без немедленного коммита запись теряется при падении). |
| 4 | `kie_http.py` (новая функция рядом с `run_generation`) | `resume_generation(api, task_id, out_path)`: только `poll_task` + `download`, без `create_task`. Покрыть лимитом опросов. |
| 5 | `generate_music.py:43-58` | Перед подхватом файла проверять `verify_artifact`, а не только размер. |
| 6 | `observation.py:853` | Помимо `error_type` писать `task_id` из `exc.context` (если есть) в `node_error`, чтобы инцидент ссылался на задачу. |

### 3.10 Тесты без сети
- `run_generation` с подставленными `create_task`, `poll_task`, `download` (`monkeypatch`): проверить, что колбэк вызывается ровно один раз сразу после `create_task` и до `poll_task`.
- `resume_generation` с подставленным `poll_task`: `create_task` не вызывается ни разу (спай-счётчик = 0).
- Короткое замыкание: расширить `tests/test_music_disk_short_circuit.py` случаем «файл < 1000 байт игнорируется» и «пользовательский BGM не принимается за результат ноды».
- `generate_music.run` с подставленными `gpt_client`, `run_generation`: на втором запуске при существующем файле GPT и Suno не вызываются (счётчики = 0).

---

## 4. Нода `sfx_plan`

### 4.1 Поток (`plan_sfx.py`, `sfx_plan.py`)
- `plan_sfx.run`: `sfx_planning`; при `SFX_ENABLED=false` сквозной пропуск (`plan_sfx.py:25-31`); нет кадров = ошибка (`:46-47`); `plan_sfx_events` (`sfx_plan.py:164-253`).
- `plan_sfx_events`: таймлайн из `duration_seconds` (по умолчанию 3.0 с на кадр, если пусто, `:72`) → промпт (`_PLAN_PROMPT`, `:142-161`) → `text_job` (GPT, до 2 попыток с обратной связью, `ai_result_io.py:77-142`) → `_parse_events` → `validate_events` → запись `sfx_plan.json` (`:235-245`).
- Чекпоинт `ai_jobs["sfx_plan"]` сохраняется только при успешной валидации (`ai_result_io.py:127-128`). При его наличии GPT не вызывается вообще (`:94-97`).

### 4.2 Внешний вызов и стоимость
- Только GPT-текст (через `gpt_ask_fresh`, обёрнутый `observe_model`, так что в журнале есть `model_start/model_end`). Доли цента; GPT откалиброван.

### 4.3 Идемпотентность
- Чекпоинт — естественный ключ: повтор при успехе = ноль вызовов.
- **Ловушка:** `text_job` возвращает чекпоинт без перепроверки (`sfx_plan.py:225` комментарий). `plan_sfx_events` валидирует его заново (`:226-232`) и при провале бросает `RuntimeError`. Чекпоинт никто не стирает → каждая следующая попытка (нативная и оркестратора) падает идентично и бесплатно, пока кто-то не вызовет `drop_ai_job_checkpoint` (`ai_result_io.py:54-64`). Типичный повод: после изменения длительностей кадров старый план вышел за таймлайн.
- Ограничение «4-12 событий» заявлено только в промпте (`sfx_plan.py:154`). `validate_events` (`:78-113`) проверяет лишь по 3 события на кадр, шаг 0.8 с, длительность и границы. Верхнего ограничения на общее число нет. Это прямо влияет на цену `sfx_gen` (§5).

### 4.4 Состояние
- `project.meta["ai_jobs"]["sfx_plan"]` (в памяти до коммита ноды, `plan_sfx.py:48-53`), файл `sfx_plan.json`, `meta["sfx_plan"]=True`.

### 4.5 Классы отказов
- Провал валидации после 2 попыток: `RuntimeError("ai_job sfx_plan: ...")` (`ai_result_io.py:142`). В журнале это `node_error`; `parse_result` для sfx_plan не пишется, потому что исключение вылетает из `text_job` до `record_sfx_parse` (`sfx_plan.py:207-216`). Поэтому `invalid_json` / `missing_payload` не возникает (`record_sfx_parse` и так не ставит `payload_required`, `:408-411`), класс — `contract_rejected`.

### 4.6 Что безопасно
- **Сейчас:** диагностика. Дополнительно read-only: сверка чекпоинта с текущим таймлайном без вызова GPT.
- **Bounded retry ×1** (после доработок). Предусловия:
  1. `sfx_files` ещё не существует (иначе они привязаны к `idx` старого плана, §5.3) — либо оркестратор обязан сбросить и их (отдельное решение человека);
  2. чекпоинт `ai_jobs["sfx_plan"]` сбрасывается оркестратором **перед** повтором, иначе повтор бесполезен;
  3. не более 1 повтора на execution, `user_stop` не стоит;
  4. после повтора обязательна проверка `validate_events` + потолок числа событий.

### 4.7 Верификаторы
Есть: `validate_events` (`sfx_plan.py:78-113`), `observe_sfx_result` для плана (`:468-473`: наличие чекпоинта и файла плана).
Нужны: потолок общего числа событий (например ≤ 12, как в промпте); проверка, что события не выходят за `total_duration`; различие «источник плана = checkpoint» и «= model» уже журналируется (`:217`).

### 4.8 HITL / необратимость
HITL нет (`sfx_plan_ready → generating_sfx` под `approve_videos`, `auto_advance.py:189-193`). Необратимых эффектов нет, только копеечная трата.

### 4.9 Изменения кода
| # | Файл:строка | Изменение |
|---|---|---|
| 1 | `sfx_plan.py:78-113` | В `validate_events` добавить `MAX_EVENTS_TOTAL=12` и ошибку при превышении. Это одновременно ограничитель стоимости `sfx_gen`. |
| 2 | `sfx_plan.py:207-216` | Оборачивать `text_job`: при `RuntimeError` вызывать `record_sfx_parse(... parse_error=...)`, чтобы в журнале появился `parse_result` и `runtime_check failed`. |
| 3 | `sfx_plan.py:408-411` | Добавлять `payload_required: True`, если нужно, чтобы нода попадала в `invalid_json/missing_payload` (`diagnosis.py:104-106`). Решение владельца: это включит ноду в «текстовое» восстановление. |
| 4 | `node_capabilities.py:71-73` | Только после п.1-3 и принятия: `managed=True`, `actions=READ_ACTIONS+('retry_same',)`, `native_json_retry=False`, `recovery_continuation='none'` (по образцу `plan`/`script`, `:44-51`). |
| 5 | `ai_result_io.py:54-64` | Использовать существующую `drop_ai_job_checkpoint` в действии оркестратора. Новой функции не нужно. |

### 4.10 Тесты без сети
- Чистые тесты `validate_events` на 13 событий (ожидается ошибка после п.1).
- `text_job` с подставленным `gpt_client.gpt_ask_fresh`: при существующем чекпоинте GPT не вызывается (`attempts==0`); после `drop_ai_job_checkpoint` вызывается.
- `plan_sfx_events` с чекпоинтом, не проходящим текущий таймлайн: `RuntimeError`, GPT-счётчик 0 — фиксирует «ловушку».
- `derive_incidents` на синтетических фактах после п.2/3.

---

## 5. Нода `sfx_gen`

### 5.1 Поток (`generate_sfx.py`, `sfx_gen.py`)
- `generate_sfx.run` (`:46-75`): `generating_sfx`; при `SFX_ENABLED=false` пропуск; план из `load_sfx_plan` (`sfx_plan.py:256-280`, чекпоинт или файл); нет плана = ошибка; затем `generate_sfx_files` и коммит.
- `generate_sfx_files` (`sfx_gen.py:190-276`) для каждого события по порядку:
  1. если событие уже в чекпоинте `sfx_files` и файл существует, берётся как есть (`:206-222`);
  2. при ключе ElevenLabs вызывается `_elevenlabs_sfx` (`POST /v1/sound-effects`, таймаут 120 с, `text[:450]`, `duration_seconds` 0.5-22, `:162-184`), путь `sfx_<idx>_<kind>.mp3`; без ключа локальный синтез WAV (`:223-231`);
  3. **любое исключение вызова** (в том числе таймаут, 402, 5xx) ловится, пишется WARNING и событие молча синтезируется локально в `.wav` (`:232-243`), `provider="local_synth"`;
  4. `verify_audio_file(expect_duration, tol=0.6)` (`:245-249`); провал означает «событие пропущено, проблема в список», но нода продолжает;
  5. чекпоинт `sfx_files` пишется **один раз в конце** (`:267`) и коммитится нодой (`generate_sfx.py:68-74`).
- Если не прошёл ни один файл — ошибка (`:264-265`). Если прошёл хотя бы один — нода «готова» с неполным набором (`generate_sfx.py:68-75`).

### 5.2 Внешний вызов и стоимость
- `POST https://api.elevenlabs.io/v1/sound-effects`, заголовок `xi-api-key`. Оценка стоимости события в `MEDIA_NODES_READINESS.md` (≈$0.0125) — аналог по Kie, для прямого API не подтверждена **[НЕ ПРОВЕРЕНО]**.
- Максимум вызовов за прогон = число событий. Из-за отсутствия потолка в `validate_events` (§4.3) оно ограничено только «3 на кадр».
- Повторного вызова при ошибке внутри `_elevenlabs_sfx` нет; ошибка сразу превращается в локальный файл.

### 5.3 Идемпотентность и состояние
- Пути детерминированы: `sfx_<idx:02d>_<kind>.<ext>`; перезапись безопасна. Если провайдер упал, остаётся `.wav` с другим расширением.
- Чекпоинт хранит `provider` по событию (`sfx_gen.py:260`). Значит, **после** завершения прогона можно отличить «настоящий» ElevenLabs от отката. Это существенно мягче, чем пишет `MEDIA_NODES_READINESS.md` («откат маскирует ошибку»): маскируется на лету, но след остаётся в чекпоинте.
- **Окно потери:** чекпоинт пишется после цикла. Падение процесса посреди прогона теряет все ранее оплаченные события. Файлы на диске остаются, но в чекпоинте их нет, следующий запуск вызовет провайдера снова для тех же `idx` (перезапишет те же файлы, повторная оплата).
- **Привязка к плану только по `idx`** (`sfx_gen.py:208-222`): хэша плана нет. Если `sfx_plan` пересоздан, события получают те же `idx`, но другие `kind`/`prompt`, и нода молча возьмёт старые файлы. Дополнительно `collect_sfx_inputs` (`sfx_mix.py:27-48`) подмешивает в финальный микс всё, что есть в чекпоинте, независимо от соответствия плану.
- Файловые артефакты: записей `Artifact` в БД нет (`ORCHESTRATOR_STATUS_CURRENT.md`, строка SFX; подтверждено: в `sfx_gen.py` и `generate_sfx.py` нет `session.add(Artifact...)`).
- Параметр `force=True` существует (`sfx_gen.py:195`), но `run` его не передаёт и выборочного режима по `idx` нет.

### 5.4 Классы отказов
- Ошибки провайдера не доходят до журнала: они проглатываются (`sfx_gen.py:232-243`). Остаётся WARNING в логе.
- Видимы: «нет плана», «ни один файл не прошёл верификацию» (`RuntimeError` → `contract_rejected`).
- Верификатор `observe_sfx_result` сравнивает число записей чекпоинта с числом событий плана (`sfx_plan.py:478-479`): при пропуске события получится `proof.status=failed`, но без инцидента (§1.3).
- **Ограничение верификатора:** `_sfx_wav_record` читает файл как PCM WAV 44.1 кГц моно (`sfx_plan.py:419-442`). `mp3` от ElevenLabs этот код отклонит (`wave.Error`), и `proof.status` станет `failed` для каждого нормального платного результата. Следствие: при реальном ключе верификация SFX будет ложно красной, а при локальном синтезе зелёной. **[НЕ ПРОВЕРЕНО]**: не воспроизводилось, вывод из чтения кода. Подтверждается тем, что приёмка `native-sfx-worker-02` описана только для WAV.

### 5.5 Что безопасно
- **Сейчас:** диагностика. Read-only: сверить чекпоинт, диск и план; перечислить события с `provider!="elevenlabs"` при настроенном ключе; прогнать `verify_audio_file`.
- **Bounded retry по событиям** (после доработок). Предусловия:
  1. каждое событие повторяется не более 1 раза за execution; список `idx` задаёт оркестратор;
  2. только события, которых нет в чекпоинте, либо у которых `provider=local_synth` при настроенном ключе, либо провал `verify_audio_file`;
  3. потолок вызовов = число таких событий (и не более 12);
  4. нет `user_stop`, нет другого активного воркера;
  5. 402/«нет кредитов» останавливает всю серию.
  - Прогон остаётся «платным»: ключа идемпотентности нет, потому повтор события, чей первый вызов, возможно, был принят, может задвоить оплату. Потолок ограничивает потери копейками, но не устраняет риск. **[НЕ ПРОВЕРЕНО]**: цена вызова ElevenLabs SFX.

### 5.6 Верификаторы
Есть: `verify_audio_file` (размер ≥ 1000 Б, ffprobe-длительность в допуске 0.6 с, RMS ≥ −60 dB; `ai_result_io.py:182-211`); `observe_sfx_result` для WAV.
Нужны: поддержка mp3 в `_sfx_wav_record` (через ffprobe) или проверка расширения; инцидент по `node_result_verification` в `diagnosis.py`; проверка «число файлов = число событий» как жёсткая ошибка ноды (сейчас нода при частичном успехе помечается готовой).

### 5.7 HITL / необратимость
HITL нет. Необратимо: оплата вызовов. Файлы можно пересоздать.

### 5.8 Изменения кода
| # | Файл:строка | Изменение |
|---|---|---|
| 1 | `sfx_gen.py:232-243` | Сохранять причину отката: `fallback_reason` (тип исключения и HTTP-статус) в запись чекпоинта. `record_fact("provider_call", ...)` до и после вызова при наличии контекста. |
| 2 | `sfx_gen.py:162-184` | Различать 402/401 (остановить всю серию, не откатываться по событиям) и временные ошибки. |
| 3 | `sfx_gen.py:262-267` | Писать чекпоинт после **каждого** события и коммитить (устраняет окно потери). |
| 4 | `sfx_gen.py:195,206-222` | Параметр `only_idx: set[int] | None` вместо «всё или ничего»; хранить `plan_sha256` и сверять его при подхвате файлов. |
| 5 | `generate_sfx.py:68-75` | Если файлов меньше событий, не помечать ноду готовой без явной метки `partial` в `project.meta`. |
| 6 | `sfx_plan.py:419-442` | Принять mp3: проверка через `ffprobe` вместо `wave.open`. |
| 7 | `diagnosis.py:176-221` | Новая ветка: `node_result_verification.status in {'failed'}` → `contract_rejected` с `fact`. |
| 8 | `node_capabilities.py:74-76` | Менять `actions` только после п.1-6. |

### 5.9 Тесты без сети
- `generate_sfx_files` с подставленными `_elevenlabs_sfx` (первое событие `RuntimeError`, остальные успех) и `settings.elevenlabs_api_key="stub"`: ожидается `provider=local_synth` у первого и `fallback_reason` (после п.1).
- Падение на третьем событии при чекпоинте после каждого события (после п.3): два ранее готовых события остаются в чекпоинте.
- `only_idx={2}`: подставленный провайдер вызывается ровно один раз.
- Подмена плана (те же `idx`, другие `kind`): без `plan_sha256` файлы подхватываются, с ним нет.
- Для тестов верификатора использовать синтетический WAV из `_write_wav` (без ffmpeg) и пропуск, если нет `ffprobe`.

---

## 6. Нода `assemble`

### 6.1 Поток (`assemble.py`, `assembly.py`)
1. `run` → `montage_lane_claim` → `_run_assemble` (`:114-124`).
2. Подготовка: `recover_before_assemble`; `can_enter_running` (при отказе статус откатывается на `rollback` или `generating_audio`, `:127-136`); кадры из БД, при пустых подхватываются с диска (`:138-161`); кадры без клипа в сборку не попадают с WARNING (`:163-182`).
3. Нужен `Artifact(audio)` и существующий файл (`:184-200`). Режим: `per_frame_tts` (из метаданных аудио + наличие `frame_NNN.mp3`) либо Excel R15 / variant2 (`:204-334`).
4. Субтитры: слова из words JSON или пересчёт ASR; при включённых субтитрах без слов ошибка (`:333-334`). Артефакт `subtitle` добавляется **до** сборки (`:459-462`).
5. Сборка:
   - per-frame: `assemble(...)` (`:511-520`, `assembly.py:152-282`): подрезка клипов, `concat`, хвост, микс BGM+SFX, прожиг субтитров;
   - иначе `run_variant2` (`:522-528`) + прожиг субтитров отдельным ffmpeg (`:531-557`).
6. Опциональный `trim_intro_seconds` (на месте, `:559-561`, `assembly.py:285-307`).
7. `Artifact(final_video)`, статус `assembled`, `on_child_montage_complete`, `send_hitl_video(approve_final)` (`:577-594`).

### 6.2 Внешние вызовы и стоимость
- Только локальные `ffmpeg`/`ffprobe`/ASR. Деньги провайдеру не платятся.
- Побочный внешний эффект: `send_hitl_video` отправляет видео в Telegram владельцу (`hitl.py:271-276`) при активном Telegram; иначе публикует событие (`:256-268`).
- `on_child_montage_complete` может запустить **следующую тему массовой очереди** (создаёт дочерний проект и стартует `plan`, `mass_factory.py:522-572`). Это расходует деньги косвенно (GPT и т. д.) и срабатывает при каждом успешном проходе, если проект массовый.
- ffmpeg вызывается без таймаута (`assembly.py:62-73`): зависший процесс блокирует воркер бессрочно. **[НЕ ПРОВЕРЕНО]** в `run_variant2` (`app/services/montage/variant2.py`), его внутренности не аудировались.

### 6.3 Идемпотентность и состояние
- Выходной путь детерминирован: `final/<slug>.mp4` (`assemble.py:439-440`). Запись идёт прямо в этот файл (`assembly.py:270,278`), без временного файла и подмены, так что любой повтор или падение посреди кодирования **разрушает предыдущий финал**. После падения может остаться усечённый файл, на который указывает старый `Artifact(final_video)`.
- Каждый успех добавляет новые строки `Artifact(subtitle)` и `Artifact(final_video)`, дедупликации нет. `publish` берёт самый новый (`publish.py:66-73`).
- Каждый успех создаёт новый `HITLRequest(approve_final)` и новое сообщение в Telegram (`hitl.py:57-78,241-278`).
- Ошибка внутри `_assemble_body` откатывает статус в `audio_ready` (`assemble.py:356-364`); ошибки до `try` (например, нет артефакта аудио) оставляют `assembling` и опираются на шаговую политику.
- Нативный soft-retry: `recover_before_assemble` (`step_failure_policy.py` `_soft_retry_without_wipe`, ветка `assemble`).

### 6.4 Классы отказов
- Нативные сообщения на русском/английском, `RuntimeError`: нет кадров/видео/аудио, нет Excel R15, нет слов для субтитров, `ffmpeg exited with N`, `ffmpeg subtitle burn failed`, `trim intro failed` (`assemble.py:159-260,303,334,550`; `assembly.py:73,302`).
- Диагностика: `contract_rejected` («причина неизвестна»). Тип ошибки ffmpeg теряется: stderr уходит только в лог (`assembly.py:72`), в journal не попадает.

### 6.5 Что безопасно
- **Сейчас (read-only):** собрать входной «манифест» (список клипов, аудио, ASS, BGM, SFX) и проверить его существование; `verify_artifact(kind="assembly")` для существующего `final/<slug>.mp4`; проверка ASS (`kind="subtitles"`).
- **Bounded retry ×1** (после доработок), все предусловия:
  1. нет pending и нет одобренного `approve_final` для текущего финала; нет статуса `publishing/published`;
  2. входной манифест полон (все клипы и аудио на диске и проходят верификацию);
  3. нет `user_stop`;
  4. запись выхода атомарна (§6.9 п.1), старый финал не перетирается до успешной проверки нового;
  5. после запуска автоматически проходит выходной верификатор (§6.6); не пройдёт = hold и эскалация.
  - Принять заранее: повторный запуск создаёт ещё один HITL и Telegram-сообщение. Если очередь массовая, срабатывает `on_child_montage_complete`.

### 6.6 Верификаторы
Есть: ни одного на выходе ноды. `verify_artifact` вызывается только в preview публикации.
Нужны (после запуска):
- **S1.** `verify_artifact(final, kind="assembly", expected={...})`: потоки video+audio, формат известен.
- **S2.** Длительность: `audio_duration + tail − skip_intro` ± 0.5 с (`assemble.py:511-520,559-561`; `assembly.py:213-216`).
- **S3.** Размеры: равны размеру первого клипа (`assembly.py:122-137`) или контракту проекта.
- **S4.** Звук не пуст (`audio_rms_db`).
- **S5.** Субтитры: `verify_artifact(subs, kind="subtitles", expected={"max_duration": audio_duration})`. Факт прожига ffprobe не покажет; полагаться на код возврата.
- **S6.** Для per-frame: число клипов = числу кадров с клипом, монотонные тайминги.
- **S7.** Сравнение `sha256` нового выхода и записанного в `Artifact.meta` (привязка артефакта к проверке).

### 6.7 HITL и необратимость
- `approve_final` — единственная точка, где человек видит итог (`assemble.py:588-594`). Дальше `publishing`.
- Необратимо: перезапись финала на диске (не из корзины); отправка в Telegram; возможный запуск следующей темы массовой очереди.
- Обратимо: артефакт пересобирается из исходников, если клипы и аудио не изменились.

### 6.8 Изменения кода
| # | Файл:строка | Изменение |
|---|---|---|
| 1 | `assembly.py:254-279`, `assemble.py:439-440` | Писать в `final/<slug>.tmp.mp4` и после проверки `os.replace` на финальное имя. Либо версионировать имя файла (`<slug>_<ts>.mp4`). |
| 2 | `assembly.py:62-73` | Таймаут на `communicate()`, kill по таймауту; сохранять хвост stderr в исключение (`context`), чтобы он попадал в `node_error`. |
| 3 | `assemble.py:577-594` | Перед `Artifact(final_video)`/HITL вызвать `verify_artifact` и положить результат в `Artifact.meta["verification"]`; при провале не создавать HITL и поднять ошибку. |
| 4 | `assemble.py:577-594` | Не создавать новый `approve_final`, если есть pending для того же `sha256`. Идемпотентный ключ: `(project_id, final_sha256)`. |
| 5 | `assemble.py:356-364` | Явная фиксация причины отката статуса в `NodeRun`/journal (сейчас только WARNING в логе). |
| 6 | `node_capabilities.py:68-70` | Добавить действие `retry_same` лишь после п.1-4 и принятия верификатора; `managed` пока `False`. |
| 7 | `observation.py:853` | В `node_error` включить `stderr_tail`/`exit_code` из `exc.context`. |

### 6.9 Тесты без сети
- `assemble(...)` с подставленным `_run` (запись вызовов): проверить порядок команд и что выход пишется во временный файл, а не в финальный (после п.1).
- Падение посреди: `_run` бросает `RuntimeError` на втором вызове; ожидается, что старый `final/<slug>.mp4` не изменился (после п.1).
- Идемпотентность HITL: два успешных вызова `_assemble_body` с подставленными ffmpeg и Telegram создают один `approve_final` для одного `sha256` (после п.4).
- `verify_artifact(kind="assembly")` на синтетическом файле из `lavfi` (с `pytest.skip` без `ffmpeg`/`ffprobe`, как `tests/observe_v1/test_media_contract.py:712-724`) на предмет S1-S4.
- Расчёт ожидаемой длительности: чистая функция `(audio, tail, skip)` с табличными случаями.

---

## 7. Нода `publish`

### 7.1 Поток (`publish.py`, `publishers.py`)
- `preview_publication` (`publish.py:20-48`): read-only. Показывает `dry_run=True`, `send_allowed=False`, платформы и их `already_recorded`, проверяет последний `final_video` через `verify_artifact(kind="assembly")`. Браузер не открывается. Используется в `media_view.py:~64-66`.
- `run` (`:51-119`):
  - **жёсткая защита**: при активном разрешении оркестратора или `mode=='managed'` бросает `MediaPolicyError("managed publication is disabled; use read-only preview")` (`:56-57`);
  - при `SOCIAL_PUBLISH_ENABLED=false` (по умолчанию False, `settings.py:243`) проект переводится в `published` **без публикации** (`:60-64`). Статус `published` в этом случае не значит «опубликовано»;
  - иначе берёт последний `final_video` и вызывает `publish_everywhere(...)` по 5 платформам через MoreLogin (`:66-86`, `publishers.py:170-194`).

### 7.2 Идемпотентность и состояние
- Единственный механизм: `project.meta["published_platforms"]` со словарём `платформа → url`, платформы из него пропускаются (`publish.py:78-93`).
- **Запись делается только после того, как `publish_everywhere` вернула результаты по всем платформам** (`:86-93`). Падение, таймаут или отмена посреди серии теряют информацию о платформах, где публикация уже прошла. Повторный запуск задвоит публикацию.
- `PublishResult.ok=True` ставится по факту «кнопка нажата, прошло 8 секунд» (`publishers.py:95-99`), без проверки результата. `url=page.url` может быть страницей загрузки. Ложно-положительные записи блокируют будущие публикации на эту платформу; ложно-отрицательные (исключение после клика) приводят к дублям при повторе.
- В `publishers.py:7-8` прямо сказано, что селекторы платформ **не верифицированы** на живом UI.
- После частичного успеха в коде не меняется статус (`publish.py:113-119`), хотя комментарий говорит «останемся в assembled»: фактически остаётся `publishing`. **[НЕ ПРОВЕРЕНО]**: поведение воркера на повтор.
- Нет проверки, что последний `approve_final` одобрен: `publish.run` полагается на то, что статус `publishing` выставил `auto_advance` после одобрения (`auto_advance.py:199-202`). `verify_artifact` в боевом `run` не вызывается (только в preview), поэтому усечённый файл от неудачной сборки (§6.3) будет отправлен как есть.

### 7.3 Внешний вызов и стоимость
- Деньги: нет. Внешний эффект: публикация видео на TikTok, YouTube Shorts, Instagram Reels, VK Clips, Likee с аккаунта MoreLogin. Необратимо в смысле репутации и доставленного контента; удаление возможно только вручную.

### 7.4 Классы отказов
- Ошибки платформ превращаются в `PublishResult(ok=False, error=...)`, а не в исключения (`publishers.py:100-102`), и попадают в Telegram-сообщение (`publish.py:98-106`). Журнала нет. Диагностика не знает ничего о публикации: ни одного особого правила в `diagnosis.py`.

### 7.5 Рекомендация: human-only forever (запись)
Причины: необратимость, отсутствие реальной проверки успеха, потеря записи при падении, неверифицированные селекторы, неразличимость «ok» и «ok-ложно». Любой автоматический повтор задваивает публикацию.
Допустимо для оркестратора (read-only):
- `preview_publication` (уже есть);
- сверка `published_platforms` с фактом: перечисляет, что записано, и сигналит человеку несоответствия, без записи;
- чтение последнего `approve_final`.

### 7.6 Защитные изменения (для самой ноды, не для оркестратора)
| # | Файл:строка | Изменение |
|---|---|---|
| 1 | `publish.py:66-86` | Перед публикацией: последний `approve_final` для того же `final.uuid` должен быть `approved` (проверка внутри `run`, а не только в `auto_advance`); `verify_artifact(kind="assembly")` должен вернуть `passed`. |
| 2 | `publish.py:86-93`, `publishers.py:170-194` | Write-ahead: записывать `published_platforms[<p>]={"state":"started"}` и коммитить **до** клика; после клика `confirmed`/`unknown`. Состояние `started` без подтверждения = «неизвестно», только человек. |
| 3 | `publishers.py:95-99` | Подтверждение успеха признаком на странице вместо `sleep(8)` (после ручной калибровки селекторов). |
| 4 | `node_capabilities.py:77-79` | Оставить `("read_events","read_contract","human_review")` без изменений. |
| 5 | `settings.py:243` | Документировать, что `SOCIAL_PUBLISH_ENABLED=false` ведёт к статусу `published` без публикации; рассмотреть отдельный статус. |

### 7.7 Тесты без сети
- `publish.run` с подставленным `publish_everywhere` (`AsyncMock`): при `get_permission(...)` не `None` бросается `MediaPolicyError`, `publish_everywhere` не вызывается (спай = 0). Уже существующая защита, тест её фиксирует.
- Падение посреди серии (подставленный `publish_everywhere` бросает после второй платформы): текущий код не сохраняет ничего. После п.2 `started`-записи остаются.
- `preview_publication` с `tmp_path`-проектом: убедиться, что нет `import playwright` / вызовов MoreLogin (подмена `morelogin_browser` с `AssertionError`).
- `publish_everywhere` с заглушкой `Publisher.publish` для `skip_platforms`: пропущенные платформы не вызываются.

---

## 8. Единый порядок работ (если направление принимается)

1. **Видимость, без изменения поведения.** Факты `provider_request_reserved` и `provider_response` для ElevenLabs TTS, Suno (через колбэк) и ElevenLabs SFX. Сохранение `task_id` Suno с немедленным коммитом. Поля `error_kind` и `request_may_be_accepted` в `node_error` и их учёт в `diagnosis.py:206`. Ветка диагностики для `node_result_verification`. (§1.1-1.3, 2.9 п.3-4,6; 3.9 п.2,3,6; 5.8 п.1,7)
2. **Привязка результата к входу.** `text_sha256` / `voice_id` для audio, `plan_sha256` для sfx, sha256 финала. (§2.9 п.2; 5.8 п.4; 6.8 п.4)
3. **Верификаторы на выходе нод.** Подключить `verify_artifact` в `validate_after_audio`, `validate_after_music`, `assemble`. Полоса длительность/текст для audio после офлайн-калибровки. (§2.7, 3.7, 6.6)
4. **Атомарность и идемпотентность.** Атомарная запись финала и озвучки; чекпоинт SFX после каждого события; дедупликация HITL `approve_final`. (§2.9, 5.8 п.3, 6.8 п.1,4)
5. **Только после 1-4** включать действия: audio `resume-only` (флаг `allow_tts=False`), music `resume-only`, sfx_plan `retry_same`, sfx_gen по событиям, assemble ×1. Каждое отдельным решением владельца и отдельной приёмкой. Publish не включать никогда.

Требует решения владельца (вне технической части): продлевать ли шлюз `media_jobs` на реальные вызовы (`ORCHESTRATOR_STATUS_CURRENT.md`: «Architectural decision needed») и калибровать ли реальные тарифы ElevenLabs/Suno.

---

## 9. Неопределённости и непроверенное

- Цены: ElevenLabs TTS/SFX (прямой API) и Suno через Kie не калибровались. Все суммы в `MEDIA_NODES_READINESS.md` и здесь — оценки из статического каталога (`kie_catalog.py:1166`). Списывает ли ElevenLabs деньги за запрос, оборванный по таймауту, из кода не определить.
- Поведение ElevenLabs: возвращает ли он идентификатор запроса в заголовке и доступна ли история генераций для сверки (возможная read-only проверка «был ли запрос принят») — не проверялось, сеть запрещена.
- Срок хранения результатов Suno на стороне Kie (определяет допустимость «опроса по taskId» позже) неизвестен.
- Состав и внутренности `run_variant2` / montage-v3 не аудировались; выводы по `assemble` относятся к пути `assemble()` и общему каркасу `_run_assemble`.
- Вывод о ложно-красной верификации SFX с mp3 (§5.4) и о продвижении статуса из `audio_ready` в auto_mode (§3.8) сделан по чтению кода, без запуска.
- Диапазоны символов в секунду (§2.7 A3) — стартовая гипотеза; калибровать на реальных артефактах.
- Не выполнялись `git log/diff`: состояние рабочего дерева и ветки не проверялось. Ссылки на строки соответствуют файлам на момент чтения.
