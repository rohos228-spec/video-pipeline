"use client";

import { Handle, Position, type NodeProps } from "@xyflow/react";
import {
  CheckCircle2,
  Circle,
  Loader2,
  AlertCircle,
  Hourglass,
  MinusCircle,
  Play,
  Unlink,
  Trash2,
  Download,
  Eye,
  Plus,
  Ban,
} from "lucide-react";
import type { NodeRunStatus } from "@/lib/types";
import { getNodeSpec, formatNodeTypeLabel } from "@/lib/node-catalog";
import { getNodeIcon } from "@/lib/node-icons";
import { cn } from "@/lib/utils";
import { useCanvasActionsOptional } from "./canvas-actions-context";
import { defaultPromptSlots, isEnrichNode } from "@/lib/node-prompts";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

export interface PipelineNodeData extends Record<string, unknown> {
  nodeKey: string;
  type: string;
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
  const actions = useCanvasActionsOptional();
  const disabled = actions?.disabledNodes.has(d.nodeKey) ?? false;

  const statusConfig = STATUS_CONFIG[d.status];
  const StatusIcon = statusConfig.icon;
  const running = d.status === "running";
  const slots = defaultPromptSlots(d.type);

  const assetKind =
    d.type === "hero" || d.type === "hitl_hero"
      ? "hero"
      : d.type === "items"
        ? "items"
        : d.type === "images" || d.type === "hitl_images"
          ? "images"
          : d.type === "videos" || d.type === "hitl_videos"
            ? "videos"
            : null;

