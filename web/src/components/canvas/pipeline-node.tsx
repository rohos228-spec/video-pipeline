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
import { getNodeSpec, formatNodeTypeLabel, SD_AGENT_LABELS } from "@/lib/node-catalog";
import { getNodeIcon } from "@/lib/node-icons";
import { groupHue } from "@/lib/group-color";
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
import { StoragePanel } from "./storage-panel";
import { ShotsReportPanel } from "./shots-report-panel";
import { HeroConfigPanel } from "./hero-config-panel";
import { ItemsConfigPanel } from "./items-config-panel";
import { AssembleMontageTrigger } from "./assemble-montage-board";
import { ShotMenuPanel, ShotMenuTrigger } from "./shot-menu-panel";
import { GptOperatorCardPanel } from "./gpt-operator-card-panel";
import { NodeModelPicker } from "./node-model-picker";

import {
  excelGptAttachmentChipTitle,
  isExcelGptNode,
  isShotsReportNode,
  workModeChip,
  type ExcelGptInputSource,
  type ExcelGptWorkMode,
} from "@/lib/excel-gpt-config";
import { isBranchingRole, roleChip } from "@/lib/gpt-operator";

export interface PipelineNodeData extends Record<string, unknown> {
  nodeKey: string;
  type: string;
  label?: string;
  description?: string;
  slotIndex?: number;
  /** Нода вне enrich-слотов 1..5 (например проверки scene-веера) — запуск по node_key. */
  slotOverflow?: boolean;
  inputSource?: ExcelGptInputSource;
  uploadedFileName?: string;
  workMode?: ExcelGptWorkMode;
  /** Полная роль оператора (assist/review/…/gate); workMode — legacy. */
  role?: string;
  /** Имя агента для legacy-нод sd_agent (characters/world/style/camera/action). */
  agent?: string;
  /** Маркер scene-агента на ноде «Работа с GPT» (data.sd_agent, + "assemble"). */
  sdAgent?: string;
  /** Импортированная группа (штамп при вставке группы) — рамка на канвасе. */
  groupId?: string;
  groupTitle?: string;
  /** Выбранная модель vibecode (id каталога). */
  modelId?: string;
  /** Канал цен (всегда дорогой / stable). */
  modelChannel?: "stable" | string;
  /** Параметры media-ноды (картинка/видео) из пикера. */
  imageResolution?: string;
  imageQuality?: string;
  aspectRatio?: string;
  status: NodeRunStatus;
  progress: number;
  progressText: string | null;
  error: string | null;
  attempts: number;
}

