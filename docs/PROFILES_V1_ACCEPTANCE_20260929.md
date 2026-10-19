# Profiles-v1 — локальная синтетическая приёмка 29.09.2026

**GREEN в согласованном объёме.** База Decision-v1: 914c320fbafecf070c48a318aa36095fed1e3209. Финальный SHA записан отдельно в checkpoint.json после локального commit; исходники привязаны не только к базе, но и к реально загруженным SHA-256.

## Реализация

- Аддитивная таблица node_profile_overlays: execution/project/node/source attempt, новая attempt, неизменная базовая версия и candidate, catalog/config/body hashes, одобрение, активация, отмена, история.
- По умолчанию профиль отсутствует. Production allowlist пуст; единственный synthetic-json-v1 разрешён лишь для loopback с synthetic credential. Не утверждается совместимость с production-провайдером.
- Оператор одобряет фиксированный вариант через существующий authenticated API. Decision выбирает только уже одобренный overlay_id. Произвольных моделей, параметров, промптов, SQL/shell/code tools нет.
- Отдельное retry_profile использует прежний recovery executor/worker/planner. Новый профиль расходует ту же единственную recovery и общий HTTP budget; attempts/лимиты не сбрасываются.
- Сохраняется защита retry_same: неизменный прежний request hash. retry_profile проверяет прежний body/config, утверждённый candidate и ожидаемый первый effective request. Повторные transport-вызовы также проходят общую policy и limiter.
- Overlay ограничен конкретной попыткой. Переиспользованы llm_override и prompt resolution; group-specific reasoning/model priority не отменяет профиль. Глобальные настройки и общие шаблоны не изменяются.
- Отмена возвращает только конфигурацию. Бизнес-данные и расход не откатывает; active/await_verify не отменяет. Queued cancellation сохраняет ticket/budget и переводит execution в hold/human review.

## Финальный обязательный набор

| Проверка | Результат | Доказательства внутри outputs/profiles-v1 |
|---|---|---|
| Profiles unit/API/policy | 17 OK | final-profiles/artifacts/test-output.txt |
| Decision integration | 33 OK | final-decision/artifacts/test-output.txt |
| Actions integration | 24 OK | final-actions/artifacts/test-output.txt |
| Runtime | 25 OK | final-runtime/artifacts/test-output.txt |
| Journal | 8 OK | final-journal/artifacts/test-output.txt |
| Diagnosis | 25 OK | final-diagnose/artifacts/test-output.txt |
| Frontend contracts | 64 assertions OK: 17+21+12+12+2 | frontend-final/artifacts/frontend-contract.txt |
| TypeScript | exit 0 | frontend-final/artifacts/typecheck.txt; config.json |
| Fresh build v645 | exit 0, 43 exported files | frontend-final/artifacts/build.txt; build-manifest.json |
| Actual browser | success/reload и human review/reload, errors 0 | ui-verified-final; ui-human-final |

Всего **132 Python tests**, пять worker acceptance cases, fresh frontend/UI. Агрегат реально исполненных строк новых Profiles модулей: profiles_state 96.3%, profiles_v1 96.8%. Метрика не заменяет сценарные проверки.

## Настоящий worker и общий бюджет

| Сценарий | Фактическое выполнение |
|---|---|
| worker-02 / profiles-success | 3 brain POST + 5 worker POST = 8 новых; ledger 18→26. Одна target attempt с gpt-synthetic-alt, medium, max_output_tokens 2048 и фиксированным prompt variant. Остальные четыре LLM-ноды на прежнем профиле. Все шесть done, HTML сформирован исходным приложением; n_script pending. |
| worker-bad / profiles-bad | 2 brain POST + 2 worker POST (штатный JSON retry в одной дополнительной attempt) = 4 новых; ledger 18→22. Verification отрицательная, human_review/hold, больше recovery/POST нет. |
| regression-decision | Прежний retry_same: 3 brain + 5 worker = 8, одна recovery, verify, шесть done; n_script pending. |
| regression-managed | Decision/Profiles выключены: шесть done, пять model POST. |
| regression-off | Managed mode выключен: шесть done, пять model POST. |

Успешное действие: 1f581c6c-a533-473b-84c2-d288e6a36dfc; new attempt 33a026ca-b343-4cb8-8ac4-43bc7468ef6e.
Неудачное действие: c40f8a06-143f-4988-aca2-5c6c6ca402a6; new attempt 1b5e8f9a-1e3b-4cdb-9fe7-4a4df4e0ee0f.
Execution 8b57489f-15ed-47e0-aa8d-a766e60e096f; incident be26c775-ead5-5be9-b47d-042da4a5134d; source attempt 1847492d-82c6-4609-a0c3-d10e2e5c2cc3.

Каждый recovery case имеет новый run_id и отдельную копию genuine exhausted checkpoint Actions-A, в котором исходный runtime уже исчерпал 9 попыток/18 POST со штатным backoff. Сохраняются execution/incident IDs как lineage копии. Исходные 401 файла checkpoint не изменялись; counters не подделывались и длинный native тест повторно не запускался.

