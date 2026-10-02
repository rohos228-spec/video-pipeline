# Безопасные ретраи платных асинхронных медиа-API: Kie.ai, ElevenLabs и др.

Дата исследования: 2026-10-02. Метод: только публичные документы и веб (WebSearch/WebFetch), без ключей, без платных вызовов, локальные файлы не читались.
Весь веб-контент рассматривался как недоверенные данные. Содержимое страниц пересказывалось через промежуточную модель-суммаризатор (WebFetch), поэтому отдельные формулировки могли быть искажены. Такие места помечены в уровне уверенности.

Шкала уверенности: **В** = высокая (официальная документация, прямой текст), **С** = средняя (один вторичный источник или вывод из косвенных признаков), **Н** = низкая / НЕ ПРОВЕРЕНО.

---

## 0. Главные выводы (TL;DR для дизайна оркестратора)

1. **У Kie.ai нет ключа идемпотентности и нет эндпоинта отмены.** Задокументированы только `POST /api/v1/jobs/createTask` и `GET /api/v1/jobs/recordInfo`. Повторный createTask всегда создаёт новую задачу. Единственная защита от двойной оплаты на нашей стороне: сразу сохранить `taskId` и потом только поллить.
2. **Таймаут при поллинге не означает, что задача провалилась.** Есть живое подтверждение: PR в чужом проекте описывает, что локальный таймаут плюс повторный createTask привели к двум оплаченным задачам на одно изображение.
3. **Webhook (callBackUrl) ненадёжен как единственный канал.** Kie делает ограниченное число ретраев (3 подряд, затем перестаёт), ждёт ответа 15 секунд и допускает дубли на один taskId. Нужны идемпотентный обработчик и поллинг как страховка (reconciliation).
4. **Результаты живут недолго и документация противоречива:** 24 часа (документ recordInfo), 14 дней (FAQ, Suno, Runway), 20 минут (временные ссылки из download-url). Закладывать худший случай: скачивать в свой storage сразу после `success`.
5. **Списание за неудачу:** официально "failed tasks are not charged", но есть жалобы на списание за зависшие/упавшие задачи. Момент списания (при submit с возвратом или при успехе) в документации Kie явно НЕ описан. Есть косвенный признак предварительного списания: код 531 "credits refunded" в Suno-callback.
6. **ElevenLabs: тоже нет ключа идемпотентности** в документации. Синхронный запрос: если клиент отвалился по таймауту, сервер может всё равно выполнить генерацию и списать символы. Защита: заголовки `request-id` / `history-item-id`, сверка через History API, длинные таймауты.
7. **Контроль затрат:** у Kie есть `GET /api/v1/chat/credit` (баланс), ограничение на ключ (по отзывам: часовой/дневной/общий лимит, IP whitelist; подтверждено частично) и код 433 "subkey usage exceeded". У ElevenLabs есть лимит кредитов на API-ключ (документация), scope-ограничения, IP allowlist. Песочницы/тест-режима у Kie не найдено.

---

## 1. Kie.ai: жизненный цикл задачи

### 1.1 Создание задачи и taskId
- **Утверждение:** `POST https://api.kie.ai/api/v1/jobs/createTask` с телом `{model, input, callBackUrl?}`. HTTP 200 означает только, что задача создана, а не выполнена; в ответе приходит taskId.
- **Источник:** https://docs.kie.ai/ (Getting Started), https://www.bitdoze.com/kie-ai-video-generation/
- **Уверенность:** В
- **Следствие:** запись taskId в БД должна происходить в одной логической операции с получением ответа (до любых других действий). Состояние до ответа createTask ("отправили, ответа нет") надо хранить как `SUBMITTING/UNKNOWN`, см. раздел 3.

### 1.2 Опрос статуса (polling)
- **Утверждение:** `GET https://api.kie.ai/api/v1/jobs/recordInfo?taskId=...`. Поля: `taskId, model, state, param, createTime, updateTime, completeTime, costTime, creditsConsumed, resultJson (строка JSON, только при успехе), failCode, failMsg, progress (только sora2)`. Время в миллисекундах Unix.
- **Статусы `state`:** `waiting`, `queuing`, `generating`, `success`, `fail`. Терминальные: `success`, `fail`.
- **Источник:** https://docs.kie.ai/market/common/get-task-detail
- **Уверенность:** В
- **Следствие:** `resultJson` нужно парсить как строку внутри JSON (`resultUrls`). Поле `creditsConsumed` в recordInfo полезно для сверки расходов (но см. 1.7: заполнено ли оно для неуспешных задач, не проверено). Коды HTTP для recordInfo: 400 нет taskId, 401, 404 задача не найдена, 422, 429, 500.

