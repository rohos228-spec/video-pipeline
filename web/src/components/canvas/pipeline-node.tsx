"use client";

import { useRef } from "react";
import { Handle, Position, type NodeProps } from "@xyflow/react";
import {
  CheckCircle2,
  Circle,
  Loader2,
  AlertCircle,
  Hourglass,
  MinusCircle,
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
import { NodeResultBadge } from "./node-result-badge";
import { hideResultBadgeForNodeType } from "@/lib/xlsx-sheets";
import { isHitlNodeType } from "@/lib/gpt-text-steps";
import { ExcelFeedPanel } from "./excel-feed-panel";
import { HeroConfigPanel } from "./hero-config-panel";
import { AssembleMontageTrigger } from "./assemble-montage-board";

import {
  excelGptAttachmentChipTitle,
  isExcelGptNode,
  workModeChip,
  type ExcelGptInputSource,
  type ExcelGptWorkMode,
} from "@/lib/excel-gpt-config";

export interface PipelineNodeData extends Record<string, unknown> {
  nodeKey: string;
  type: string;
  label?: string;
  description?: string;
  slotIndex?: number;
  inputSource?: ExcelGptInputSource;
  uploadedFileName?: string;
  workMode?: ExcelGptWorkMode;
  status: NodeRunStatus;
  progress: number;
  progressText: string | null;
  error: string | null;
  attempts: number;
}

/** Wide panel nodes keep airy card; others are circular orbs (Canon C). */
function needsWidePanel(type: string): boolean {
  return type === "excel_feed" || type === "hero";
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
  const wide = needsWidePanel(d.type);

  const slots = actions?.getPromptSlots(d.nodeKey, d.type) ?? [];
  const assetKind = assetTrayKindForNodeType(d.type);
  const vMenuOpen = actions?.vMenuNodeKey === d.nodeKey;
  const resultSnapshot = actions?.getNodeResult(d.type, d.status, d.nodeKey);
  const isExcelFeed = d.type === "excel_feed";
  const isHero = d.type === "hero";
  const isAssemble = d.type === "assemble";
  const anchorRef = useRef<HTMLDivElement>(null);

  const title = (d.label && d.label.trim()) || spec.label || formatNodeTypeLabel(d.type);

  return (
    <>
      <div className="relative">
        {isAssemble && actions?.projectId && (
          <AssembleMontageTrigger
            active={actions.montageBoardOpen}
            busy={actions.montageBusy}
            onClick={() => {
              if (actions.montageBoardOpen) {
                actions.onCloseMontageBoard();
              } else {
                actions.onOpenMontageBoard();
              }
            }}
          />
        )}

        {wide ? (
          <div
            ref={anchorRef}
            className={cn(
              "group relative w-[260px] overflow-visible rounded-3xl border border-white/10 bg-card/80 shadow-lg shadow-black/40 backdrop-blur-md premium-node-glow",
              "hover:-translate-y-0.5 hover:border-primary/35",
              running && "glow-running border-primary/45",
              d.status === "done" && "border-emerald-500/30",
              d.status === "failed" && "border-destructive/50",
              d.status === "waiting_hitl" && "border-amber-400/50 pulse-soft",
              selected && "ring-1 ring-primary/50 ring-offset-2 ring-offset-background",
              disabled && "opacity-45 grayscale",
            )}
          >
            {!isExcelFeed && <HandleWithDetach side="in" nodeKey={d.nodeKey} />}
            <Handle
              type="source"
              position={Position.Right}
              id="out"
              className="!h-3.5 !w-3.5 !cursor-crosshair !rounded-full !border !border-primary/40 !bg-background hover:!scale-125 hover:!border-primary"
              style={{ right: -7 }}
            />
            {actions && resultSnapshot && !hideResultBadgeForNodeType(d.type) && (
              <NodeResultBadge
                snapshot={resultSnapshot}
                nodeType={d.type}
                projectId={actions.projectId}
                onClick={(e) => {
                  e.stopPropagation();
                  actions.onOpenNodeResult(d.nodeKey, d.type);
                }}
              />
            )}
            {actions && !isHitlNodeType(d.type) && !isExcelFeed && (
              <VTrigger
                open={!!vMenuOpen}
                onToggle={() => actions.setVMenuNodeKey(vMenuOpen ? null : d.nodeKey)}
              />
            )}
            {actions && !isHitlNodeType(d.type) && !isExcelFeed && (
              <NodeVMenu
                open={!!vMenuOpen}
                anchorRef={anchorRef}
                nodeKey={d.nodeKey}
                nodeType={d.type}
                slots={slots}
                disabled={disabled}
                projectId={actions.projectId}
                inputSource={d.inputSource}
                uploadedFileName={d.uploadedFileName}
                workMode={d.workMode}
                slotIndex={d.slotIndex}
                canvasZoom={actions.canvasZoom}
                hasAssets={assetKind != null}
                onClose={() => actions.setVMenuNodeKey(null)}
                onSelectPrompt={(slot) => actions.onOpenPrompt(d.nodeKey, d.type, slot)}
                onOpenGptText={() => {
                  actions.setVMenuNodeKey(null);
                  window.setTimeout(() => actions.onOpenGptText(d.nodeKey, d.type), 32);
                }}
                onAddPrompt={() => actions.onAddPrompt(d.nodeKey, d.type)}
                onRemovePrompt={(slot) => actions.onRemovePrompt(d.nodeKey, d.type, slot)}
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
            )}
            <div className="relative flex items-start gap-2.5 px-3.5 pb-2.5 pt-3">
              <OrbIcon accent={spec.accent} size="md">
                <Icon className="h-4 w-4" />
              </OrbIcon>
              <div className="min-w-0 flex-1 pr-8 leading-tight">
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate text-[13px] font-semibold tracking-tight">{title}</span>
                  <span className={cn("status-pill shrink-0", statusConfig.bg, statusConfig.text)}>
                    {running ? <Loader2 className="h-2.5 w-2.5 animate-spin" /> : <StatusIcon className="h-2.5 w-2.5" />}
                    {statusConfig.label}
                  </span>
                </div>
                <span className="mt-0.5 line-clamp-2 text-[10.5px] leading-snug text-muted-foreground">
                  {spec.description}
                </span>
                {isExcelGptNode(d.type) ? (
                  <div className="mt-1.5 flex flex-wrap gap-1">
                    <span className="rounded-full border border-violet-400/25 bg-violet-500/10 px-1.5 py-0.5 text-[9px] font-medium text-violet-100/90">
                      {workModeChip(d.workMode)}
                    </span>
                    <span className="rounded-full border border-emerald-400/20 bg-emerald-500/10 px-1.5 py-0.5 text-[9px] font-medium text-emerald-100/90">
                      {excelGptAttachmentChipTitle(d.inputSource ?? "project_xlsx")}
                    </span>
                  </div>
                ) : null}
              </div>
            </div>
            {isExcelFeed && actions?.projectId && (
              <ExcelFeedPanel projectId={actions.projectId} nodeKey={d.nodeKey} />
            )}
            {isHero && actions?.projectId && <HeroConfigPanel projectId={actions.projectId} />}
            {d.progressText && d.status === "running" && (
              <div className="border-t border-white/[0.06] bg-black/20 px-3 py-1 font-mono text-[10px] text-muted-foreground">
                {d.progressText}
              </div>
            )}
            {d.error && d.status === "failed" && (
              <div className="border-t border-destructive/30 bg-destructive/10 px-3 py-1.5 text-[10px] text-destructive">
                {truncate(d.error, 80)}
              </div>
            )}
          </div>
        ) : (
          /* Canon C: circular orb + label */
          <div
            ref={anchorRef}
            className={cn(
              "group relative flex w-[108px] flex-col items-center gap-2",
              disabled && "opacity-45 grayscale",
            )}
          >
            <div
              className={cn(
                "canon-node-orb relative flex h-[72px] w-[72px] items-center justify-center rounded-full border border-white/10 bg-gradient-to-b from-white/[0.07] to-black/40 backdrop-blur-md",
                running && "glow-running border-primary/50",
                d.status === "done" && "border-emerald-400/35",
                d.status === "failed" && "border-destructive/50",
                d.status === "waiting_hitl" && "border-amber-400/50 pulse-soft",
                selected && "ring-1 ring-primary/60 ring-offset-2 ring-offset-background",
              )}
              style={{
                background: `radial-gradient(circle at 35% 30%, hsl(${spec.accent} / 0.35), hsl(0 0% 4% / 0.92) 62%)`,
                boxShadow: selected
                  ? `0 0 0 1px hsl(${spec.accent} / 0.45), 0 12px 40px hsl(0 0% 0% / 0.55)`
                  : undefined,
              }}
            >
              {!isExcelFeed && <HandleWithDetach side="in" nodeKey={d.nodeKey} />}
              <Handle
                type="source"
                position={Position.Right}
                id="out"
                className="!h-3 !w-3 !cursor-crosshair !rounded-full !border !border-white/30 !bg-background hover:!scale-125 hover:!border-primary"
                style={{ right: -6 }}
              />

              {actions && resultSnapshot && !hideResultBadgeForNodeType(d.type) && (
                <div className="absolute -right-1 -top-1 z-20 scale-90">
                  <NodeResultBadge
                    snapshot={resultSnapshot}
                    nodeType={d.type}
                    projectId={actions.projectId}
                    onClick={(e) => {
                      e.stopPropagation();
                      actions.onOpenNodeResult(d.nodeKey, d.type);
                    }}
                  />
                </div>
              )}

              {actions && !isHitlNodeType(d.type) && (
                <button
                  type="button"
                  className={cn(
                    "node-v-trigger nodrag nopan nowheel absolute -right-1 top-0 z-30 flex h-5 w-5 items-center justify-center rounded-full border text-[9px] font-bold shadow-sm backdrop-blur transition-colors",
                    vMenuOpen
                      ? "border-primary/60 bg-primary/25 text-primary"
                      : "border-white/15 bg-black/50 text-muted-foreground hover:border-primary/50 hover:text-primary",
                  )}
                  onPointerDown={(e) => e.stopPropagation()}
                  onMouseDown={(e) => e.stopPropagation()}
                  onClick={(e) => {
                    e.stopPropagation();
                    e.preventDefault();
                    actions.setVMenuNodeKey(vMenuOpen ? null : d.nodeKey);
                  }}
                  title="Меню промтов (V)"
                >
                  V
                </button>
              )}

              {actions && !isHitlNodeType(d.type) && (
                <NodeVMenu
                  open={!!vMenuOpen}
                  anchorRef={anchorRef}
                  nodeKey={d.nodeKey}
                  nodeType={d.type}
                  slots={slots}
                  disabled={disabled}
                  projectId={actions.projectId}
                  inputSource={d.inputSource}
                  uploadedFileName={d.uploadedFileName}
                  workMode={d.workMode}
                  slotIndex={d.slotIndex}
                  canvasZoom={actions.canvasZoom}
                  hasAssets={assetKind != null}
                  onClose={() => actions.setVMenuNodeKey(null)}
                  onSelectPrompt={(slot) => actions.onOpenPrompt(d.nodeKey, d.type, slot)}
                  onOpenGptText={() => {
                    actions.setVMenuNodeKey(null);
                    window.setTimeout(() => actions.onOpenGptText(d.nodeKey, d.type), 32);
                  }}
                  onAddPrompt={() => actions.onAddPrompt(d.nodeKey, d.type)}
                  onRemovePrompt={(slot) => actions.onRemovePrompt(d.nodeKey, d.type, slot)}
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
              )}

              <div
                className="flex h-9 w-9 items-center justify-center rounded-full"
                style={{ color: `hsl(${spec.accent})` }}
              >
                {running ? (
                  <Loader2 className="h-5 w-5 animate-spin text-primary" />
                ) : (
                  <Icon className="h-5 w-5" />
                )}
              </div>

              {d.status === "running" && d.progress > 0 && (
                <div
                  className="pointer-events-none absolute inset-0 rounded-full"
                  style={{
                    background: `conic-gradient(hsl(var(--primary)) ${d.progress}%, transparent 0)`,
                    mask: "radial-gradient(farthest-side, transparent calc(100% - 2px), #000 calc(100% - 2px))",
                    WebkitMask:
                      "radial-gradient(farthest-side, transparent calc(100% - 2px), #000 calc(100% - 2px))",
                    opacity: 0.7,
                  }}
                />
              )}
            </div>

            <div className="flex w-full flex-col items-center gap-0.5 px-0.5 text-center">
              <span className="line-clamp-2 text-[11px] font-medium leading-tight tracking-tight text-foreground/95">
                {title}
              </span>
              <span className={cn("status-pill", statusConfig.bg, statusConfig.text)}>
                {running ? <Loader2 className="h-2.5 w-2.5 animate-spin" /> : <StatusIcon className="h-2.5 w-2.5" />}
                {statusConfig.label}
              </span>
              {isExcelGptNode(d.type) ? (
                <div className="mt-0.5 flex max-w-full flex-wrap justify-center gap-0.5">
                  <span className="rounded-full border border-violet-400/25 bg-violet-500/10 px-1 py-0.5 text-[8px] font-medium text-violet-100/90">
                    {workModeChip(d.workMode)}
                  </span>
                  <span className="rounded-full border border-emerald-400/20 bg-emerald-500/10 px-1 py-0.5 text-[8px] font-medium text-emerald-100/90">
                    {excelGptAttachmentChipTitle(d.inputSource ?? "project_xlsx")}
                  </span>
                </div>
              ) : null}
              {disabled && (
                <span className="text-[9px] font-medium uppercase tracking-wider text-amber-400">
                  отключена
                </span>
              )}
              {d.error && d.status === "failed" && (
                <span className="line-clamp-2 text-[9px] text-destructive">{truncate(d.error, 48)}</span>
              )}
              {d.progressText && d.status === "running" && (
                <span className="line-clamp-1 font-mono text-[9px] text-muted-foreground">
                  {d.progressText}
                </span>
              )}
            </div>
          </div>
        )}
      </div>
    </>
  );
}

function VTrigger({ open, onToggle }: { open: boolean; onToggle: () => void }) {
  return (
    <button
      type="button"
      className={cn(
        "node-v-trigger nodrag nopan nowheel absolute right-2 top-2 z-30 flex h-6 w-6 items-center justify-center rounded-full border shadow-sm backdrop-blur transition-colors",
        open
          ? "border-primary/60 bg-primary/20 text-primary"
          : "border-white/12 bg-black/40 text-muted-foreground hover:border-primary/50 hover:text-primary",
      )}
      onPointerDown={(e) => e.stopPropagation()}
      onMouseDown={(e) => e.stopPropagation()}
      onClick={(e) => {
        e.stopPropagation();
        e.preventDefault();
        onToggle();
      }}
      title="Меню промтов (V)"
    >
      <span className="text-[11px] font-bold">V</span>
    </button>
  );
}

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
        className="!pointer-events-auto !left-1/2 !top-1/2 !h-3.5 !w-3.5 !-translate-x-1/2 !-translate-y-1/2 !cursor-crosshair !rounded-full !border !border-white/30 !bg-background hover:!scale-125 hover:!border-primary"
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

function OrbIcon({
  accent,
  children,
  size = "md",
}: {
  accent: string;
  children: React.ReactNode;
  size?: "md" | "lg";
}) {
  return (
    <div
      className={cn(
        "flex shrink-0 items-center justify-center rounded-full shadow-inner",
        size === "md" ? "h-9 w-9" : "h-11 w-11",
      )}
      style={{
        background: `linear-gradient(145deg, hsl(${accent} / 0.32), hsl(${accent} / 0.08))`,
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
  queued: { icon: Hourglass, label: "в очереди", bg: "bg-sky-500/15", text: "text-sky-400" },
  running: { icon: Loader2, label: "в работе", bg: "bg-primary/20", text: "text-primary" },
  waiting_hitl: { icon: Hourglass, label: "проверка", bg: "bg-amber-500/15", text: "text-amber-400" },
  done: { icon: CheckCircle2, label: "готово", bg: "bg-emerald-500/15", text: "text-emerald-400" },
  failed: { icon: AlertCircle, label: "ошибка", bg: "bg-destructive/15", text: "text-destructive" },
  skipped: { icon: MinusCircle, label: "пропуск", bg: "bg-muted/80", text: "text-muted-foreground" },
};

function truncate(s: string, n: number): string {
  if (s.length <= n) return s;
  return s.slice(0, n - 1) + "…";
}
