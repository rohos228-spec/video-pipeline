"use client";
import {useEffect, useState} from "react";
import {http} from "@/lib/api";

// Read-only local spend totals versus caps (server: app/services/spend_guard.py). 0 cap = unlimited.
interface SpendState {
  caps: {daily: number; project: number; run: number};
  daily_spent: number; project_spent: number | null; run_spent: number | null; run_id: string | null;
}

const usd = (v: number | null) => (v === null ? "—" : `$${v.toFixed(2)}`);
const cap = (v: number) => (v > 0 ? `$${v.toFixed(2)}` : "без лимита");

export function SpendSection({projectId}: {projectId: number | null}) {
  const project = projectId ?? 0;
  const [state, setState] = useState<SpendState | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const next = await http<SpendState>(`/api/projects/${project}/observations/spend`);
        if (!cancelled) { setState(next); setError(false); }
      } catch {
        if (!cancelled) setError(true);
      }
      if (!cancelled) timer = setTimeout(poll, 15000);
    };
    void poll();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [project]);

  return (
    <section data-testid="spend-panel" className="mt-3 rounded border border-white/10 p-2 space-y-1 text-xs text-white/80">
      <h4>Расходы надзорного слоя (локальный учёт)</h4>
      {error && <p className="text-amber-200">Данные о расходах недоступны.</p>}
      {state && (
        <ul className="text-white/60">
          <li>Сегодня: {usd(state.daily_spent)} из {cap(state.caps.daily)}</li>
          <li>Проект: {usd(state.project_spent)} из {cap(state.caps.project)}</li>
          <li>Запуск: {usd(state.run_spent)} из {cap(state.caps.run)}</li>
        </ul>
      )}
    </section>
  );
}
