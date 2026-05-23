"use client";

import { Handle, Position, type NodeProps } from "@xyflow/react";
import { CheckCircle2, Circle, Loader2, AlertCircle, Hourglass, MinusCircle } from "lucide-react";
import type { NodeRunStatus, NodeType } from "@/lib/types";
import { getNodeSpec } from "@/lib/node-catalog";
import { getNodeIcon } from "@/lib/node-icons";
import { cn } from "@/lib/utils";

export interface PipelineNodeData extends Record<string, unknown> {
  nodeKey: string;
  type: NodeType;
  status: NodeRunStatus;
  progress: number;
  progressText: string | null;
  error: string | null;
  attempts: number;
}

export function PipelineNode({ data, selected }: NodeProps) {
  const d = data as PipelineNodeData;
  const spec = getNodeSpec(d.type);
  const Icon = getNodeIcon(spec.iconKey);

  const statusConfig = STATUS_CONFIG[d.status];
  const StatusIcon = statusConfig.icon;

  const running = d.status === "running";
  const ringClass = selected ? "ring-2 ring-primary/60 ring-offset-2 ring-offset-background" : "";

  return (
    <div
      className={cn(
        "group relative w-[244px] overflow-hidden rounded-xl border border-border bg-card shadow-sm transition-all duration-150",
        "hover:-translate-y-0.5 hover:border-primary/60 hover:shadow-lg hover:shadow-primary/10",
        running && "glow-running border-primary/50",
        d.status === "done" && "border-success/40",
        d.status === "failed" && "border-destructive/50",
        d.status === "waiting_hitl" && "border-warning/50 pulse-soft",
        ringClass,
      )}
      style={{ borderLeftColor: `hsl(${spec.accent})`, borderLeftWidth: 3 }}
    >
      <Handle
        type="target"
        position={Position.Left}
        id="in"
        style={{
          background: "hsl(var(--muted-foreground))",
          width: 7,
          height: 7,
          border: "2px solid hsl(var(--card))",
        }}
      />
      <Handle
        type="source"
        position={Position.Right}
        id="out"
        style={{
          background: "hsl(var(--muted-foreground))",
          width: 7,
          height: 7,
          border: "2px solid hsl(var(--card))",
        }}
      />
      <div className="flex items-start gap-2.5 px-3.5 pb-2.5 pt-3">
        <div
          className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md"
          style={{ background: `hsl(${spec.accent} / 0.15)`, color: `hsl(${spec.accent})` }}
        >
          <Icon className="h-4 w-4" />
        </div>
        <div className="flex min-w-0 flex-1 flex-col leading-tight">
          <div className="flex items-center justify-between gap-2">
            <span className="truncate text-[13px] font-medium">{spec.label}</span>
            <span
              className={cn(
                "flex h-[18px] items-center gap-1 rounded-md px-1.5 text-[9px] font-medium uppercase tracking-wider",
                statusConfig.bg,
                statusConfig.text,
              )}
              title={statusConfig.label}
            >
              {running ? (
                <Loader2 className="h-2.5 w-2.5 animate-spin" />
              ) : (
                <StatusIcon className="h-2.5 w-2.5" />
              )}
              <span>{statusConfig.label}</span>
            </span>
          </div>
          <span className="mt-0.5 line-clamp-2 text-[10.5px] leading-snug text-muted-foreground">
            {spec.description}
          </span>
        </div>
      </div>
      {d.progressText && d.status === "running" && (
        <div className="border-t border-border bg-muted/30 px-3 py-1 font-mono text-[10px] text-muted-foreground">
          {d.progressText}
        </div>
      )}
      {d.error && d.status === "failed" && (
        <div className="border-t border-destructive/30 bg-destructive/10 px-3 py-1 font-mono text-[10px] text-destructive">
          {truncate(d.error, 80)}
        </div>
      )}
      {d.status === "running" && d.progress > 0 && (
        <div className="absolute bottom-0 left-0 h-0.5 bg-primary transition-all" style={{ width: `${d.progress}%` }} />
      )}
    </div>
  );
}

const STATUS_CONFIG: Record<NodeRunStatus, {
  icon: typeof Circle;
  label: string;
  bg: string;
  text: string;
}> = {
  pending: {
    icon: Circle,
    label: "ждёт",
    bg: "bg-muted",
    text: "text-muted-foreground",
  },
  running: {
    icon: Loader2,
    label: "выполняется",
    bg: "bg-primary/15",
    text: "text-primary",
  },
  waiting_hitl: {
    icon: Hourglass,
    label: "ждёт HITL",
    bg: "bg-warning/15",
    text: "text-warning",
  },
  done: {
    icon: CheckCircle2,
    label: "готово",
    bg: "bg-success/15",
    text: "text-success",
  },
  failed: {
    icon: AlertCircle,
    label: "ошибка",
    bg: "bg-destructive/15",
    text: "text-destructive",
  },
  skipped: {
    icon: MinusCircle,
    label: "пропущено",
    bg: "bg-muted",
    text: "text-muted-foreground",
  },
};

function truncate(s: string, n: number): string {
  if (s.length <= n) return s;
  return s.slice(0, n - 1) + "…";
}
