# Decision read-only tool cycle — GREEN

Дата: 2026-09-29. Ветка feature/orchestrator-observe-v1.
Исходный HEAD d287e717b4fb95b0cbbb5a41f583828d8c493c77 подтверждён.
Проверенный checkpoint теста: aff6cd1cfc620357f33e5352d8a370f8cc37d04d.
Production-код app/config не изменён относительно d287e717.
Изменены только целевой тест и диспетчер существующего offline harness.

## Сценарий и происхождение

Исходник: outputs/actions-v1-final/recovery-a-08/artifacts/exhausted-checkpoint.
Принятый исходный запуск: exit=0, checkpoint 93d8c939f39a432e0b7a26b0704a78b55ddaa2d5.
Это genuine exhausted checkpoint ДО supervisor recovery: 9 штатных ошибок,
18 synthetic model POST. Последняя попытка: caa73916-c0b3-4c54-a40b-f6c812efde6c.
Это другой кейс — failed creating/script node; не исторический QC до e4140.
QC-срез, его unknown retry и прежний online one-shot не использовались/не менялись.
Ни abandoned/exhausted/failed, ни counters не выставлялись тестом для допуска.

Копия находится в accepted-02/artifacts/free-run-data/state.db плюс проектная
synthetic videos/diagnostic-synthetic-scene-2026-09-24/project.db.
Это физически отдельный контейнер/каталог без монтирования рабочих данных.
UUID execution сохранён при копировании для целостности исходных связей:
391292c6-ed32-4b8d-8e24-a970ea2e13aa. Он действует только в этой отдельной тестовой БД.
Decision ID: 56794cb9-2581-40a0-b0f1-d319088a3213. Для будущего online потребуется новое разрешение и состояние.
Исходный seed совпадает по SHA256 с неизменённой входной копией контейнера:
401 файлов, source-seed-manifest.json. Исходные попытки/события/счётчики сохранены.
Никаких последующих успешных recovery/HTML в input checkpoint нет.

## Штатный цикл

1. Стандартная migrate_observation_schema и observer.reload над копией.
2. Оригинальный DecisionSupervisor.configure создаёт control/binding/decision_mode audit.
3. Запускается только оригинальный Observer.loop, не app.main и не worker.
4. loop вызывает штатные diagnosis/catch_up/reconcile/tick. Recovery tickets отсутствуют.
5. tick видит genuine exhaustion и сам вызывает begin; _context/_live работают без подмен.
6. Три HTTP fixture шага: read_events → read_contract → human_review.
7. Оригинальные tools читают реальные записи/контракт синтетического проекта.
   Их точные результаты найдены в последующих HTTP input, не только строка tool_result.
8. Итог human_review, held=true, model_steps=3; Decision/turns/audit сохранены.
   Последующие polling-циклы не дали четвёртого POST, затем observer task остановлен.

Ни tick/step/begin, ни результат tool тестовым runner вручную не вызываются/не подменяются.
configure вызывается как разрешённое действие оператора. Trace подтверждает фактические
begin/tick/step/_context/_live/read_events/read_contract; история отражает решения runtime.
Ответы fixture заданы заранее: это проверка исполнения диалога, не качества реальной модели.

## Конфигурация, ограничения и расходы

accepted-02/artifacts/runtime-config.json: mode=synthetic, endpoint
http://127.0.0.1:18766/codex/v1/responses, gpt-5-6-sol, low, output 1024 tokens.
Only read_events/read_contract/wait/human_review; profiles={}.
Production config не включалась, реальные .env/ключи не читались.
Сохранённый requests_used=18 НЕ сброшен. При подготовке только в копии request_limit
снижен с 40 до 21. Штатные HttpDecision/ActionSupervisor reservations: 18 → 19 → 20 → 21.
Три decision_model_start и три completed decision_model_end с usage сохранены.
Максимум 3 шага — существующий MAX_STEPS. Synthetic POST также ограничен fixture cap=3.
Это не новая система хранения/шлюз/адаптер: используются исходный HTTP client,
Decision state и существующий Docker test harness.

