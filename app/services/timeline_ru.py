"""B04 / W3-1: единая таблица «вид записи журнала -> русская фраза» для ленты проекта.

Один модуль, один язык для всех читателей журнала: лента (`timeline.py`), экспорт, Telegram и панель
наблюдения. Тексты панели в `web/src/lib/observation-view.ts` (`EVENT_KIND_LABELS`) держатся равными
`EVENT_LABELS_RU` отсюда: это проверяет тест `tests/w3_1_timeline_ru_test.py`.

Главная функция: ``describe(row) -> (title_ru, detail_ru, severity)``.

``row`` - любой словарь (строка таблицы-источника, превращённая в dict). Нужны ключи:

* ``source_table`` (или ``source``) - таблица-источник. Для строк ``spend_ledger`` у таблицы есть своя колонка
  ``source`` (кто списал): тогда таблица передаётся в ``source_table``, а колонка остаётся в ``source``
  (или в ``spend_source``). Допустимые таблицы (``observation_events``, ``observation_incidents``, ``decision_records``,
  ``action_v1_records``, ``action_layer_history``, ``spend_ledger``, ``provider_task_ledger``,
  ``operator_audit``, ``publications``, ``notification_log``, ``node_runs``, ``jobs``). Есть и короткие имена
  (``observe``, ``incident``, ``decision``, ``action``, ``plan``, ``spend``, ``task``, ``operator``, ``publication``,
  ``notify``, ``job``);
* ``kind`` (или ``event`` / ``type`` / ``action_kind`` - что есть в таблице);
* остальные поля таблицы как есть (``payload``, ``status``, ``usd``, ``note``, ``error`` ...).

Правила:

* ничего не бросает наружу: любая строка получает текст; незнакомый вид получает
  «Событие <вид> (подробности в журнале)» и попадает в отчёт покрытия (``unknown_report()``), а тест покрытия
  падает, когда в коде появляется вид без фразы;
* уровень только ``info`` / ``warn`` / ``error`` (контракт ленты);
* секретов не добавляет: в detail идут только короткие поля, а ошибки и заметки режутся и проходят ``redact_text``.
"""
from __future__ import annotations

import json
import threading
from typing import Any, Mapping

from app.services.action_layer.messages_ru import HISTORY_EVENT_RU

SEV_INFO, SEV_WARN, SEV_ERROR = "info", "warn", "error"
_UNKNOWN: dict[tuple[str, str], int] = {}
_UNKNOWN_LOCK = threading.Lock()
_DETAIL_LIMIT = 400

# --- observation_events (все 49+ видов из record_fact / emit / stage / _queue_event) --------------------
# Текст = короткое русское название вида без подробностей. Подробности добавляет event_title_ru().
EVENT_LABELS_RU: dict[str, str] = {
    "node_start": "Исполнитель начал ноду",
    "node_end": "Исполнитель вернулся (запись данных не подтверждена)",
    "node_input": "Вход ноды",
    "node_output": "Выход ноды",
    "node_error": "Ошибка исполнителя",
    "node_change": "Изменение состояния ноды в БД",
    "node_result_verification": "Нативная проверка результата ноды",
    "model_start": "Запрос к модели отправлен",
    "model_end": "Ответ модели получен",
    "model_error": "Ошибка запроса к модели",
    "model_stream_event": "Событие потока ответа модели",
    "model_retrieve_start": "Запрос на получение готового ответа модели отправлен",
    "model_retrieve_end": "Готовый ответ модели получен",
    "parse_result": "Разбор ответа модели",
    "json_retry": "Штатный повтор разбора JSON",
    "domain_check": "Вердикт проверки",
    "rule_diary": "Дневник правил кода",
    "runtime_check": "Проверка во время выполнения",
    "runtime_wait": "Ожидание",
    "project_control": "Ограничение проекта",
    "data_applied": "Изменения БД записаны",
    "apply_input": "Вход применения изменений",
    "apply_error": "Ошибка применения изменений",
    "planner_successor": "Решение планировщика о переходе",
    "artifact_created": "Артефакт создан",
    "artifact_saved": "Артефакт сохранён на диск",
    "artifact_verified": "Файл артефакта проверен",
    "assemble_retry": "Повтор сборки",
    "action_blocked": "Действие заблокировано защитой",
    "human_approval_pending": "Ожидается решение человека",
    "local_recovery_guard": "Защита локального восстановления: платные вызовы запрещены",
    "managed_successor": "Управляемый переход на следующую ноду выполнен",
    "managed_successor_denied": "Управляемый переход на следующую ноду отклонён",
    "observer_control": "Управление наблюдением",
    "observer_stop": "Наблюдение остановлено",
    "profile_effective_context": "Применён профиль настроек для попытки",
    "provider_fallback": "Платный провайдер не ответил: использован запасной путь",
    "animation_degraded": "Промты анимации получены запасным путём",
    "recovery_continuation_deferred": "Продолжение после восстановления отложено",
    "recovery_continuation_submitted": "Продолжение после восстановления отправлено",
    "recovery_verification": "Проверка результата восстановления",
    "media_request_reserved": "Под медиа-запрос зарезервирован бюджет",
    "media_request_input": "Входные данные медиа-запроса приняты",
    "media_response": "Ответ медиа-провайдера получен",
    "media_wait": "Ожидание результата медиа-провайдера",
    "media_phase": "Медиа-задача перешла в новую фазу",
    "media_download": "Файл медиа-задачи скачан",
    "media_failure": "Медиа-задача завершилась сбоем",
    "media_policy_denied": "Отправка медиа-запроса остановлена защитным ограничением",
    "media_auxiliary_denied": "Вспомогательный платный вызов запрещён",
    "media_node_verified": "Результат медиа-ноды подтверждён",
    "body_cleanup": "Старые тела запросов очищены по сроку хранения",
    "body_quota_blocked": "Тело запроса не сохранено: превышена квота хранилища",
    "decision_model_start": "Запрос к модели решений отправлен",
    "decision_model_end": "Ответ модели решений получен",
    "decision_model_error": "Ошибка запроса к модели решений",
    "decision_retrieve_start": "Запрос на получение ответа модели решений отправлен",
    "decision_retrieve_denied": "Получение ответа модели решений запрещено",
    "decision_request_blocked": "Запрос к модели решений заблокирован",
    "decision_continuation_denied": "Продолжение по решению запрещено",
    "decision_gateway_ack": "Шлюз решений подтвердил приём",
    "decision_gateway_ack_error": "Шлюз решений не подтвердил приём",
    # Этапы фонового цикла наблюдателя (имена этапов, показываются в панели как события обслуживания).
    "watchdog": "Сторожевая проверка зависших запросов",
    "reconcile": "Сверка журнала с состоянием БД",
    "reload": "Перечитана конфигурация наблюдения",
    "maintenance": "Плановое обслуживание журнала",
    "decision": "Решение системы по инциденту",
    "diagnosis": "Диагностика инцидентов",
    "consumer": "Передача событий потребителю журнала",
    "sfx_skipped": "Звуковые эффекты пропущены: генерация выключена настройкой",
    "sfx_node_degraded": "Звуковые эффекты подменены процедурным звуком: шаг не считается готовым нормально",
    "audio_step_failure": "Шаг озвучки завершился сбоем",
    "audio_result_verification": "Независимая проверка аудиофайла",
    "scene_design_skipped": "Проектирование сцен пропущено: выключено настройкой",
    "operator_check_not_checked": "Проверка оператором не выполнена: нет ключа GPT-провайдера",
    "operator_stub_output": "Ответ оператора подменён заглушкой: нет ключа GPT-провайдера",
}

