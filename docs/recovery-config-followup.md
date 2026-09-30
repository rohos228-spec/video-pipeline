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

Остальная регрессия будет указана отдельно после фактического выполнения.
UI и transport не изменены; предыдущие browser/build результаты не объявляются
повторно выполненными. Полного нового worker exhaustion/restart пока не было.
