"use client";
import {useEffect, useState} from "react";
import {Button} from "@/components/ui/button";
import {describeError, EVENT_RU, formatCredits, getHistory, type HistoryItem} from "@/lib/action-layer";

const LIMIT = 15;

function when(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso.endsWith("Z") || /[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso}Z`);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString("ru-RU");
}

export function HistoryList({refreshKey}: {refreshKey: number}) {
  const [items, setItems] = useState<HistoryItem[] | null>(null);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState("");
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let cancelled = false;
    getHistory(LIMIT)
      .then((r) => { if (!cancelled) { setItems(r.items); setTotal(r.total); setError(""); } })
      .catch((e) => { if (!cancelled) setError(describeError(e)); });
    return () => { cancelled = true; };
  }, [refreshKey, tick]);

  return (
    <details className="text-[11px]" data-testid="action-history">
      <summary className="cursor-pointer text-white/60 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary">
        Журнал действий{items ? ` (последние ${items.length} из ${total})` : ""}
      </summary>
      <div className="mt-1.5 space-y-1">
        <Button size="sm" variant="ghost" className="h-6 text-[11px]" onClick={() => setTick((t) => t + 1)}>Обновить</Button>
        {error && <p role="alert" className="text-red-300">{error}</p>}
        {items && items.length === 0 && <p className="text-white/40">Записей пока нет.</p>}
        {items?.map((h) => (
          <div key={h.id} className="rounded border border-white/[0.06] p-1.5">
            <p className="text-white/80">
              {EVENT_RU[h.event] ?? h.event}{h.action_id ? ` · ${h.action_id}` : ""}
              {h.step_index != null ? ` (шаг ${h.step_index + 1})` : ""}
            </p>
            <p className="text-white/45">{when(h.created_at)} · {h.actor}{h.outcome ? ` · итог: ${h.outcome}` : ""}</p>
            {h.request_text && <p className="text-white/45">Запрос: {h.request_text}</p>}
            {(h.cost_estimate.high != null || h.cost_recorded != null) && (
              <p className="text-white/45">
                Стоимость: оценка {formatCredits(h.cost_estimate.low, h.cost_estimate.high, h.cost_estimate.low != null && h.cost_estimate.high != null)}
                {" · "}факт {h.cost_recorded != null ? formatCredits(h.cost_recorded, h.cost_recorded) : "неизвестно"}
              </p>
            )}
            {h.error && <p className="text-red-300">Ошибка: {h.error}</p>}
          </div>
        ))}
      </div>
    </details>
  );
}
