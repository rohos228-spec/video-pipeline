# RFC: контракт `ws3-r3.4`

Статус: принято в коде, `CONTRACT_VERSION` = `ws3-r3.4`.

Поля и события ниже — провод этой версии. Сервер r3.3 их не отдаёт: клиент
без `kill_switch` читается как `false`, а события `message_start` /
`message_end` на старом контракте не приходят.

Три дыры r3.3, которые r3.4 закрывает, не трогая FSM и `BLOCK_OUTCOMES`:

1. аварийный стоп нельзя нажать полем, только фразой в тексте;
2. `missing_project_id` нельзя снять: `grant_approval` снова упирается в пустой `project_id`;
3. у `token` нет границы ответа, и WS4 склеивает токены двух реплик.

## (a) Kill switch полем `MessageIn`

Сейчас `MessageIn` — это `text: str`, `mode: MessageMode | null`, `project_id: int | null`.
Фразы «аварийный стоп», `kill switch`, `kill-switch` перебивают явный `mode` и зовут
`request_kill`. Отдельного поля у входа нет. Событие `kill_switch` в проде несёт
`stop_key_set: false`, `stop_key: "pending"`, `only_node: true`. Это r3.4 не меняет.

Новый режим enum не добавляем. Неизвестное значение `mode` на r3.3 уже даёт 422,
а клиент, который шлёт новый режим старому серверу, потеряет кнопку. Опциональное
поле со значением по умолчанию старый сервер просто игнорирует (лишние поля
`MessageIn` не принимает как ошибку: модель без `extra=forbid`).

### Поля

| Поле | Тип | Где | Смысл |
|---|---|---|---|
| `kill_switch` | `bool`, по умолчанию `false` | `MessageIn` | `true` — тот же путь, что фраза: interrupt + `request_kill`, даже если задан `mode` |
| `kill_switch` | `bool` | payload `classification` | уже есть в r3.3; сервер ставит `true`, если сработало поле или фраза |

`mode` остаётся `interrupt | steer | append | task | null`. Поле не заменяет `mode`:
при `kill_switch: true` классификация публикуется как `mode=interrupt` и
`kill_switch=true`.

### Пример

Кнопка, текст можно оставить человеческим:

```json
{
  "text": "останови генерацию",
  "mode": "append",
  "kill_switch": true
}
```

Ответ HTTP тот же: `{"ok": true, "session": "s1", "ack_seq": 4}`.
Дальше в SSE:

```json
{
  "type": "classification",
  "task_id": 7,
  "seq": 5,
  "session_id": "s1",
  "payload": {
    "mode": "interrupt",
    "tier": "fast",
    "deterministic": true,
    "confidence": 1.0,
    "kill_switch": true
  }
}
```

```json
{
  "type": "kill_switch",
  "task_id": 7,
  "seq": 6,
  "session_id": "s1",
  "payload": {
    "stop_key_set": false,
    "stop_key": "pending",
    "only_node": true,
    "task_id": 7
  }
}
```

`kill_switch: false` или поле отсутствует — поведение r3.3, включая фразы.

### Обратная совместимость с r3.3

- Клиент r3.3 поле не шлёт. Сервер r3.4 читает это как `false`.
- Сервер r3.3 поле игнорирует. Кнопка на старом сервере без фразы **не** останавливает задачу. WS4 не должен считать поле достаточным, пока `version` не `ws3-r3.4`.
- Набор `MessageMode` и payload `kill_switch` не меняются. `stop_key_set` в проде по-прежнему `false`, пока узел не вооружён отдельным хуком.

### Что меняется для WS4

Кнопка аварийного стопа шлёт `kill_switch: true` и не обязана подмешивать фразу в `text`.
Явный `mode` больше не прячет стоп. Разбор события `kill_switch` тот же: смотреть
`stop_key_set` / `stop_key` / `only_node`, не выдумывать ключ на клиенте.

### Тест-план

- `kill_switch: true` при `mode: "append"` публикует `classification.kill_switch=true` и `kill_switch`, отменяет неплатный шаг.
- `kill_switch: false` и отсутствующее поле не зовут `request_kill` на тексте без фразы.
- Фраза по-прежнему перебивает `mode`, даже если поле `false`.
- Тело без поля проходит те же очереди, что r3.3.
- Продовый payload стопа остаётся `stop_key_set=false`, `stop_key=pending`. Значение ключа в лог и в assert не печатать.

## (b) Поздний `project_id`

