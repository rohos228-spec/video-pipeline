# QC no-op v1: acceptance and contract correction

Дата: 2026-09-29. Ветка: feature/orchestrator-observe-v1.
Начальная база: c8edfd0184db9668828401ae4dae729bc3068107.
Runtime checkpoint: 090d3ba5 (полный SHA — в final-state-v2.json). Предыдущий checkpoint 270608c27023a72c7a246508f4a0eb4ba304b2bc сохранён.
Корень стенда: `<корень стенда>`. Все пути `outputs/...` ниже относительно этого корня.
Все новые проверки локальные, synthetic, Docker --network none.
Новых реальных API-вызовов: 0. Frontend и production-конфигурация не изменены.

## 1. Историческое противоречие

Источники: outputs/diagnostics/20260924T-online-real-04 и сохранённый read-only pilot.
Развёрнутые пути и SHA256 тел/исходников: outputs/qc-noop-v1/historical-contract.json.
Архивный QC-запрос прямо разрешал {"ops":[]} при отсутствии исправлений.
Записанный вход оператора: n_excel_gpt_fw_qc, role=assist, output_mode=project_file.
check_mode не передан; в сохранённой сигнатуре default=False, assist не проверочная роль.
Поэтому использовался JSON project_file путь, а не check/text verdict путь.

Короткая хронология оригинального online-real-20260924-04:

| seq | Факт |
| --- | --- |
| 4016 / 4018 | Очередь и старт настоящего worker |
| 4044 / 4051 | Вход оператора и фактически отправленный первый QC POST |
| 4127 | Полный первый ответ: пустой ops-массив |
| 4131 | JSON синтаксически разобран, извлечён ops=[] |
| 4132 | Оператор считает отсутствие операций отсутствием обязательного результата |
| 4133 / 4138 / 4140 | Штатный JSON retry, резервирование и второй POST |

allow_empty_ops устанавливался enrich_xlsx и принимался run_apply_ops_batched,
но отсутствовал в api_kw вызова run_operator_api и в сигнатуре оператора.
_apply_ops_has_payload давал False; apply_ops превращался в None.
Условие project_file && !is_check && apply_ops is None запускало JSON retry
с требованием непустых изменений, противоречащим QC-промпту.

Сохранённые staged-исходники четырёх затронутых модулей совпадают с c63d0a53
после нормализации CRLF/LF и со staged manifest. Это подтверждение версии
сохранённого source, а не выдуманная аттестация загруженных модулей:
архив 04 не содержит прежних per-module loaded SHA256. Ограничение сохранено.
В текущей базе тот же путь подтверждён HTTP-регрессионным RED, не предположением.

## 2. Узкий фикс в существующем runtime

- node_write_contract.allows_qc_noop: фиксированный разрешённый контракт
  shots_qc_ru, main slot, тип excel_gpt и группа script_frames_qc (включая instance suffix).
  Название ноды и содержимое промпта не дают разрешение. Остальные варианты fail-closed.
- enrich_xlsx передаёт эту политику batching; batching передаёт её оператору.
- Оператор принимает строго завершённый, не salvaged, JSON {"ops":[]}.
  Пустая строка/объект, неправильный ops, error/refusal и uncertain не становятся no-op.
  Для QC с исправлениями сохраняются исходные фильтры и настоящий db_apply.
- _apply_ops_has_payload глобально не изменён. Default creating nodes требуют данные
  и сохраняют штатный JSON retry. У QC JSON retry повторяет контракт, разрешающий no-op.
- Managed QC дополнительно проверяет audit gaps до и после записи qc_noop check.
  Реальное исчерпание body store после первого HTTP запрещает no-op и второй POST.
- QC batching больше не превращает except Exception в успешное завершение;
  исключение передаётся дальше без adaptive split в этой ветке.
- Логирующая сводка _log_apply_ops_coverage ранее вызывала декорированный
  coverage_report и создавала ложное failed даже без обязательного N/N gate.
  Для подтверждённого no-op она не вызывается; recorded performed=false,
  not_applicable объясняет неприменимость записи каждого кадра. Обязательные gates не отключены.
- Decision read_contract отдаёт явно разрешение пустого ops и требование непустых изменений.
  Decision/Actions executor, permission, request budgets и FSM не переписаны.

## 3. Наблюдение, diagnosis, verify

parse_result отдельно содержит syntax_valid, parsed, schema_valid,
ops_field_present, ops_type_valid, ops_count, payload_present,
empty_ops_allowed, empty_ops_accepted, nonempty_payload_required,
semantic_quality_verified=false. Старый payload_required относится к JSON-контракту;
это не утверждение обязательности непустых изменений.

