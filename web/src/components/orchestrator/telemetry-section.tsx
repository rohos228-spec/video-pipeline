"use client";
import {useEffect, useState} from "react";
import {Button} from "@/components/ui/button";
import {observations, type TelemetrySummary} from "@/lib/observations";

const WINDOWS: {label: string; hours: number | undefined}[] = [
  {label: "1 ч", hours: 1},
  {label: "24 ч", hours: 24},
  {label: "7 дней", hours: 168},
  {label: "всё время", hours: undefined},
];

function pct(x: number | null): string {
  return x == null ? "неизвестно" : `${Math.round(x * 100)}%`;
}

export function TelemetrySection({projectId}: {projectId: number | null}) {
  const [summary, setSummary] = useState<TelemetrySummary | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [windowHours, setWindowHours] = useState<number | undefined>(24);

  useEffect(() => {
    setSummary(null);
    setError("");
    if (projectId == null) return;
    const controller = new AbortController();
    let cancelled = false;
    setBusy(true);

    // Initial paint comes from the plain GET (fast first render); live
    // updates afterward come from the SSE stream (same aggregation,
    // app/services/telemetry.py::project_telemetry -- the stream just
    // re-pushes it on a ~2s interval, deduplicated server-side). If the
    // stream itself fails to connect/parses nothing, the initial GET
    // result still stands -- this never regresses to a blank panel.
    void observations
      .telemetry(projectId, windowHours)
      .then((data) => {
        if (cancelled) return;
        setSummary(data);
        setError("");
      })
      .catch(() => {
        if (cancelled) return;
        setError("Телеметрия недоступна; сохранённые значения могут быть устаревшими.");
      })
      .finally(() => {
        if (!cancelled) setBusy(false);
      });

    void observations
      .telemetryStream(projectId, windowHours, (data) => {
        if (cancelled) return;
        setSummary(data);
        setError("");
        setBusy(false);
      }, controller.signal)
      .catch(() => {
        // Stream itself failed (not an aborted cleanup) -- the one-shot GET
        // above already rendered something; no further action needed here,
        // live updates just won't arrive until the next window/project change.
      });

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [projectId, windowHours]);

  if (projectId == null) return null;

  return (
    <section data-testid="telemetry-panel" className="space-y-2 rounded border border-white/10 p-2 text-xs">
      <div className="flex flex-wrap items-center gap-2">
        <strong>Телеметрия · проект #{projectId}</strong>
        <div className="flex gap-1">
          {WINDOWS.map((w) => (
            <Button
              key={w.label}
              size="sm"
              variant={windowHours === w.hours ? "default" : "outline"}
              className="h-6 px-2 text-[11px]"
              disabled={busy}
              onClick={() => setWindowHours(w.hours)}
            >
              {w.label}
            </Button>
          ))}
        </div>
        {busy && <span className="text-white/40">обновление…</span>}
      </div>
      <p className="text-white/40">
        Агрегация по уже записанному журналу (Observe/Diagnose/Decision/Actions) за весь проект,
        а не одно наблюдение. Ничего не изменяет и не запускает.
      </p>
      {error && (
        <p role="alert" className="text-amber-200">
          {error}
        </p>
      )}
      {summary && (
        <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
          <div className="rounded border border-white/10 p-2">
            <strong>Инциденты</strong>
            <p>
              Всего: {summary.incidents.total} · требуют внимания: {summary.incidents.blocking_open} ·
              подтверждённо исправлено: {summary.incidents.resolved_confirmed}
            </p>
            {Object.keys(summary.incidents.by_type).length > 0 && (
              <ul className="mt-1 space-y-0.5 text-white/60">
                {Object.entries(summary.incidents.by_type)
                  .sort((a, b) => b[1] - a[1])
                  .map(([type, count]) => (
                    <li key={type}>
                      {type}: {count}
                    </li>
                  ))}
              </ul>
            )}
          </div>
          <div className="rounded border border-white/10 p-2">
            <strong>Decision Engine</strong>
            <p>
              Завершённых циклов: {summary.decisions.terminal_count} / {summary.decisions.total} ·
              доля REQUIRES_HUMAN_INTERVENTION: {pct(summary.decisions.human_review_rate)}
            </p>
            <p>
              Среднее число шагов модели: {summary.decisions.avg_model_steps?.toFixed(2) ?? "неизвестно"} из
              максимум {summary.decisions.max_model_steps ?? "неизвестно"} (circuit breaker)
            </p>
          </div>
          <div className="rounded border border-white/10 p-2">
            <strong>Задержка запросов к модели</strong>
            {Object.keys(summary.node_latency.by_node).length === 0 && (
              <p className="text-white/50">Завершённых пар model_start/model_end не записано.</p>
            )}
            <ul className="space-y-0.5 text-white/60">
              {Object.entries(summary.node_latency.by_node).map(([node, stats]) => (
                <li key={node}>
                  {node}: {stats.count} запрос(ов), ~{stats.avg_seconds.toFixed(1)} с (
                  {stats.min_seconds.toFixed(1)}–{stats.max_seconds.toFixed(1)} с)
                </li>
              ))}
            </ul>
            {summary.node_latency.unmatched_requests.length > 0 && (
              <p className="mt-1 text-amber-200">
                Без завершения: {summary.node_latency.unmatched_requests.length} запрос(ов) — не скрыто,
                это операционно значимо.
              </p>
            )}
          </div>
          <div className="rounded border border-white/10 p-2">
            <strong>Бюджет по execution</strong>
            {Object.keys(summary.budgets_by_execution).length === 0 && (
              <p className="text-white/50">Managed execution для этого проекта ещё не включались.</p>
            )}
            <ul className="space-y-0.5 text-white/60">
              {Object.entries(summary.budgets_by_execution).map(([execId, budget]) => (
                <li key={execId}>
                  {execId.slice(0, 8)}: {budget.requests_used}/{budget.request_limit} (
                  {pct(budget.utilization)}){budget.held ? " · на паузе" : ""}
                </li>
              ))}
            </ul>
            <p className="mt-1 text-white/40">
              Лимит запросов учитывает Decision/worker/media; это не денежный потолок.
            </p>
          </div>
          <div className="rounded border border-white/10 p-2">
            <strong>Стоимость LLM-вызовов</strong>
            {summary.cost.total_calls === 0 ? (
              <p className="text-white/50">
                Пока не отслежено ни одного реального вызова (нет credentials / ни один запрос ещё не ушёл).
              </p>
            ) : (
              <>
                <p>
                  ${summary.cost.total_cost_usd.toFixed(4)} за {summary.cost.total_calls} запрос(ов)
                  {summary.cost.failed_calls > 0 ? ` (${summary.cost.failed_calls} с ошибкой)` : ""}
                </p>
                <p className="text-white/60">
                  Токены: {summary.cost.total_prompt_tokens} prompt / {summary.cost.total_completion_tokens}{" "}
                  completion
                  {summary.cost.total_cached_tokens > 0 ? ` / ${summary.cost.total_cached_tokens} cached` : ""}
                </p>
                <ul className="mt-1 space-y-0.5 text-white/60">
                  {Object.entries(summary.cost.by_model).map(([model, stats]) => (
                    <li key={model}>
                      {model}: ${stats.cost_usd.toFixed(4)} ({stats.calls} запрос(ов))
                    </li>
                  ))}
                </ul>
              </>
            )}
            <p className="mt-1 text-white/40">
              Цена по тарифу api-tracker на момент вызова ({summary.cost.source}); не ценовое предложение
              провайдера.
            </p>
          </div>
        </div>
      )}
    </section>
  );
}
