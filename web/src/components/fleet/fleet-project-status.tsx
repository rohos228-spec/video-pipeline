"use client";

import { Badge } from "@/components/ui/badge";
import { formatProjectStatus } from "@/lib/format-labels";
import { isProjectRunningStatus } from "@/lib/project-running";
import { cn } from "@/lib/utils";
import { Loader2 } from "lucide-react";

function statusVariant(
  s: string,
): "default" | "success" | "warning" | "destructive" | "info" | "muted" {
  if (s === "new") return "muted";
  if (s === "paused" || s === "failed") return "destructive";
  if (s === "published" || s === "assembled") return "success";
  if (s.endsWith("_ready") || s === "audio_ready" || s === "videos_ready" || s === "music_ready") {
    return "info";
  }
  if (isProjectRunningStatus(s)) return "warning";
  return "default";
}

export function FleetProjectStatus({ status }: { status: string }) {
  const running = isProjectRunningStatus(status);
  const variant = statusVariant(status);

  return (
    <div className="flex min-w-0 items-center gap-1.5">
      {running ? (
        <span className="inline-flex items-center gap-1 rounded-full bg-warning/15 px-1.5 py-0.5 text-[9px] text-warning">
          <Loader2 className="h-3 w-3 animate-spin" />
          в работе
        </span>
      ) : null}
      <Badge
        variant={variant}
        className="h-[18px] max-w-full truncate border-white/[0.06] px-1.5 text-[9px] font-normal tracking-normal normal-case shadow-none"
      >
        {formatProjectStatus(status)}
      </Badge>
    </div>
  );
}

export function FleetProjectRow({
  slug,
  topic,
  status,
  montageReady,
  montageQueued,
  montageQueuePosition,
  onOpen,
  onMontage,
  exportHint,
}: {
  slug: string;
  topic?: string | null;
  status: string;
  montageReady?: boolean;
  montageQueued?: boolean;
  montageQueuePosition?: number | null;
  onOpen: () => void;
  onMontage?: () => void;
  exportHint?: string;
}) {
  const running = isProjectRunningStatus(status);
  const showExportBtn = !montageQueued && onMontage;
  const exportLabel = montageReady ? "На монтаж" : "На hub";

  return (
    <div
      className={cn(
        "rounded-lg border px-2.5 py-2 text-xs transition-colors",
        running ? "border-warning/30 bg-warning/[0.04]" : "border-border/60 bg-card/20",
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <div className="truncate font-medium">{slug}</div>
          {topic ? (
            <div className="mt-0.5 truncate text-[10px] text-muted-foreground">{topic}</div>
          ) : null}
          <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
            <FleetProjectStatus status={status} />
            {montageQueued ? (
              <Badge
                variant="info"
                className="h-[18px] border-white/[0.06] px-1.5 text-[9px] font-normal tracking-normal normal-case shadow-none"
              >
                {montageQueuePosition ? `очередь #${montageQueuePosition}` : "в очереди"}
              </Badge>
            ) : null}
          </div>
        </div>
        <div className="flex shrink-0 flex-col gap-1">
          <button
            type="button"
            onClick={onOpen}
            className="rounded-md border border-primary/30 bg-primary/10 px-2 py-1 text-[10px] font-medium text-primary hover:bg-primary/20"
          >
            Перейти
          </button>
          {exportHint && !showExportBtn ? (
            <span className="max-w-[88px] text-center text-[9px] text-muted-foreground">{exportHint}</span>
          ) : null}
          {showExportBtn ? (
            <button
              type="button"
              onClick={onMontage}
              className="rounded-md border border-border bg-muted/40 px-2 py-1 text-[10px] hover:bg-muted"
            >
              {exportLabel}
            </button>
          ) : null}
        </div>
      </div>
    </div>
  );
}