runtime_check qc_noop фиксирует реально выполненные структурные проверки,
завершённость ответа и доверенную политику. Семантическое качество неизвестно.
Нет фиктивного data_applied, записи кадров ради отчёта или ручного done.
Принятый no-op не создаёт missing_payload; недоставленные checks не считаются успехом.
Непринятый структурно результат с корректным JSON не называется синтаксической ошибкой.
Ошибка обязательного runtime/domain gate остаётся отдельным contract_rejected.

Recovery verify допускает отдельно accepted_noop и result_confirmed.
Нужны same request model_start/end complete, корректный parse, выполненный qc_noop check,
текущий доверенный контракт, последняя попытка именно action.new_attempt_id,
NodeRun done, node_end и transactional project node_change до done.
Исходные graph/audit/user_stop/permission/request-completion guards сохранены.
data_applied и accepted_payload остаются false для no-op; quality_verified=false.
Недостающие checks, другой текущий attempt или другой контракт блокируют verify.
No-op не закрывает отдельный DB disagreement, journal gap или ошибку другой попытки.

## 4. Офлайн-переоценка прежнего пилота

Отдельная версия: outputs/qc-noop-v1/pilot-semantic-review-v2.json.
Исходная rubric, semantic-review.json и model evaluation не переписаны;
хеши старого files-manifest проверены: pilot-original-preservation.json.

Рекомендация wait обоснована уже зарезервированным/отправленным native retry:
на cutoff его ответ, исчерпание и конечный статус неизвестны.
Модель заметила конфликт разрешённого empty ops и поведения приложения,
отметила гипотезу как inference и не установила конкретную причину в коде.
required_payload=present допустимо трактовать как наличие ops-поля;
это не доказательство непустых изменений или принятия старым оператором.
Фраза об отсутствии очевидного исправления в кадрах не является независимой
проверкой качества и не засчитывается как доказанный качественный QC.
Это квалифицированная переоценка одного сохранённого ответа, не новый успешный
online pilot и не подтверждение надёжности модели на остальных задачах.

## 5. Сохранённые неуспешные проверки и вмешательства

- red-01: неверный synthetic input без top-level voiceover_text; не валидный RED.
  Исправлен только тестовый seed. Исходные результаты сохранены.
- red-02: валидный HTTP RED: 4 failures, 2 passing controls; checkpoint e612d7b4.
- green-02: ошибки тестового fixture (GET был посчитан POST; штатный timeout
  оборачивается GptApiError). Исправлен только HTTP GET fixture, защитные ожидания
  приведены к фактическому runtime. Production retry для этого не менялся.
- group-01: шесть done и HTML, но acceptance RED из-за ложного coverage event.
  Это подтверждённый дефект сводки, устранён отдельно; события не удалялись.
- operator-01/02: в прежнем образе отсутствует pytest, затем не скопирован py.py shim.
  Переиспользованы только pure-Python тестовые пакеты уже существующего work/venv2,
  с SHA256 manifest, без pip install, загрузок и монтирования venv/home/учётных данных.
- Старый test_qc_allow_empty_does_not_split_on_gpt_fail усилен: требуется исходное
  исключение и всё ещё ровно одна попытка пачки. Удалено ожидание ложного успеха.
- postaudit-01: fixture использовал прежний короткий execution_id, который настоящий
  body store отклоняет до POST. Заменён только test execution ID на UUID.
  postaudit-02 подтвердил настоящий body_total_limit после ответа: один POST,
  нет apply и нет успешного qc_noop check. Диагностический лимит изменён
  только в изолированном тесте; production лимиты остаются прежними.
- accepted-decision-final: 1 ошибка нового test fixture (не был импортирован Project),
  остальные 79 тестов GREEN. Исправлен только импорт, весь Decision набор
  повторён отдельно: accepted-decision-retest, 33/33 GREEN. Старый aggregate
  с exit=1 сохранён; он не выдаётся за успешный целиком.
- Новый group mode qc-noop меняет только ответ внешнего HTTP fixture этой ноды.
  Исходный API запускает первую ноду; worker, planner, БД и HTML остаются настоящими.
  Managed scope включён штатным API. Следующие ноды вручную не запускались.

### Дополнительная защита неполного SSE