### 1.3 Рекомендации по поллингу из документации
- **Утверждение:** использовать callback, чтобы не поллить; экспоненциальный backoff от 2-3 секунд; прекращать поллинг через 10-15 минут; скачивать результаты сразу. Сторонний гайд: backoff от 5 с с ростом на 25% до потолка 15 с, общий таймаут 15 минут. Для Veo в документации иногда советуют опрос раз в 30 с.
- **Источник:** https://docs.kie.ai/market/common/get-task-detail, https://www.bitdoze.com/kie-ai-video-generation/
- **Уверенность:** В (рекомендации), С (конкретные параметры из стороннего гайда)
- **Следствие:** "Прекратить поллинг через 10-15 минут" не должно означать "пометить как failed и отправить заново". См. 3.1: после мягкого таймаута задача переходит в состояние `TIMED_OUT_RESUMABLE` и поллится реже (сутки), а повторная отправка запрещена до явного решения.

### 1.4 Callback / webhook (callBackUrl)
- **Утверждения:**
  - Market API: callbackUrl опционален, "рекомендуется для продакшена". Тело: POST с `taskId, state, resultJson` (в вариантах: `code, msg, data{taskId, info{resultUrls,...}, creditsConsumed}`; формат отличается по семействам продуктов).
  - Подпись: заголовки `X-Webhook-Timestamp` (секунды) и `X-Webhook-Signature`; подпись = Base64(HMAC-SHA256(`taskId.timestamp`, webhookHmacKey)); сравнивать в константное время.
  - Veo/Suno/Runway: "после 3 подряд неудачных ретраев система перестаёт слать callback"; сервер должен ответить за 15 секунд, иначе таймаут; "один и тот же taskId может получить несколько callback, обработка должна быть идемпотентной"; отвечать 200 сразу, тяжёлую логику выносить асинхронно.
  - Политика ретраев и таймаут в документе проверки подписи (webhook-verification) не указаны.
  - Совет сторонних гайдов: считать callback только триггером и всегда перечитывать recordInfo.
- **Источники:** https://docs.kie.ai/common-api/webhook-verification, https://docs.kie.ai/veo3-api/generate-veo-3-video-callbacks, https://docs.kie.ai/suno-api/generate-music-callbacks, https://docs.kie.ai/runway-api/generate-ai-video-callbacks, https://www.bitdoze.com/kie-ai-video-generation/
- **Уверенность:** В для Veo/Suno/Runway; С для Market API (параметры ретраев для него явно не найдены, предполагаем те же).
- **Следствие:** приёмник webhook: проверять подпись, отвечать 200 быстро, дедуплицировать по `(taskId, state)`, после получения всегда делать `recordInfo` и только по нему менять состояние; параллельно держать поллер, потому что после 3 неудач callback теряется навсегда. Для публичного приёмника на локальной машине callback почти всегда недоступен: тогда только поллинг.

### 1.5 Сроки хранения результатов (противоречия)
| Источник | Срок | Уверенность |
|---|---|---|
| recordInfo: "Generated content URLs typically expire after 24 hours" | 24 ч | В (но "typically") |
| FAQ kie.ai: медиа 14 дней, логи 2 месяца | 14 дн / 2 мес | В |
| Suno / Runway / Veo callbacks: файлы/видео 14 дней | 14 дн | В |
| download-url: временная ссылка на скачивание "valid for 20 minutes" | 20 мин (только эти временные ссылки) | В |
- URLs: https://docs.kie.ai/market/common/get-task-detail, https://kie.ai/, https://docs.kie.ai/common-api/download-url, https://docs.kie.ai/suno-api/generate-music
- **Следствие:** считать худшим случаем 24 часа для URL из recordInfo и скачивать сразу при `success` в собственное хранилище с проверкой хеша/размера; не хранить только URL. Для внешних (не kie.ai) URL endpoint download-url возвращает 422.

### 1.6 Лимиты запросов
- **Утверждение:** 20 новых запросов за 10 секунд (до 120/мин) на аккаунт, 100+ параллельных задач; превышение даёт 429 и запрос НЕ ставится в очередь. Повышение по запросу в поддержку.
- **Источник:** https://docs.kie.ai/ , https://kie.ai/ (FAQ), https://www.bitdoze.com/kie-ai-video-generation/
- **Уверенность:** В
- **Следствие:** клиентский token-bucket на 20/10 с. 429 в createTask безопасно ретраить: запрос отклонён до создания задачи (но см. оговорку Stripe: для не-идемпотентных API "безопасность" 429 выводится из того, что лимитер стоит перед созданием; у Kie это прямо не подтверждено, поэтому перед ретраем 429 после неоднозначной ошибки сверять список задач недоступно, см. 3.2). **Н:** документация явно не гарантирует, что 429 никогда не создаёт задачу.