Сейчас `project_id` можно передать только в `MessageIn` в момент приёма задачи.
`0`, `bool` и неположительные значения не сохраняются (`usable_project_id`).
План с платным или мутирующим шагом без id встаёт в `waiting_approval` с причиной
`missing_project_id`. `grant_approval` перечитывает чекпоинт и снова останавливается:
записать id задним числом некуда. Причина не терминальная, поэтому grant не запрещён,
но цикл бесконечный.

r3.4 добавляет один вызов. Новый статус FSM не заводится.

### Вызов

`POST /api/orchestrator/chat/{session}/project`

Тело:

| Поле | Тип | Правило |
|---|---|---|
| `project_id` | `int` | обязательно; то же правило, что `usable_project_id`: не `bool`, `>= 1` |
| `task_id` | `int \| null` | необязательно; если нет — активная задача сессии |

Ответ `200`:

| Поле | Тип |
|---|---|
| `ok` | `bool` |
| `session` | `str` |
| `task_id` | `int` |
| `project_id` | `int` |
| `status` | `str` |

`409`, если задача не в `waiting_approval`, причина блока не `missing_project_id`,
или сессия без такой задачи. `422`, если id неположительный, `0` или `bool`.
Тело задачи и чекпоинт при `409`/`422` не меняются.

Успех:

1. записать `orch_tasks.project_id`;
2. снять удержание `missing_project_id`;
3. пойти тем же путём, что `grant_approval`: `executing`, заново `_screen_plan`, продолжить с позиции чекпоинта.

Другая причина экрана (риск, бюджет, пустой план) остаётся как сейчас. Этот вызов
не подменяет grant для `risk` и не оживляет терминальный `empty_plan` / `deletion_blocked`.

### Примеры

Запрос:

```json
{ "project_id": 13 }
```

Ответ:

```json
{
  "ok": true,
  "session": "s1",
  "task_id": 7,
  "project_id": 13,
  "status": "executing"
}
```

Отказ:

```json
{ "project_id": 0 }
```

`422`, статус задачи остаётся `waiting_approval`, `project_id` в строке по-прежнему `null`.

После успеха в SSE прежний `agent_status` со `status: "executing"`. Если экран снова
остановил задачу, приходит новый `approval_required` со своей `reason`, не
`missing_project_id`.

### Обратная совместимость с r3.3

- Маршрута в r3.3 нет. Старый клиент его не вызывает, grant и цикл `missing_project_id` живут как сейчас.
- Старый сервер на этот POST отвечает `404`. WS4 показывает поле ввода id только при `version == ws3-r3.4` и `approval_required.reason == missing_project_id`.
- Смысл `usable_project_id` не ослабляется: `0` и `false` по-прежнему не id проекта.

### Что меняется для WS4

На `approval_required` с `reason: "missing_project_id"` — форма с целым `project_id` и POST
на `/api/orchestrator/chat/{session}/project`. Кнопка «продолжить» без id по-прежнему
только `grant` и сама по себе цикл не разрывает. После `200` WS4 ждёт `agent_status`
или новый `approval_required`, а не считает задачу выполненной по HTTP.

### Тест-план

- Задача без id на платном шаге ждёт `missing_project_id`. POST `13` пишет id, снимает причину, доходит до шага (или до следующей честной причины).
- Повторный grant **без** этого POST снова оставляет `missing_project_id`.
- `0`, `-1`, `false` → `422`, строка и статус не меняются.
- Вызов на `done` / `failed` / другой причине (`risk`) → `409`, id не записывается.
- Read-only задача, которая и так шла без id, этим маршрутом не обязана пользоваться.

## (c) Граница сообщения в SSE

Сейчас `token` эфемерный: payload только `{"text": "<кусок>"}`, в `orch_events` он не пишется.
Целиком реплика приходит событием `message` (`role`, `text`). Идентификатора ответа
в токене нет. Два быстрых `append` подряд WS4 склеивает в одну строку, если буфер
не сбрасывается между ними.

r3.4 помечает один ответ тремя вещами: старт, тот же `message_id` на каждом токене, конец.
Событие `message` остаётся: старый клиент по-прежнему собирает полный текст из него.

### Поля

| Событие | Поле payload | Тип | Персист |
|---|---|---|---|
| `message_start` | `message_id` | `int` | да |
| `message_start` | `role` | `"assistant" \| "user"` | да |
| `token` | `text` | `str` | нет (кольцо, как в r3.3) |
| `token` | `message_id` | `int` | нет |
| `message_end` | `message_id` | `int` | да |
| `message_end` | `role` | `"assistant" \| "user"` | да |
| `message` | `role`, `text` | как в r3.3 | да |