red-uncertain: ошибка селектора тестового harness, исправлена только диспетчеризация;
контейнер и snapshot сохранены. red-uncertain-2: настоящий HTTP/operator RED —
поток без response.completed отклонён, но при raw.status=None persisted held=false.
Это конкретный пробел защиты managed QC от следующего POST.
Исправление: initial и JSON-retry QC ветки используют общий узкий
_require_completed_qc_response; он вызывает уже существующий hold_uncertain_request
перед отклонением неопределённого ответа. Worker, generic payload и native retries
не переписаны. green-uncertain: persisted hold=true, последующее charge_request
запрещено, один фактический synthetic POST, ноль apply. Финальный набор 41/41
включает этот сценарий. Архивные failures и журналы не удалены.

## 6. Ограничения

QC no-op означает принятый структурный ответ без правок, а не доказанное качество кадров.
Разрешена только каталоговая shots_qc_ru нода в script_frames_qc; расширение на другие
QC-контракты требует отдельной явной политики и теста, слово QC не даёт право.
Recovery verify no-op дополнительно проверен изолированными persisted SQLite-тестами;
длинный новый exhaustion+supervisor сценарий не повторялся. Сама группа проверена worker.
Отдельного content-quality validator, новой модели или автономного редактирования кода нет.
UI/build и длинные exhaustion/browser наборы не повторены: frontend не менялся.
Новый online pilot не запускался. .env и реальные ключи не читались.

## 7. Итоговая приёмка

Все перечисленные итоговые наборы GREEN; архивные промежуточные failures сохранены.

| Набор | Результат | Фактический журнал |
| --- | --- | --- |
| HTTP batch→operator→gpt + policy/diagnosis/verify/audit | 41/41 | outputs/qc-noop-v1/accepted-qc-noop-v2/artifacts/test-output.txt |
| Дополнительные чистые no-op rules, включая same-attempt JSON retry resolution | 6/6 | outputs/qc-noop-v1/accepted-noop-rules/artifacts/test-output.txt |
| Прежние operator/apply_ops_batches pytest | 71/71 | outputs/qc-noop-v1/accepted-operator-regression-v2/artifacts/test-output.txt |
| Runtime | 25/25 | outputs/qc-noop-v1/accepted-runtime/artifacts/test-output.txt |
| Diagnosis | 25/25 | outputs/qc-noop-v1/accepted-diagnose/artifacts/test-output.txt |
| Decision runtime | 28/28 | outputs/qc-noop-v1/accepted-decision-final/artifacts/regression-summary.json |
| Decision после исправления test import | 33/33 | outputs/qc-noop-v1/accepted-decision-retest/artifacts/test-output.txt |
| Actions | 24/24 | outputs/qc-noop-v1/accepted-decision-final/artifacts/regression-summary.json |
| Profiles | 17/17 | тот же regression-summary.json |
| Planner boundary | 2/2 | тот же regression-summary.json |
| Journal | 8/8 | тот же regression-summary.json |
| Штатный API→worker→все шесть нод→HTML | PASS | outputs/qc-noop-v1/accepted-group-v2/artifacts/group-result.json |

Числа не суммируются как количество уникальных тестов: QC target включает
18 импортированных DurableActions случаев, rules target повторяет существующие
чистые правила и добавляет один новый случай. Результаты конкретных запусков сохранены.

### Фактические версии

SHA256 реально скопированных и загруженных файлов сохранены для каждого запуска
в source/source-manifest.json и artifacts/loaded-modules.json; номинальный git SHA
не заменяет эти сведения. Финальные 41 QC и 71 operator тестов и новая полная
worker-группа проверяют QC completion guard из 090d3ba5. Остальные функции
runtime/diagnosis/actions/decision/profiles после соответствующих GREEN наборов
не менялись; их результаты переиспользованы по хешам соответствующих модулей.
Старый hash QC operator в этих ранних наборах явно исключён из утверждения
о совпадении актуальных файлов и покрыт новыми тестами, а не скрыт.
Decision metadata refinement подтверждён полным повтором 33/33.
Сверка по модулям: outputs/qc-noop-v1/acceptance-proof-v2.json;
офлайн verifier: outputs/qc-noop-v1/verify_acceptance_v2.py.
Предыдущие proof/verifier/checkpoints сохранены.
Шесть новых функций имеют 100% исполняемых строк по сохранённой трассировке;
это coverage этих функций, не всего приложения.

### Полная группа