### 1.7 Списание кредитов и возвраты
- **Что утверждает Kie:** FAQ: "Failed tasks are not charged. If a generation task fails, KIE will not charge credits for that failed task." Кредиты не сгорают. Ответ компании на Trustpilot: "credits will be automatically returned to your account". Док по Suno-callback: код `531` = "Generation failed; credits refunded". Док по Kling/ByteDance: callback содержит `creditsConsumed` только в успешном ответе (вывод суммаризатора: "credits are only consumed on success" это интерпретация, не цитата).
- **Что НЕ задокументировано:** момент списания (при submit с последующим возвратом или по факту успеха); лаг возврата; что происходит при "зависании" без перехода в `fail`.
- **Косвенные признаки пред-списания:** код 531 "credits refunded" и формулировка "pre-deducted credits returned" в поисковой выдаче. **С**.
- **Жалобы:** Trustpilot 2.1-2.5 из 5 (около 10-15 отзывов): задачи "зависают на 99%", списание при сбоях, "stuck processing for 24 hours", медленная поддержка (Discord/Telegram, часовой пояс Азии). Один обзор: тест Nano Banana Pro, "четыре из пяти вызовов подряд фейлятся". Другие обзоры: "failed tasks weren't billed".
- **Источники:** https://kie.ai/ , https://docs.kie.ai/suno-api/generate-music-callbacks , https://docs.kie.ai/market/kling/text-to-video , https://www.trustpilot.com/review/kie.ai , https://aiinsightsnews.net/kie-ai-review/ , https://aisofting.com/kie-ai-review/
- **Уверенность:** официальная политика В; практика (отзывы) С (малая выборка, возможна предвзятость; ревью-сайты могут быть аффилированными).
- **Следствие:** (а) вести собственный леджер: `reserved` при submit, `settled` по `creditsConsumed` из recordInfo/callback, `refunded` по сверке баланса; (б) раз в N минут сверять `GET /api/v1/chat/credit` с леджером и алертить на расхождение; (в) для зависших задач не пересоздавать, а заводить тикет в поддержку с taskId (хранить taskId обязательно).

### 1.8 Подтверждённый кейс двойной оплаты при ретрае
- **Утверждение:** в проекте anomalia PR #325 "Resume a timed-out kie task instead of opening another": функция поллинга возвращала `undefined` и для `state=fail` (бесплатно ретраить), и для локального таймаута (задача на Kie продолжает рендериться и биллится). Вызывающий код ретраил созданием НОВОЙ задачи, "KIE charged for both". Worst case: 10 минут и две платные задачи на одно изображение. Исправление: три исхода `done | failed | timeout(taskId)`, при таймауте второй попытке передаётся `resumeTaskId` и createTask пропускается; таймаут пишется в журнал расходов с taskId без угаданной стоимости. Отмечено, что API не имеет эндпоинта отмены.
- **Источник:** https://github.com/anomaliaso/anomalia/pull/325
- **Уверенность:** С (первичное свидетельство одной команды, не официальный документ, но механика согласуется с документацией)
- **Следствие:** ровно этот паттерн нужно заложить в оркестратор: тип результата с тремя исходами; `timeout` никогда не равен `fail`; resume по сохранённому taskId; расходы по таймаутам видны в учёте.

### 1.9 Коды ошибок
| Код | Значение (по докам Kie) | Рекомендуемая обработка |
|---|---|---|
| 200 | Задача создана / запрос ок | сохранить taskId |
| 400 | Нарушение политики контента, не-английский промпт (Veo), недоступное изображение, неверные параметры | не ретраить без изменения входа; пометить `REJECTED_MODERATION` |
| 401 | Неверный ключ | остановить пайплайн, алерт |
| 402 | Недостаточно кредитов | остановить ВСЮ очередь (circuit breaker), не ретраить; пополнить |
| 404 | Не найдено (taskId) | сверить taskId; не пересоздавать автоматически |
| 422 | Валидация; также "fallback model rejected" (Veo); внешний URL для download-url | не ретраить; чинить вход |
| 429 | Лимит частоты | backoff+jitter, token-bucket |
| 433 | Превышен лимит subkey | остановить, это бюджетный стоп |
| 451 | Не удалось получить изображение (Suno) | проверить URL входного файла |
| 455 | Сервис на обслуживании | backoff, подождать |
| 500 | Ошибка сервера | в common-quickstart: до 3 ретраев с 2^n секунд (для GET/статуса безопасно; для createTask считать "исход неизвестен", см. 3.2) |
| 501 | Генерация не удалась | терминальный fail, возможна повторная отправка (бесплатно по политике) |
| 505 | Функция отключена | не ретраить |
- Источники: https://docs.kie.ai/common-api/quickstart , https://docs.kie.ai/market/common/get-task-detail , https://docs.kie.ai/market/kling/text-to-video , https://docs.kie.ai/market/bytedance/v1-lite-text-to-video , https://docs.kie.ai/veo3-api/generate-veo-3-video-callbacks , https://docs.kie.ai/suno-api/generate-music
- **Уверенность:** В для перечня кодов; С для стратегии обработки (это наши рекомендации).
- Замечание: в разных семействах продуктов список кодов различается, а в одном из ответов в поисковой выдаче `code: 505` соседствует с `msg: success` (неоднозначный пример из справки, вероятно, ошибка документации). **С**.

---

## 2. Остальные провайдеры