`message_id` — id строки `orch_messages` этого ответа. Одно и то же число на
`message_start`, всех `token` этого ответа и `message_end`. Следующий ответ — другое число.

Порядок seq: `message_start` < первый `token` < последний `token` < `message_end`.
Событие `message` идёт следом и не обязано совпадать seq с `message_end`, но
несёт тот же смысл реплики. `task_id` на конверте события — как у остальных событий шины.

Токены после рестарта по-прежнему не восстанавливаются из БД. `message_start` /
`message_end` пишутся в `orch_events`, поэтому reconnect по `Last-Event-ID` видит
границу, даже если кольцо токенов уже умерло. Полный текст reconnect берёт из `message`.

### Пример одной реплики

```json
{
  "type": "message_start",
  "task_id": null,
  "seq": 10,
  "session_id": "s1",
  "payload": { "message_id": 41, "role": "assistant" }
}
```

```json
{
  "type": "token",
  "task_id": null,
  "seq": 11,
  "session_id": "s1",
  "payload": { "text": "добро ", "message_id": 41 }
}
```

```json
{
  "type": "token",
  "task_id": null,
  "seq": 12,
  "session_id": "s1",
  "payload": { "text": "пожаловать", "message_id": 41 }
}
```

```json
{
  "type": "message_end",
  "task_id": null,
  "seq": 13,
  "session_id": "s1",
  "payload": { "message_id": 41, "role": "assistant" }
}
```

```json
{
  "type": "message",
  "task_id": null,
  "seq": 14,
  "session_id": "s1",
  "payload": { "role": "assistant", "text": "добро пожаловать" }
}
```

Второй ответ начинается с нового `message_start` и другого `message_id`. Токены
`41` и токены `42` в один буфер не кладутся.

### Обратная совместимость с r3.3

- Сервер r3.3 шлёт `token.payload` только с `text` и не шлёт `message_start` / `message_end`.
- Клиент r3.3 не знает новых типов и не читает `message_id`. Он по-прежнему клеит все `token` до события `message` — это текущее поведение, не регрессия старого клиента.
- Клиент r3.4 на сервере r3.3: нет `message_id` → один поток, как сейчас. Не считать отсутствие поля ошибкой.
- `token` остаётся эфемерным. В `EVENT_TYPES` r3.3 этих двух имён нет; добавление имён — часть будущего изменения кода, не этого RFC.

### Что меняется для WS4

Держать буфер на `message_id`, а не один буфер на сессию.

- `message_start` открывает буфер.
- `token` с тем же `message_id` дописывается в него. Чужой `message_id` чужой буфер не трогает.
- `message_end` закрывает буфер.
- Нет `message_id` (сервер r3.3) — прежняя склейка до `message`.
- Неизвестный тип на старом контракте игнорировать. На `ws3-r3.4` `message_start` и `message_end` обязательны к разбору.
- После reconnect не ждать токены из БД. Границы и полный `message` приходят replay-ем; оборванный токен внутри живого процесса по-прежнему только из кольца.

### Тест-план

- Два `append` подряд: у токенов первого ответа один `message_id`, у второго другой; склейка по id не смешивает тексты.
- `message_start.seq` меньше seq первого токена, `message_end.seq` больше seq последнего токена того же id.
- `token` нет в `persisted_types`. `message_start` и `message_end` есть и возвращаются replay-ем после `Last-Event-ID`.
- Событие без `message_id` (снимок r3.3) тест клиента собирает в одну строку, как раньше.
- Повторный reconnect не порождает второй `message_id` для уже закрытой реплики.

## Экспорт `ApprovalDecision` не входит в r3.3

`grant_approval(session, decision=None, *, retry_grant=False)` принимает необязательный
`decision`. Пока версия `ws3-r3.3`, `export_contract_schemas()` не содержит
`ApprovalDecisionBody`, enum `ApprovalDecision` и `rules.approval_decision_labels`.
Старое тело без этих полей валидируется и идёт прежним путём. В экспорт они
попадут только вместе с бампом версии.

## Что r3.4 сознательно не делает

- Не меняет `CONTRACT_VERSION`, пока этот RFC не принят отдельным коммитом кода.
- Не добавляет режим `kill_switch` в `MessageMode`.
- Не превращает `missing_project_id` в терминальный отказ и не пишет id внутри `grant_approval`.
- Не кладёт токены в `orch_events`.
- Не выбирает заново `empty_plan` и не описывает повтор платного шага с неизвестным исходом: это уже `waiting_approval` / `paid_outcome_unknown` в текущем коде r3.3, не предмет этого контракта.
