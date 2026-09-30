# Recovery → images: сохранение конфигурации после restart

Продолжение от `0fe59381db937f43d40a563b1ba7bff154fcdf76`, 30.09.2026.
Локальная feature-ветка; внешних запросов и чтения ключей нет.

## Воспроизведение

`observe_attempt` фиксирует `AttemptContext.runtime_config_hash` при входе.
`worker_finished` не сохранял его в `RecoveryAction.result`, а `reconcile`
создавал контекст продолжения без hash. Поэтому существующий gate правильно
отказывал в переходе к изображениям даже при неизменённой конфигурации.
Это касается отложенного reconcile, включая restart, а не только restart.

Существующий `test_observe_offline.ps1 -Target media-observation`, network none:

- `outputs/media-v1-20260930/recovery-config-red-01`: ошибка новой фикстуры
  (`ObservationEvent.created_at`), **не валидный RED приложения**. Архив сохранён.
- `outputs/media-v1-20260930/recovery-config-red-02`: 24 теста, **2 assertion failures,
  0 errors**. Независимо воспроизведены отсутствие persisted hash и его потеря
  при восстановлении контекста; остальные 22 проверки проходят.

Пять добавленных проверок используют отдельные синтетические master/project БД,
явно подготовленные completion facts и recovery ticket. Настоящие finish/reconcile,
DB sync, planner и FSM не подменены. Обнулён живой AttemptContext, заменён Observer,
права перечитаны из БД. Это интеграционная проверка восстановления, не новый
worker/exhaustion прогон и не имитация перезагрузки ОС.

Защитные проверки: смена конфигурации, старый ticket без hash, ошибка recovery;
положительная проверка требует ровно одного перехода и идемпотентного reconcile.
Архивные execution, counters, данные и код не изменяются.

## Исправление и GREEN

После RED checkpoint `d8232618` изменён только `actions_recovery.py`:
`worker_finished` сохраняет исходный `ctx.runtime_config_hash` в JSON result,
`reconcile` передаёт сохранённое значение в новый `AttemptContext`.
Миграция, новый executor или ослабление gate не нужны. Отсутствие hash у старого
ticket остаётся отсутствием доказательства; текущей конфигурацией не замещается.

`recovery-config-green-01`: **24/24**, 72.427s, exit0, загруженные исходники
совпадают с manifest. Восстановление разрешает один FSM-переход image pending→running
и синхронизирует master; повторный reconcile не меняет attempts и не создаёт
второго события. Изменённая конфигурация, старый ticket и ошибка recovery
продолжение не разрешают. Проверяется механизм восстановления, не новая генерация.

## Финальная регрессия

Проверенный код: **`d38a9a8a4242f6d54ff865e43f29163375e890dc`**.
Финальный checkpoint меняет только эту инструкцию и ссылку в handoff.

| Существующий target | Локальная папка в `outputs/media-v1-20260930` | Результат |
|---|---|---|
| `media-observation` | `recovery-config-green-01` | 24/24, 187 загруженных app-модулей |
| `decision-final` | `recovery-config-common-01` | 125/125: runtime policy41, Decision33, Actions24, Profiles17, planner2, journal8; 182 модуля |
| `qc-noop` | `recovery-config-qc-01` | 41/41; 179 модулей |

Это **190 успешных выполнений тестов**, не 190 новых/уникальных тестов:
QC target повторно загружает часть Actions-контрактов. Новых тестов — пять.
Все три контейнера: network none, exit0, OOMKilled=false; своих активных runner
после приёмки нет. Failed-контейнеры и результаты сохранены без изменений,
кроме добавления снимка их конечного состояния в отдельный файл.

В каждой папке доступны `artifacts/test-output.txt`, `run-config.json`,
`loaded-modules.json`, `container-state.json`, исходники и `source-manifest.json`.
Общая сверка — `outputs/media-v1-20260930/recovery-config-source-proof.json`:
все загруженные файлы и все файлы трёх manifest совпали с текущим worktree.
SHA256 `app/services/actions_recovery.py`:
`c28b609b5520a50472b5d60b2cd64646464826865f278a09135f13482ac8e085`.

## Точная граница

- Подтверждена запись исходной конфигурации при finish и её чтение после потери
  контекста и reload Observer/permissions. Использованы настоящие отдельные БД,
  sync, verification, original continuation, planner/FSM. Подготовленные test facts
  не являются доказательством нового исполнения всей группы worker.
- Source→image переход происходит один раз при неизменённой конфигурации.
  Старый ticket без hash, изменённая конфигурация и ошибка recovery не запускают
  images. Существующие stop/scope/uncertain/budget/revision проверки проходят.
- Верификация завершённой текстовой попытки и разрешение следующей ноды остаются
  разными решениями. `continuation_submitted` означает вызов исходного continuation;
  при отказе media gate решающим доказательством остаются pending целевой ноды и
  `managed_successor_denied`, а не это поле ticket. Его семантика не изменена.
- UI и transport не изменены; предыдущие browser/build результаты не объявляются
  повторно выполненными. Полный новый worker exhaustion + restart + image completion
  этим follow-up не проверялся. Прежние mixed/worker архивы сохранены отдельно.
- Production выключен. Реальных API, ключей, новых зависимостей, push/PR/deploy — 0.
  Оставшиеся video/TTS/ASR/assembly зависимости и production-приёмка описаны
  в основном handoff; поддержки этих семейств данное исправление не добавляет.

Повторить можно тем же `scripts/test_observe_offline.ps1` с target из таблицы
и новой папкой `-ResultsRoot`; последовательно, на кэшированном образе.
Внешняя диагностическая обёртка, launcher, сетевые настройки и adapters в этой
работе не менялись. Единственный production diff — `actions_recovery.py`.
