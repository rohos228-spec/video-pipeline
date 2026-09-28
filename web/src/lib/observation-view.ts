export interface ObservationEvent {
  id: number; event_id: string; project_id: number; execution_id?: string;
  node_key?: string | null; attempt_id?: string | null; request_id?: string | null;
  kind: string; db_scope?: string; created_at?: string;
  payload: Record<string, unknown>;
}

export function usageLabel(usage: Record<string, unknown> | null | undefined): string {
  if (!usage) return "usage неизвестен";
  const fields = ["input_tokens", "output_tokens", "total_tokens", "credits_consumed"];
  const known = fields.filter(key => typeof usage[key] === "number");
  return known.length ? known.map(key => `${key}: ${usage[key]}`).join(" · ") : "usage неизвестен";
}

export function mergeEvents(old: ObservationEvent[], incoming: ObservationEvent[], projectId: number): ObservationEvent[] {
  const unique = new Map<string, ObservationEvent>();
  for (const event of [...old, ...incoming]) if (event.project_id === projectId) unique.set(event.event_id, event);
  return [...unique.values()].sort((a, b) => a.id - b.id);
}

export function eventLabel(event: Pick<ObservationEvent, "kind" | "payload">): string {
  const p = event.payload;
  if (event.kind === "runtime_check") return `${p.check ?? "проверка"}: ${p.status ?? "неизвестно"}`;
  if (event.kind === "parse_result") return `Разбор: ${p.parsed === true ? "извлечён" : p.parsed === false ? "не извлечён" : "неизвестно"}; schema ${p.schema_valid == null ? "not_checked" : String(p.schema_valid)}`;
  if (event.kind === "json_retry") return "Штатный JSON retry";
  if (event.kind === "node_change") {
    const transitions = p.transitions as {from: string; to: string; initiator?: string}[] | undefined;
    return transitions?.length ? transitions.map(t => `${t.from} → ${t.to} (${t.initiator ?? "инициатор неизвестен"})`).join("; ") : "Изменение состояния БД; инициатор неизвестен";
  }
  if (event.kind === "model_end") return `Ответ модели · ${usageLabel(p.usage as Record<string, unknown> | null)}`;
  if (event.kind === "runtime_wait") return `Ожидание: ${p.reason ?? "неизвестно"} (${p.initiator ?? "неизвестно"})`;
  if (event.kind === "project_control") return `Ограничение проекта: user_stop=${String(p.user_stop ?? "неизвестно")}`;
  const labels: Record<string, string> = {node_start:"Worker начал ноду",node_end:"Worker вернулся (не подтверждение commit)",model_start:"Запрос тестового/настроенного транспорта",model_stream_event:"Событие потока",domain_check:"Вердикт проверки",data_applied:"Изменения БД committed outbox",apply_input:"Вход применения",planner_successor:"Решение planner о переходе",artifact_created:"Артефакт создан",node_input:"Вход ноды",node_output:"Выход ноды",node_error:"Ошибка worker",model_error:"Ошибка транспорта",apply_error:"Ошибка применения",observer_stop:"Наблюдение остановлено"};
  return labels[event.kind] ?? event.kind;
}
