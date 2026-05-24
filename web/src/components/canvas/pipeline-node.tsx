"use client";

import { useEffect } from "react";
import { Handle, Position, type NodeProps } from "@xyflow/react";
import {
  CheckCircle2,
  Circle,
  Loader2,
  AlertCircle,
  Hourglass,
  MinusCircle,
  Sparkles,
  X,
} from "lucide-react";
import type { NodeRunStatus } from "@/lib/types";
import { getNodeSpec, formatNodeTypeLabel } from "@/lib/node-catalog";
import { getNodeIcon } from "@/lib/node-icons";
import { cn } from "@/lib/utils";
import {
  assetTrayKindForNodeType,
  useCanvasActionsOptional,
} from "./canvas-actions-context";
import { NodeVMenu } from "./node-v-menu";
import { NodeHitlBadge, resolveHitlBadgeState } from "./node-hitl-badge";

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

  const slots = actions?.getPromptSlots(d.nodeKey, d.type) ?? [];
  const assetKind = assetTrayKindForNodeType(d.type);
  const vMenuOpen = actions?.vMenuNodeKey === d.nodeKey;

  const hitlBadge =
    actions &&
    resolveHitlBadgeState({
      nodeType: d.type,
      nodeStatus: d.status,
      autoMode: actions.autoMode,
      hitlList: actions.hitlList,
    });

  useEffect(() => {
    if (!vMenuOpen) return;
    const close = (ev: MouseEvent) => {
      const t = ev.target as HTMLElement;
      if (t.closest(".node-v-menu") || t.closest(".node-v-trigger")) return;
      actions?.setVMenuNodeKey(null);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [vMenuOpen, actions]);

  return (
    <>
      <div
        className={cn(
          "group relative w-[260px] overflow-visible rounded-2xl border border-white/10 bg-gradient-to-br from-card/95 via-card/90 to-card/70 shadow-lg shadow-black/40 backdrop-blur-md transition-all duration-200 premium-node-glow",
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
        {hitlBadge && (
          <NodeHitlBadge
            state={hitlBadge}
            onClick={(e) => {
              e.stopPropagation();
              actions?.onOpenHitlReview?.(d.nodeKey, d.type);
            }}
          />
        )}

        <div className="pointer-events-none absolute inset-0 overflow-hidden rounded-2xl bg-[radial-gradient(ellipse_at_top_right,hsl(45_90%_60%/0.08),transparent_55%)]" />

        {/* Соединительные кружки. При hover-е появляется крестик-кнопка,
            клик по которой удаляет все рёбра, прикреплённые к этой
            стороне ноды (in/out). На сами кружки drag/connect ведёт
            себя как обычно (xyflow). */}
        <HandleWithDetach side="in" nodeKey={d.nodeKey} />
        <Handle type="source" position={Position.Right} id="out" className="!h-4 !w-4 !cursor-crosshair !rounded-full !border-2 !border-amber-400/50 !bg-background hover:!scale-125 hover:!border-primary" style={{ right: -8 }} />

        {actions && (
          <>
            <button
              type="button"
              className={cn(
                "node-v-trigger nodrag absolute right-2 top-2 z-10 flex h-6 w-6 items-center justify-center rounded-md border shadow-sm backdrop-blur transition-colors",
                vMenuOpen
                  ? "border-primary/60 bg-primary/20 text-primary"
                  : "border-border/60 bg-background/80 text-muted-foreground hover:border-primary/50 hover:text-primary",
              )}
              onMouseDown={(e) => e.stopPropagation()}
              onClick={(e) => {
                e.stopPropagation();
                actions.setVMenuNodeKey(vMenuOpen ? null : d.nodeKey);
              }}
              title="Меню ноды"
            >
              <span className="text-[11px] font-bold">V</span>
            </button>

            {/* AI-кружок СПРАВА от ноды (видим только когда нода
                выделена). Кликом по нему открывается ИИ-диалог
                (AiNodeDialog) — управляется через
                window event 'canvas-open-ai-node'. */}
            {selected && (
              <button
                type="button"
                title="ИИ-помощник для этой ноды"
                aria-label="ИИ-помощник"
                className="nodrag absolute -right-12 top-1/2 z-20 flex h-9 w-9 -translate-y-1/2 items-center justify-center rounded-full border border-violet-400/60 bg-gradient-to-br from-violet-500/70 to-amber-500/40 text-white shadow-lg shadow-violet-500/30 transition hover:scale-110 hover:border-violet-300"
                onMouseDown={(e) => e.stopPropagation()}
                onClick={(e) => {
                  e.stopPropagation();
                  window.dispatchEvent(
                    new CustomEvent("canvas-open-ai-node", {
                      detail: { nodeKey: d.nodeKey, nodeType: d.type },
                    }),
                  );
                }}
              >
                <Sparkles className="h-4 w-4" />
              </button>
            )}

            <NodeVMenu
              open={!!vMenuOpen}
              slots={slots}
              disabled={disabled}
              hasAssets={assetKind != null}
              onClose={() => actions.setVMenuNodeKey(null)}
              onSelectPrompt={(slot) => {
                actions.setVMenuNodeKey(null);
                actions.onOpenPrompt(d.nodeKey, d.type, slot);
              }}
              onAddPrompt={() => actions.onAddPrompt(d.nodeKey, d.type)}
              onViewAllPrompts={() => {
                actions.setVMenuNodeKey(null);
                actions.onViewAllPrompts(d.nodeKey, d.type);
              }}
              onDownloadPrompts={() => actions.onDownloadPrompts(d.nodeKey, d.type)}
              onRunNode={() => {
                actions.setVMenuNodeKey(null);
                actions.onRunNode(d.nodeKey, d.type);
              }}
              onOpenAssets={
                assetKind
                  ? () => {
                      actions.setVMenuNodeKey(null);
                      actions.onOpenAssets(assetKind, d.type);
                    }
                  : undefined
              }
              onDetachNode={() => {
                actions.setVMenuNodeKey(null);
                actions.onDetachNode(d.nodeKey);
              }}
              onToggleDisable={() => actions.onToggleDisable(d.nodeKey, !disabled)}
              onDeleteNode={() => {
                actions.setVMenuNodeKey(null);
                actions.onDeleteNode(d.nodeKey);
              }}
            />
          </>
        )}

        <div className="relative flex items-start gap-2.5 px-3.5 pb-2.5 pt-3">
          <NodeCardIcon accent={spec.accent}>
            <Icon className="h-4 w-4" />
          </NodeCardIcon>
          <div className="min-w-0 flex-1 flex-col leading-tight pr-8">
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
            className="absolute bottom-0 left-0 h-0.5 rounded-b-2xl bg-gradient-to-r from-primary via-amber-400/80 to-primary transition-all"
            style={{ width: `${d.progress}%` }}
          />
        )}
      </div>
    </>
  );
}

/** Соединительный кружок ноды с возможностью отсоединения по hover-X.
 *
 *  - side="in"  → target handle слева (Position.Left, id="in").
 *  - side="out" → source handle справа (Position.Right, id="out").
 *
 *  Внешний кружок-handle получает hover-обёртку (group/conn). При hover
 *  показывается красный крестик; клик по нему диспетчит
 *  `canvas-detach-handle` — FlowCanvas удалит все рёбра, прикреплённые
 *  к этой стороне ноды.
 */
function HandleWithDetach({
  side,
  nodeKey,
}: {
  side: "in" | "out";
  nodeKey: string;
}) {
  const isIn = side === "in";
  return (
    <div
      className={cn(
        "group/conn pointer-events-none absolute top-1/2 z-10 -translate-y-1/2",
        isIn ? "-left-3" : "-right-3",
      )}
      style={{ width: 22, height: 22 }}
    >
      <Handle
        type={isIn ? "target" : "source"}
        position={isIn ? Position.Left : Position.Right}
        id={isIn ? "in" : "out"}
        className="!pointer-events-auto !left-1/2 !top-1/2 !h-4 !w-4 !-translate-x-1/2 !-translate-y-1/2 !cursor-crosshair !rounded-full !border-2 !border-amber-400/50 !bg-background hover:!scale-125 hover:!border-primary"
      />
      <button
        type="button"
        title="Отсоединить эту сторону ноды"
        aria-label="Отсоединить"
        className={cn(
          "nodrag pointer-events-auto absolute z-20 flex h-4 w-4 items-center justify-center rounded-full border border-destructive/60 bg-destructive text-destructive-foreground opacity-0 shadow ring-1 ring-background transition group-hover/conn:opacity-100",
          isIn ? "-top-2 -left-2" : "-top-2 -right-2",
        )}
        onMouseDown={(e) => {
          e.preventDefault();
          e.stopPropagation();
        }}
        onClick={(e) => {
          e.preventDefault();
          e.stopPropagation();
          window.dispatchEvent(
            new CustomEvent("canvas-detach-handle", {
              detail: { nodeKey, side, autoSave: true },
            }),
          );
        }}
      >
        <X className="h-2.5 w-2.5" />
      </button>
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
