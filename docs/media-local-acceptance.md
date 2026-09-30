# Текст и медиа: локальная приёмка — 30.09.2026

Статус: частичная приёмка; Docker Engine стал недоступен во время diagnosis.
Финальные mixed/browser и diagnosis GREEN не заявляются. Production выключен;
новых реальных запросов, чтения ключей, публикаций и установок зависимостей — 0.
Проверенная текущая production-база — `259dc487` (полный SHA в Git/run-config);
последующий checkpoint этого отчёта меняет только документацию.

## Что входит в текущую версию

Текстовый контур использует принятые Observe/Diagnosis/Decision/Actions/Profiles,
исходные planner/worker/граф и интерфейс. Его предыдущая приёмка сохранена в
[first-version-local-acceptance.md](first-version-local-acceptance.md).
Реальный read-only Decision pilot не повторялся; его классифицированный incident
не выдаётся за самостоятельное обнаружение причины моделью.

Для изображений подключены исходные `generate_images` и `kie_http`: постоянный
job, общий счётчик перед HTTP, статус провайдера, ограниченная загрузка,
структурный verifier, запись Artifact в исходной транзакции и финальная проверка
перед переходом ноды. Нет второго планировщика или исполнителя.

Панель v649 показывает фазу, job/attempt, тип результата, проверку, ссылку на
локальный файл и раздельные submit/status/download. Неизвестный прогресс не
заменяется процентами; тариф GET не считается нулевым. Dry-run публикации читает
существующий итоговый Artifact и каталог площадок, не вызывает publisher и не
меняет статус проекта.

## Матрица возможностей

| Тип / исходный исполнитель | Поддерживается | Разрешённые действия | Граница проверки |
| --- | --- | --- | --- |
| `excel_gpt`, `script_frames_qc` | Существующий текстовый цикл, разрешённый retry/profile, verify, hold/escalation | По прежним permission/scope/config и бюджетам | Синтетическая текстовая приёмка; реальная read-only область отдельно |
| `images`, `generate_images → kie_http` | Job, наблюдение, фазы, проверка и однократное применение PNG, безопасный preview | Scoped start; детерминированный polling известного job; read/wait/human review | Только синтетический профиль, один основной PNG на кадр; reference upload и автоматическая смена модели не приняты |
| `videos`, `generate_videos → kie_http` | Типизированный контракт, общий transport/job механизм; управляемый запуск выключен | Наблюдение, чтение, человек | Нет ffprobe; video worker/apply не принят. Общий HTTP-тест с PNG не является video acceptance |
| `audio`, `generate_audio → ElevenLabs + ASR` | Контракт и явный отказ управляемого запуска | Наблюдение, чтение, человек | Нет ffprobe, пакетов ASR и локальных весов; TTS/align worker не принят |
| Субтитры внутри `assemble` | Исходный `assembly.make_simple_ass` и структурный verifier | Локальная проверка структуры | Отдельной registry-ноды нет; интервалы проверены, точность речи и полная сборка не приняты |
| `assemble`, исходный FFmpeg | Контракт и явный отказ управляемого запуска | Наблюдение, чтение, человек | Нет ffmpeg/ffprobe; вывод, stderr и прогресс реальной сборки ещё не проверены |
| `publish`, исходный publisher | Read-only dry-run: вход, локальная проверка, список площадок; защита прямого legacy-входа | Наблюдение/валидация; отправка выключена | Нет публикации или подтверждения внешних аккаунтов; unknown-файл не становится готовым |

Успешное открытие картинки не доказывает художественное качество. Job ID/HTTP 200
не доказывают готовность файла. Остановка локального ожидания не равна отмене
задачи провайдера или возврату денег. Неизвестный submit не отправляется повторно.

## Уже записанные доказательства

Корень материалов: `outputs/media-v1-20260930` в существующем workspace.
Каталоги содержат исходный manifest, фактически загруженные модули и журналы.
Старые и неуспешные прогоны сохранены. Результаты разных snapshots не складываются
в утверждение, что всё проверено на одном commit.