# Виды, которые по умолчанию тревожные (если нет уточнения из полезной нагрузки).
_EVENT_SEVERITY: dict[str, str] = {
    "node_error": SEV_ERROR, "model_error": SEV_ERROR, "apply_error": SEV_ERROR, "media_failure": SEV_ERROR,
    "decision_model_error": SEV_ERROR, "decision_gateway_ack_error": SEV_ERROR,
    "action_blocked": SEV_WARN, "managed_successor_denied": SEV_WARN, "media_policy_denied": SEV_WARN,
    "media_auxiliary_denied": SEV_WARN, "body_quota_blocked": SEV_WARN, "provider_fallback": SEV_WARN,
    "animation_degraded": SEV_WARN, "assemble_retry": SEV_WARN, "json_retry": SEV_WARN,
    "recovery_continuation_deferred": SEV_WARN, "decision_retrieve_denied": SEV_WARN,
    "decision_request_blocked": SEV_WARN, "decision_continuation_denied": SEV_WARN,
    "human_approval_pending": SEV_WARN, "local_recovery_guard": SEV_WARN, "observer_stop": SEV_WARN,
    "sfx_skipped": SEV_WARN, "sfx_node_degraded": SEV_WARN, "audio_step_failure": SEV_ERROR,
    "scene_design_skipped": SEV_WARN, "operator_check_not_checked": SEV_WARN, "operator_stub_output": SEV_WARN,
}

CHECK_NAMES_RU: dict[str, str] = {
    "apply_ops_validation": "проверка операций перед записью в БД",
    "native_animation_prompt_batch": "проверка пачки промтов анимации",
    "native_sfx_events_validation": "проверка списка звуковых событий",
    "original_harness_gate": "проверка прежней обвязки исполнителя",
    "regen_budget": "лимит повторных генераций",
    "sfx_plan_checkpoint_reset": "сброс контрольной точки плана звуков",
    "frame_coverage_gate": "проверка покрытия закадра кадрами",
    "qc_noop": "контроль качества: изменений не требуется",
    "native_image_prompt_batch": "проверка пачки промтов изображений",
    "native_split_voiceover_coverage": "проверка покрытия закадра разбивкой",
    "auto_approve_without_review": "автоодобрение без проверки",
    "observe_only_plan_result": "проверка результата плана (режим наблюдения)",
    "observe_only_script_result": "проверка результата сценария (режим наблюдения)",
    "observe_only_split_result": "проверка результата разбивки (режим наблюдения)",
    "observe_only_image_prompts_result": "проверка промтов изображений (режим наблюдения)",
}
WAIT_REASONS_RU: dict[str, str] = {
    "user_stop": "остановка пользователем",
    "queue_predecessor": "очередь: впереди другой проект",
    "failure_sleep": "пауза после сбоя",
    "parallel_slots_full": "заняты все параллельные места",
    "recovery_verification_pending": "ждёт проверки результата восстановления",
}
STATUS_WORDS_RU: dict[str, str] = {
    "passed": "пройдена", "failed": "не пройдена", "unknown": "результат неизвестен",
    "ok": "успешно", "error": "ошибка", "denied": "отказано", "rejected": "отклонено",
    "unchecked": "не проверено",
}

