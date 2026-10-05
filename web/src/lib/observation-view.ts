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

export function eventLabel(event: Pick<ObservationEvent, "kind" | "payload">): string {
  const p = event.payload;
  if (event.kind === "observer_control") return `Наблюдение: ${p.action ?? "неизвестно"} (${p.initiator ?? "инициатор неизвестен"})`;
  if (event.kind === "runtime_check") return `${p.check ?? "проверка"}: ${p.status ?? "неизвестно"}`;
  if (event.kind === "parse_result" && p.contract === "native_split" && p.fallback_used === true)
    return `Разбивка: ответ модели непригоден${p.dash_rejected === true ? " (тире не покрывает закадр)" : ""}; кадры получены локальной разбивкой закадра`;
  if (event.kind === "parse_result") return `Разбор: ${p.parsed === true ? "извлечён" : p.parsed === false ? "не извлечён" : "неизвестно"}; схема ${p.schema_valid == null ? "не проверялась" : p.schema_valid ? "верна" : "нарушена"}`;
  if (event.kind === "rule_diary") {
    const stop = typeof p.stop_rule === "string" && p.stop_rule ? `; остановило правило ${p.stop_rule}` : "";
    const rules = p.rules && typeof p.rules === "object" ? Object.keys(p.rules as Record<string, unknown>).length : 0;
    return `Дневник правил кода: правил сработало ${rules}${stop}`;
  }
  if (event.kind === "node_result_verification") {
    const st = p.status === "passed" ? "подтвердила результат" : p.status === "failed" ? "не подтвердила результат; запуск удержан" : p.status === "unknown" ? "не смогла сравнить результат со входом; запуск удержан" : `статус ${p.status ?? "неизвестен"}`;
    return `Нативная проверка ${st}`;
  }
  if (event.kind === "node_error" && typeof p.rule === "string" && p.rule) return `Ошибка исполнителя: результат остановило правило ${p.rule}`;
  if (event.kind === "json_retry") return "Штатный повтор разбора JSON";
  if (event.kind === "domain_check") return `Вердикт: ${p.verdict ?? "неизвестно"}; качество модели не проверено`;
  if (event.kind === "node_change") {
    const transitions = p.transitions as {from: string; to: string; initiator?: string}[] | undefined;
    return transitions?.length ? transitions.map(t => `${t.from} → ${t.to} (${t.initiator ?? "инициатор неизвестен"})`).join("; ") : "Изменение состояния БД; инициатор неизвестен";
  }
  if (event.kind === "model_end") return `Ответ модели · ${usageLabel(p.usage as Record<string, unknown> | null)}`;
  if (event.kind === "runtime_wait") return `Ожидание: ${p.reason ?? "неизвестно"} (${p.initiator ?? "неизвестно"})`;
  if (event.kind === "project_control") return `Ограничение проекта: user_stop=${String(p.user_stop ?? "неизвестно")}`;
  const labels: Record<string, string> = {node_start:"Исполнитель начал ноду",node_end:"Исполнитель вернулся (запись данных не подтверждена)",model_start:"Запрос к модели отправлен",model_stream_event:"Событие потока",domain_check:"Вердикт проверки",data_applied:"Изменения БД записаны",apply_input:"Вход применения",planner_successor:"Решение планировщика о переходе",artifact_created:"Артефакт создан",node_input:"Вход ноды",node_output:"Выход ноды",node_error:"Ошибка исполнителя",model_error:"Ошибка запроса к модели",apply_error:"Ошибка применения",observer_stop:"Наблюдение остановлено"};
  return labels[event.kind] ?? event.kind;
}
