export interface ObservationEvent {
  id: number; event_id: string; project_id: number; execution_id?: string;
  node_key?: string | null; attempt_id?: string | null; request_id?: string | null;
  kind: string; db_scope?: string; created_at?: string;
  payload: Record<string, unknown>;
}

/** Restore the exact saved scope; never silently choose the first graph group. */
export function groupForScope(groups: {id:string;keys:string[]}[], nodeKeys: string[]): string {
  if (!nodeKeys.length) return '';
  const keys = new Set(nodeKeys);
  return groups.find(g => g.keys.length === keys.size && g.keys.every(k => keys.has(k)))?.id ?? '';
}

export function usageLabel(usage: Record<string, unknown> | null | undefined): string {
  if (!usage) return "расход токенов неизвестен";
  const fields = ["input_tokens", "output_tokens", "total_tokens", "credits_consumed"];
  const known = fields.filter(key => typeof usage[key] === "number");
  return known.length ? known.map(key => `${key}: ${usage[key]}`).join(" · ") : "расход токенов неизвестен";
}

export function mergeEvents(old: ObservationEvent[], incoming: ObservationEvent[], projectId: number): ObservationEvent[] {
  const unique = new Map<string, ObservationEvent>();
  for (const event of [...old, ...incoming]) if (event.project_id === projectId) unique.set(event.event_id, event);
  // Master and project outboxes can be drained in different batches. The API
  // cursor remains the durable journal ID; display uses the recorded time.
  return [...unique.values()].sort((a, b) => (a.created_at ?? "").localeCompare(b.created_at ?? "") || a.id - b.id);
}

/** Равно EVENT_LABELS_RU в app/services/timeline_ru.py (сверяет tests/w3_1_timeline_ru_test.py): сервер, лента и панель говорят одно. */
export const EVENT_KIND_LABELS: Record<string, string> = {
  node_start: "Исполнитель начал ноду",
  node_end: "Исполнитель вернулся (запись данных не подтверждена)",
  node_input: "Вход ноды",
  node_output: "Выход ноды",
  node_error: "Ошибка исполнителя",
  node_change: "Изменение состояния ноды в БД",
  node_result_verification: "Нативная проверка результата ноды",
  model_start: "Запрос к модели отправлен",
  model_end: "Ответ модели получен",
  model_error: "Ошибка запроса к модели",
  model_stream_event: "Событие потока ответа модели",
  model_retrieve_start: "Запрос на получение готового ответа модели отправлен",
  model_retrieve_end: "Готовый ответ модели получен",
  parse_result: "Разбор ответа модели",
  json_retry: "Штатный повтор разбора JSON",
  domain_check: "Вердикт проверки",
  rule_diary: "Дневник правил кода",
  runtime_check: "Проверка во время выполнения",
  runtime_wait: "Ожидание",
  project_control: "Ограничение проекта",
  data_applied: "Изменения БД записаны",
  apply_input: "Вход применения изменений",
  apply_error: "Ошибка применения изменений",
  planner_successor: "Решение планировщика о переходе",
  artifact_created: "Артефакт создан",
  artifact_saved: "Артефакт сохранён на диск",
  artifact_verified: "Файл артефакта проверен",
  assemble_retry: "Повтор сборки",
  action_blocked: "Действие заблокировано защитой",
  human_approval_pending: "Ожидается решение человека",
  local_recovery_guard: "Защита локального восстановления: платные вызовы запрещены",
  managed_successor: "Управляемый переход на следующую ноду выполнен",
  managed_successor_denied: "Управляемый переход на следующую ноду отклонён",
  observer_control: "Управление наблюдением",
  observer_stop: "Наблюдение остановлено",
  profile_effective_context: "Применён профиль настроек для попытки",
  provider_fallback: "Платный провайдер не ответил: использован запасной путь",
  animation_degraded: "Промты анимации получены запасным путём",
  recovery_continuation_deferred: "Продолжение после восстановления отложено",
  recovery_continuation_submitted: "Продолжение после восстановления отправлено",
  recovery_verification: "Проверка результата восстановления",
  media_request_reserved: "Под медиа-запрос зарезервирован бюджет",
  media_request_input: "Входные данные медиа-запроса приняты",
  media_response: "Ответ медиа-провайдера получен",
  media_wait: "Ожидание результата медиа-провайдера",
  media_phase: "Медиа-задача перешла в новую фазу",
  media_download: "Файл медиа-задачи скачан",
  media_failure: "Медиа-задача завершилась сбоем",
  media_policy_denied: "Отправка медиа-запроса остановлена защитным ограничением",
  media_auxiliary_denied: "Вспомогательный платный вызов запрещён",
  media_node_verified: "Результат медиа-ноды подтверждён",
  body_cleanup: "Старые тела запросов очищены по сроку хранения",
  body_quota_blocked: "Тело запроса не сохранено: превышена квота хранилища",
  decision_model_start: "Запрос к модели решений отправлен",
  decision_model_end: "Ответ модели решений получен",
  decision_model_error: "Ошибка запроса к модели решений",
  decision_retrieve_start: "Запрос на получение ответа модели решений отправлен",
  decision_retrieve_denied: "Получение ответа модели решений запрещено",
  decision_request_blocked: "Запрос к модели решений заблокирован",
  decision_continuation_denied: "Продолжение по решению запрещено",
  decision_gateway_ack: "Шлюз решений подтвердил приём",
  decision_gateway_ack_error: "Шлюз решений не подтвердил приём",
  watchdog: "Сторожевая проверка зависших запросов",
  reconcile: "Сверка журнала с состоянием БД",
  reload: "Перечитана конфигурация наблюдения",
  maintenance: "Плановое обслуживание журнала",
  decision: "Решение системы по инциденту",
  diagnosis: "Диагностика инцидентов",
  consumer: "Передача событий потребителю журнала",
  sfx_skipped: "Звуковые эффекты пропущены: генерация выключена настройкой",
  sfx_node_degraded: "Звуковые эффекты подменены процедурным звуком: шаг не считается готовым нормально",
  audio_step_failure: "Шаг озвучки завершился сбоем",
  audio_result_verification: "Независимая проверка аудиофайла",
  scene_design_skipped: "Проектирование сцен пропущено: выключено настройкой",
  operator_check_not_checked: "Проверка оператором не выполнена: нет ключа GPT-провайдера",
  operator_stub_output: "Ответ оператора подменён заглушкой: нет ключа GPT-провайдера",
};