# --- инциденты (observation_incidents.type = ключи diagnosis.TITLES) ------------------------------------
# Копия заголовков diagnosis.TITLES нужна, чтобы лента не тянула тяжёлый модуль; тест сверяет оба списка.
INCIDENT_TITLES_RU: dict[str, str] = {
    "media_failure": "Результат медиа-задачи не подтверждён",
    "media_policy": "Отправка остановлена защитным ограничением",
    "invalid_json": "Ответ не разобран как обязательный JSON",
    "missing_payload": "Нет обязательных данных в ответе",
    "contract_rejected": "Ответ отклонён существующей проверкой",
    "api_error": "Зафиксирована ошибка API или транспорта",
    "uncertain_request": "Завершение внешнего запроса не подтверждено",
    "runtime_wait": "Ожидание очереди, блокировки или подтверждения",
    "user_stop": "Остановка пользователем",
    "db_disagreement": "Состояния master и project расходятся",
    "journal_gap": "Доказательства неполны: пробел журнала",
    "stalled": "Запрос или выполнение зависли: нет завершения в заданное время",
    "split_degraded": "Разбивка получена локальным запасным путём: ответ модели непригоден",
    "verification_hold": "Нативная проверка не подтвердила результат; запуск удержан",
    "provider_degraded": "Платный провайдер не отдал результат: звук подменён локальным синтезом",
    "animation_degraded": "Промты анимации получены запасным путём или частично: сбой провайдера, шаг продолжен без ответа модели",
    "spend_mismatch": "Расхождение в учёте денег: журнал трат не сходится со сверкой",
    "spend_reconcile": "Расхождение в учёте денег: журнал трат не сходится со сверкой",
}
INCIDENT_STATUS_RU = {"open": "открыт", "resolved": "закрыт", "closed": "закрыт", "acknowledged": "принят в работу",
                      "suppressed": "скрыт", "stale": "потерял актуальность"}

# --- решения (decision_records.status) -------------------------------------------------------------------
DECISION_STATUS_RU = {
    "ready": "Решение по инциденту готово к обработке",
    "waiting_worker": "Решение ждёт исполнителя",
    "leased": "Решение взято в работу",
    "proposed": "Система предложила действие",
    "approved": "Предложенное действие одобрено",
    "applied": "Решение выполнено",
    "executed": "Решение выполнено",
    "denied": "Решение отклонено политикой",
    "rejected": "Решение отклонено",
    "failed": "Решение завершилось ошибкой",
    "human_review": "Решение передано человеку",
    "expired": "Решение устарело",
    "closed": "Решение закрыто",
}

# --- слой действий v1 (action_v1_records.action_kind) ---------------------------------------------------
ACTION_V1_RU = {
    "enable": "Наблюдение включено",
    "resume": "Проект возобновлён",
    "hold": "Проект поставлен на удержание",
    "close_execution": "Запуск закрыт",
    "start_node": "Запуск ноды",
    "verify_result": "Проверка результата ноды",
    "human_review": "Передано человеку на проверку",
    "recovery_denied": "Восстановление отклонено",
    "node_recovery": "Восстановление ноды",
    "node_profile_recovery": "Восстановление ноды с другим профилем",
    "media_recovery": "Восстановление медиа-задачи",
    "verification_wait": "Ожидание проверки результата",
    "decision_mode": "Изменён режим решений",
    "kill_switch_on": "Аварийный стоп включён",
    "kill_switch_off": "Аварийный стоп снят",
    "managed_launch": "Управляемый запуск",
    "watchdog_stalled": "Сторожевая проверка: запрос завис",
    "managed_successor": "Управляемый переход на следующую ноду",
    "incident_notify": "Уведомление человека об инциденте",
    "incident_notify_off": "Удержание проверкой записано в журнал уведомлений без отправки (Telegram отключён)",
}
INCIDENT_NOTIFY_STATUS_RU = {
    "notified": "Человек уведомлён об инциденте",
    "notify_failed": "Уведомление об инциденте не доставлено",
}
_ACTION_V1_SEVERITY = {"incident_notify": SEV_INFO, "incident_notify_off": SEV_WARN, "recovery_denied": SEV_WARN, "kill_switch_on": SEV_WARN, "watchdog_stalled": SEV_WARN,
                       "human_review": SEV_WARN, "hold": SEV_WARN}

# --- журнал слоя действий (action_layer_history.event) --------------------------------------------------
ACTION_LAYER_RU = HISTORY_EVENT_RU  # единственный источник фраз - action_layer/messages_ru.py
_ACTION_LAYER_SEVERITY = {"step_failed": SEV_ERROR, "step_unknown": SEV_WARN, "budget_block": SEV_WARN,
                          "kill_switch_block": SEV_WARN, "resume_refused": SEV_WARN, "job_stop_after_failure": SEV_ERROR,
                          "step_cancelled": SEV_WARN, "step_skipped": SEV_WARN}

