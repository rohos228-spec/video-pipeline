# Локальный кандидат v1 — 30.09.2026

Область: только `script_frames_qc`. Новых реальных запросов в этой работе — **0**.
Инструкция: [first-version-local.md](first-version-local.md).
Единая матрица остаётся в `outputs/decision-runtime-v1/acceptance-matrix.json`;
её прежнее состояние и все неудачные пилоты сохранены отдельно.

## Что изменено

- UI восстанавливает группу по точному scope сохранённого execution; ошибки
  чтения controls не маскируются состоянием «выключено» и блокируют новые действия.
- Три остановки описаны раздельно. Передача человеку без recovery больше не
  выдаётся за провал verification. Свежий export UI — v648.
- Необязательный `incident_scope` связывает execution/incident/node/source attempt
  и одну recovery. Binding и общий HTTP budget сохраняют config revision.
  Точечный allowlist проверяется также прямым recovery API после выключения Decision.
  Пустой каталог scoped-пилота не заменяется синтетическим каталогом.
- Существующие fixture-функции перенесены без изменения ответов в
  `tests/observe_v1/group_runner.py`. Group runner больше не импортирует `diag_v2`.
  Архивы остаются отдельными явными входными данными, не кодом приложения.

## Последовательность и сохранённые препятствия

1. `612428e9`: зафиксирована принятая read-only область. В ней два принятых ответа,
   `read_contract` и затем `human_review`; контекст уже содержал классификацию.
   Это не доказательство независимой диагностики или разрешение recovery.
2. `af78913d` → `ad8cc289`: RED/фикс группы и неизвестного состояния controls.
3. `0199d6c5` → `cc8633e8`: RED/фикс incident binding и revision перед отправкой.
4. Старый exhausted seed `recovery-a-08` отвергнут настоящим recovery gate:
   `input/config revision changed ... settings,node_code`. Первая проба также
   меняла transport retries 4→0; это убрано, исходные настройки сохранены.
   Два изменённых файла node fingerprint: `enrich_xlsx.py`, `node_write_contract.py`.
   События и хеши seed не исправлялись ради GREEN. Неудачная проба сохранена в
   `outputs/first-version-local-20260930/managed-pilot-ui` (2 fixture Decision, 0 worker).
5. `9bb479de` → `43710676`: UI RED/фикс ошибочного текста verification.
6. `b583e4fe`: версионированные фикстуры, явный seed, шаблон и инструкция.
   Старый runner `recovery-a` бесплатно воспроизвёл 9 native failures / 18 POST
   на текущем коде, сохранил новый exhausted checkpoint до supervisor, затем
   выполнил одну recovery: все 6 done, verify подтверждён, n_script pending.
   Это новое синтетическое происхождение, не переписанная история старого запуска.
7. `9952e212`: RED для прямого обхода allowlist и пустого profile catalog.
   `1ca2a03a`: исправление; 125 regression tests GREEN (41 runtime policy,
   33 Decision, 24 Actions, 17 Profiles, 2 planner, 8 journal).
8. `managed-pilot-final`: промежуточный неуспех проверочного клиента
   `RemoteProtocolError` при API polling после 3 Decision + 4 worker fixture POST.
   Verification recovery уже подтверждён, вся группа ещё не завершена.
   Новая проба использует свежие соединения только для control-plane клиента
   runner: без повторов POST, подавления исключений и изменения дедлайнов.
  Idle keepalive под нагрузкой — возможная причина, не доказанный дефект worker.
   `d0bee49b`: изменение проверочного клиента; следующая полная проба GREEN.

## Финальные проверки

| Проверка | Результат | Материал под outputs/first-version-local-20260930 |
| --- | --- | --- |
| Python policy/runtime/Decision/Actions/Profiles/planner/journal | 125 tests, 0 failures/errors | final-regression/artifacts/regression-summary.json |
| Неизменённые runtime и diagnosis | Повтор не нужен: по 25 tests OK, хеши основных модулей и тестов совпали | unchanged-runtime-diagnosis.json → qc-noop-v1/accepted-runtime, accepted-diagnose |
| Frontend contracts, TypeScript, новый build | exit 0, v648 | review-label-green/artifacts |
| Genuine exhaustion → Actions recovery | 18 native + 5 новых fixture POST, все 6 done, verify; n_script pending | current-recovery-seed/artifacts/group-result.json |
| Подготовленный scoped Decision → Actions → worker → verify | 3 Decision + 5 worker; 18→26/30; одна recovery; incident resolved_confirmed; все 6 done; n_script pending | managed-pilot-current/artifacts/group-result.json |
| Scoped Decision выбирает человека | 1 Decision, 0 worker, 18→19/30; hold, incident требует человека | managed-review-current/artifacts/group-result.json |
| Точный profile конкретного attempt | 3 Decision + 5 worker; gpt-synthetic-alt/medium/2048 только target; остальные исходные; отмена config без отката данных/расхода | profiles-current/artifacts/group-result.json, actual-worker-requests.json |
| Обычный живой UI + reload | Группа, execution, 26/30, verification, Decision и пустой каталог scoped-пилота; отдельный human_review без ложного verify failure | managed-pilot-current/artifacts/ui-complete*.txt/png; managed-review-current/artifacts/ui-human-review.txt/png |