| Каталог | Результат | Что именно подтверждено |
| --- | --- | --- |
| `worker-image-02` | GREEN | Настоящий API → worker → 2 image jobs → 2 Artifact; видео осталось pending |
| `artifact-safety-red-01` → `artifact-safety-green-01` | 12/12 после исправления | Подмена preview, выход за каталог проекта; восстановление с диска только с job proof |
| `final-gate-red-01` → `final-gate-green-01` | 13/13 после исправления | Дополнительно: изменённый применённый PNG не проходит исходный final gate |
| `worker-proof-red-02` → `worker-proof-green-01` | GREEN после исправления | Настоящий worker удерживает execution на файлах без proof; 0 HTTP, 0 Artifact, следующая нода pending |
| `publication-gates-green-01` | 11/11 | Media Observe/Decision, несовместимые типы, read-only preview и запрет legacy publish |
| `jobs-current-01` | 6/6 | Постоянный budget, known/unknown job, stop/scope/revision, запрет close при незавершённой операции |
| `ui-current-01` | GREEN | 80 frontend assertions, TypeScript, offline build v649; browser ещё не принят |
| `common-current-01` | 120 pass, 5 fail | Несогласованный тип старой Decision-фикстуры; причина и повтор описаны ниже |
| `common-current-02` | 124 pass, 1 error | Decision/Actions/Profiles/planner/journal GREEN; legacy approve_plan требовал отдельной plan-фикстуры |
| `runtime-adapter-current-03` | 41/41 | Весь затронутый runtime-adapter набор GREEN после восстановления plan-типа только для legacy-review |
| `runtime-current-01` → `runtime-current-02` | 25/25 после исправления фикстуры | Crash/tail и context API: тестовая ASGI БД теперь использует штатную startup migration |
| `diagnosis-current-01` | UNKNOWN, runner exit 1 | Docker API сначала 500, затем named pipe исчез; результаты контейнера не получены |

`mixed-01` прервался вместе с недоступностью Docker: контейнер затем найден
завершённым с exit 255, OOMKilled=false. Остались материалы подготовки, результата
группы нет. Это не GREEN; причина сбоя Engine неизвестна. Engine/WSL не изменялись.

## Изменения по результатам проверок

### Текущая остановка и точка продолжения

30.09.2026 около 04:46–04:49 MSK Docker API вернул 500 на read-only список
контейнеров, затем inspect сообщил отсутствие `dockerDesktopLinuxEngine`.
Runner завершился на строке 264: `Cannot wait for offline test container`.
Контейнер `vp-observe-test-20260930044407-43da6922` не удалён; его конечный статус
и результат diagnosis неизвестны. Каталог `diagnosis-current-01/artifacts` пуст.
Это ошибка доступности Engine, а не доказанный провал diagnosis-тестов.

Сохранены `diagnosis-current-01/daemon-inspect-02-audit.json` и stderr.
Первая попытка inspect (exit 64) содержала ошибку экранирования format; она тоже
сохранена и не используется как доказательство отказа daemon. Собственные
host runner и probe завершились. Worker внутри недоступного Engine не объявлен
остановленным. Чужие процессы, Engine/WSL и настройки компьютера не изменялись.

После восстановления Engine сначала inspect/collect именно этот контейнер.
Не создавать параллельный runner при неизвестном состоянии. Затем:

1. Проверить результат diagnosis; повторять только при отсутствии результата.
2. Выполнить текущий `media-observation` (11 тестов, добавлен отказ legacy plan).
3. Выполнить `group -GroupMode media-mixed -Browser` на свежей копии seed.
4. Проверить живую панель v649, preview, reload и неизменность HTTP-счётчиков;
   сохранить скриншоты и реальные loaded-module hashes.
5. Записать итог группы/HTML/PNG и checkpoint. Полный текстовый exhaustion и
   неизменённый frontend build повторять не нужно.

### Совместимость сохранённых результатов

`outputs/media-v1-20260930/source-compatibility-current.json` сравнивает реальные
файлы с manifests. Все 358 app-файлов совпадают у common-current-02,
runtime-adapter-current-03, runtime-current-02, publication-gates-green-01 и
jobs-current-01. Все 195 файлов frontend build manifest также совпадают.
SHA256 текущего `web/out/index.html`:
`e56e7227384b575aef780cd4cce2e2c9e06a6d23cbc6c6d3848abcdcaed02f96`.

После final-gate/worker-proof изменены только node_capabilities, media_view и
publish; image capability и file-validation код сохранены. Поэтому эти строки
остаются evidence конкретных snapshots, а текущий mixed worker всё ещё нужен.

### Исправления

- Preview managed Artifact проверяет каталог проекта, структуру и сохранённый
  хеш; изменённый файл возвращает 409, выход за каталог — 403.
- Исходное восстановление изображения с диска переиспользует подтверждённый job
  и детерминированный Artifact UUID. Файл сам по себе не разрешает применение.
- Worker больше не проглатывает защитную ошибку восстановления. Перед ready
  исходный validator повторно проверяет файл, job и запись бизнес-БД.
- Прямой legacy publish не может поставить published управляемому проекту при
  выключенной отправке. Dry-run реализован отдельным read-only методом того же
  исходного модуля.
- Непринятый video adapter остаётся выключенным даже на машине, где позже
  появится ffprobe. Появление инструмента не является разрешением исполнения.
- Общие Decision-тесты раньше маркировали JSON-фикстуру как `plan`, хотя задавали
  `active_excel_gpt_node_key` и требовали apply-ops. Теперь только этот подкласс
  использует реальный тип `excel_gpt`; Actions/planner сохраняют прежний тип.
  Проверки и production allowlist не ослаблены.