/** Равно CHECK_NAMES_RU в timeline_ru.py. */
export const CHECK_NAMES_RU: Record<string, string> = {
  apply_ops_validation: "проверка операций перед записью в БД",
  native_animation_prompt_batch: "проверка пачки промтов анимации",
  native_sfx_events_validation: "проверка списка звуковых событий",
  original_harness_gate: "проверка прежней обвязки исполнителя",
  regen_budget: "лимит повторных генераций",
  sfx_plan_checkpoint_reset: "сброс контрольной точки плана звуков",
  frame_coverage_gate: "проверка покрытия закадра кадрами",
  frame_uuid_repair: "починка идентификаторов кадров",
  json_text_repair: "починка JSON в ответе модели",
  write_contract: "фильтр операций записи по контракту ноды",
  qc_noop: "контроль качества: изменений не требуется",
  native_image_prompt_batch: "проверка пачки промтов изображений",
  native_split_voiceover_coverage: "проверка покрытия закадра разбивкой",
  auto_approve_without_review: "автоодобрение без проверки",
  observe_only_plan_result: "проверка результата плана (режим наблюдения)",
  observe_only_script_result: "проверка результата сценария (режим наблюдения)",
  observe_only_split_result: "проверка результата разбивки (режим наблюдения)",
  observe_only_image_prompts_result: "проверка промтов изображений (режим наблюдения)",
};

/** Равно WAIT_REASONS_RU в timeline_ru.py. */
export const WAIT_REASONS_RU: Record<string, string> = {
  user_stop: "остановка пользователем",
  queue_predecessor: "очередь: впереди другой проект",
  failure_sleep: "пауза после сбоя",
  parallel_slots_full: "заняты все параллельные места",
  recovery_verification_pending: "ждёт проверки результата восстановления",
};

/** Равно STATUS_WORDS_RU в timeline_ru.py. */
export const STATUS_WORDS_RU: Record<string, string> = {
  passed: "пройдена",
  failed: "не пройдена",
  unknown: "результат неизвестен",
  ok: "успешно",
  error: "ошибка",
  denied: "отказано",
  rejected: "отклонено",
  unchecked: "не проверено",
  performed: "выполнено",
  filtered: "отфильтровано",
};

const NODE_STATUS_RU: Record<string, string> = {
  pending: "ожидает", queued: "в очереди", running: "выполняется", waiting_hitl: "ждёт решения человека",
  done: "готово", failed: "ошибка", skipped: "пропущено", cancelled: "отменено",
};
const INITIATOR_RU: Record<string, string> = {
  worker: "воркер", api: "кнопка в Studio", ui_restart: "перезапуск из Studio", auto_unstick: "авто-расклинивание",
  api_reset: "сброс из Studio", api_stop: "стоп из Studio", decision_supervisor: "решение надзора",
  only_agent_scope: "режим наблюдения", observation_api: "наблюдение", diagnosis: "диагностика",
  watchdog: "сторож", sync: "синхронизация баз", supervisor_recovery: "восстановление надзором",
  supervisor: "надзор", startup_reconcile: "сверка при старте", sidecar: "боковой процесс",
  only_agent_reconcile: "сверка режима наблюдения", observation_auto_off: "автоотключение наблюдения",
  native_split_verifier: "проверка разбивки", native_sfx_plan_verifier: "проверка плана звуков",
};

