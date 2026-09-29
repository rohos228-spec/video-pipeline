# Decision runtime acceptance

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
