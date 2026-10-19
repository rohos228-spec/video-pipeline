# Подключение модели решений к текущему runtime

Используется существующий `gpt_api.chat` (Kie Responses) либо один non-stream
POST к Vibecode chat/completions (см. раздел ниже), текущие Decision tools,
Actions supervisor, worker и verification. Второго executor нет. Параметры
модели решений отделены от профиля исполняемой ноды. Каталог подтверждает
совместимость, а разрешение даёт только локальная конфигурация оператора.

## Настройка

1. Скопировать `config/orchestrator-runtime.example.json` в локальный файл.
   Исходный пример выключен: модель/маршрут не заданы, allowlist пуст.
2. Указать путь в `ORCHESTRATOR_RUNTIME_CONFIG`. Это JSON политики, без ключей.
   Ключи использует штатный клиент; новые секреты в репозиторий не добавлять.
3. После отдельного согласования задать точный HTTPS endpoint одобренного
   маршрута: Kie Responses (`/codex/v1/responses`) или Vibecode chat/completions
   (`/v1/chat/completions`). Relay/VPS маршрут должен совпадать с фактическим
   `_chat_url`; скрытое переключение запрещено.
4. Задать `decision`: `catalog_id`, `model_id`, `reasoning_effort`,
   `max_output_tokens`, `timeout_s`. Поддерживаются ровно два `catalog_id`:
   `gpt-kie` и `gpt-5.6-sol-vibecode` (без алиасов); провайдер выводится из него.
   Совместимость text model проверяется по существующему локальному каталогу.
   В offline-приёмке: `gpt-5-6-sol`, low, 1024, 30 секунд. Это НЕ подтверждение
   наличия/цены/качества модели в реальном аккаунте.
5. Разрешить конкретные `allowed_actions`: `read_events`, `read_contract`,
   `wait`, `human_review`, `finish`, `retry_same`, `retry_profile`.
   Выбор модели не выдаёт права на инструменты или другие проекты.
6. `profiles` оставить `{}`, пока не согласован точный профиль ноды. Профиль:
   `model_id`, `provider=kie`, `kind=text`, `channel=stable`, `reasoning_effort`,
   `max_output_tokens`, `prompt_variant=require-apply-ops-v1`,
   `synthetic_only=false`. Произвольные параметры, промпты и код отклоняются.
7. Создать наблюдение для одной группы `script_frames_qc`, разрешить managed
   execution с общим лимитом, включить цикл решений и запустить первую ноду.
   Следующие turns/tools/recovery/verify выполняет приложение.

Конфигурация привязывается к execution хешем и сохранённым снимком. Смена файла
при активном execution отклоняется; нужен новый явно разрешённый execution.
Одобрение overlay привязано к node/base/catalog/attempt. Возврат конфигурации
не возвращает расходы и не откатывает бизнес-данные.

## Защиты

Общий постоянный счётчик резервируется до HTTP POST/GET; различаются purpose
`node`, `review`, `decision` и получение ответа по ID. Продолжения и разделение
текста используют ту же транспортную границу. Неизвестный/частичный результат
удерживает execution: новый POST запрещён. Стоп, audit gap, scope и устаревший
граф блокируют отправку. Обязательное ревью без однозначной ноды в scope
отклоняется. Legacy chat/Fix bugs сохраняют запрет обхода managed policy.

## Первый online-пилот — подготовлен, запуск запрещён

| Условие | Значение / блокер |
|---|---|
| Задача | Только диагноз обезличенного сохранённого инцидента обязательного JSON |
| Модель | Реальная модель ещё не одобрена; тест совместимости использовал gpt-5-6-sol |
| Маршрут | Точный реальный endpoint/relay ещё не задан и не подтверждён |
| Вход | Только выбранные редактированные события, контракт, версии, статусы, лимит и история tools; без ключей, полных файлов проекта и произвольного чтения диска |
| HTTP | Новый отдельный execution, request_limit=1, включая получение ответа по ID; не 8 |
| Выход | max_output_tokens=1024; не более одного HTTP POST модели благодаря общему request_limit=1; штатный supervisor имеет до 3 turns, но второй HTTP блокируется; automatic continuation/volume completion и сетевые retry выключены |
| Действия | allowed_actions только read_events/read_contract; retry_same/retry_profile/finish и изменяющие tools выключены сервером |
| Worker | Не запускать ноды; копия обезличенного завершённого инцидента; никакой генерации |
| Цена | Не исследовалась на этом этапе; доказанной верхней денежной границы нет |
| Сеть | Требуется отдельное согласованное ограничение точного маршрута для online-контейнера; нынешний network none сохраняется |
| Разрешение | Требуется новая команда, старое разрешение на расходы не действует |

Сейчас `online_enabled=false`, `decision=null`, `allowed_actions=[]`: даже первый
POST блокируется. До online требуются подтверждённая цена/денежная граница,
маршрут, профиль/параметры, доступ через штатные настройки без раскрытия ключа,
согласованный обезличенный вход и отдельное разрешение запуска. Обещания
денежного потолка на основании числа запросов нет.

Проверенная область: text `script_frames_qc` и ошибки обязательного JSON/payload.
Другие провайдеры, arbitrary code/prompt edits и media-агенты не добавлены.

## Vibecode chat/completions (опционально, выключено по умолчанию)

Пример: `config/orchestrator-runtime.vibecode.example.json` (`online_enabled=false`).
Секретов в конфигурации нет: ключ берётся только из `VIBECODE_API_KEY` в
настройках приложения (при relay дополнительно `X-VP-Relay-Token`), не читается из
конфига и не пишется в факты/логи. Пустой ключ отклоняется до резервирования запроса.

- `catalog_id=gpt-5.6-sol-vibecode`, `model_id=gpt-5.6-sol` (ровно `api_model`
  каталога), `reasoning_effort=null` (параметр reasoning Vibecode не отправляется),
  `max_output_tokens` 1..16384 (рекомендуется 2048-4096: токены рассуждений входят в лимит).
- Только `mode=production`, `model_transport=real`, `external_calls_enabled=true`,
  `continuation_allowed=false`, `retrieval_allowed=false`, без `gateway_ack`,
  `profiles={}` и без `retry_profile` в `allowed_actions` (профили только Kie).
- Хост endpoint: `vibecode.moe`, хост VPS-relay из настроек (если задан) или
  loopback (тесты); HTTPS, без userinfo/query/fragment, путь `/v1/chat/completions`.
  Фактический URL строит `_chat_url`; он обязан совпасть с `endpoint` до отправки.
  При relay контекст решений идёт через сторонний VPS.
- Один non-stream POST на ход: без ретраев, продолжений и повторного POST.
  Лимит выводится параметром `max_completion_tokens` (проба 2026-10-03: принимается, но
  vibecode.moe его не соблюдает); отправляется `response_format: json_object` (проба: поддерживается),
  `temperature` не отправляется. Схема и `allowed_actions` проверяются на сервере как и раньше.
- Ответ с `finish_reason=length` (или `completion_tokens >= лимита`) даёт отдельную
  ошибку `DecisionTruncated` и эскалацию `human_review` без повторного POST.
- Проверено живой пробой 2026-10-03 (5 запросов, около $0.03 по каталогу): формат ответа и `usage`, `finish_reason=stop`,
  оба параметра лимита принимаются, лимит не соблюдается, `response_format: json_object` работает; соединение прямое с vibecode.moe.
  Не проверено: поведение через relay на ответах до 60-90 секунд, реальная цена, тело ответа 402. Бюджет считает запросы, не деньги.
