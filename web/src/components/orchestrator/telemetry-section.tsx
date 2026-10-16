"use client";
import {useEffect, useState} from "react";
import {Button} from "@/components/ui/button";
import {observations, type TelemetrySummary} from "@/lib/observations";
import {INCIDENT_TYPE_RU} from "@/lib/diagnosis-view";

const WINDOWS: {label: string; hours: number | undefined}[] = [
  {label: "1 ч", hours: 1},
  {label: "24 ч", hours: 24},
  {label: "7 дней", hours: 168},
  {label: "всё время", hours: undefined},
];

type CostExtra = {
  by_provider?: Record<string, {calls: number; cost_usd: number; unknown_cost_calls: number}>;
  unknown_cost_calls?: number;
};

function CostNotes({cost}: {cost: TelemetrySummary["cost"] & CostExtra}) {
  const providers = Object.keys(cost.by_provider ?? {});
  const onlyVibecode = providers.length > 0 && providers.every((p) => p === "vibecode");
  const vibecode = cost.by_provider?.vibecode;
  return (
    <>
      {!onlyVibecode && (
        <p className="text-white/40">
          Оценка по тарифам, откалиброванным по списанию кредитов Kie (кэш учтён); возможны отклонения для других форм запросов.
        </p>
      )}
      {vibecode && (
        <p className="text-white/40">
          Vibecode: ${vibecode.cost_usd.toFixed(4)} ({vibecode.calls} запрос(ов)) — оценка по прайсу из снимка каталога
          (файл-снимок каталога, дата снимка неизвестна), не откалибрована по списанию. Фактический баланс
          в Vibecode может отличаться.
        </p>
      )}
      {(cost.unknown_cost_calls ?? 0) > 0 && (
        <p className="text-white/40">
          У {cost.unknown_cost_calls} запрос(ов) стоимость неизвестна: провайдер не вернул usage или модели нет в прайсе
          (в сумму они входят как $0).
        </p>
      )}
    </>
  );
}

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
      .catch(() => {});

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
        Сводка по уже записанному журналу (наблюдение, диагностика, цикл решений, действия) за весь проект,
        а не по одному наблюдению. Ничего не изменяет и не запускает.
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
                      {INCIDENT_TYPE_RU[type] ?? type}: {count}
                    </li>
                  ))}
              </ul>
            )}
          </div>
          <div className="rounded border border-white/10 p-2">
            <strong>Цикл решений</strong>
            <p>
              Завершённых циклов: {summary.decisions.terminal_count} / {summary.decisions.total} ·
              доля случаев, переданных человеку: {pct(summary.decisions.human_review_rate)}
            </p>
            <p>
              Среднее число шагов модели: {summary.decisions.avg_model_steps?.toFixed(2) ?? "неизвестно"} из
              максимум {summary.decisions.max_model_steps ?? "неизвестно"} (предохранитель от зацикливания)
            </p>
          </div>
          <div className="rounded border border-white/10 p-2">
            <strong>Задержка запросов к модели</strong>
            {Object.keys(summary.node_latency.by_node).length === 0 && (
              <p className="text-white/50">Завершённых запросов к модели (начало и конец) не записано.</p>
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
            <strong>Лимит запросов по запускам</strong>
            {Object.keys(summary.budgets_by_execution).length === 0 && (
              <p className="text-white/50">Управляемые запуски для этого проекта ещё не включались.</p>
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
              Лимит запросов учитывает цикл решений, исполнителей и медиа; это не денежный потолок.
            </p>
          </div>
          <div className="rounded border border-white/10 p-2">
            <strong>Стоимость запросов к языковым моделям</strong>
            {summary.cost.total_calls === 0 ? (
              <p className="text-white/50">
                Пока не отслежено ни одного реального вызова (нет ключей доступа или ни один запрос ещё не ушёл).
              </p>
            ) : (
              <>
                <p>
                  ${summary.cost.total_cost_usd.toFixed(4)} за {summary.cost.total_calls} запрос(ов)
                  {summary.cost.failed_calls > 0 ? ` (${summary.cost.failed_calls} с ошибкой)` : ""}
                </p>
                <p className="text-white/60">
                  Токены: {summary.cost.total_prompt_tokens} на входе / {summary.cost.total_completion_tokens}{" "}
                  на выходе
                  {summary.cost.total_cached_tokens > 0 ? ` / ${summary.cost.total_cached_tokens} из кэша` : ""}
                </p>
                <CostNotes cost={summary.cost} />
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
              Цена записана счётчиком расходов по тарифу на момент вызова; это не ценовое предложение
              провайдера.
            </p>
          </div>
        </div>
      )}
    </section>
  );
}