Run ID: `observe-20260928-d6372be5-d9bc-4d83-a44b-d24ffbf8fda9`; execution ID: `6e815166-32ac-4ace-ac59-067c43e9fe17`.
Все 6 нод done. Незавершённых нод scope: 0.
Всего model POST: 5 (локальный fixture), QC POST: 1, QC data_applied events: 0.
Снимок uuid/number/voiceover/всех attrs кадров до QC идентичен снимку после HTML.
Supervisor recoveries отсутствуют; QC missing_payload/invalid_json отсутствуют.
n_script вне scope pending, после HTML проверены четыре штатных цикла.
HTML: 8113 байт, SHA256 `aedd3c80a1a2680e4fc2cbd141bbc576dda79d48b1edbaf79cfb496e3abd847b`.
Создан исходной `_run_fw_report_node`, не fixture и не обёрткой.
Проверено содержимое HTML: 1 сцена, 3 строки логических кадров,
все ожидаемые shot IDs присутствуют. В БД 3 frame-строки и
5 вхождений shot в attrs: родитель хранит полный список,
дочерние строки — его зеркала. Исходный shots_report._collect_shots пропускает
role=shot детей и дедуплицирует id. Разница 5/3 не объявлена потерей данных;
это подтверждённое кодом представление. Изначально offline HTML verifier
ошибочно сравнил число строк с сырым количеством вхождений; oracle уточнён
по уникальным IDs и исходному правилу, приложение не изменялось.
Наивный snapshot shot_voiceover_matches для parent не использован как
семантический gate: он сравнивает агрегат shots с VO отдельной DB-ячейки.

Артефакт: outputs/qc-noop-v1/accepted-group-v2/artifacts/free-run-data/videos/
diagnostic-synthetic-scene-2026-09-24/reports/shots-report.html.
Качество реальной модели и семантическое качество кадров этим не подтверждаются.

### Охват реально записанных событий

Все следующие виды доступны в accepted-group-v2/artifacts/api-history.json
и в сохранённых synthetic state/project DB; полные тела — в
accepted-group-v2/artifacts/free-run-data/observations/bodies (ссылки и SHA256 в событиях).

| Факт | Событие/состояние | Что подтверждено |
| --- | --- | --- |
| Настоящий запуск и переходы шести нод | node_start, node_end, node_change | Worker и транзакционные статусы |
| Запрос и завершённый ответ QC | model_start, model_end | Один связанный request_id, complete=true |
| Синтаксис, структура и принятие no-op | parse_result | Ops присутствует, массив пуст, контракт разрешает |
| Реально выполненные проверки no-op | runtime_check qc_noop | strict JSON, completion, trusted policy |
| N/N запись не требуется | runtime_check frame_coverage_gate | performed=false, not_applicable, явная причина |
| Операции QC не записывались | отсутствие data_applied плюс сравнение всех кадров | Не фиктивное применение, не только отсутствие журнала |
| Продолжение и HTML | следующее node_start, artifact_created, непустой файл | Штатная цепочка и исходный HTML writer |
| Диагностика | diagnosis.json | Нет ложного QC format incident, recovery не запускался |
| Scope и лимит | group-result.json, actions state | 5 requests_used, n_script pending |
| Неизвестное качество | parse/check/proof flags | semantic_quality_verified=false |

Отдельные persisted verify fixtures покрывают valid no-op, недостающие checks,
неправильный контракт и более новый attempt. HTTP fixture тесты покрывают
malformed, неправильный ops, refusal/error, provider failure, incomplete,
защитные stop/audit/budget исключения, исправления и обязательный creating payload.
Post-response audit тест использует настоящий body store с исчерпанием лимита;
никакого успешного qc_noop check или второго POST после этого нет.

### Изменения вне клона

Папка outputs/qc-noop-v1 содержит все RED/GREEN промежуточные запуски,
source snapshots/manifests, исходные журналы тестов, новые исторические
ссылки/хеши, versioned pilot review, preservation proof и verify_acceptance.py.
Последний — офлайн-анализатор сохранённых файлов, без импорта/запуска приложения.
Новых транспортных обёрток, стендов, зависимостей или production credentials нет.
verify_acceptance.py потребовал явный UTF-8 при чтении manifests на Windows;
исправлен только офлайн-декодер, не runtime.
Полный список file SHA256 сохранён отдельно. Существующий main clone и архивы не изменялись.

Итог: противоречие воспроизведено и устранено в конфигурационной передаче и
операторной политике; сохранены обязательные creating/gate/stop/audit/budget ограничения.
Новая версия отчёта пилота отделяет наличие ops, принятие формата и неизвестное качество.
Работа заканчивается перед новым online pilot. Реальные API-вызовы не выполнялись.