Фактический размер тел трёх HTTP запросов: 17591, 20572, 22380 UTF-8 байт.
История tools увеличивает размер следующих запросов; это размеры этого synthetic кейса,
а не гарантированный размер любого будущего реального incident.
Будущий настоящий многошаговый тест: максимум три модельных обращения,
1024 выходных токена на обращение, запрет retry/recovery/profile/start/resume/finish/code/data tools.
Он потребует отдельного разрешения и согласованного шлюза; текущее разрешение бесплатное.
Реальная стоимость и credits/USD не подтверждены; число запросов не денежный cap.

## Защита исполнителя и бизнес-данных

- Allowlist ограничена серверной runtime_config и validate_proposal, не только prompt.
- retry_same/retry_profile/start/resume/finish/shell отвергнуты на actual context.
- Прямой вызов оригинального recover_node с decision_id отвергнут executor:
  `incident is held by its decision owner; no competing recovery`. Recovery tickets/новые attempts отсутствуют.
- До начала loop на копии всех SQLite файлов созданы диагностические BEFORE DML triggers:
  бизнес-таблицы, recovery и profile writes запрещены. Это тестовая защита, не изменение runtime.
- Новый action audit разрешает только decision_mode/human_review, не modifying action.
- Реальная попытка UPDATE node_runs отклонена SQLite: `diagnostic executor forbids business/profile/recovery write`.
- business-before.json и business-after.json побайтно равны; все non-service таблицы,
  включая projects/workflow_runs/node_runs/frames/scenes/frame_texts/scene_design_cells/profile/recovery,
  сравниваются во всех синтетических DB. Новых node_start событий нет.
- Служебные изменения разрешены: observation/diagnosis, decision control/binding/record/turns,
  action permission requests_used/held, audit. Это не изменение business status.
- Штатных model POST=3, worker start=0, Actions dispatch=0, recoveries=0.
  Не включались worker, API приложения, старый чат, profiles или другие runtime consumers.

## Изоляция и фактические версии

Существующий image vp-offline-diagnostic:20260924; --network none,
--cap-drop ALL, no-new-privileges, без mounts/.env/home/Git/SSH/Docker socket.
Только loopback fixture внутри контейнера. Connect к 192.0.2.1:9 блокирован, данных не посылалось.
Container /vp-observe-test-20260929190639-dc219530 остановлен, exit=0; container-inspect.json сохранён.
Source manifests и loaded-modules.json проверены по текущим файлам.
Никакого push/PR/deploy/новых зависимостей/реальных API.

## Артефакты

Все пути относительно outputs/decision-readonly-tool-cycle:

- accepted-02/artifacts/result.json — acceptance assertions и счётчики.
- accepted-02/artifacts/requests.json — полные synthetic HTTP input.
- accepted-02/artifacts/fixture-responses.json — подготовленные model proposals.
- accepted-02/artifacts/turns.json и free-run-data/state.db — действительная durable история/SSE/usage.
- accepted-02/artifacts/free-run-data/observations/bodies — очищенные полные HTTP тела со ссылками/хешами.
- accepted-02/artifacts/business-before.json / business-after.json — фактические данные после setup/после loop.
- accepted-02/artifacts/runtime-config.json / run-config.json — конфигурация и версия.
- accepted-02/source/source-manifest.json / artifacts/loaded-modules.json — точные SHA256 source/imports.
- acceptance-proof.json / durable-state-proof.json — независимая сверка сохранённых доказательств.
- source-seed-manifest.json / accepted-02/seed-copy-proof — доказательство неизменности оригинального seed.

Первый attempt-01 сохранён: runtime assertions прошли, но container exit=1 из-за
finish_coverage без обязательного аргумента results. Исправлен только test harness;
accepted-02 повторяет тот же сценарий на новой копии, exit=0. Failure не выдан за GREEN.
Исторические длинные tests/UI/build повторно не запускались: production/frontend не менялись.

## Повтор запуска

Из существующего worktree, при работающем Docker Engine, с новым ResultsRoot:

```powershell
.\scripts\test_observe_offline.ps1 -Target decision-readonly `
  -DenialSeed '..\..\outputs\actions-v1-final\recovery-a-08\artifacts\exhausted-checkpoint' `
  -ResultsRoot '..\..\outputs\decision-readonly-tool-cycle\new-run'
```

Команда синтетическая и не разрешает online. Существующие каталоги результатов
не перезаписываются. Завершено; новые turn/платные запросы не запускаются.
