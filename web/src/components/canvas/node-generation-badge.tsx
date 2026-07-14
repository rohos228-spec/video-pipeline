"use client";

import {
  AlertCircle,
  CheckCircle2,
  Circle,
  Hourglass,
  Loader2,
  MinusCircle,
} from "lucide-react";
import type { NodeRunStatus } from "@/lib/types";
import { cn } from "@/lib/utils";

const STATUS_VISUAL: Record<
  NodeRunStatus,
  { icon: typeof Circle; className: string; connector: string; label: string }
> = {
  pending: {
    icon: Circle,
    className: "border-muted-foreground/50 bg-muted/90 text-muted-foreground",
    connector: "border-muted-foreground/50",
    label: "Ожидание",
  },
  queued: {
    icon: Hourglass,
    className: "border-sky-400/70 bg-sky-500/20 text-sky-300",
    connector: "border-sky-400/60",
    label: "В очереди",
  },
  running: {
    icon: Loader2,
    className: "border-primary/60 bg-primary/20 text-primary shadow-primary/20",
    connector: "border-primary/50",
    label: "Генерация",
  },
  waiting_hitl: {
    icon: Hourglass,
    className: "border-amber-400/70 bg-amber-500/25 text-amber-300",
    connector: "border-amber-400/70",
    label: "Проверка",
  },
  done: {
    icon: CheckCircle2,
    className: "border-emerald-500/60 bg-emerald-500/20 text-emerald-400",
    connector: "border-emerald-500/60",
    label: "Завершено",
  },
  failed: {
    icon: AlertCircle,
    className: "border-destructive/60 bg-destructive/20 text-destructive",
    connector: "border-destructive/60",
    label: "Ошибка",
  },
  skipped: {
    icon: MinusCircle,
    className: "border-muted-foreground/40 bg-muted/70 text-muted-foreground",
    connector: "border-muted-foreground/40",
    label: "Пропуск",
  },
};

/** Индикатор статуса генерации над нодой (только отображение, без клика). */
export function NodeGenerationBadge({
  status,
}: {
  nodeType?: string;
  status: NodeRunStatus;
  progress?: number;
  progressText?: string | null;
  error?: string | null;
  attempts?: number;
  projectStatus?: string | null;
  generationActive?: boolean;
  autoMode?: boolean;
  hitlList?: unknown[];
  onOpenHitl?: (e: React.MouseEvent) => void;
}) {
  const visual = STATUS_VISUAL[status];
  const Icon = visual.icon;

  return (
    <>
      <div
        className={cn(
          "pointer-events-none absolute -top-5 left-1/2 z-10 h-5 w-px -translate-x-1/2 border-l-2 border-dashed",
          visual.connector,
        )}
      />
      <div
        className={cn(
          "pointer-events-none absolute -top-12 left-1/2 z-20 flex h-7 w-7 -translate-x-1/2 items-center justify-center rounded-full border-2 shadow-md",
          visual.className,
          status === "running" && "animate-pulse",
        )}
        title={`Статус генерации: ${visual.label}`}
        aria-hidden
      >
        <Icon className={cn("h-4 w-4", status === "running" && "animate-spin")} />
      </div>
    </>
  );
}
