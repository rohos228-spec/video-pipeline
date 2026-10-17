# D04: дефекты нативного конвейера. Статус

Класс доказательства: U/D (заменитель провайдера на уровне httpx, журнал задач во временной SQLite). Живого запроса в Kie не было, поэтому «L» остаётся открытым (см. внизу).

| # | Дефект | Статус | Чем доказано |
|---|---|---|---|
| 1 | Рестарт во время `generating_*` заново ставит кадры с живой задачей | Закрыт раньше (волна W5-2, коммит cc914542) | `tests/w5_d04_native_pipeline_test.py`: `test_D04_1_restart_during_generation_keeps_one_paid_task_for_every_frame`, `test_D04_1_startup_reconcile_then_step_pays_once`, `test_D04_1_startup_names_outsee_frames_that_must_not_be_resent`. Платная задача одна, кадры идут по своим task id. |
| 2 | `RETRY_BURN` создаёт новую задачу при живой | Закрыт раньше | `test_D04_2_burn_on_a_generic_error_does_not_create_a_second_task`, `..._after_the_burn_a_good_answer_is_taken_from_the_same_task`, `..._poll_timeout_burn_is_held_not_resubmitted`. |
| 3 | task id записывается только после опроса | Закрыт раньше | `test_D04_3_task_id_is_durable_before_the_first_status_request` (строка `bound/submitted` есть до первого recordInfo). Окно между ответом createTask и записью закрыто запретом второго createTask: `test_D04_3_process_dies_between_the_create_answer_and_the_task_id_row`. |
| 4 | Скачивание не сразу | Закрыт раньше | `test_D04_4_download_follows_the_success_in_the_same_call`; неудача скачивания держится, следующий вызов качает ту же задачу: `test_D04_4_failed_download_is_held_and_the_next_call_downloads_without_a_new_task`. |
| 5 | Авторизация и референсы в `json_request` | Авторизация: БЫЛА ОТКРЫТА, закрыта сейчас. Референсы: закрыты как «отказ по умолчанию», загрузка не реализована (решение, не дефект кода) | см. ниже |

## Пункт 5 подробно

Что это значит. `app/services/media_transport.json_request` это управляемый транспорт (режим managed: `create_task` и `poll_task` в `kie_http.py` ходят через него, когда есть `current_job`). Обычный путь (`kie_post_json`, `client.get(..., headers=_headers())`) заголовок Authorization нёс, управляемый нет.

Открытый дефект (был). `json_request(method, path, *, payload, params)` не принимал заголовки, и вызовы `kie_http.create_task` (около строки 191) и `kie_http.poll_task` (около строки 358) уходили без `Authorization`. Сценарий отказа: управляемая задача против настоящего Kie отправляет createTask без ключа, получает 401, а шаг расценивает это как отказ провайдера. Сегодня боевой профиль выключен, а фикстура на loopback ключ не проверяет, поэтому тесты этого не видели.

Правка.
- `app/services/media_transport.py`: `json_request(..., headers=None)`, заголовки передаются только в `client.stream`. В `record_fact` уходят только `payload`/`params`, заголовки в журнал не попадают; тела по-прежнему проходят `redact` в `save_body`.
- `app/bots/kie_http.py`: оба вызова `json_request` теперь с `headers=_headers()` (та же функция, что в обычном пути, включая relay-токен).

Протечки ключа проверены: ключ не попадает в сообщения `MediaPolicyError`, в `record_fact` и в сохранённые тела (тест проверяет, что в фактах нет значения ключа).

Референсы. `generate_kie_image` при managed и `reference_images` бросает `MediaPolicyError("managed reference upload has no authorized adapter")`. Загрузка файла идёт на другой хост (`kie_upload_base_url`, `upload_file`), которого нет в списке маршрутов `ROUTES` и в allowlist `check_url` (только loopback http). Реализовать загрузку значит добавить боевой маршрут вне loopback, то есть открыть боевой профиль media (D01 и F4, решение ВЛАДЕЛЬЦА и живой шаг). Отказ по умолчанию оставлен намеренно: молча генерировать кадр без референса хуже. Тест фиксирует границу: `test_D04_5_managed_references_stay_refused_fail_closed`.

## Тесты, которые падали до правки и проходят после

`tests/w5_d04_5_managed_auth_test.py` (3 теста). Проверено временным откатом двух правок: все 3 падают (`headers` не доходит до провода, `poll_task` вызывает `json_request` с `headers=None`), после возврата правок все проходят. Старый тест `test_D04_5_...` в `w5_d04_native_pipeline_test.py` утверждал отсутствие `headers` в сигнатуре; обновлён под новое поведение.

## Не закрыто

- L для D04 (реальный запрос с рестартом посреди опроса, одна платная задача): нужен платный шаг, не выполнялся.
- Загрузка референсов в управляемом режиме: зависит от решения по боевому профилю media (D01/F4).