### 2.1 Suno через Kie
- Генерация: `POST` generate (параметры `model` V4..V6, `custom_mode`, `instrumental`, `callBackUrl` опционален), ответ с `taskId`; поллинг `GET https://api.kie.ai/api/v1/generate/record-info?taskId=...`.
- Статусы: `PENDING`, `TEXT_SUCCESS`, `FIRST_SUCCESS`, `SUCCESS`; ошибки: `CREATE_TASK_FAILED`, `GENERATE_AUDIO_FAILED`, `SENSITIVE_WORD_ERROR`, `CALLBACK_EXCEPTION`.
- Callback стадии: `text`, `first`, `complete`, `error`. Коды callback: 200, 400 (защищённый авторским правом контент), 408 (лимит/таймаут), 413 (конфликт с существующим произведением), 500, 501, **531 (сбой, кредиты возвращены)**. 3 неудачных ретрая и callback прекращается; ответ в течение 15 с; дубли возможны.
- Файлы хранятся 14 дней. Коды API: 401, 402, 422, 429, 451, 455, 500.
- Источники: https://kieai.mintlify.app/suno-api/quickstart , https://docs.kie.ai/suno-api/generate-music , https://docs.kie.ai/suno-api/generate-music-callbacks
- **Уверенность:** В (страница docs.kie.ai/suno-api/quickstart отдавала 404 при прямой выборке, использовано зеркало на mintlify; содержимое может отличаться от актуального).
- **Следствие:** `FIRST_SUCCESS` уже содержит первый трек: для нужд пайплайна можно считать задачу пригодной после `SUCCESS`, но не терять `FIRST_SUCCESS`-URL при последующей ошибке; Suno возвращает обычно 2 трека за один платный вызов, что надо учитывать в учёте стоимости.

### 2.2 Veo через Kie
- Отдельное семейство (не Market): свой generate и record-info (`successFlag`), callback с `code` 200/400/422/500/501, `fallbackFlag`, `creditsConsumed`. Нарушения политики: 400 (промпт, язык, небезопасные изображения); при включённом fallback возможен 422 "fallback тоже отклонил". Советуют только английские промпты.
- Поля `successFlag` и точные значения НЕ проверены (страницы record-info отдавали 404).
- Источники: https://docs.kie.ai/veo3-api/generate-veo-3-video-callbacks , https://github.com/andrewlwn77/kie-ai-mcp-server
- **Уверенность:** С / Н для successFlag.
- Также: 1080p-апскейл недоступен для видео, сгенерированных в fallback-режиме (по README MCP-сервера, С).

### 2.3 Kling / Seedance (Market API)
- Единая схема Market: createTask -> recordInfo (waiting/queuing/generating/success/fail); callBackUrl; коды 401/402/404/422/429/433/455/500/501/505. Параметры: Kling 2.6 text-to-video: `prompt` до 1000 символов, `sound`, `aspect_ratio`, `duration` 5 или 10. Seedance 2.0: до 9 референс-изображений, 3 видео, 3 аудио, промпт до 20000 символов, длительность 4-15 с.
- Модерация: у Seedance строгий фильтр лиц/знаменитостей/IP. Утверждение стороннего блога, что отказ приходит "только при получении результата после полного рендера с 422" НЕ подтвердилось при проверке второго источника (статья MindStudio этого не говорит). **Н**.
- Источники: https://docs.kie.ai/market/kling/text-to-video , https://docs.kie.ai/market/bytedance/v1-lite-text-to-video , https://kie.ai/seedance-2-0 , https://www.mindstudio.ai/blog/seedance-2-0-content-restrictions-workarounds
- **Уверенность:** В для схемы, С/Н для моментов модерации.
- **Следствие:** модерационный отказ может прийти как `state=fail` (с `failCode/failMsg`) после очереди; различать `fail`-модерацию (не ретраить без правки входа) и инфраструктурный `fail` (можно ретраить 1-2 раза). Классифицировать по `failCode/failMsg`; точные значения НЕ проверены.

### 2.4 GPT через `POST https://api.kie.ai/codex/v1/responses`
- Формат OpenAI Responses: `model, stream, input[{role, content[{type: input_text|input_image}]}], tools (web_search), reasoning`. Не стриминг: полный JSON после завершения (синхронный вызов); стриминг: SSE `response.output_text.delta`, `response.completed`. В ответе `credits_consumed` (пример 0.48 кредита) и usage. Коды: 400, 401, 429, 500. Руководства по ретраям нет. Идемпотентности и `background` режима в документации Kie нет.
- Источник: https://docs.kie.ai/market/chat/gpt-5-5
- **Уверенность:** В (формат), Н (поддерживает ли прокси `background:true`/получение по id; не проверено).
- Для сравнения у OpenAI Responses API есть background mode: id ответа, поллинг, cancel (идемпотентный), но retention ~10 минут при ZDR: https://developers.openai.com/api/docs/guides/background. У Kie эта возможность не документирована.
- **Следствие:** текстовый вызов синхронный: таймаут клиента = неизвестный исход, но стоимость малая; ретраить допустимо с кэшем по хешу промпта, учитывая небольшую стоимость.