/** Все ноды — прямоугольные карточки (старый стиль). */
function needsWidePanel(_type: string): boolean {
  return true;
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

  const rawSlots = actions?.getPromptSlots(d.nodeKey, d.type) ?? [];
  // Scene-агент: legacy-тип (sd_agent/sd_assemble) ИЛИ marked «Работа с GPT»
  // (excel_gpt + data.sd_agent). agentName — characters/…/action/assemble.
  const agentName =
    d.sdAgent ??
    (d.type === "sd_agent" || d.type === "sd_assemble" ? d.agent : undefined) ??
    (d.type === "sd_assemble" ? "assemble" : undefined);
  const isSdAgent =
    d.type === "sd_agent" || d.type === "sd_assemble" || !!d.sdAgent;
  // Ноде агента подставляем её файл промпта (05_excel_gpt/sd_<агент>.md).
  const slots =
    isSdAgent && agentName
      ? rawSlots.map((s) =>
          s.id === "main"
            ? {
                ...s,
                preferredFile: `sd_${agentName}`,
                title: `Промт: ${SD_AGENT_LABELS[agentName] ?? agentName}`,
              }
            : s,
        )
      : rawSlots;
  const assetKind = assetTrayKindForNodeType(d.type);
  const vMenuOpen = actions?.vMenuNodeKey === d.nodeKey;
  const resultSnapshot = actions?.getNodeResult(d.type, d.status, d.nodeKey);
  const isExcelFeed = d.type === "excel_feed";
  const isStorage = d.type === "storage";
  const isShotMenu = d.type === "shot_menu";
  const isHero = d.type === "hero";
  const isItems = d.type === "items";
  const isExcelGpt = isExcelGptNode(d.type);
  const isGptWork = isExcelGpt || isSdAgent;
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
        {isShotMenu && actions?.projectId && (
          <ShotMenuTrigger
            active={actions.shotMenuOpen}
            onClick={() => {
              if (actions.shotMenuOpen) {
                actions.onCloseShotMenu();
              } else {
                actions.onOpenShotMenu();
              }
            }}
          />
        )}

        {wide ? (
          <div
            ref={anchorRef}
            className={cn(
              "group relative overflow-visible rounded-3xl border border-zinc-700/70 bg-zinc-900/95 shadow-xl shadow-black/60 backdrop-blur-md transition-all duration-200",
              isGptWork || isStorage || isShotMenu ? "w-[320px]" : "w-[280px]",
              "hover:-translate-y-0.5 hover:border-zinc-500",
              running && "glow-running border-amber-400/80 shadow-[0_0_28px_rgba(245,158,11,0.3)] ring-1 ring-amber-400/50",
              d.status === "done" && "border-emerald-500/50 shadow-[0_0_20px_rgba(16,185,129,0.16)] hover:border-emerald-400/70",
              d.status === "failed" && "border-red-500/60 shadow-[0_0_24px_rgba(239,68,68,0.25)] ring-1 ring-red-500/40",
              d.status === "waiting_hitl" && "border-amber-400/60 shadow-[0_0_20px_rgba(251,191,36,0.2)] pulse-soft",
              selected && "ring-2 ring-sky-400/90 shadow-[0_0_22px_rgba(56,189,248,0.3)] ring-offset-2 ring-offset-background",
              disabled && "opacity-40 grayscale",
            )}
          >
            {/* Вход/выход без точек: связь с левого и правого края по всей высоте */}
            {!isExcelFeed && <SideTargetStrip nodeKey={d.nodeKey} />}
            <SideSourceStrip nodeKey={d.nodeKey} />
            {actions && resultSnapshot && !hideResultBadgeForNodeType(d.type) && (
              <NodeResultBadge
                snapshot={resultSnapshot}
                nodeType={d.type}
                nodeStatus={d.status}
                projectId={actions.projectId}
                onClick={(e) => {
                  e.stopPropagation();
                  actions.onOpenNodeResult(d.nodeKey, d.type);
                }}
              />
            )}
            {actions &&
              !isHitlNodeType(d.type) &&
              !isExcelFeed &&
              !isStorage &&
              !isShotMenu &&
              !isShotsReportNode(d.nodeKey) && (
              <VTrigger
                open={!!vMenuOpen}
                title={
                  isExcelGpt
                    ? "Пульт оператора + промты (V)"
                    : isSdAgent
                      ? "Промт агента + меню (V)"
                      : "Меню промтов (V)"
                }
                label={isGptWork ? "GPT" : "V"}
                onToggle={() => actions.setVMenuNodeKey(vMenuOpen ? null : d.nodeKey)}
              />
            )}
            {actions &&
              !isHitlNodeType(d.type) &&
              !isExcelFeed &&
              !isStorage &&
              !isShotMenu &&
              !isShotsReportNode(d.nodeKey) && (
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
            <div className="relative flex items-start gap-3 px-4 pb-3 pt-3.5">
              <OrbIcon accent={spec.accent} size="md">
                <Icon className="h-4.5 w-4.5" />
              </OrbIcon>
              <div className="min-w-0 flex-1 pr-8 leading-tight">
                <div className="flex items-center justify-between gap-2">
                  <span className="flex min-w-0 items-center gap-1.5">
                    {d.groupId ? (
                      <span
                        className="inline-block h-2 w-2 shrink-0 rounded-full"
                        style={{ background: `hsl(${groupHue(d.groupId)} 85% 65%)` }}
                        title={`Импортированная группа: ${d.groupTitle ?? d.groupId}`}
                      />
                    ) : null}
                    <span className="truncate text-sm sm:text-[14.5px] font-bold tracking-normal text-zinc-100">{title}</span>
                  </span>
                  <span className={cn("status-pill shrink-0", statusConfig.bg, statusConfig.text)}>
                    {running ? <Loader2 className="h-3 w-3 animate-spin" /> : <StatusIcon className="h-3 w-3" />}
                    {statusConfig.label}
                  </span>
                </div>
                {!isShotMenu && d.type !== "topic" ? (
                  <NodeModelPicker
                    nodeKey={d.nodeKey}
                    nodeType={d.type}
                    modelId={d.modelId}
                    imageResolution={d.imageResolution}
                    imageQuality={d.imageQuality}
                    aspectRatio={d.aspectRatio}
                  />
                ) : null}
                {isShotMenu ? (
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    <span className="rounded-full border border-sky-400/40 bg-sky-500/15 px-2.5 py-0.5 text-[11px] font-semibold text-sky-200">
                      меню · не шаг
                    </span>
                  </div>
                ) : null}
                {isSdAgent ? (
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    <span className="rounded-full border border-violet-400/30 bg-violet-500/15 px-2.5 py-0.5 text-[11px] font-semibold text-violet-200">
                      {agentName && agentName !== "assemble"
                        ? `агент: ${SD_AGENT_LABELS[agentName] ?? agentName}`
                        : "сборщик сцен"}
                    </span>
                    <span className="rounded-full border border-emerald-400/30 bg-emerald-500/15 px-2.5 py-0.5 text-[11px] font-semibold text-emerald-200">
                      GPT API
                    </span>
                  </div>
                ) : null}
                {!isSdAgent && isShotsReportNode(d.nodeKey) ? (
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    <span className="rounded-full border border-sky-400/30 bg-sky-500/15 px-2.5 py-0.5 text-[11px] font-semibold text-sky-200">
                      HTML-отчёт
                    </span>
                  </div>
                ) : !isSdAgent && isExcelGpt ? (
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    <span className="rounded-full border border-violet-400/30 bg-violet-500/15 px-2.5 py-0.5 text-[11px] font-semibold text-violet-200">
                      {d.role ? roleChip(d.role) : workModeChip(d.workMode)}
                    </span>
                    {isBranchingRole(d.role || d.workMode) ? (
                      <span className="rounded-full border border-amber-400/40 bg-amber-500/15 px-2.5 py-0.5 text-[11px] font-semibold text-amber-200">
                        ок · не ок
                      </span>
                    ) : (
                      <span className="rounded-full border border-emerald-400/30 bg-emerald-500/15 px-2.5 py-0.5 text-[11px] font-semibold text-emerald-200">
                        {excelGptAttachmentChipTitle(d.inputSource ?? "project_xlsx")}
                      </span>
                    )}
                  </div>
                ) : null}
              </div>
            </div>
            {isExcelFeed && actions?.projectId && (
              <ExcelFeedPanel projectId={actions.projectId} nodeKey={d.nodeKey} />
            )}
            {isStorage && actions?.projectId && (
              <StoragePanel projectId={actions.projectId} nodeKey={d.nodeKey} />
            )}
            {isShotsReportNode(d.nodeKey) && actions?.projectId && (
              <ShotsReportPanel projectId={actions.projectId} nodeKey={d.nodeKey} />
            )}
            {isShotMenu && actions?.projectId && (
              <ShotMenuPanel
                projectId={actions.projectId}
                onOpenBoard={(cellIndex) => actions.onOpenShotMenu(cellIndex)}
              />
            )}
            {isHero && actions?.projectId && <HeroConfigPanel projectId={actions.projectId} />}
            {isItems && actions?.projectId && <ItemsConfigPanel projectId={actions.projectId} />}
            {isExcelGpt && !isShotsReportNode(d.nodeKey) && actions?.projectId && (
              <GptOperatorCardPanel
                projectId={actions.projectId}
                nodeKey={d.nodeKey}
                onOpenStudio={() => {
                  actions.setVMenuNodeKey(null);
                  window.setTimeout(() => actions.onOpenGptText(d.nodeKey, d.type), 32);
                }}
              />
            )}
            {d.progressText && d.status === "running" && (
              <div className="border-t border-white/[0.06] bg-black/20 px-3 py-1 font-mono text-[10px] text-muted-foreground">
                {d.progressText}
              </div>
            )}
            {d.error && (
              <div
                className="border-t border-destructive/30 bg-destructive/10 px-3 py-1.5 text-[10px] text-destructive"
                title={d.error}
              >
                {truncate(d.error, 120)}
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
                running && "glow-running border-amber-400/60",
                d.status === "done" && "border-emerald-400/45",
                d.status === "failed" && "border-destructive/60",
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
              {!isExcelFeed && <SideTargetStrip nodeKey={d.nodeKey} />}
              <SideSourceStrip nodeKey={d.nodeKey} />

              {actions && resultSnapshot && !hideResultBadgeForNodeType(d.type) && (
                <div className="absolute -right-1 -top-1 z-20 scale-90">
                  <NodeResultBadge
                    snapshot={resultSnapshot}
                    nodeType={d.type}
                    nodeStatus={d.status}
                    projectId={actions.projectId}
                    onClick={(e) => {
                      e.stopPropagation();
                      actions.onOpenNodeResult(d.nodeKey, d.type);
                    }}
                  />
                </div>
              )}

              {actions &&
                !isHitlNodeType(d.type) &&
                !isShotMenu &&
                !isShotsReportNode(d.nodeKey) && (
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

              {actions &&
                !isHitlNodeType(d.type) &&
                !isShotMenu &&
                !isShotsReportNode(d.nodeKey) && (
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
              {isShotsReportNode(d.nodeKey) ? (
                <span className="mt-0.5 rounded-full border border-sky-400/25 bg-sky-500/10 px-1 py-0.5 text-[8px] font-medium text-sky-100/90">
                  HTML-отчёт
                </span>
              ) : isExcelGptNode(d.type) ? (
                <div className="mt-0.5 flex max-w-full flex-wrap justify-center gap-0.5">
                  <span className="rounded-full border border-violet-400/25 bg-violet-500/10 px-1 py-0.5 text-[8px] font-medium text-violet-100/90">
                    {d.role ? roleChip(d.role) : workModeChip(d.workMode)}
                  </span>
                  {isBranchingRole(d.role || d.workMode) ? (
                    <span className="rounded-full border border-amber-400/30 bg-amber-500/10 px-1 py-0.5 text-[8px] font-medium text-amber-100/90">
                      ок · не ок
                    </span>
                  ) : (
                    <span className="rounded-full border border-emerald-400/20 bg-emerald-500/10 px-1 py-0.5 text-[8px] font-medium text-emerald-100/90">
                      {excelGptAttachmentChipTitle(d.inputSource ?? "project_xlsx")}
                    </span>
                  )}
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

function VTrigger({
  open,
  onToggle,
  title = "Меню промтов (V)",
  label = "V",
}: {
  open: boolean;
  onToggle: () => void;
  title?: string;
  label?: string;
}) {
  return (
    <button
      type="button"
      title={title}
      className={cn(
        "node-v-trigger nodrag nopan nowheel absolute right-2 top-2 z-30 flex h-6 min-w-6 items-center justify-center rounded-full border px-1 shadow-sm backdrop-blur transition-colors",
        open
          ? "border-primary/60 bg-primary/20 text-primary"
          : "border-white/12 bg-black/40 text-muted-foreground hover:border-primary/50 hover:text-primary",
        label.length > 1 && "rounded-md px-1.5",
      )}
      onPointerDown={(e) => e.stopPropagation()}
      onMouseDown={(e) => e.stopPropagation()}
      onClick={(e) => {
        e.stopPropagation();
        e.preventDefault();
        onToggle();
      }}
    >
      <span className="text-[10px] font-bold tracking-tight">{label}</span>
    </button>
  );
}

/** Невидимая полоса справа: тянуть выходные связи с края, без точки. */
function SideSourceStrip({ nodeKey }: { nodeKey: string }) {
  return (
    <div
      className="group/out pointer-events-none absolute inset-y-0 -right-2 z-20 w-4"
      data-node-out={nodeKey}
    >
      <Handle
        type="source"
        position={Position.Right}
        id="out"
        title="Тяни связь с правого края"
        className={cn(
          "!pointer-events-auto !left-auto !right-0 !top-0 !h-full !w-3 !translate-x-0 !translate-y-0",
          "!cursor-crosshair !rounded-none !border-0 !bg-transparent !opacity-0",
          "hover:!bg-primary/15 hover:!opacity-100",
        )}
        style={{ right: 0, top: 0, transform: "none" }}
      />
      <div className="pointer-events-none absolute inset-y-1 right-0 w-0.5 rounded-full bg-primary/0 transition group-hover/out:bg-primary/45" />
    </div>
  );
}

/** Невидимая полоса слева: принимать связи на весь левый край, без точки. */
function SideTargetStrip({ nodeKey }: { nodeKey: string }) {
  return (
    <div
      className="group/in pointer-events-none absolute inset-y-0 -left-2 z-20 w-4"
      data-node-in={nodeKey}
    >
      <Handle
        type="target"
        position={Position.Left}
        id="in"
        title="Подключи сюда с левого края"
        className={cn(
          "!pointer-events-auto !left-0 !right-auto !top-0 !h-full !w-3 !translate-x-0 !translate-y-0",
          "!cursor-crosshair !rounded-none !border-0 !bg-transparent !opacity-0",
          "hover:!bg-emerald-400/15 hover:!opacity-100",
        )}
        style={{ left: 0, top: 0, transform: "none" }}
      />
      <div className="pointer-events-none absolute inset-y-1 left-0 w-0.5 rounded-full bg-emerald-400/0 transition group-hover/in:bg-emerald-400/45" />
      <button
        type="button"
        title="Отсоединить вход"
        aria-label="Отсоединить вход"
        className="nodrag pointer-events-auto absolute left-0 top-1 z-30 flex h-4 w-4 -translate-x-1/2 items-center justify-center rounded-full border border-destructive/60 bg-destructive text-destructive-foreground opacity-0 shadow ring-1 ring-background transition group-hover/in:opacity-100"
        onMouseDown={(e) => {
          e.preventDefault();
          e.stopPropagation();
        }}
        onClick={(e) => {
          e.preventDefault();
          e.stopPropagation();
          window.dispatchEvent(
            new CustomEvent("canvas-detach-handle", {
              detail: { nodeKey, side: "in", autoSave: true },
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
  pending: { icon: Circle, label: "ожидание", bg: "bg-white/[0.06] border-white/10", text: "text-zinc-400" },
  queued: { icon: Hourglass, label: "в очереди", bg: "bg-sky-500/15 border-sky-400/30", text: "text-sky-300" },
  running: { icon: Loader2, label: "в работе", bg: "bg-amber-500/20 border-amber-400/40", text: "text-amber-300" },
  waiting_hitl: { icon: Hourglass, label: "проверка", bg: "bg-amber-500/15 border-amber-400/30", text: "text-amber-300" },
  done: { icon: CheckCircle2, label: "готово", bg: "bg-emerald-500/15 border-emerald-400/30", text: "text-emerald-300" },
  failed: { icon: AlertCircle, label: "ошибка", bg: "bg-red-500/15 border-red-400/30", text: "text-red-300" },
  skipped: { icon: MinusCircle, label: "пропуск", bg: "bg-white/[0.04] border-white/5", text: "text-zinc-500" },
};

function truncate(s: string, n: number): string {
  if (s.length <= n) return s;
  return s.slice(0, n - 1) + "…";
}
