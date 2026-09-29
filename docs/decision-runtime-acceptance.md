# Decision runtime acceptance

## Текущее состояние первой версии — 30.09.2026

Область: текстовая группа `script_frames_qc`, не весь контент-завод.
Единая итоговая матрица: `outputs/decision-runtime-v1/acceptance-matrix.json`
(поле `current_acceptance`; прежние строки и неуспешные архивы сохранены).

Observe/diagnose/Actions/Profiles и полный Decision → worker → verify подтверждены
синтетическими тестами, включая успешную/неуспешную recovery, профиль конкретной
attempt, reload, scope и общий бюджет. См. исходные отчёты Actions/Decision/Profiles.

Принят реальный **read-only** цикл на `a3d91a7b`: run `291e3aa68d86`, два POST,
два принятых ответа, четыре локальных ACK; `read_contract` → фактический результат
в следующем запросе → `human_review`, выбранный моделью. Бизнес-таблицы неизменны.
Контекст уже содержал classified synthetic `invalid_json`; независимое определение
причины и ранний реальный QC с синтаксически корректным JSON этим не доказаны.
Отдельные исходы: gateway process graceful; ledger settled; container exit **137**.
Доказательства: `outputs/decision-launcher-check-20260930/normal-01/artifacts/online-readonly-01/analysis-report.md`.

Настоящие изменяющие действия через модель пока **не проверены и не разрешены**.
Следующий участок: обычное локальное использование, незакрытые ограничения
будущего managed-пилота и инструкция. Новых платных запросов не разрешено.

## Историческая синтетическая приёмка

Локальная бесплатная приёмка завершена в области mandatory JSON/payload
`script_frames_qc`: 112 Python tests, 71 frontend assertions, TypeScript,
свежая сборка v646, положительный и отрицательный **живые** UI/API/worker/
Decision/Actions/verify сценарии. API провайдера заменено только на HTTP-границе,
все тестовые контейнеры `--network none`, реальных вызовов 0.

Положительный сценарий: 9 native attempts → read_events → retry_same →
одна recovery → verification → 6 done + HTML. Всего 26 бесплатных fixture POST.
Отрицательный: копия подлинного synthetic exhaustion checkpoint → одна
неудачная recovery → hold/human review, без повторных запусков.

Оригинальный planner в отдельном сравнимом тесте продолжает plan → script
при managed OFF; при ограниченном scope следующая нода не dispatch-ится.
Найденный пропуск soft FSM refusal устранён только для managed пути.

Полный отчёт, исходные материалы, RED, screenshots, manifests, итоговый SHA и
чистота дерева: [outputs/decision-runtime-v1](../../../outputs/decision-runtime-v1/acceptance-report.md).
Конфигурация и условия первого **не запущенного** online-пилота:
[decision-runtime-configuration.md](decision-runtime-configuration.md).

Реальная модель/цена/маршрут/ключ и денежная верхняя граница не проверялись.
Default production allowlist пуст. Синтетический успех не является проверкой
семантического качества или разрешением первого платного POST.
