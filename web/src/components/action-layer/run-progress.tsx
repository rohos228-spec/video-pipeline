"use client";
import {useCallback, useEffect, useState} from "react";
import {Button} from "@/components/ui/button";
import {
  describeError, formatCredits, getPlan, isTerminal, PLAN_STATUS_RU, STEP_STATUS_RU, stopPlan, type PlanView,
} from "@/lib/action-layer";
import {cn} from "@/lib/utils";

interface Props {
  planId: string;
  onFinished: () => void;
}

export function RunProgress({planId, onFinished}: Props) {
  const [view, setView] = useState<PlanView | null>(null);
  const [error, setError] = useState("");
  const [stopping, setStopping] = useState(false);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let notified = false;
    const poll = async () => {
      let done = false;
      try {
        const next = await getPlan(planId);
        if (cancelled) return;
        setView(next); setError("");
        done = isTerminal(next.status) && !next.live;
        if (done && !notified) { notified = true; onFinished(); }
      } catch (e) {
        if (!cancelled) setError(describeError(e));
      }
      if (!cancelled && !done) timer = setTimeout(poll, 1500);
    };
    void poll();
    return () => { cancelled = true; if (timer) clearTimeout(timer); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [planId]);

  const stop = useCallback(async () => {
    setStopping(true);
    try { await stopPlan(planId); } catch (e) { setError(describeError(e)); } finally { setStopping(false); }
  }, [planId]);

  if (!view) return <p className="text-white/45">{error || "Загружаю состояние плана…"}</p>;
  const active = view.status === "running";
  return (
    <section aria-label="Выполнение плана" data-testid="action-run-progress" className="space-y-2 rounded border border-white/10 p-2">
      <div className="flex items-center gap-2">
        <h5 className="flex-1 font-semibold text-white/85">
          Выполнение: {PLAN_STATUS_RU[view.status]}
          {view.interrupted && " (прервано перезапуском)"}
        </h5>
        {active && (
          <Button size="sm" variant="destructive" className="h-7 text-xs" disabled={stopping || view.stop_requested} onClick={() => void stop()}>
            {view.stop_requested ? "Останавливаю…" : "Остановить"}
          </Button>
        )}
      </div>
      <ol className="space-y-1" aria-live="polite">
        {view.steps.map((s) => (
          <li key={s.index} className="rounded border border-white/[0.06] p-1.5">
            <div className="flex items-center gap-2">
              <span className="text-white/40">{s.index + 1}.</span>
              <span className="flex-1 text-white/85">{s.action_id}</span>
              <span className={cn(
                s.status === "failed" || s.status === "unknown" ? "text-red-300"
                  : s.status === "succeeded" ? "text-emerald-300"
                  : s.status === "running" ? "text-sky-200" : "text-white/50")}>
                {STEP_STATUS_RU[s.status]}
              </span>
            </div>
            {s.status === "running" && (
              <div className="mt-1">
                <div role="progressbar" aria-valuemin={0} aria-valuemax={100}
                  aria-valuenow={s.progress != null ? Math.round(s.progress * 100) : undefined}
                  aria-label={`Прогресс шага ${s.index + 1}`} className="h-1.5 overflow-hidden rounded bg-white/10">
                  <div className="h-full bg-sky-300/70" style={{width: `${Math.round((s.progress ?? 0) * 100)}%`}} />
                </div>
                {s.progress_note && <p className="text-white/45">{s.progress_note}</p>}
              </div>
            )}
            {(s.cost.actual != null || s.cost.high != null) && (
              <p className="text-white/45">
                Стоимость: оценка {formatCredits(s.cost.low, s.cost.high, s.cost.low != null && s.cost.high != null)}
                {s.cost.actual != null ? ` · фактически ${formatCredits(s.cost.actual, s.cost.actual)}` : " · факт пока неизвестен"}
              </p>
            )}
            {s.error && <p role="alert" className="text-red-300">Ошибка: {s.error}</p>}
            {s.status === "unknown" && <p className="text-red-300">Состояние шага неизвестно: проверьте результат вручную, автоповтора нет.</p>}
          </li>
        ))}
      </ol>
      <p className="text-white/60">
        Потрачено: {formatCredits(view.spent_credits, view.spent_credits)} из потолка {view.budget_cap_credits} кр.
      </p>
      {view.stop_reason && (
        <p role="status" className="text-amber-200">
          Причина остановки: {view.stop_reason === "kill_switch_active" ? "включён аварийный стоп оркестратора (kill_switch_active)" : view.stop_reason}
        </p>
      )}
      {view.error && <p role="alert" className="text-red-300">Ошибка плана: {view.error}</p>}
      {error && <p role="alert" className="text-red-300">{error}</p>}
    </section>
  );
}