### 2.5 ElevenLabs
- **Идемпотентность:** документация TTS и SFX не описывает ключ идемпотентности, таймауты и гарантии; единственный задокументированный код у конвертера: 422. Параметр `seed` даёт "best effort" детерминизм, не гарантию. Источники: https://elevenlabs.io/docs/api-reference/text-to-speech/convert , https://elevenlabs.io/docs/api-reference/text-to-sound-effects/convert. **В** (отсутствие подтверждено по доке), поэтому идемпотентность отсутствует (вывод).
- **Request ID:** заголовки ответа `request-id`, `x-trace-id`, `character-cost` (через raw response в SDK); в каждом теле ошибки есть `request_id`. Заголовок `history-item-id` на TTS-ответе (по использованию в чужом проекте). Источники: https://elevenlabs.io/docs/api-reference/introduction , https://elevenlabs.io/docs/eleven-api/resources/errors , https://github.com/andreabuttarelli/feega/pull/67. **В** для request-id/character-cost, **С** для history-item-id.
- **History API:** `GET /v1/history` (page_size до 1000, `start_after_history_item_id`, `voice_id`, `model_id`, `date_after_unix`, `date_before_unix`, `sort_direction`, `search`, `source` TTS/STS/Flows), элементы с `history_item_id`, `request_id`, `date_unix`, `character_count_change_from/to`, `text`, `model_id`, `state`. `GET /v1/history/{id}`, скачивание аудио, `DELETE`. Прямого фильтра по `request_id` нет, нужна фильтрация на клиенте. Значения `state` не документированы. Источники: https://elevenlabs.io/docs/api-reference/history/list , https://elevenlabs.io/docs/api-reference/history/get. **В**.
- **SFX в History API:** в UI звуковые эффекты видны во вкладке History; поддержка в `source` enum API (TTS/STS/Flows) для SFX явно НЕ подтверждена. **Н**.
- **Zero Retention (`enable_logging=false`, только Enterprise):** история не сохраняется, сверка через History невозможна. https://elevenlabs.io/docs/eleven-api/resources/zero-retention-mode. **В**.
- **Реконструкция "уже сгенерировано, ответ потерян":** issue #229 elevenlabs-python: ReadTimeout на тексте 2418 символов, аудио при этом видно в веб-интерфейсе, пользователь "сжёг много кредитов" на ретраях. Make.com: стандартный таймаут модуля 40 с, длинные генерации (до 5000 символов) занимают 2-3 минуты; обход: HTTP-модуль с таймаутом 300 с. Источники: https://github.com/elevenlabs/elevenlabs-python/issues/229 , https://community.make.com/t/timeout-limit-with-elevenlab-searching-for-a-workaround/41839. **С**.
- **Списание при сбое:** третьесторонние страницы: "failed requests do not consume credits, credits are deducted only upon successful generation". В официальной доке ошибок это НЕ найдено; help-центр ElevenLabs (403 на выборке) не прочитан. **Н/С**.
- **Ограничения:** 429 бывает двух видов: `too_many_concurrent_requests` (лимит параллелизма по тарифу: Free 2, Starter 3, Creator 5, Pro 10, Scale/Business 15 по справочным данным, **С**) и `system_busy` (временная перегрузка, backoff с jitter). Первый лечится очередью/семафором, а не ретраем. 402 `payment_required` (кредиты, а также `paid_plan_required` для библиотечных голосов на free-плане). 5xx считать "исход неизвестен". Источники: https://elevenlabs.io/docs/eleven-api/resources/errors , https://help.elevenlabs.io/hc/en-us/articles/19571824571921-API-Error-Code-429 (через поиск), https://gist.github.com/finndunning/2a057479f33f302117405148d3d21a95.
- **Счётчик символов запаздывает:** `GET /v1/user/subscription` (`character_count`, `character_limit`, `next_character_count_reset_unix`) отстаёт от реального использования (замер: 74 символа, затем 690); эндпоинт ограничен по частоте, кэшировать. Источник: https://gist.github.com/finndunning/2a057479f33f302117405148d3d21a95 (замеры третьего лица, **С**).
- **Следствие:** для ElevenLabs "resume polling" невозможен (синхронный запрос), поэтому: (1) хранить хеш `(text, voice_id, model_id, settings, seed)` и сразу писать запись `intent` до вызова; (2) таймаут клиента делать большим (300 с+) и без автоматических ретраев на read-timeout; (3) после неоднозначной ошибки сначала сверяться с History (по `date_after_unix` и хешу `text`+`voice_id`) и скачать готовое аудио вместо повторной генерации; (4) на `too_many_concurrent_requests` не ретраить вслепую, использовать семафор по числу слотов тарифа; (5) удалять History-элементы только после сохранения нашей копии (как в feega #67).

---

## 3. Общие практики ретраев для платных неидемпотентных асинхронных API

### 3.1 Принципы (с источниками)
1. **Идемпотентный ключ, если провайдер его поддерживает.** Stripe: ключ `Idempotency-Key`, результат первой попытки (включая 500) сохраняется, ключи живут минимум 24 часа, несовпадение параметров даёт ошибку. https://docs.stripe.com/api/idempotent_requests , https://docs.stripe.com/error-low-level. Проект IETF `Idempotency-Key` истёк как draft (version 07, expired 2026-04-18), то есть стандартом не стал: https://datatracker.ietf.org/doc/draft-ietf-httpapi-idempotency-key-header/. Google AIP-155: request_id должен гарантировать идемпотентность, повтор возвращает прежний ответ: https://google.aip.dev/155. **В.** У Kie/ElevenLabs такого механизма в документации нет, поэтому идемпотентность нужно строить самим над ними.
2. **Исход неизвестен после таймаута.** Stripe явно: сетевую ошибку и 500 трактовать как неопределённый исход, сверять через webhooks/метаданные с локальным идентификатором. https://docs.stripe.com/error-low-level. **В.** Вывод для нас: после отправки createTask без полученного taskId состояние `SUBMIT_UNKNOWN`, а не `FAILED`.
3. **Durable execution: at-least-once.** Temporal: активность может исполниться более одного раза, нужен ключ идемпотентности на стороне внешнего сервиса (например WorkflowRunID+ActivityID). https://docs.temporal.io/activity-definition. **В.**
4. **Фиксация job id немедленно (persist-before-poll)** и **"resume, never resubmit"**: подтверждено на практике PR anomalia #325 (п.1.8), в OpenAI Background mode (id ответа, поллинг, повторный cancel идемпотентен, https://developers.openai.com/api/docs/guides/background), в Replicate (urls.get / urls.cancel, https://replicate.com/docs/topics/predictions/create-a-prediction). **В/С.**
5. **Webhook + polling как reconciliation.** Webhook ускоряет, но не гарантирует; обработчик идемпотентен; всегда перечитывать каноническое состояние. Подтверждено документацией Kie (дубли, 3 ретрая) и Stripe. **В.**
6. **Ретраи только на одном слое** и с бюджетом/jitter (AWS Builders' Library). Страница отдала пустой контент при выборке, поэтому конкретные положения не проверены: https://aws.amazon.com/builders-library/timeouts-retries-and-backoff-with-jitter/. **Н** (общеизвестная практика, но источник не прочитан). Добавление ретраев на write-path без идемпотентности увеличивает число дублей: https://dev.to/_66d02d0cc1ece7d1137c5f/the-retry-you-added-to-the-write-path-made-duplicates-worse-3koj (только заголовок из выдачи, **С**). Для агентных фреймворков: retry вызова инструмента создаёт новый ключ и второе списание (stripe/ai issue #402, https://github.com/stripe/ai/issues/402; по заголовку выдачи, **С**).
7. **Дедупликация по хешу запроса.** Хеш `(provider, model, canonical(input))` -> существующая задача/артефакт. Поскольку генеративные модели недетерминированны, повторный результат не равен прежнему: дедуп нужен именно для того, чтобы не платить повторно за тот же замысел. Это инженерная рекомендация, прямых официальных источников нет. **С.**
8. **Леджер расходов с честным учётом неопределённого:** резерв при submit, расчёт по `creditsConsumed`, отдельное состояние "ожидаемая оплата неизвестна" для таймаутов (как в anomalia PR #325). Параллельная проблема: баг, когда возврат/компенсация не срабатывает на терминальном сбое из-за ошибки счётчика попыток (BullMQ, https://github.com/akoita/resonate/issues/1778): возвраты нужно делать устойчивыми и проверяемыми. **С.**

### 3.2 Предлагаемый автомат состояний (вывод исследователя, не из источников)
```
PLANNED -> RESERVED(budget) -> SUBMITTING (intent записан на диск, requestHash)
  -> SUBMITTED(taskId записан)              // только отсюда допустим polling
  -> SUBMIT_UNKNOWN (таймаут/сеть/5xx без taskId) // НЕ ретраить автоматически
       -> reconcile: kie logs/кредитный баланс; EL history; иначе ручное решение/лимитированный повтор
SUBMITTED -> WAITING/QUEUING/GENERATING -> SUCCESS -> DOWNLOADED(hash,size) -> DONE
                                      \-> FAIL_MODERATION (не ретраить без правки)
                                      \-> FAIL_INFRA (можно 1-2 ретрая с новым taskId, бесплатный по политике)
                                      \-> POLL_TIMEOUT (resumable, редкий поллинг до 24 ч)
```
- Для Kie нет API списка задач (из доступной документации известны только createTask и recordInfo; `kie.ai/logs` - веб-интерфейс для логов), поэтому `SUBMIT_UNKNOWN` невозможно автоматически сверить с провайдером. Минимизация: добавлять свой маркер в `param` не выйдет (поле не документировано как пользовательское), но можно пробовать передавать уникальный `callBackUrl` с query-параметром `?job=<наш_id>`: в callback придёт привязка к нашему заданию. **Н** (поведение не проверено; callbackUrl может быть отвергнут или нормализован).
- Для ElevenLabs "unknown" сверяется через History (по времени и тексту).

---

## 4. Сообщения сообщества и риски

| Проблема | Что сообщают | Источник | Уверенность | Меры |
|---|---|---|---|---|
| Двойная оплата при ретрае после таймаута (Kie) | Новая задача создаётся, старая тоже биллится | https://github.com/anomaliaso/anomalia/pull/325 | С | resume by taskId |
| Зависание на 99%/"generating" сутками, списание без результата | Trustpilot, обзоры | https://www.trustpilot.com/review/kie.ai , https://aiinsightsnews.net/kie-ai-review/ | С | жёсткий потолок ожидания (15 мин мягкий, 24 ч жёсткий), тикет с taskId, алерты |
| Нестабильность отдельных моделей (Sora 2 чаще остальных) | обзоры | https://aiinsightsnews.net/kie-ai-review/ | С | circuit breaker по модели, запасные модели |
| Высокая доля сбоев Nano Banana Pro (4 из 5 в одном тесте) | обзор | https://aiinsightsnews.net/kie-ai-review/ | С/Н (одиночный тест) | смоук-тест перед пакетом, лимит ретраев |
| Фильтр контента меняется "после пополнения" (422) | утверждение обзора | https://aiinsightsnews.net/kie-ai-review/ | Н (не подтверждено) | не полагаться на прошлый успех промпта |
| Midjourney убран с платформы по требованию владельцев | обзор | https://aisofting.com/kie-ai-review/ | С | абстракция провайдера, запасной маршрут |
| Истечение ссылок | 24 ч / 14 дн / 20 мин (см. 1.5) | docs.kie.ai | В | скачивать сразу |
| Поддержка медленная вне азиатских часов, только Discord/Telegram/e-mail | обзоры | https://aisofting.com/kie-ai-review/ | С | не рассчитывать на ручные возвраты |
| ElevenLabs: таймаут клиента, генерация выполнена, кредиты потрачены | issue #229, Make | см. 2.5 | С | History-сверка, длинные таймауты |
| ElevenLabs: ретраи в LiveKit TTS не сбрасывают контекст (не про деньги) | issue closed "not planned" | https://github.com/livekit/agents/issues/4135 | В (факт issue) | ретрай должен пересобирать вход |
| Модерация: Seedance (лица, знаменитости, бренды), Veo (публичные люди, двойная проверка до и после генерации), Suno (авторский контент, sensitive words) | блоги/доки | https://www.mindstudio.ai/blog/seedance-2-0-content-restrictions-workarounds , https://docs.kie.ai/veo3-api/generate-veo-3-video-callbacks , https://docs.kie.ai/suno-api/generate-music-callbacks | В для документированных кодов, С для блогов | предфильтр промптов, описание вместо имён, английский для Veo, не ретраить без правки |
| Возможное списание за отклонённые Veo-генерации (у Google Flow, не Kie) | отзывы пользователей | https://creatide.ai/blog/veo-content-violation-why-flow-rejects-prompts-and-wastes-credits | С/Н (другая платформа) | отдельно проверять учёт для отказов |

Не найдено: упоминаний о выявленных реальных "двойных списаниях" Kie в Reddit/HN (поиск по Reddit через WebSearch не вернул тредов; **Н**). Первичные Reddit/Discord-источники в выдачу не попали.

---

## 5. Тактики контроля затрат

| Тактика | Подтверждение | Уверенность |
|---|---|---|
| Предпроверка баланса Kie: `GET https://api.kie.ai/api/v1/chat/credit` -> `{code:200,data:<кредиты>}`; 402 при исчерпании | https://kieai.mintlify.app/common-api/get-account-credits , https://docs.kie.ai/common-api/quickstart | В |
| Цена кредита: 1 кредит = $0.005 (по стороннему гайду); примерно 10-50 кредитов за изображение (Nano Banana около 4 кредитов = $0.02 в README MCP), 100-500 за видео; LLM-вызов в кредитах (0.48 в примере) | https://www.bitdoze.com/kie-ai-video-generation/ , https://docs.kie.ai/market/quickstart , https://github.com/andrewlwn77/kie-ai-mcp-server | С (цены меняются; брать с https://kie.ai/pricing) |
| Лимиты на ключ Kie: часовой/дневной/общий лимит и IP-whitelist; код 433 = превышен лимит subkey | поисковый агрегат (без первичного документа), https://docs.kie.ai/market/bytedance/v1-lite-text-to-video (код 433) | С/Н (первичная страница настроек не найдена) |
| Лимит кредитов на ключ ElevenLabs, scope-ограничения, IP allowlist (1-100 записей) | https://elevenlabs.io/docs/overview/administration/workspaces/api-keys | В |
| Баланс ElevenLabs: `GET /v1/user/subscription` (счётчик запаздывает, вести свой учёт) | https://elevenlabs.io/docs/api-reference/user/subscription/get , gist выше | В/С |
| Рендерить по частям (по битам/сценам), чтобы ретраи были дешёвыми | https://gist.github.com/finndunning/2a057479f33f302117405148d3d21a95 | С |
| Preflight-узлы без выполнения (ComfyUI-Kie-API: Kling 3.0 Preflight валидирует вход и возвращает payload без запуска; узел баланса для проверки ключа) | https://github.com/gateway/ComfyUI-Kie-API | С |
| Песочница / тест-режим | У Kie отсутствует в документации (в оглавлении llms.txt нет песочницы/тест-ключей); у ElevenLabs не найдено | Н (отсутствие не доказано до конца) |
| Дешёвая модель для смоук-теста | Практика: использовать самые дешёвые модели/короткие длительности (Kling 5 с, низкое разрешение Seedance 480p, Nano Banana вместо Pro) для проверки цепочки; официального рецепта нет | инженерная рекомендация |
| Мягкие потолки: за прогон, за модель, за сутки + остановка по 402/433 | инженерная рекомендация | - |

Расчёт потолка на прогон: `max_spend = Σ (price_per_unit × planned_units × (1 + retry_budget))`, где `retry_budget` включает только повторы после известного `fail`, а не после таймаута.

---

## 6. Что НЕ удалось проверить (явный список)

1. Момент списания кредитов Kie (на submit с возвратом или по успеху); лаг и надёжность возвратов. Официально только "failed tasks are not charged".
2. Есть ли у Kie скрытый/недокументированный идемпотентный ключ или поле клиентского id (в документации нет; вывод об отсутствии основан на её чтении и на PR #325).
3. Содержит ли `recordInfo` `creditsConsumed` для неудавшихся/зависших задач.
4. Конкретные значения `failCode` Market API и их классификация "модерация/инфраструктура".
5. Параметры ретраев callback именно для Market API (для Veo/Suno/Runway: 3 ретрая, таймаут 15 с).
6. Veo `successFlag` и точные поля record-info (страницы 404).
7. Поведение при `callBackUrl` с query-параметрами.
8. Формулировка правила оплаты в help-центре ElevenLabs (403), политика списания при 5xx/таймауте, наличие SFX в History API и допустимые значения `state`.
9. Провайдер **OUTSEE**: никаких публичных данных/документации найти не удалось; рекомендации выше на него не распространяются.
10. Реддит/HN/Discord: первичных тредов о двойных списаниях Kie найти не удалось; используются обзоры, Trustpilot и GitHub.
11. AWS Builders' Library (содержимое не получено); Replicate (идемпотентность и retention в доках не найдены).
12. Работает ли Kie `/codex/v1/responses` с background-режимом.

---

## 7. Рекомендации по дизайну (сводка)

1. **Таблица jobs** (БД): `id, provider, model, request_hash, input_json, provider_task_id, state, submitted_at, last_polled_at, cost_estimate, credits_consumed, result_path, result_sha256`. Запись `SUBMITTING` до HTTP-вызова; `provider_task_id` пишется сразу по ответу (в одной транзакции со сменой состояния).
2. **Никогда не пересоздавать задачу, если есть `provider_task_id` и состояние не `fail`.** При `POLL_TIMEOUT` продолжать поллинг с растущим интервалом до 24 ч.
3. **`SUBMIT_UNKNOWN` не ретраится автоматически**: перед повтором проверить баланс/логи (kie.ai/logs руками или сверка баланса), ограничить число таких повторов и выделить на них отдельный бюджет.
4. **Классификация ошибок:** 401/402/433 -> остановка всего прогона; 400/422/451/413 -> отказ без ретрая; 429/455 -> backoff+jitter; 500/501 -> ограниченные ретраи (для createTask как неизвестный исход); `fail` с модерацией -> не ретраить без правки входа.
5. **Webhook + поллер одновременно;** обработчик idempotent по `(taskId, state)`; всегда перечитывать recordInfo; проверять HMAC.
6. **Скачивание сразу при success** (и в течение минут): проверка размера/хеша, запись пути, только после этого `DONE`; ссылки не хранить как итог.
7. **Бюджет:** предрасчёт, потолок на прогон/сутки/модель, проверка баланса до старта и периодически (Kie `chat/credit`; ElevenLabs subscription с кэшем), сверка леджера с балансом, автостоп по 402.
8. **ElevenLabs:** семафор по лимиту параллелизма тарифа, таймаут 300 с+, без ретраев после read-timeout без сверки с History, сохранять `request-id`, `history-item-id`, `character-cost`.
9. **Тестовый режим:** флаг dry-run (без вызовов), смоук на дешёвых моделях, ограниченный бюджетом отдельный ключ с лимитом.
10. **Абстракция провайдера** и запасные модели: платформа-посредник может убрать модель без предупреждения (Midjourney).