# --- траты (spend_ledger.kind / source) -----------------------------------------------------------------
SPEND_KIND_RU = {
    "reserve": "Зарезервирован бюджет под платный вызов",
    "settle": "Резерв закрыт по итогу вызова",
    "actual": "Списание: сумма из ответа провайдера или замера",
    "estimated": "Списание: сумма оценена по тарифу, провайдер её не подтвердил",
    "event": "Событие учёта расходов",
}
SPEND_SOURCE_RU = {
    "planner": "планировщик команд",
    "action_layer": "слой действий",
    "media_native": "платный вызов медиа-провайдера",
    "media_402": "провайдер ответил «нет средств» (402)",
    "credit_error": "ошибка кредитов провайдера",
    "spend_guard": "защита от перерасхода",
    "paid_gate_block": "платный вызов остановлен защитой",
    "killswitch_guard": "аварийный стоп",
    "native_call": "платный вызов модели",
    "elevenlabs": "озвучка ElevenLabs",
    "voiceover": "озвучка",
    "composer": "сборка",
    "write_failed": "ошибка записи в журнал расходов",
    "reply_text_fallback": "запасной разбор ответа",
}
_SPEND_EVENT_SEVERITY = {"credit_error": SEV_ERROR, "media_402": SEV_ERROR, "paid_gate_block": SEV_WARN,
                         "killswitch_guard": SEV_WARN, "write_failed": SEV_ERROR}

# --- задачи провайдеров (provider_task_ledger.event / status) -------------------------------------------
TASK_EVENT_RU = {
    "intent": "Намерение отправить задачу провайдеру записано",
    "intent_done": "Итог отправки задачи провайдеру записан",
    "submitted": "Задача отправлена провайдеру",
    "bound": "Задача привязана к платному запросу",
    "done": "Задача провайдера завершена",
    "failed": "Задача провайдера завершилась ошибкой",
    "polled": "Состояние задачи провайдера проверено",
}
TASK_STATUS_RU = {
    "submit_unknown": "отправка не подтверждена",
    "submitted": "отправлена",
    "bound": "привязана",
    "rejected": "отклонена",
    "done": "готова",
    "success": "готова",
    "failed": "ошибка",
    "pending": "в обработке",
    "processing": "в обработке",
}

# --- ноды запусков (node_runs.status) ---------------------------------------------------------------------
NODE_RUN_STATUS_RU = {
    "pending": "Нода ждёт запуска",
    "queued": "Нода поставлена в очередь",
    "running": "Нода выполняется",
    "waiting_hitl": "Нода ждёт решения человека",
    "done": "Нода завершена",
    "failed": "Нода завершилась ошибкой",
    "skipped": "Нода пропущена",
}
_NODE_RUN_SEVERITY = {"failed": SEV_ERROR, "waiting_hitl": SEV_WARN}

# --- публикации ---------------------------------------------------------------------------------------------
PUBLICATION_STATUS_RU = {
    "planned": "Публикация запланирована (намерение записано до клика)",
    "clicked": "Нажата кнопка публикации; итог ещё не подтверждён",
    "confirmed": "Публикация подтверждена",
    "unconfirmed": "Публикация не подтверждена: нужна проверка человеком",
    "failed": "Публикация не удалась",
    "rejected": "Платформа отклонила публикацию",
    "skipped": "Публикация пропущена",
}
_PUBLICATION_SEVERITY = {"unconfirmed": SEV_WARN, "failed": SEV_ERROR, "rejected": SEV_ERROR, "clicked": SEV_WARN}

# Причины в записях публикаций (publications.error_code): те же фразы, что оператор видит в панели «Публикации»
# (web/src/lib/publication-text.ts, PUBLICATION_CODE_RU; равенство таблиц проверяет tests/w7_b_*).
MANUAL_CONFIRMED_CODE = "manual_confirmed"
MANUAL_NOT_PUBLISHED_CODE = "manual_not_published"
_PUBLICATION_CODE_EXTRA_RU = {
    "manual_confirmed": "Подтверждено человеком вручную.",
    "manual_not_published": "Закрыто человеком как «не опубликовано».",
    "publish_disabled": "Автопубликация выключена настройкой: ничего не отправлялось.",
    "confirm_timeout": "Площадка не подтвердила публикацию вовремя. Проверьте площадку вручную.",
}
try:  # те же фразы, что и в отказах площадок (publish_accounts.REFUSALS): один источник
    from app.services.publish_accounts import REFUSALS as _REFUSALS
except Exception:  # noqa: BLE001
    _REFUSALS = {}
PUBLICATION_CODE_RU: dict[str, str] = {**_PUBLICATION_CODE_EXTRA_RU, **_REFUSALS}


def publication_code_text(code: Any) -> str:
    """Русская фраза для кода причины публикации; незнакомый код не показывается как есть."""
    c = str(code or "").strip()
    if not c:
        return ""
    return PUBLICATION_CODE_RU.get(c) or "Причина записана в журнале (код не расшифрован)."

# --- уведомления (notification_log) ----------------------------------------------------------------------
NOTIFICATION_STATUS_RU = {
    "sent": "доставлено", "delivered": "доставлено", "ok": "доставлено",
    "failed": "не доставлено", "error": "не доставлено", "skipped": "пропущено", "disabled": "отключено",
    "queued": "в очереди", "pending": "в очереди",
}
NOTIFICATION_KIND_RU = {
    "telegram": "Сообщение в Telegram", "hitl": "Запрос подтверждения оператору",
    "incident": "Оповещение об инциденте", "spend": "Оповещение о расходах",
    "run_finished": "Оповещение о завершении запуска", "run_failed": "Оповещение о сбое запуска",
    "test": "Тестовое сообщение",
    "hitl_request": "Запрос подтверждения оператору", "hitl_photo": "Фото на подтверждение оператору",
    "hitl_video": "Видео на подтверждение оператору", "hitl_photo_tail": "Продолжение фото на подтверждение",
    "hitl_edit_prompt": "Просьба отредактировать промпт", "hitl_original": "Оригинал на подтверждение оператору",
    "step_done": "Шаг выполнен", "step_done_xlsx": "Шаг выполнен, таблица отправлена",
    "publish_summary": "Итог публикации", "worker_advance_error": "Ошибка продвижения запуска",
    "hero_failed": "Не удалось создать главное изображение",
    "image_capture_failed": "Не удалось получить изображение",
}
# Префиксные виды: kind начинается с ключа.
NOTIFICATION_KIND_PREFIX_RU = {
    "step_failure": "Сбой шага", "excel_hero": "Главное изображение из таблицы",
    "test_prompt": "Проверка промпта",
}