Вмешательства harness: копирование синтетического checkpoint, явное операторское одобрение профиля, включение Decision, подмена только внешнего LLM-транспорта fixtures, отмена конфигурации после terminal attempt, штатное завершение теста. Следующие ноды/успешные статусы вручную не выставлялись. Worker/planner/БД/apply/HTML настоящие. Synthetic brain replies заранее заданы и не являются проверкой реальных рассуждений.

## Где лежат фактические данные

Для worker-02 и worker-bad в artifacts:
- group-result.json, decision-state.json: actual tools/model turns/policy/action/verification/бюджет/конечное состояние;
- actual-worker-requests.json: фактически отправленные синтетические bodies целевой и остальных нод;
- brain-fixture-requests.json: модельный диалог, включая реальные результаты инструментов;
- profile-approval-response.json, profile-state.json: одобрение, base/candidate/expected-body hashes, attempt и история отмены;
- free-run-data/state.db: observation_events, execution/decision/actions/recovery/profile/HTTP budget;
- free-run-data/videos/diagnostic-synthetic-scene-2026-09-24/project.db: фактические node_runs, сцены, кадры, committed transitions;
- free-run-data/observations: локальные redacted full bodies и journal references/hashes;
- loaded-modules.json, run-config.json, line-coverage.json: реальная версия загруженных модулей и исполнение;
- free-run-data/studio-live.log: сохранённый runtime журнал.

acceptance-matrix.json сопоставляет каждый сценарий с current source; container-isolation.json фиксирует image IDs и конечное состояние 24 исторических/final контейнеров. У всех network none, mounts 0, OOM false, running false. RED и harness failures сохраняются, не выдаются за успешную приёмку.

Финальные UI viewers: backend 158/156 фактически загруженных модулей совпали с текущими файлами. В каждой browser-сессии проверены SHA-256 12 реально загруженных JS/CSS against fresh 43-file export. Сохранённый проект #2 восстановился без ручного выбора; версия/verify/human hold пережили reload, другая выбранная нода не получила overlay. Screenshots: ui-verified-final/profile-verified.png; ui-human-final/profile-human-review.png; decision-human-review.png.

UI viewer не запускает worker, модель, observer loop. Исторические incidents сохраняются; отстающая diagnosis видна явно. В human viewer исходный GET /projects/{id}/run синхронизирует только project WorkflowRun.edges_snapshot и updated_at с project.meta.canvas_graph (25→24 edges, убирает n_plan→n_script). Подтверждён путь projects.get_project_run → canvas_graph.sync_run_snapshot_from_canvas_graph. Все остальные WorkflowRun поля, node attempts/statuses, сцены/кадры и чужие проекты неизменны: ui-database-validation.json. Это существующая API projection, не действие supervisor.

## RED → минимальные исправления

1. Новый overlay ожидал строковый Responses input, а настоящая нода использует list(role/content). worker-01 остановился до brain/model POST; message-input-red воспроизвёл контракт. Добавлена строгая поддержка текстового list без мутации исходного body. worker-02 и 17 final unit GREEN.
2. UI mount ссылался на selected вне области видимости: frontend typecheck RED. Использован текущий context/group scope; frontend-fixed GREEN.
3. Browser обнаружил React #418, воспроизведённый также на предыдущей принятой v643 (ui-baseline/browser-console-errors.json). Старый usePersistedState читал localStorage до первого client render и расходился с SSR default. hydration-red сохраняет failing regression test. Минимальное исправление читает persisted state после mount. frontend-final: два first-render contract assertions GREEN, cold/reload UI errors 0. Backend/worker не менялись из-за этой frontend правки; их результаты переиспользованы лишь после сверки actual source hashes.
4. Первоначальный внешний скрипт сверки fresh export ожидал ещё не созданный build-manifest.json. FileNotFoundError сохранён как ошибка consolidation harness; manifest затем сформирован по фактическим 43 файлам и проверен against репозиторий и browser. Ни тесты, ни сборка не повторялись из-за этого.

Сохранены промежуточные frontend v644/старые UI с React #418 и acceptance-matrix-before-hydration-fix.json. Итоговая матрица использует только frontend-final и UI-final.

## Внешние диагностические инструменты и ограничения

Вне клона: outputs/profiles-v1/prepare_viewer.py, viewer_control.py, consolidate.py; их hashes и reused local_browser_bridge.mjs записаны в external-tools-manifest.json. Viewer использует оригинальный history_replay/socket_bridge в клоне. Нет скрытых изменений бизнес-логики через обёртку; контейнеру переданы только staged source, synthetic copies и results, без Docker socket/home/Git/.env.

Ограничения: один фиксированный совместимый synthetic profile; одна immutable версия на node/execution; production catalog пуст. Произвольная смена provider/модели/промпта/кода не разрешена. Отмена не является rollback данных/стоимости. HTML/group done не означает done всего WorkflowRun. Реальное качество LLM reasoning, production pricing/limits и онлайн-режим не проверялись. Badge unknown обусловлен отсутствием .git в isolated build; версию доказывают фактически загруженные hashes.

За эту разработку/приёмку: **реальных API вызовов 0, публикаций 0, изменений удалённого репозитория 0**. Это не утверждение об исторических online-прогонах до текущего задания. Все описанные модельные проверки синтетические.
