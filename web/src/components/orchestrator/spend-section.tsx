"use client";
import {useEffect, useState} from "react";
import {http} from "@/lib/api";
import {cn} from "@/lib/utils";

// Read-only local spend totals versus caps (server: app/services/spend_guard.py). 0 cap = unlimited.
interface SpendState {
  caps: {daily: number; project: number; run: number};
  daily_spent: number; project_spent: number | null; run_spent: number | null; run_id: string | null;
  runs?: SpendRun[];
}
interface SpendRun {run_id: string; group_id: string; enabled: boolean; held: boolean; created_at: string | null; run_spent: number | null}
interface ProviderTask {
  task_id: string; key: string; provider: string; api: string; model: string; status?: string;
  submitted_at: string; updated_at?: string; est_credits: number | null; est_usd: number | null; events: number; note?: string;
}

const runLabel = (r: SpendRun) =>
  `${(r.created_at ?? "").slice(0, 16).replace("T", " ") || "время неизвестно"} · ${r.run_id.slice(0, 8)} · ${r.group_id} · ${
    r.enabled ? (r.held ? "на паузе" : "идёт") : "закрыт"} · ${usd(r.run_spent)}`;

const usd = (v: number | null) => (v === null ? "—" : `$${v.toFixed(2)}`);
const cap = (v: number) => (v > 0 ? `$${v.toFixed(2)}` : "без лимита");

const TASK_STATUS_RU: Record<string, string> = {
  submitted: "отправлена", pending: "в ожидании", waiting: "в ожидании", queuing: "в очереди", generating: "генерируется",
  succeeded: "готова", success: "готова", failed: "ошибка", unknown: "исход неизвестен", cancelled: "отменена",
};
const taskStatus = (s?: string) => (s ? (TASK_STATUS_RU[s] ?? s) : "статус неизвестен");

/** Доля лимита и состояние: ниже 80% обычное, от 80% предупреждение, от 100% превышен. */
export function capState(spent: number | null, limit: number): {pct: number | null; level: "none" | "ok" | "warn" | "over"} {
  if (spent === null || !(limit > 0)) return {pct: null, level: "none"};
  const pct = (spent / limit) * 100;
  return {pct, level: pct >= 100 ? "over" : pct >= 80 ? "warn" : "ok"};
}

function Row({label, spent, limit}: {label: string; spent: number | null; limit: number}) {
  const {pct, level} = capState(spent, limit);
  const left = spent !== null && limit > 0 ? limit - spent : null;
  return (
    <li className="space-y-0.5">
      <div className="flex flex-wrap justify-between gap-x-3">
        <span>{label}: {usd(spent)} из {cap(limit)}</span>
        <span className={cn("text-white/50", level === "warn" && "text-amber-200", level === "over" && "text-red-300")}>
          {level === "over" ? `лимит превышен на ${usd(-(left as number))}` : left !== null ? `осталось ${usd(left)}` : ""}
          {pct !== null ? ` · ${Math.round(pct)}%` : ""}
        </span>
      </div>
      {pct !== null && (
        <div role="progressbar" aria-label={`Расход: ${label}`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.min(100, Math.round(pct))}
          className="h-1.5 overflow-hidden rounded bg-white/10">
          <div className={cn("h-full", level === "over" ? "bg-red-400/80" : level === "warn" ? "bg-amber-300/80" : "bg-sky-300/70")}
            style={{width: `${Math.min(100, pct)}%`}}/>
        </div>
      )}
    </li>
  );
}

export function SpendSection({projectId}: {projectId: number | null}) {
  const project = projectId ?? 0;
  const [state, setState] = useState<SpendState | null>(null);
  const [tasks, setTasks] = useState<ProviderTask[] | null>(null);
  const [error, setError] = useState(false);
  const [pickedRun, setPickedRun] = useState("");

  useEffect(() => { setPickedRun(""); }, [project]);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const next = await http<SpendState>(`/api/projects/${project}/observations/spend${pickedRun ? `?run_id=${encodeURIComponent(pickedRun)}` : ""}`);
        if (!cancelled) { setState(next); setError(false); }
      } catch {
        if (!cancelled) setError(true);
      }
      try {
        const t = await http<{tasks: ProviderTask[]}>(`/api/projects/${project}/observations/provider-tasks`);
        if (!cancelled) setTasks(t.tasks);
      } catch {
        if (!cancelled) setTasks(null);
      }
      if (!cancelled) timer = setTimeout(poll, 15000);
    };
    void poll();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [project, pickedRun]);

  return (
    <section data-testid="spend-panel" className="mt-3 rounded border border-white/10 p-2 space-y-1.5 text-xs text-white/80">
      <h4>Расходы надзорного слоя (локальный учёт)</h4>
      {error && <p className="text-amber-200">Данные о расходах недоступны.</p>}
      {state && (
        <ul className="space-y-1.5 text-white/70">
          <Row label="Сегодня (все проекты)" spent={state.daily_spent} limit={state.caps.daily}/>
          <Row label="Проект" spent={state.project_spent} limit={state.caps.project}/>
          <Row label={state.run_id ? `Запуск ${state.run_id.slice(0, 8)}` : "Запуск"} spent={state.run_spent} limit={state.caps.run}/>
        </ul>
      )}
      {state?.runs && state.runs.length > 0 && (
        <label className="flex flex-wrap items-center gap-2 text-white/60">Запуск
          <select aria-label="Запуск для расчёта расхода" value={state.run_id ?? ""} onChange={(e) => setPickedRun(e.target.value)}
            className="max-w-full rounded border border-white/15 bg-[#161616] p-1">
            {!state.run_id && <option value="">не выбран</option>}
            {state.runs.map((r) => <option key={r.run_id} value={r.run_id}>{runLabel(r)}</option>)}
          </select>
        </label>
      )}
      {state && !state.run_id && <p className="text-white/45">Нет активного управляемого запуска: расход запуска не считается.{state.runs?.length ? " Выберите прошлый запуск, чтобы увидеть его расход." : ""}</p>}
      {state && <p className="text-white/40">Лимит 0 означает «без лимита». Превышение лимита блокирует новые платные запросы до отправки.</p>}
      {tasks && (
        <details data-testid="provider-tasks">
          <summary className="cursor-pointer text-white/55">Задачи провайдера медиа · {tasks.length}</summary>
          {!tasks.length && <p className="mt-1 text-white/45">Задач пока нет.</p>}
          <ul className="mt-1 space-y-1">
            {tasks.map((t) => (
              <li key={t.task_id} className="rounded border border-white/[0.08] p-1.5 break-words">
                <p>{t.provider || "провайдер неизвестен"} · {t.model || "модель неизвестна"} · <span className="text-white/90">{taskStatus(t.status)}</span></p>
                <p className="text-white/50">
                  {t.key ? `${t.key} · ` : ""}{t.task_id} · событий: {t.events}
                  {t.est_credits != null ? ` · оценка ${t.est_credits} кр.` : ""}{t.est_usd != null ? ` (~$${t.est_usd.toFixed(2)})` : ""}
                  {t.updated_at ? ` · обновлено ${t.updated_at.slice(0, 19).replace("T", " ")}` : ""}
                </p>
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}