# --- фоновые задачи (create_jobs / _JOBS) ---------------------------------------------------------------------
JOB_STATUS_RU = {
    "queued": "Задача создания поставлена в очередь", "running": "Задача создания выполняется",
    "done": "Задача создания завершена", "failed": "Задача создания завершилась ошибкой",
    "error": "Задача создания завершилась ошибкой", "cancelled": "Задача создания отменена",
    "interrupted": "Задача создания прервана перезапуском",
}
_JOB_SEVERITY = {"failed": SEV_ERROR, "error": SEV_ERROR, "interrupted": SEV_WARN, "cancelled": SEV_WARN}

# --- ручные действия оператора ---------------------------------------------------------------------------------
_METHOD_RU = {"POST": "Оператор выполнил действие", "PUT": "Оператор изменил данные",
              "PATCH": "Оператор изменил данные", "DELETE": "Оператор удалил данные"}
_OUTCOME_RU = {"ok": "выполнено", "denied": "отказано в доступе", "rejected": "запрос отклонён",
               "error": "ошибка сервера"}

# Короткие имена источников -> таблицы; ленту строит timeline.py, но говорить должна одинаково.
SOURCE_ALIASES = {
    "observe": "observation_events", "event": "observation_events", "observation": "observation_events",
    "incident": "observation_incidents", "decision": "decision_records",
    "action": "action_v1_records", "action_v1": "action_v1_records",
    "plan": "action_layer_history", "action_layer": "action_layer_history",
    "spend": "spend_ledger", "task": "provider_task_ledger",
    "operator": "operator_audit", "publication": "publications",
    "notify": "notification_log", "notification": "notification_log",
    "node_run": "node_runs", "job": "jobs",
}


