"use client";

import type { FleetTransfer } from "@/lib/fleet-api";
import { cn } from "@/lib/utils";

function transferTitle(t: FleetTransfer): string {
  const slug = t.slug ? ` «${t.slug}»` : "";
  const node = t.source_node ? ` · ${t.source_node}` : "";
  if (t.direction === "from_agent") {
    return `Загрузка #${t.project_id}${slug} с воркера${node}`;
  }
  if (t.direction === "to_hub") {
    return `Отправка #${t.project_id}${slug} на hub`;
  }
  return t.message || `Передача #${t.project_id}${node}`;
}

export function FleetTransferBar({ transfers }: { transfers: FleetTransfer[] }) {
  if (!transfers.length) return null;

  return (
    <div className="space-y-2 border-b border-border bg-muted/20 px-4 py-2">
      <p className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
        Передача bundle
      </p>
      {transfers.map((t) => (
        <div key={`${t.project_id}-${t.job ?? "handoff"}`} className="space-y-1">
          <div className="flex items-center justify-between gap-2 text-xs">
            <span className="min-w-0 truncate">{transferTitle(t)}</span>
            <span className="shrink-0 tabular-nums text-muted-foreground">
              {t.percent}%
              {t.total_mb ? ` · ${Math.round(t.sent_mb ?? 0)}/${Math.round(t.total_mb)} MB` : ""}
            </span>
          </div>
          {t.message ? (
            <p className="truncate text-[10px] text-muted-foreground">{t.message}</p>
          ) : null}
          <div className="h-1.5 overflow-hidden rounded-full bg-muted">
            <div
              className={cn(
                "h-full rounded-full transition-all duration-300",
                t.percent >= 100 ? "bg-green-600" : "bg-primary",
              )}
              style={{ width: `${Math.max(2, t.percent)}%` }}
            />
          </div>
        </div>
      ))}
    </div>
  );
}