- ASGITransport в runtime-тесте не запускает lifespan. Фикстура создавала
  таблицы до lazy import media-моделей и зависела от порядка тестов. Теперь
  используется существующая `migrate_observation_schema`, как при штатном
  старте; проверка принудительного завершения дочернего процесса сохранена.

Совместимая общая регрессия: 41 runtime-adapter на `8b132692` и 84 остальных
теста из `common-current-02` на `c22a2406`. Между ними изменились только шесть
строк выбора типа фикстуры в `test_decision_runtime.py`; production-код совпадает.
Повтор 84 неизменённых тестов не требовался. Это 125 уникальных проверок,
а не сумма всех повторных запусков.

## Как воспроизвести на подготовленном стенде

### Границы проверок асинхронной задачи

- `test_http_restart_after_known_job_polls_without_resubmit`: реальный loopback
  HTTP; сохранён running, локальное ожидание отменено, Observer перезагружен,
  новый attempt читает прежний job. Итог — один submit, несколько status,
  download и проверенный файл. Это проверка reload наблюдения, не аварийного
  отключения всей Windows.
- `test_http_uncertain_no_second_post`: неизвестный исход submit сохраняет hold
  после reload permissions; второго POST нет.
- Corrupt/failed/redirect тесты не принимают файл и не создают новую генерацию.
- Повторное применение и rollback проверены в настоящей SQLite-транзакции.
  UUID артефакта определяется job, повтор не создаёт дубль.
- `test_stop_scope_revision_and_direct_service_bypass_block` и проверка config
  запрещают операции при user_stop, чужой ноде/проекте, изменённой конфигурации
  и прямом вызове сервиса без managed attempt.
- Общий постоянный budget различает submit/status/download. Тариф всех этих
  операций обозначен unknown; число операций не выдаётся за денежный cap.
- Обычный polling делает код; Decision не вызывается для каждого status.
  Для media недоступны универсальный текстовый retry и смена профиля.

### Запуск

Рабочая ветка: `feature/orchestrator-observe-v1`, существующий worktree.
Нужны уже имеющиеся Docker images `vp-offline-diagnostic:20260924` и
`vp-observe-frontend-deps:20260928`. Синтетический исходный seed в backend image
используется как явный тестовый материал, не как зависимость обычного приложения.
Runner копирует только разрешённые исходники; `.env`, home, Git/SSH и Docker
socket внутрь не передаются. Контейнер имеет `--network none`, `--cap-drop ALL`
и `no-new-privileges`. Loopback fixture находится внутри него.

Из worktree, строго последовательно и с новым ResultsRoot для каждой команды:

```powershell
.\scripts\test_observe_offline.ps1 -Target media-transport -ResultsRoot <fresh-path>
.\scripts\test_observe_offline.ps1 -Target media-observation -ResultsRoot <fresh-path>
.\scripts\test_observe_offline.ps1 -Target media-jobs -ResultsRoot <fresh-path>
.\scripts\test_observe_offline.ps1 -Target decision-final -ResultsRoot <fresh-path>
.\scripts\test_observe_offline.ps1 -Target runtime -ResultsRoot <fresh-path>
.\scripts\test_observe_offline.ps1 -Target diagnose -ResultsRoot <fresh-path>
.\scripts\test_observe_offline.ps1 -Target group -GroupMode media-unverified -ResultsRoot <fresh-path>
.\scripts\test_observe_offline.ps1 -Target group -GroupMode media-mixed -ResultsRoot <fresh-path>
.\scripts\test_observe_frontend.ps1 -ResultsRoot <fresh-path> -Build
```

UI build не запускается параллельно worker. Для просмотра результата используется
тот же group runner с `-Browser` и существующий `local_browser_bridge.mjs`.
Обычный интерфейс приложения не требует history viewer или кода вне клона.
Production template `config/orchestrator-runtime.example.json` выключен.

## Что требуется до расширения области

Для видео/сборки нужны ffmpeg и ffprobe в тестовой среде. Для исходного audio
worker дополнительно нужны настроенный ASR backend и его локальные веса:
в текущем image отсутствуют faster_whisper, torch, nemo и cached ASR models.
Нельзя подменять распознавание фиктивными успешными результатами или объявлять
сборку проверенной без этих средств. Установка этим заданием запрещена.

После появления зависимостей требуется подключить и проверить original worker
apply для каждой семьи, затем расширить смешанный сценарий. Инструкция адаптера —
[orchestrator-media.md](orchestrator-media.md), раздел Adding the next real adapter.
Реальные маршруты, доступы, provider quality, цены и расходы требуют отдельного
согласования. Текущая синтетическая приёмка их не подтверждает.