  return (
    <div
      className={cn(
        "group relative w-[260px] overflow-hidden rounded-2xl border border-white/10 bg-gradient-to-br from-card/95 via-card/90 to-card/70 shadow-lg shadow-black/40 backdrop-blur-md transition-all duration-200",
        "hover:-translate-y-0.5 hover:border-primary/40 hover:shadow-xl hover:shadow-primary/10",
        running && "glow-running border-primary/50",
        d.status === "done" && "border-emerald-500/30",
        d.status === "failed" && "border-destructive/50",
        d.status === "waiting_hitl" && "border-amber-400/50 pulse-soft",
        selected && "ring-2 ring-primary/50 ring-offset-2 ring-offset-background",
        disabled && "opacity-45 grayscale",
      )}
      style={{ borderLeftColor: `hsl(${spec.accent})`, borderLeftWidth: 3 }}
    >
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_at_top_right,hsl(45_90%_60%/0.08),transparent_55%)]" />

      <Handle
        type="target"
        position={Position.Left}
        id="in"
        className="!h-2.5 !w-2.5 !border-2 !border-card !bg-muted-foreground"
      />
      <Handle
        type="source"
        position={Position.Right}
        id="out"
        className="!h-2.5 !w-2.5 !border-2 !border-card !bg-muted-foreground"
      />

      {actions && (
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button
              type="button"
              className="absolute right-2 top-2 z-10 flex h-6 w-6 items-center justify-center rounded-md border border-border/60 bg-background/80 text-[11px] font-bold text-muted-foreground shadow-sm backdrop-blur hover:border-primary/50 hover:text-primary"
              onClick={(e) => e.stopPropagation()}
              title="Меню ноды"
            >
              V
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-64" onClick={(e) => e.stopPropagation()}>
            <DropdownMenuLabel>Схема работы</DropdownMenuLabel>
            {slots.map((slot, i) => (
              <DropdownMenuItem key={slot.id} onClick={() => actions.onOpenPrompt(d.nodeKey, d.type, slot)}>
                <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded bg-primary/15 text-[10px] font-bold text-primary">
                  {i + 1}
                </span>
                <span className="flex flex-col">
                  <span>{slot.title}</span>
                  {slot.description && (
                    <span className="text-[10px] text-muted-foreground">{slot.description}</span>
                  )}
                </span>
              </DropdownMenuItem>
            ))}
            <DropdownMenuItem onClick={() => actions.onAddPrompt(d.nodeKey, d.type)}>
              <Plus className="h-3.5 w-3.5" />
              Добавить промт
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem onClick={() => actions.onRunNode(d.nodeKey, d.type)}>
              <Play className="h-3.5 w-3.5" />
              Запустить шаг
            </DropdownMenuItem>
            {isEnrichNode(d.type) && (
              <DropdownMenuItem
                onClick={() =>
                  actions.onOpenPrompt(d.nodeKey, d.type, {
                    id: "excel",
                    title: "Excel",
                    kind: "excel",
                  })
                }
              >
                <Eye className="h-3.5 w-3.5" />
                Просмотр Excel
              </DropdownMenuItem>
            )}
            <DropdownMenuItem onClick={() => actions.onDownloadPrompts(d.nodeKey, d.type)}>
              <Download className="h-3.5 w-3.5" />
              Скачать промты
            </DropdownMenuItem>
            {assetKind && (
              <DropdownMenuItem onClick={() => actions.onOpenAssets(assetKind, d.type)}>
                <Eye className="h-3.5 w-3.5" />
                Файлы и превью
              </DropdownMenuItem>
            )}
            <DropdownMenuSeparator />
            <DropdownMenuItem onClick={() => actions.onDetachNode(d.nodeKey)}>
              <Unlink className="h-3.5 w-3.5" />
              Открепить связи
            </DropdownMenuItem>
            <DropdownMenuItem onClick={() => actions.onToggleDisable(d.nodeKey, !disabled)}>
              <Ban className="h-3.5 w-3.5" />
              {disabled ? "Включить ноду" : "Отключить ноду"}
            </DropdownMenuItem>
            <DropdownMenuItem
              className="text-destructive focus:text-destructive"
              onClick={() => actions.onDeleteNode(d.nodeKey)}
            >
              <Trash2 className="h-3.5 w-3.5" />
              Удалить ноду
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      )}

      <div className="relative flex items-start gap-2.5 px-3.5 pb-2.5 pt-3">
        <NodeCardIcon accent={spec.accent}>
          <Icon className="h-4 w-4" />
        </NodeCardIcon>
        <div className="min-w-0 flex-1 flex-col leading-tight pr-6">
          <div className="flex items-center justify-between gap-2">
            <span className="truncate text-[13px] font-semibold tracking-tight">
              {spec.label || formatNodeTypeLabel(d.type)}
            </span>
            <span className={cn("status-pill shrink-0", statusConfig.bg, statusConfig.text)}>
              {running ? (
                <Loader2 className="h-2.5 w-2.5 animate-spin" />
              ) : (
                <StatusIcon className="h-2.5 w-2.5" />
              )}
              {statusConfig.label}
            </span>
          </div>
          <span className="mt-0.5 line-clamp-2 text-[10.5px] leading-snug text-muted-foreground">
            {spec.description}
          </span>
          {disabled && (
            <span className="mt-1 text-[9px] font-medium uppercase tracking-wider text-amber-400">
              отключена
            </span>
          )}
        </div>
      </div>

      {d.progressText && d.status === "running" && (
        <div className="border-t border-border/50 bg-black/20 px-3 py-1 font-mono text-[10px] text-muted-foreground">
          {d.progressText}
        </div>
      )}
      {d.error && d.status === "failed" && (
        <div className="border-t border-destructive/30 bg-destructive/10 px-3 py-1.5 text-[10px] text-destructive">
          {truncate(d.error, 80)}
        </div>
      )}
      {d.status === "running" && d.progress > 0 && (
        <div
          className="absolute bottom-0 left-0 h-0.5 bg-gradient-to-r from-primary via-amber-400/80 to-primary transition-all"
          style={{ width: `${d.progress}%` }}
        />
      )}
    </div>
  );
}

function NodeCardIcon({ accent, children }: { accent: string; children: React.ReactNode }) {
  return (
    <div
      className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl shadow-inner"
      style={{
        background: `linear-gradient(135deg, hsl(${accent} / 0.25), hsl(${accent} / 0.08))`,
        color: `hsl(${accent})`,
      }}
    >
      {children}
    </div>
  );
}

const STATUS_CONFIG: Record<
  NodeRunStatus,
  { icon: typeof Circle; label: string; bg: string; text: string }
> = {
  pending: { icon: Circle, label: "ожидание", bg: "bg-muted/80", text: "text-muted-foreground" },
  running: { icon: Loader2, label: "работа", bg: "bg-primary/20", text: "text-primary" },
  waiting_hitl: { icon: Hourglass, label: "проверка", bg: "bg-amber-500/15", text: "text-amber-400" },
  done: { icon: CheckCircle2, label: "готово", bg: "bg-emerald-500/15", text: "text-emerald-400" },
  failed: { icon: AlertCircle, label: "ошибка", bg: "bg-destructive/15", text: "text-destructive" },
  skipped: { icon: MinusCircle, label: "пропуск", bg: "bg-muted/80", text: "text-muted-foreground" },
};

function truncate(s: string, n: number): string {
  if (s.length <= n) return s;
  return s.slice(0, n - 1) + "…";
}