# --------------------------------------------------------------------------------------------------------------
def _get(row: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        try:
            value = row.get(key)
        except AttributeError:
            value = None
        if value not in (None, ""):
            return value
    return None


def _payload(row: Mapping[str, Any]) -> dict:
    raw = _get(row, "payload", "details", "detail_json")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = None
    return raw if isinstance(raw, dict) else {}


def _clip(value: Any, limit: int = _DETAIL_LIMIT) -> str:
    text = " ".join(str(value).split())
    try:
        from app.services.redact import redact_text
        text = redact_text(text)
    except Exception:  # redact обязателен для секретов, но лента не должна падать из-за него
        pass
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _money(value: Any) -> str:
    try:
        return f"${float(value):.4f}"
    except (TypeError, ValueError):
        return "сумма неизвестна"


def _note_unknown(source: str, kind: str) -> None:
    with _UNKNOWN_LOCK:
        _UNKNOWN[(source, kind)] = _UNKNOWN.get((source, kind), 0) + 1


def unknown_report() -> dict[str, int]:
    """Какие (источник, вид) встретились без фразы: «источник:вид» -> сколько раз. Для отчёта покрытия."""
    with _UNKNOWN_LOCK:
        return {f"{s}:{k}": n for (s, k), n in sorted(_UNKNOWN.items())}


def reset_unknown_report() -> None:
    with _UNKNOWN_LOCK:
        _UNKNOWN.clear()


def _fallback(source: str, kind: str, label: str = "Событие") -> str:
    _note_unknown(source, kind)
    return f"{label} {kind or 'без вида'} (подробности в журнале)"


# --- события наблюдения -----------------------------------------------------------------------------------------
def event_title_ru(kind: str, payload: Mapping[str, Any] | None = None) -> str:
    """Русский заголовок события панели. Порт ``eventLabel`` из observation-view.ts + все остальные виды."""
    p = payload if isinstance(payload, Mapping) else {}
    unknown = "неизвестно"
    if kind == "observer_control":
        return f"Наблюдение: {p.get('action') or unknown} ({p.get('initiator') or 'инициатор неизвестен'})"
    if kind == "runtime_check":
        name = str(p.get("check") or "проверка")
        return f"{CHECK_NAMES_RU.get(name, name)}: {STATUS_WORDS_RU.get(str(p.get('status')), p.get('status') or unknown)}"
    if kind == "parse_result" and p.get("contract") == "native_split" and p.get("fallback_used") is True:
        tail = " (тире не покрывает закадр)" if p.get("dash_rejected") is True else ""
        return f"Разбивка: ответ модели непригоден{tail}; кадры получены локальной разбивкой закадра"
    if kind == "parse_result":
        parsed = "извлечён" if p.get("parsed") is True else "не извлечён" if p.get("parsed") is False else unknown
        schema = "не проверялась" if p.get("schema_valid") is None else "верна" if p.get("schema_valid") else "нарушена"
        return f"Разбор: {parsed}; схема {schema}"
    if kind == "rule_diary":
        stop = f"; остановило правило {p['stop_rule']}" if isinstance(p.get("stop_rule"), str) and p.get("stop_rule") else ""
        rules = len(p["rules"]) if isinstance(p.get("rules"), dict) else 0
        return f"Дневник правил кода: правил сработало {rules}{stop}"
    if kind == "node_result_verification":
        status = p.get("status")
        held = "" if p.get("observe_only") is True else "; запуск удержан"
        tail = "; режим наблюдения, запуск не остановлен" if p.get("observe_only") is True else ""
        text = ("подтвердила результат" if status == "passed"
                else f"не подтвердила результат{held}{tail}" if status == "failed"
                else f"не смогла сравнить результат со входом{held}{tail}" if status == "unknown"
                else f"статус {status or 'неизвестен'}")
        return f"Нативная проверка {text}"
    if kind == "node_error" and isinstance(p.get("rule"), str) and p.get("rule"):
        return f"Ошибка исполнителя: результат остановило правило {p['rule']}"
    if kind == "domain_check":
        return f"Вердикт: {p.get('verdict') or unknown}; качество модели не проверено"
    if kind == "node_change":
        transitions = p.get("transitions")
        if isinstance(transitions, list) and transitions:
            return "; ".join(f"{t.get('from')} → {t.get('to')} ({t.get('initiator') or 'инициатор неизвестен'})"
                             for t in transitions if isinstance(t, Mapping))
        return "Изменение состояния БД; инициатор неизвестен"
    if kind == "model_end":
        usage = p.get("usage")
        known = [f"{k}: {usage[k]}" for k in ("input_tokens", "output_tokens", "total_tokens", "credits_consumed")
                 if isinstance(usage, Mapping) and isinstance(usage.get(k), (int, float))]
        return f"Ответ модели · {' · '.join(known) if known else 'расход токенов неизвестен'}"
    if kind == "runtime_wait":
        reason = str(p.get("reason") or unknown)
        return f"Ожидание: {WAIT_REASONS_RU.get(reason, reason)} ({p.get('initiator') or unknown})"
    if kind == "project_control":
        stop = p.get("user_stop")
        return "Ограничение проекта: остановка пользователем " + ("включена" if stop is True else "снята" if stop is False else unknown)
    if kind == "media_phase" and p.get("phase"):
        return f"Медиа-задача перешла в фазу «{p['phase']}»"
    if kind == "media_failure" and p.get("phase"):
        return f"Медиа-задача завершилась сбоем на фазе «{p['phase']}»"
    if kind == "human_approval_pending" and p.get("kind"):
        return f"Ожидается решение человека: {p['kind']}"
    if kind == "planner_successor" and p.get("from") and p.get("to"):
        return f"Решение планировщика о переходе: {p['from']} → {p['to']}"
    if kind == "provider_fallback" and p.get("provider"):
        return f"Провайдер «{p['provider']}» не ответил: использован запасной путь"
    if kind in EVENT_LABELS_RU:
        return EVENT_LABELS_RU[kind]
    return _fallback("observation_events", kind)


def _event_severity(kind: str, p: Mapping[str, Any]) -> str:
    if kind in ("node_result_verification", "runtime_check", "media_node_verified"):
        status = p.get("status")
        if status == "failed":
            return SEV_ERROR
        if status == "unknown":
            return SEV_WARN
        return SEV_INFO
    if kind == "parse_result":
        if p.get("fallback_used") is True or p.get("parsed") is False or p.get("schema_valid") is False:
            return SEV_WARN
        return SEV_INFO
    if kind == "domain_check":
        return SEV_WARN if str(p.get("verdict") or "").lower() in {"fail", "failed", "reject", "rejected", "blocked"} else SEV_INFO
    if kind == "model_end" and isinstance(p.get("status_code"), int) and p["status_code"] >= 400:
        return SEV_ERROR
    return _EVENT_SEVERITY.get(kind, SEV_INFO)


def _event_detail(kind: str, row: Mapping[str, Any], p: Mapping[str, Any]) -> str:
    parts: list[str] = []
    node = _get(row, "node_key")
    if node:
        parts.append(f"нода {node}")
    if p.get("error_type"):
        parts.append(f"тип ошибки {p['error_type']}")
    if p.get("reason") and kind != "runtime_wait":
        parts.append(f"причина: {p['reason']}")
    if p.get("status_code") is not None:
        parts.append(f"код ответа {p['status_code']}")
    if p.get("model"):
        parts.append(f"модель {p['model']}")
    if p.get("job_id"):
        parts.append(f"задача {p['job_id']}")
    if p.get("bytes") is not None and kind == "media_download":
        parts.append(f"{p['bytes']} байт")
    if p.get("frame_count") is not None:
        parts.append(f"кадров: {p['frame_count']}")
    if p.get("summary"):
        parts.append(_clip(p["summary"], 200))
    return _clip("; ".join(parts)) if parts else ""


# --- публичная функция ----------------------------------------------------------------------------------------------
def describe(row: Mapping[str, Any]) -> tuple[str, str, str]:
    """(title_ru, detail_ru, severity) для строки любого источника ленты. Не бросает исключений."""
    try:
        return _describe(row)
    except Exception as exc:  # лента должна показать строку, даже если текст собрать не вышло
        kind = str(_get(row, "kind", "event", "type") or "")
        _note_unknown(str(_get(row, "source") or "?"), kind or type(exc).__name__)
        return (f"Запись журнала {kind or 'без вида'} (подробности в журнале)", "", SEV_INFO)


def _describe(row: Mapping[str, Any]) -> tuple[str, str, str]:
    raw_source = str(_get(row, "source_table") or _get(row, "source") or "observation_events")
    source = SOURCE_ALIASES.get(raw_source, raw_source)
    p = _payload(row)

    if source == "observation_events":
        kind = str(_get(row, "kind", "event") or "")
        return event_title_ru(kind, p), _event_detail(kind, row, p), _event_severity(kind, p)

    if source == "observation_incidents":
        kind = str(_get(row, "type", "kind") or "")
        title = INCIDENT_TITLES_RU.get(kind) or _get(row, "title") or _fallback(source, kind, "Инцидент")
        status = str(_get(row, "status") or "")
        sev_raw = str(_get(row, "severity") or "").lower()
        severity = SEV_ERROR if sev_raw in ("error", "critical", "high") else SEV_WARN if sev_raw in ("warn", "warning", "medium") else SEV_INFO
        detail = [f"статус: {INCIDENT_STATUS_RU.get(status, status)}"] if status else []
        fact = _get(row, "fact") or p.get("fact")
        if fact:
            detail.append(_clip(fact, 250))
        if _get(row, "node_key"):
            detail.append(f"нода {row['node_key']}")
        return str(title), _clip("; ".join(detail)), severity

    if source == "decision_records":
        status = str(_get(row, "status", "kind") or "")
        title = DECISION_STATUS_RU.get(status) or _fallback(source, status, "Решение")
        detail = []
        proposal = row.get("proposal") if isinstance(row.get("proposal"), Mapping) else {}
        if proposal.get("action"):
            detail.append(f"предложено: {ACTION_V1_RU.get(str(proposal['action']), proposal['action'])}")
        if _get(row, "node_key"):
            detail.append(f"нода {row['node_key']}")
        severity = SEV_ERROR if status == "failed" else SEV_WARN if status in ("denied", "rejected", "human_review", "expired") else SEV_INFO
        return title, _clip("; ".join(detail)), severity

    if source == "action_v1_records":
        kind = str(_get(row, "action_kind", "kind") or "")
        title = ACTION_V1_RU.get(kind) or _fallback(source, kind, "Действие")
        notify_status = str(_get(row, "status") or "")
        if kind == "incident_notify" and notify_status in INCIDENT_NOTIFY_STATUS_RU:
            title = INCIDENT_NOTIFY_STATUS_RU[notify_status]
        detail = []
        if _get(row, "initiator"):
            detail.append(f"инициатор: {row['initiator']}")
        if _get(row, "node_key"):
            detail.append(f"нода {row['node_key']}")
        if _get(row, "rationale"):
            detail.append(_clip(row["rationale"], 250))
        severity = SEV_WARN if kind == "incident_notify" and notify_status == "notify_failed" else _ACTION_V1_SEVERITY.get(kind, SEV_INFO)
        return title, _clip("; ".join(detail)), severity

    if source == "action_layer_history":
        kind = str(_get(row, "event", "kind") or "")
        title = ACTION_LAYER_RU.get(kind) or _fallback(source, kind, "Событие плана")
        detail = []
        if row.get("step_index") is not None:
            detail.append(f"шаг {int(row['step_index']) + 1}")
        if _get(row, "action_id"):
            detail.append(f"действие {row['action_id']}")
        if _get(row, "actor"):
            detail.append(f"оператор: {row['actor']}")
        if _get(row, "error"):
            detail.append(f"причина: {_clip(row['error'], 250)}")
        sev = _ACTION_LAYER_SEVERITY.get(kind, SEV_INFO)
        if kind == "plan_finished" and str(_get(row, "outcome") or "") in ("failed", "error"):
            sev = SEV_ERROR
        return title, _clip("; ".join(detail)), sev

    if source == "spend_ledger":
        kind = str(_get(row, "kind") or "")
        src = str(_get(row, "spend_source") or (_get(row, "source") if _get(row, "source_table") else "") or "")
        src_ru = SPEND_SOURCE_RU.get(src, src)
        title = SPEND_KIND_RU.get(kind) or _fallback(source, kind, "Запись расходов")
        sev = _SPEND_EVENT_SEVERITY.get(src, SEV_INFO) if kind == "event" else SEV_INFO
        if kind == "event" and src_ru:
            title = f"{title}: {src_ru}"
        detail = []
        if kind != "event":
            detail.append(_money(row.get("usd")))
        if row.get("credits"):
            detail.append(f"{row['credits']} кредитов")
        if _get(row, "provider"):
            detail.append(f"провайдер {row['provider']}")
        if src_ru and kind != "event":
            detail.append(f"источник: {src_ru}")
        if _get(row, "note"):
            detail.append(_clip(row["note"], 200))
        return title, _clip("; ".join(detail)), sev

    if source == "provider_task_ledger":
        kind = str(_get(row, "event", "kind") or "")
        status = str(_get(row, "status") or "")
        title = TASK_EVENT_RU.get(kind) or _fallback(source, kind, "Событие задачи провайдера")
        detail = []
        if status:
            detail.append(f"статус: {TASK_STATUS_RU.get(status, status)}")
        for key, label in (("provider", "провайдер"), ("model", "модель"), ("task_id", "задача")):
            if _get(row, key):
                detail.append(f"{label} {row[key]}")
        sev = SEV_ERROR if status in ("failed",) else SEV_WARN if status in ("submit_unknown", "rejected") else SEV_INFO
        return title, _clip("; ".join(detail)), sev

    if source == "operator_audit":
        method = str(_get(row, "method") or "").upper()
        from app.services.operator_phrases import operator_phrase
        title = (_get(row, "summary_ru") or operator_phrase(method, str(_get(row, "route_template", "path") or ""))
                 or _METHOD_RU.get(method) or _fallback(source, method, "Действие оператора"))
        outcome = str(_get(row, "outcome") or "")
        # Настоящий путь запроса (в route_template лежит шаблон с {project_id}, человеку он ничего не говорит).
        detail = [f"{method} {_get(row, 'path', 'route_template') or ''}".strip()]
        code = _get(row, "status_code")
        if code is not None:
            detail.append(f"код ответа {code}")
        if outcome:
            detail.append(_OUTCOME_RU.get(outcome, outcome))
        if _get(row, "actor"):
            detail.append(f"оператор: {row['actor']}")
        sev = SEV_ERROR if outcome == "error" else SEV_WARN if outcome in ("denied", "rejected") else SEV_INFO
        return str(title), _clip("; ".join(d for d in detail if d)), sev

    if source == "publications":
        status = str(_get(row, "status", "kind") or "")
        code = str(_get(row, "error_code") or "")
        title = PUBLICATION_STATUS_RU.get(status) or _fallback(source, status, "Публикация")
        severity = _PUBLICATION_SEVERITY.get(status, SEV_INFO)
        detail = []
        if _get(row, "platform"):
            detail.append(f"площадка {row['platform']}")
        if _get(row, "post_url"):
            detail.append(str(row["post_url"]))
        if code == MANUAL_NOT_PUBLISHED_CODE:
            # Решение человека, а не сбой: не красная ошибка и не входит в счётчик ошибок.
            title, severity = "Закрыто человеком: публикация помечена как «не опубликовано»", SEV_INFO
        elif code == MANUAL_CONFIRMED_CODE:
            title, severity = "Подтверждено человеком вручную как опубликованное", SEV_INFO
        elif code:
            detail.append(f"причина: {publication_code_text(code).rstrip('.')}")
        return title, _clip("; ".join(detail)), severity

    if source == "notification_log":
        kind = str(_get(row, "kind") or "")
        channel = str(_get(row, "channel") or "")
        status = str(_get(row, "status") or "")
        if not status and "ok" in row and row.get("ok") is not None:
            status = "sent" if row.get("ok") else "failed"
        base = NOTIFICATION_KIND_RU.get(kind)
        if base is None:
            for prefix, label in NOTIFICATION_KIND_PREFIX_RU.items():
                if kind.startswith(prefix):
                    base = label
                    break
        base = base or NOTIFICATION_KIND_RU.get(channel) or "Уведомление оператору"
        if channel == "telegram_off":
            word, sev = "не доставлено (Telegram отключён)", SEV_WARN
        else:
            word = NOTIFICATION_STATUS_RU.get(status, status or "статус неизвестен")
            sev = SEV_ERROR if status in ("failed", "error") else SEV_INFO
        detail = []
        if _get(row, "error"):
            detail.append(f"причина: {_clip(row['error'], 250)}")
        if _get(row, "text", "summary", "text_ru", "text_redacted"):
            detail.append(_clip(_get(row, "text", "summary", "text_ru", "text_redacted"), 200))
        return f"{base}: {word}", _clip("; ".join(detail)), sev

    if source == "node_runs":
        status = str(_get(row, "status") or "")
        title = NODE_RUN_STATUS_RU.get(status) or _fallback(source, status, "Состояние ноды")
        detail = []
        if _get(row, "node_key"):
            detail.append(f"нода {row['node_key']}")
        if _get(row, "error"):
            detail.append(_clip(row["error"], 250))
        elif _get(row, "progress_text"):
            detail.append(_clip(row["progress_text"], 200))
        return title, _clip("; ".join(detail)), _NODE_RUN_SEVERITY.get(status, SEV_INFO)

    if source == "jobs":
        status = str(_get(row, "status", "kind") or "")
        title = JOB_STATUS_RU.get(status) or _fallback(source, status, "Задача")
        detail = [_clip(row["error"], 250)] if _get(row, "error") else []
        return title, _clip("; ".join(detail)), _JOB_SEVERITY.get(status, SEV_INFO)

    kind = str(_get(row, "kind", "event", "type") or "")
    return _fallback(source, kind), "", SEV_INFO


# --- что обязано иметь фразу (для теста покрытия и отчёта) ------------------------------------------------------------
def covered_event_kinds() -> frozenset[str]:
    return frozenset(EVENT_LABELS_RU)


def coverage_tables() -> dict[str, frozenset[str]]:
    """Источник -> множество видов, у которых есть фраза (для теста и документации)."""
    return {
        "observation_events": frozenset(EVENT_LABELS_RU),
        "observation_incidents": frozenset(INCIDENT_TITLES_RU),
        "decision_records": frozenset(DECISION_STATUS_RU),
        "action_v1_records": frozenset(ACTION_V1_RU),
        "action_layer_history": frozenset(ACTION_LAYER_RU),
        "spend_ledger": frozenset(SPEND_KIND_RU),
        "provider_task_ledger": frozenset(TASK_EVENT_RU),
        "publications": frozenset(PUBLICATION_STATUS_RU),
        "node_runs": frozenset(NODE_RUN_STATUS_RU),
        "jobs": frozenset(JOB_STATUS_RU),
    }