export function eventLabel(event: Pick<ObservationEvent, "kind" | "payload">): string {
  const p = event.payload ?? {};
  const unknown = "неизвестно";
  if (event.kind === "observer_control") return `Наблюдение: ${p.action ?? unknown} (${p.initiator ?? "инициатор неизвестен"})`;
  if (event.kind === "runtime_check") {
    const name = String(p.check ?? "проверка");
    const status = p.status == null ? unknown : (STATUS_WORDS_RU[String(p.status)] ?? String(p.status));
    return `${CHECK_NAMES_RU[name] ?? name}: ${status}`;
  }
  if (event.kind === "parse_result" && p.contract === "native_split" && p.fallback_used === true)
    return `Разбивка: ответ модели непригоден${p.dash_rejected === true ? " (тире не покрывает закадр)" : ""}; кадры получены локальной разбивкой закадра`;
  if (event.kind === "parse_result") return `Разбор: ${p.parsed === true ? "извлечён" : p.parsed === false ? "не извлечён" : unknown}; схема ${p.schema_valid == null ? "не проверялась" : p.schema_valid ? "верна" : "нарушена"}`;
  if (event.kind === "rule_diary") {
    const stop = typeof p.stop_rule === "string" && p.stop_rule ? `; остановило правило ${p.stop_rule}` : "";
    const rules = p.rules && typeof p.rules === "object" ? Object.keys(p.rules as Record<string, unknown>).length : 0;
    return `Дневник правил кода: правил сработало ${rules}${stop}`;
  }
  if (event.kind === "node_result_verification") {
    const obs = p.observe_only === true;
    const held = obs ? "" : "; запуск удержан";
    const tail = obs ? "; режим наблюдения, запуск не остановлен" : "";
    const st = p.status === "passed" ? "подтвердила результат" : p.status === "failed" ? `не подтвердила результат${held}${tail}` : p.status === "unknown" ? `не смогла сравнить результат со входом${held}${tail}` : `статус ${p.status ?? "неизвестен"}`;
    return `Нативная проверка ${st}`;
  }
  if (event.kind === "node_error" && typeof p.rule === "string" && p.rule) return `Ошибка исполнителя: результат остановило правило ${p.rule}`;
  if (event.kind === "domain_check") return `Вердикт: ${p.verdict ?? unknown}; качество модели не проверено`;
  if (event.kind === "node_change") {
    const transitions = p.transitions as {from: string; to: string; initiator?: string}[] | undefined;
    return transitions?.length ? transitions.map(t => `${NODE_STATUS_RU[t.from] ?? t.from} → ${NODE_STATUS_RU[t.to] ?? t.to} (${t.initiator ? (INITIATOR_RU[t.initiator] ?? t.initiator) : "инициатор неизвестен"})`).join("; ") : "Изменение состояния БД; инициатор неизвестен";
  }
  if (event.kind === "model_end") return `Ответ модели · ${usageLabel(p.usage as Record<string, unknown> | null)}`;
  if (event.kind === "runtime_wait") { const reason = String(p.reason ?? unknown); return `Ожидание: ${WAIT_REASONS_RU[reason] ?? reason} (${p.initiator ?? unknown})`; }
  if (event.kind === "project_control") return `Ограничение проекта: остановка пользователем ${p.user_stop === true ? "включена" : p.user_stop === false ? "снята" : unknown}`;
  if (event.kind === "media_phase" && p.phase) return `Медиа-задача перешла в фазу «${p.phase}»`;
  if (event.kind === "media_failure" && p.phase) return `Медиа-задача завершилась сбоем на фазе «${p.phase}»`;
  if (event.kind === "human_approval_pending" && p.kind) return `Ожидается решение человека: ${p.kind}`;
  if (event.kind === "planner_successor" && p.from && p.to) return `Решение планировщика о переходе: ${p.from} → ${p.to}`;
  if (event.kind === "provider_fallback" && p.provider) return `Провайдер «${p.provider}» не ответил: использован запасной путь`;
  return EVENT_KIND_LABELS[event.kind] ?? `Событие ${event.kind} (подробности в журнале)`;
}