Результаты инструментов передаёт настоящий Decision runtime. Успешная
последовательность решений в бесплатных случаях задана внешней fixture; это
проверка исполнения/ограничений, не самостоятельного рассуждения модели.
Unit-набор включает user_stop/late reply, uncertain, audit failure, graph/config
revision, outside scope, durable budget/restart/idempotency и запрет старого
orchestrator chat/Fix bugs. Для всех изменённых app-модулей имеются свежие
regression и загруженные worker manifests; неизменённые legacy suites не повторялись.

В совместимом seed execution `ef585304-caa5-4f3f-90b9-845cb93876b9`, исходный
attempt `fffff86c-0905-442b-bb17-d20b1488916e`. Копии разделены каталогами и
run_id; их 18 исходных reservations не обнулялись. Шаблон держит общий limit 30
(12 новых), 3 Decision steps, одну recovery одного incident. Runtime config
hash и graph revision находятся в prepared-managed-pilot.json и binding БД.
HTML в успешном scoped прогоне создан исходным writer, 8113 байт.

Все текущие app-прогоны использовали network none и synthetic credentials,
без .env, Docker socket и домашней папки. После фиксации UI собственные bridge
и runner остановлены, контейнеры и архивы сохранены. Успешные runner имеют exit 0,
shutdown_gap отсутствует; это не утверждение о завершении всех фоновых потоков
app.main — существующий runner завершает свой процесс после сохранения материалов.

## Доказательства

Корень материалов: `outputs/first-version-local-20260930`.
Исходные события/БД/ответы не включены в Git; здесь только ссылки на локальные файлы.
Итоговые результаты и хеши фиксируются в `local-evidence-summary.json` и
`checkpoint.json` рядом с материалами. Manifests фиксируют фактические файлы,
а не только HEAD: badge v648 содержит `unknown` из-за ownership Git в build-среде.
Сверены 260 реально импортированных backend-файлов и 211 файлов UI/source/export.
Дополнительно DOM браузера зафиксировал 10 script и 3 stylesheet ссылки;
их фактические пути и SHA-256 сохранены в `browser-loaded-asset-hashes.json`.
Списки: `verified-runtime-files.json`, `verified-ui-files.json`.

Предыдущий настоящий пилот хранится отдельно. Его три результата различаются:
**gateway process graceful; ledger settled; container exit 137 (OOMKilled=false)**.
Ни шлюз, ни его проверенный сетевой фильтр в этой работе не менялись.

## До пилота заказчика

- Отдельно разрешить реальный изменяющий managed-сценарий и его расходы.
- Использовать совместимый synthetic checkpoint; сохранившийся старый seed не
  обходить через правку журналов, counters, fingerprint или бизнес-статусов.
- Активировать подготовленный режим в существующем online launcher/gateway:
  текущий entrypoint преднамеренно остаётся read-only и не запускает worker.
  Нужны сверка общего ledger/request limit, фактических worker token settings,
  route и fresh keyless preflight. Read-only разрешение для этого непригодно.
  Конкретно: read-only entry задаёт другие GPT_BASE_URL, synthetic credential
  и GPT_MAX_RETRIES=0; worker fingerprint включает settings. Его нельзя просто
  переключить на запись: app settings исходной попытки должны сохраняться,
  а настоящий секрет оставаться только в шлюзе. Ослабление fingerprint не сделано.
- Цена и денежный потолок здесь не устанавливались. 12 новых запросов — общий
  allowance, не гарантия полного выполнения всех веток и не денежный cap.
- Native Windows установка заказчика, реальные профили/recovery и качество
  самостоятельной диагностики на разных ошибках пока не подтверждены.

Никаких media, произвольных prompt/code edits, новых провайдеров или нового движка.
