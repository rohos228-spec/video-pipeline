import { useQuery } from "@tanstack/react-query";
import { computeNodeActiveVariant } from "@/lib/node-active-prompt";
import { nodeBgFor, promptBgFor } from "@/components/prompts/prompt-card-catalog";
import { useCallback, useEffect, useMemo, useRef, useState, useLayoutEffect } from "react";
import { Trash2 as FlyTrash, Pencil as FlyPencil, ImagePlus as FlyImage } from "lucide-react";
import { setNodeCustomBg } from "@/lib/node-custom-bg";
import { Handle, Position, type NodeProps } from "@xyflow/react";
import {
  CheckCircle2,
  Circle,
  Loader2,
  AlertCircle,
  Hourglass,
  MinusCircle,
  X,
  Eye,
  Maximize2,
  Minimize2,
  Play,
  Square,
  Ban,
  XCircle,
  Clock,
} from "lucide-react";
import type { NodeRunStatus } from "@/lib/types";
import { getNodeSpec, formatNodeTypeLabel, SD_AGENT_LABELS } from "@/lib/node-catalog";
import { getNodeIcon } from "@/lib/node-icons";
import { groupHue } from "@/lib/group-color";
import { cn, parseUtcDate } from "@/lib/utils";
import { api } from "@/lib/api";
import { toast } from "sonner";
import { errorMessageFromUnknown } from "@/lib/error-message";
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
import { StyleExtractPanel } from "./style-extract-panel";
import { HitlStylePanel } from "./hitl-style-panel";
import { ShotsReportPanel } from "./shots-report-panel";
import { HeroConfigPanel } from "./hero-config-panel";
import { ItemsConfigPanel } from "./items-config-panel";
import { AssembleMontageTrigger } from "./assemble-montage-board";
import { ShotMenuPanel, ShotMenuTrigger } from "./shot-menu-panel";
import { Nii67CardPanel } from "./nii67-card-panel";
import { NodeModelPicker } from "./node-model-picker";
import { projectUsesNii67Script } from "@/lib/nii67";

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
  /** Сила мышления LLM: low / med / high / ultra. */
  reasoningEffort?: string;
  /** Custom uploaded tile background (URL). */
  customBg?: string;
  status: NodeRunStatus;
  progress: number;
  progressText: string | null;
  error: string | null;
  attempts: number;
  startedAt?: string | null;
  finishedAt?: string | null;
}

/** Все ноды — прямоугольные карточки (старый стиль). */
function needsWidePanel(_type: string): boolean {
  return true;
}

function NodeExecutionTimer({
  status,
  startedAt,
  finishedAt,
}: {
  status: NodeRunStatus;
  startedAt?: string | null;
  finishedAt?: string | null;
}) {
  const [now, setNow] = useState(() => Date.now());
  const localStartRef = useRef<number | null>(null);
  const localDurationRef = useRef<number | null>(null);

  useEffect(() => {
    if (status === "running") {
      if (!localStartRef.current) {
        localStartRef.current = Date.now();
      }
      localDurationRef.current = null;
      const timer = setInterval(() => {
        setNow(Date.now());
      }, 1000);
      return () => clearInterval(timer);
    } else if (status === "done" && localStartRef.current) {
      localDurationRef.current = Math.max(1, Math.floor((Date.now() - localStartRef.current) / 1000));
      localStartRef.current = null;
    } else if (status !== "done") {
      localStartRef.current = null;
      localDurationRef.current = null;
    }
  }, [status]);

  if (status === "running") {
    let elapsed = 0;
    if (startedAt) {
      const parsed = parseUtcDate(startedAt).getTime();
      if (!Number.isNaN(parsed) && parsed > 0) {
        elapsed = Math.max(0, Math.floor((now - parsed) / 1000));
      }
    } else if (localStartRef.current) {
      elapsed = Math.max(0, Math.floor((now - localStartRef.current) / 1000));
    }
    const m = Math.floor(elapsed / 60);
    const s = elapsed % 60;
    const timeStr = `${m}:${s.toString().padStart(2, "0")}`;
    return (
      <span
        title="Время выполнения ноды"
        className="pointer-events-auto inline-flex items-center gap-1.5 rounded-full border border-amber-400/60 bg-[#17140e]/95 px-2.5 py-0.5 text-[11px] font-mono font-semibold text-amber-300 shadow-[0_0_18px_rgba(245,158,11,0.35)] backdrop-blur-md tabular-nums"
      >
        <span className="h-1.5 w-1.5 rounded-full bg-amber-400 animate-pulse" />
        ⏱ {timeStr}
      </span>
    );
  }

  if (status === "done") {
    let durSec: number | null = null;
    if (startedAt && finishedAt) {
      const startMs = parseUtcDate(startedAt).getTime();
      const finishMs = parseUtcDate(finishedAt).getTime();
      if (!Number.isNaN(startMs) && !Number.isNaN(finishMs) && finishMs >= startMs) {
        durSec = Math.max(1, Math.floor((finishMs - startMs) / 1000));
      }
    }
    if (durSec == null && localDurationRef.current != null) {
      durSec = localDurationRef.current;
    }
    if (durSec != null) {
      const m = Math.floor(durSec / 60);
      const s = durSec % 60;
      const timeStr = m > 0 ? `${m}м ${s}с` : `${s}с`;
      return (
        <span
          title={`Итоговое время выполнения: ${durSec}с`}
          className="pointer-events-auto inline-flex items-center gap-1.5 rounded-full border border-emerald-500/50 bg-[#0d1612]/95 px-2.5 py-0.5 text-[10.5px] font-mono font-medium text-emerald-300 shadow-[0_0_16px_rgba(16,185,129,0.25)] backdrop-blur-md tabular-nums"
        >
          ⏱ {timeStr}
        </span>
      );
    }
  }

  return null;
}

export function PipelineNode({ data, selected }: NodeProps) {
  const d = data as PipelineNodeData;
  const spec = getNodeSpec(d.type);
  const Icon = getNodeIcon(spec.iconKey);
  const actions = useCanvasActionsOptional();
  const disabled = actions?.disabledNodes.has(d.nodeKey) ?? false;

  const statusConfig = STATUS_CONFIG[d.status] ?? STATUS_CONFIG.pending;
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
  const vMenuOpen = false; // old V-menu detached (was: actions?.vMenuNodeKey === d.nodeKey)
  const resultSnapshot = actions?.getNodeResult(d.type, d.status, d.nodeKey);
  const isExcelFeed = d.type === "excel_feed";
  const isStorage = d.type === "storage";
  const isShotMenu = d.type === "shot_menu";
  const isStyleExtract = d.type === "style_extract";
  const isHitlStyle = d.type === "hitl_style";
  const isHero = d.type === "hero";
  const isItems = d.type === "items";
  const isExcelGpt = isExcelGptNode(d.type);
  const isGptWork = isExcelGpt || isSdAgent;
  const isAssemble = d.type === "assemble";
  const anchorRef = useRef<HTMLDivElement>(null);
  const [tileExpanded, setTileExpanded] = useState(false);
  const [inlineStudio, setInlineStudio] = useState(false);
  const isOpenBox = inlineStudio || tileExpanded;
  /** vis = box is in its open geometry (drives the 350ms size/colour/crossfade animation). */
  const [vis, setVis] = useState(false);
  const closeTimer = useRef<number | null>(null);
  const beginClose = useCallback(() => {
    setVis(false);
    setTileExpanded(false);
    if (closeTimer.current) window.clearTimeout(closeTimer.current);
    closeTimer.current = window.setTimeout(() => setInlineStudio(false), 360);
  }, []);
  const [collapsedBox, setCollapsedBox] = useState<{ w: number; h: number } | null>(null);
  useLayoutEffect(() => {
    const el = anchorRef.current;
    if (!el || isOpenBox) return;
    const w = el.clientWidth;
    const h = el.clientHeight;
    setCollapsedBox((p) => (p && p.w === w && p.h === h ? p : { w, h }));
  }, [isOpenBox]);
  useEffect(() => {
    if (!inlineStudio) return;
    const onClosed = () => {
      beginClose();
    };
    const onClosing = (ev: Event) => {
      if ((ev as CustomEvent<{ nodeKey?: string }>).detail?.nodeKey === d.nodeKey) beginClose();
    };
    window.addEventListener("vp-node-closing", onClosing);
    window.addEventListener("node-studio-closed", onClosed);
    return () => {
      window.removeEventListener("vp-node-closing", onClosing);
      window.removeEventListener("node-studio-closed", onClosed);
    };
  }, [inlineStudio, beginClose, d.nodeKey]);
  const openInline = useCallback(() => {
    window.dispatchEvent(new CustomEvent("vp-node-inline-exclusive", { detail: { nodeKey: d.nodeKey } }));
    if (closeTimer.current) window.clearTimeout(closeTimer.current);
    setInlineStudio(true);
    setTileExpanded(true);
    window.dispatchEvent(
      new CustomEvent("studio-open-node-prompts", {
        detail: { nodeKey: d.nodeKey, initialTab: "settings", inline: true },
      }),
    );
  }, [d.nodeKey]);
  useEffect(() => {
    const onOpen = (ev: Event) => {
      const key = (ev as CustomEvent<{ nodeKey?: string }>).detail?.nodeKey;
      if (key === d.nodeKey && !inlineStudio) openInline();
    };
    window.addEventListener("vp-node-inline-open", onOpen);
    return () => window.removeEventListener("vp-node-inline-open", onOpen);
  }, [d.nodeKey, inlineStudio, openInline]);
  // Wheel over a node zooms the canvas (like the background). Only an inner area that
  // can actually scroll in the wheel direction keeps the wheel (React Flow skips .nowheel).
  useEffect(() => {
    const root = anchorRef.current;
    if (!root) return;
    const onWheel = (e: WheelEvent) => {
      if (e.ctrlKey) return;
      const target = e.target as HTMLElement | null;
      if (!target) return;
      // Cheap path (no layout reads): only candidates that can scroll are measured.
      const stop = root.parentElement;
      let scroller: HTMLElement | null = null;
      let cand = target.closest<HTMLElement>(".overflow-y-auto, .overflow-auto, .overflow-y-scroll, textarea, select");
      while (cand && root.contains(cand)) {
        if (cand.tagName === "TEXTAREA" || cand.tagName === "SELECT") {
          if (cand.scrollHeight > cand.clientHeight + 1) {
            scroller = cand;
            break;
          }
        } else if (cand.scrollHeight > cand.clientHeight + 1) {
          // Inside a genuinely scrollable area the wheel only scrolls (never zooms), even at its end.
          scroller = cand;
          break;
        }
        cand = cand.parentElement?.closest<HTMLElement>(".overflow-y-auto, .overflow-auto, .overflow-y-scroll") ?? null;
      }
      const touched: [HTMLElement, boolean][] = [];
      if (scroller) {
        if (!scroller.classList.contains("nowheel")) {
          scroller.classList.add("nowheel");
          touched.push([scroller, false]);
        }
      } else {
        let nw = target.closest<HTMLElement>(".nowheel");
        while (nw && nw !== stop && root.contains(nw)) {
          nw.classList.remove("nowheel");
          touched.push([nw, true]);
          nw = nw.parentElement?.closest<HTMLElement>(".nowheel") ?? null;
        }
      }
      if (touched.length) {
        window.setTimeout(() => {
          for (const [el, had] of touched) el.classList.toggle("nowheel", had);
        }, 0);
      }
    };
    root.addEventListener("wheel", onWheel, { capture: true, passive: true });
    return () => root.removeEventListener("wheel", onWheel, { capture: true });
  }, []);
  useEffect(() => {
    const onExclusive = (ev: Event) => {
      const key = (ev as CustomEvent<{ nodeKey?: string }>).detail?.nodeKey;
      if (key && key !== d.nodeKey) {
        beginClose();
      }
    };
    window.addEventListener("vp-node-inline-exclusive", onExclusive);
    return () => window.removeEventListener("vp-node-inline-exclusive", onExclusive);
  }, [d.nodeKey, beginClose]);
  // Expanded node (and its prompt detail overlay) must sit above neighbours.
  useEffect(() => {
    const el = anchorRef.current?.closest(".react-flow__node") as HTMLElement | null;
    if (!el) return;
    if (inlineStudio) {
      el.style.zIndex = "2000";
      return () => {
        el.style.zIndex = "";
      };
    }
  }, [inlineStudio]);
  const showNii67 = d.type === "script" && projectUsesNii67Script(actions?.project ?? null);

  const title = showNii67
    ? "НИИ 67 · двери"
    : (d.label && d.label.trim()) || spec.label || formatNodeTypeLabel(d.type);

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
            onContextMenu={(e) => e.preventDefault()}
            className={cn(
              "group relative overflow-visible rounded-lg border backdrop-blur-md",
              vis ? "bg-[#0a0a0a]" : "bg-[#0d0d10]/90",
              vis ? "w-[780px]" : !inlineStudio && tileExpanded ? "w-[720px]" : isStorage || isShotMenu || isStyleExtract || isHitlStyle || showNii67 ? "w-[384px]" : "w-[336px]",
              "transition-[width,border-color,background-color] duration-[350ms] ease-[cubic-bezier(0.65,0,0.35,1)]",
              d.status === "failed" ? "border-red-500/80" : "border-white/[0.12] hover:border-white/30",
              selected && "border-white/80 outline outline-1 outline-white/40",
              disabled && "opacity-40 grayscale",
            )}
          >
            {/* Вход/выход без точек: связь с левого и правого края по всей высоте */}
            {/* handles anchored to the collapsed tile box so edges never move on expand */}
            <div
              className="pointer-events-none absolute left-0 top-0"
              style={isOpenBox && collapsedBox ? { width: collapsedBox.w, height: collapsedBox.h } : { right: 0, bottom: 0 }}
            >
              {!isExcelFeed && <SideTargetStrip nodeKey={d.nodeKey} />}
              <SideSourceStrip nodeKey={d.nodeKey} />
            </div>
            {actions &&
              !isHitlNodeType(d.type) &&
              !isExcelFeed &&
              !isStorage &&
              !isShotMenu &&
              !isStyleExtract &&
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
            <PosterTile
              projectId={actions?.projectId ?? null}
              title={title}
              customBg={d.customBg}
              onFlyDelete={actions ? () => actions.onDeleteNode(d.nodeKey) : undefined}
              onFlyRename={(label: string) => {
                window.dispatchEvent(new CustomEvent("canvas-patch-node-data", { detail: { nodeKey: d.nodeKey, patch: { label } } }));
              }}
              onFlyBg={
                actions?.projectId
                  ? async (file: File) => {
                      try {
                        const fd = new FormData();
                        fd.append("file", file);
                        const res = await fetch(`/api/projects/${actions.projectId}/node-bg?node_key=${encodeURIComponent(d.nodeKey)}`, { method: "POST", body: fd });
                        if (!res.ok) throw new Error(await res.text());
                        const j = (await res.json()) as { preview_url: string };
                        window.dispatchEvent(new CustomEvent("canvas-patch-node-data", { detail: { nodeKey: d.nodeKey, patch: { customBg: j.preview_url } } }));
                        toast.success("Фон ноды обновлён");
                      } catch (e) {
                        toast.error(errorMessageFromUnknown(e));
                      }
                    }
                  : undefined
              }
              status={d.status}
              progress={d.progress}
              progressText={d.progressText}
              error={d.error}
              snapshot={resultSnapshot ?? null}
              groupId={d.groupId}
              groupTitle={d.groupTitle}
              disabled={disabled}
              model={
                !isShotMenu && !isHitlStyle && d.type !== "topic" ? (
                  <NodeModelPicker
                    nodeKey={d.nodeKey}
                    nodeType={d.type}
                    modelId={d.modelId}
                    imageResolution={d.imageResolution}
                    imageQuality={d.imageQuality}
                    aspectRatio={d.aspectRatio}
                    reasoningEffort={d.reasoningEffort}
                  />
                ) : null
              }
              onView={
                actions && resultSnapshot && !hideResultBadgeForNodeType(d.type)
                  ? () => actions.onOpenNodeResult(d.nodeKey, d.type)
                  : undefined
              }
              onRetry={actions ? () => actions.onRunNode(d.nodeKey, d.type) : undefined}
              onRun={actions ? () => actions.onRunNode(d.nodeKey, d.type) : undefined}
              onStop={
                actions?.projectId
                  ? () => {
                      void api
                        .stopProject(actions.projectId as number)
                        .then((r) => toast.success(r.message || "Остановлено"))
                        .catch((e) => toast.error(errorMessageFromUnknown(e)));
                    }
                  : undefined
              }
              description={(d.description && d.description.trim()) || spec.description || fallbackDescription(d.type, title)}
              expanded={tileExpanded && !inlineStudio}
              vis={vis}
              onMenuReady={() => setVis(true)}
              tileW={collapsedBox?.w}
              onToggleExpand={() => setTileExpanded((v) => !v)}
              settingsLines={buildSettingsLines(d, slots, isExcelGpt, isSdAgent, agentName)}
              nodeKey={d.nodeKey}
              inlineStudio={inlineStudio}
              inlineAttrs={{
                "data-status": STATUS_CONFIG[d.status]?.label ?? "",
                "data-description": (d.description && d.description.trim()) || "",
                "data-model-id": d.modelId ?? "",
                "data-image-resolution": d.imageResolution ?? "",
                "data-image-quality": d.imageQuality ?? "",
              "data-reasoning-effort": d.reasoningEffort ?? "",
                "data-aspect-ratio": d.aspectRatio ?? "",
              }}
              onOpenSettings={openInline}
            />
            {isExcelFeed && actions?.projectId && (
              <ExcelFeedPanel projectId={actions.projectId} nodeKey={d.nodeKey} />
            )}
            {isStorage && actions?.projectId && (
              <StoragePanel projectId={actions.projectId} nodeKey={d.nodeKey} />
            )}
            {isStyleExtract && actions?.projectId && (
              <StyleExtractPanel projectId={actions.projectId} nodeKey={d.nodeKey} />
            )}
            {isHitlStyle && actions?.projectId && (
              <HitlStylePanel projectId={actions.projectId} />
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
            {/* hero config strip removed from tile (still in node menu) */}
            {showNii67 && actions?.projectId && <Nii67CardPanel projectId={actions.projectId} />}
            {/* items config strip removed from tile (still in node menu) */}
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
        "node-v-trigger nodrag nopan nowheel absolute bottom-[9px] right-2 z-30 flex h-6 min-w-6 items-center justify-center rounded-none border px-1 backdrop-blur transition-colors",
        open
          ? "border-primary/60 bg-primary/20 text-primary"
          : "border-white/12 bg-black/40 text-muted-foreground hover:border-primary/50 hover:text-primary",
        label.length > 1 && "px-1.5",
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
  waiting_approval: { icon: Hourglass, label: "проверка", bg: "bg-amber-500/15 border-amber-400/30", text: "text-amber-300" },
  done: { icon: CheckCircle2, label: "готово", bg: "bg-emerald-500/15 border-emerald-400/30", text: "text-emerald-300" },
  failed: { icon: AlertCircle, label: "ошибка", bg: "bg-red-500/15 border-red-400/30", text: "text-red-300" },
  skipped: { icon: MinusCircle, label: "пропуск", bg: "bg-white/[0.04] border-white/5", text: "text-zinc-500" },
};

function truncate(s: string, n: number): string {
  if (s.length <= n) return s;
  return s.slice(0, n - 1) + "…";
}

function fallbackDescription(type: string, title: string): string {
  if (isExcelGptNode(type)) return "Обрабатывает данные проекта моделью GPT по промту этой ноды.";
  if (type.includes("image") || type.includes("hero") || type.includes("items")) return "Генерирует изображения по промтам проекта.";
  if (type.includes("video")) return "Генерирует видео по кадрам проекта.";
  if (type.includes("audio") || type.includes("voice") || type.includes("music")) return "Создаёт аудиодорожку для ролика.";
  return `Шаг «${title}» пайплайна.`;
}

function isVideoItem(i: { kind: string; previewUrl?: string | null }): boolean {
  return i.kind === "video" || /\.(mp4|webm|mov)(\?|$)/i.test(i.previewUrl ?? "");
}

function useNodeBg(nodeKey: string, projectId: number | null): string | null {
  const pid = projectId;
  const project = useQuery({
    queryKey: ["project", pid],
    queryFn: () => api.getProject(pid!),
    enabled: pid != null,
    staleTime: 10000,
  });
  const globalActive = useQuery({
    queryKey: ["prompt-global-active"],
    queryFn: () => api.getGlobalActivePrompts(),
    staleTime: 10000,
  });
  return useMemo(() => {
    if (!project.data) return null;
    try {
      const meta = (project.data.meta || {}) as Record<string, unknown>;
      const ov = (project.data.prompt_overrides || {}) as Record<string, unknown>;
      const v = computeNodeActiveVariant(meta, ov, nodeKey, globalActive.data);
      return nodeBgFor(nodeKey, promptBgFor(v, v, nodeKey));
    } catch {
      return null;
    }
  }, [project.data, globalActive.data, nodeKey]);
}

function PosterTile({
  title,
  vis,
  onMenuReady,
  tileW,
  customBg,
  onFlyDelete,
  onFlyRename,
  onFlyBg,
  status,
  progress,
  progressText,
  error,
  snapshot,
  model,
  groupId,
  groupTitle,
  disabled,
  onView,
  onRun,
  onStop,
  onRetry,
  description,
  expanded,
  onToggleExpand,
  settingsLines,
  onOpenSettings,
  nodeKey,
  inlineStudio,
  inlineAttrs,
  projectId,
}: {
  title: string;
  vis?: boolean;
  onMenuReady?: () => void;
  tileW?: number;
  customBg?: string;
  onFlyDelete?: () => void;
  onFlyRename?: (label: string) => void;
  onFlyBg?: (file: File) => void;
  status: NodeRunStatus;
  progress: number;
  progressText: string | null;
  error: string | null;
  snapshot: import("@/lib/node-result-resolver").NodeResultSnapshot | null;
  model: React.ReactNode;
  groupId?: string;
  groupTitle?: string;
  disabled: boolean;
  onView?: () => void;
  onRetry?: () => void;
  onRun?: () => void;
  onStop?: () => void;
  description: string;
  expanded: boolean;
  onToggleExpand: () => void;
  settingsLines: string[];
  onOpenSettings: () => void;
  nodeKey: string;
  inlineStudio: boolean;
  inlineAttrs: Record<string, string>;
  projectId?: number | null;
}) {
  const menuRef = useRef<HTMLDivElement>(null);
  const [menuH, setMenuH] = useState(0);
  const readyRef = useRef(false);
  useEffect(() => {
    if (!inlineStudio) {
      readyRef.current = false;
      return;
    }
    const el = menuRef.current;
    const ready = () => {
      if (readyRef.current) return;
      readyRef.current = true;
      // let the browser paint the closed geometry first, then animate
      requestAnimationFrame(() => requestAnimationFrame(() => onMenuReady?.()));
    };
    const fallback = window.setTimeout(ready, 0); // start at once; the menu lays out while still at opacity 0
    let ro: ResizeObserver | null = null;
    if (el) {
      ro = new ResizeObserver(() => {
        const h = el.offsetHeight;
        if (h > 20) {
          setMenuH(h);
          ready();
        }
      });
      ro.observe(el);
    }
    return () => {
      window.clearTimeout(fallback);
      ro?.disconnect();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [inlineStudio]);
  const genBg = useNodeBg(nodeKey, projectId ?? null);
  const fixedBg = customBg && customBg.startsWith("/api/files?path=") && /[\\:]/.test(customBg.slice(16)) ? "/api/files?path=" + encodeURIComponent(customBg.slice(16)) : customBg;
  const nodeBg = fixedBg || genBg;
  useEffect(() => {
    setNodeCustomBg(nodeKey, fixedBg);
  }, [nodeKey, customBg]);
  const [fly, setFly] = useState(false);
  const flyTimer = useRef<number | null>(null);
  const showFly = () => {
    if (flyTimer.current) window.clearTimeout(flyTimer.current);
    setFly(true);
  };
  const hideFly = () => {
    if (flyTimer.current) window.clearTimeout(flyTimer.current);
    flyTimer.current = window.setTimeout(() => setFly(false), 280);
  };
  const [renaming, setRenaming] = useState(false);
  const [draftTitle, setDraftTitle] = useState(title);
  const bgInputRef = useRef<HTMLInputElement>(null);
  const running = status === "running";
  const failed = status === "failed";
  const items = snapshot?.items ?? [];
  const has = !!snapshot?.hasResult && items.length > 0;
  const media = has ? items.filter((i) => i.previewUrl && (i.kind === "image" || isVideoItem(i) || i.kind === "frames")) : [];
  const audio = has ? items.filter((i) => i.kind === "audio" && (i.previewUrl || i.downloadUrl)) : [];
  const text = has && !media.length && !audio.length
    ? (items.map((i) => i.content).filter(Boolean).join("\n\n") || snapshot?.summary || "").trim()
    : "";
  const cfg = STATUS_CONFIG[status];
  const dot =
    status === "done" ? (
      <span className="h-2 w-2 bg-white" />
    ) : failed ? (
      <span className="h-2 w-2 bg-red-500" />
    ) : running ? (
      <Loader2 className="h-3 w-3 animate-spin text-white" />
    ) : (
      <span className={cn("h-2 w-2 border", status === "queued" || status === "waiting_hitl" ? "border-white/70" : "border-white/35")} />
    );
  const isTextNode = !media.length && !audio.length;
  const shown = expanded ? media.slice(0, 12) : media.slice(0, 4);
  const blur = running ? "blur-md opacity-70" : "";
  let body: React.ReactNode;
  if (media.length) {
    body = (
      <div className={cn("absolute inset-0 grid gap-px bg-black", shown.length === 1 ? "grid-cols-1" : expanded ? "grid-cols-4" : "grid-cols-2")}>
        {shown.map((m) =>
          isVideoItem(m) ? (
            <video key={m.id} src={m.previewUrl ?? undefined} muted preload="metadata" controls={expanded} className={cn("h-full w-full object-cover", blur)} />
          ) : (
            // eslint-disable-next-line @next/next/no-img-element
            <img key={m.id} src={m.previewUrl ?? undefined} alt={m.label} draggable={false} className={cn("h-full w-full object-cover", blur)} />
          ),
        )}
      </div>
    );
  } else if (audio.length) {
    body = (
      <div className="nodrag nopan absolute inset-x-3 bottom-4 top-10 flex flex-col justify-center gap-2">
        {audio.slice(0, expanded ? 6 : 1).map((a) => (
          <div key={a.id} className="flex flex-col gap-1">
            <span className="truncate text-[12px] text-white/60"><span className="vp-hl">{a.label}</span></span>
            <audio controls preload="none" src={(a.previewUrl || a.downloadUrl) ?? undefined} className="h-8 w-full" />
          </div>
        ))}
      </div>
    );
  } else if (settingsLines.length) {
    body = (
      <div className="absolute inset-x-0 bottom-0 top-10 flex flex-col gap-1 overflow-hidden px-3 pb-2">
        {settingsLines.slice(0, 5).map((l) => (
          <div key={l} className="truncate text-[12.5px] leading-[1.45] text-white/75" title={l}>
            <span className="vp-hl">{l}</span>
          </div>
        ))}
        {text ? (
          <div className="mt-1 line-clamp-3 whitespace-pre-wrap text-[12px] leading-[1.45] text-white/40"><span className="vp-hl">{text.slice(0, 300)}</span></div>
        ) : (
          <div className="mt-1 line-clamp-2 text-[12px] leading-[1.45] text-white/40"><span className="vp-hl">{description}</span></div>
        )}
      </div>
    );
  } else if (text) {
    body = (
      <div
        className={cn(
          "absolute inset-x-0 bottom-0 top-9 whitespace-pre-wrap px-3 pb-2 text-[12.5px] leading-[1.5] text-white/75",
          expanded ? "nowheel nodrag overflow-y-auto select-text" : "overflow-hidden",
          running && "blur-[2px]",
        )}
      >
        <span className="vp-hl">{expanded ? text : text.slice(0, 600)}</span>
      </div>
    );
  } else {
    body = (
      <div className="absolute inset-x-0 top-10 px-3 pb-2 text-[13px] leading-[1.45] text-white/70 line-clamp-3"><span className="vp-hl">{description}</span></div>
    );
  }
  return (
    <div className="relative">
      <div
        className={cn(
          "relative overflow-hidden transition-[height,border-radius] duration-[350ms] ease-[cubic-bezier(0.65,0,0.35,1)]",
          vis ? "rounded-[14px]" : "rounded-lg",
          inlineStudio ? "" : expanded ? "h-[460px]" : "h-[190px]",
        )}
        style={inlineStudio ? { height: vis ? Math.min(menuH || 680, 680) : 190 } : undefined}
      >
        {inlineStudio ? (
          <div
            ref={menuRef}
            id={`vp-inline-studio-${nodeKey}`}
            data-inline-studio
            {...inlineAttrs}
            className="nowheel nopan absolute left-0 top-0 z-40 cursor-auto"
            style={{
              width: 778,
              opacity: vis ? 1 : 0,
              transition: vis ? "opacity 175ms ease-out 175ms" : "opacity 150ms ease-in",
              pointerEvents: vis ? "auto" : "none",
            }}
            onDoubleClick={(e) => e.stopPropagation()}
          />
        ) : null}
        <div
          data-vp-tile-content
          className="absolute left-0 top-0"
          style={{
            width: inlineStudio && tileW ? tileW : "100%",
            height: inlineStudio ? 190 : "100%",
            opacity: inlineStudio && vis ? 0 : 1,
            transition: inlineStudio && vis ? "opacity 175ms ease-in" : "opacity 175ms ease-out 175ms",
            pointerEvents: inlineStudio ? "none" : undefined,
          }}
        >
        {!media.length && nodeBg ? (
          <div aria-hidden data-vp-tile-bg className="pointer-events-none absolute inset-0 overflow-hidden">
            <div className="absolute inset-0 bg-cover bg-center" style={{ backgroundImage: `url(${nodeBg})` }} />
          </div>
        ) : null}
        {!expanded ? (
          <>
            <div aria-hidden className="pointer-events-none absolute inset-x-0 top-0 h-[35%] bg-gradient-to-b from-black/70 to-transparent" />
            <div aria-hidden className="pointer-events-none absolute inset-x-0 bottom-0 h-[35%] bg-gradient-to-t from-black/70 to-transparent" />
          </>
        ) : null}
        {media.length || audio.length || expanded ? (
          body
        ) : (
          <div data-vp-tile-mid className="absolute inset-0 opacity-0 transition-opacity duration-[120ms] ease-out group-hover:opacity-100">{body}</div>
        )}
        {media.length ? <div className="pointer-events-none absolute inset-x-0 top-0 h-12 bg-gradient-to-b from-black/75 to-transparent" /> : null}
        {!expanded && text && !nodeBg ? <div className="pointer-events-none absolute inset-x-0 bottom-0 h-10 bg-gradient-to-t from-[#0d0d10] to-transparent" /> : null}
        <div className={"absolute inset-x-0 top-0 flex items-start justify-between gap-2 px-2.5 pb-2 pt-2"}>
          <span className="min-w-0 flex-1 break-words pt-0.5 text-[18px] font-normal uppercase leading-tight tracking-[0.04em] text-white [text-shadow:0_1px_4px_rgba(0,0,0,0.7)]">{renaming ? (
              <input
                autoFocus
                value={draftTitle}
                data-vp-rename
                className="nodrag nopan w-full rounded bg-black/70 px-1.5 py-0.5 text-[18px] uppercase text-white outline-none ring-1 ring-white/40"
                onMouseDown={(e) => e.stopPropagation()}
                onClick={(e) => e.stopPropagation()}
                onDoubleClick={(e) => e.stopPropagation()}
                onChange={(e) => setDraftTitle(e.target.value)}
                onKeyDown={(e) => {
                  e.stopPropagation();
                  if (e.key === "Enter") {
                    const v = draftTitle.trim();
                    if (v && v !== title) onFlyRename?.(v);
                    setRenaming(false);
                  } else if (e.key === "Escape") {
                    setRenaming(false);
                  }
                }}
                onBlur={() => setRenaming(false)}
              />
            ) : (
              <span className="vp-hl">{title}</span>
            )}</span>
          <span
            className="relative flex shrink-0 items-center gap-1.5 text-[12px] text-white/85"
            onMouseEnter={!expanded ? showFly : undefined}
            onMouseLeave={!expanded ? hideFly : undefined}
          >
            {!expanded && !inlineStudio ? (
              <span className="pointer-events-none absolute right-0 top-0 z-30 h-7 w-7" data-vp-flyout={fly ? "open" : "closed"}>
                {[
                  { k: "delete", t: "Удалить ноду", icon: FlyTrash, cls: "bg-red-600 text-white hover:bg-red-500", x: -40, y: 2, fn: () => onFlyDelete?.(), show: !!onFlyDelete },
                  { k: "rename", t: "Переименовать", icon: FlyPencil, cls: "bg-black/75 text-white hover:bg-black/90", x: -30, y: 30, fn: () => { setDraftTitle(title); setRenaming(true); }, show: !!onFlyRename },
                  { k: "bg", t: "Свой фон", icon: FlyImage, cls: "bg-black/75 text-white hover:bg-black/90", x: -2, y: 40, fn: () => bgInputRef.current?.click(), show: !!onFlyBg },
                ]
                  .filter((b) => b.show)
                  .map((b, i) => {
                    const Icon = b.icon;
                    return (
                      <button
                        key={b.k}
                        type="button"
                        title={b.t}
                        data-fly={b.k}
                        className={cn(
                          "nodrag nopan absolute left-0.5 top-0.5 flex h-6 w-6 items-center justify-center rounded-full shadow-md ring-1 ring-white/20 transition-[transform,opacity] duration-150 ease-out",
                          b.cls,
                          fly ? "pointer-events-auto opacity-100" : "pointer-events-none opacity-0",
                        )}
                        style={{
                          transform: fly ? `translate(${b.x}px, ${b.y}px) scale(1)` : "translate(0px, 0px) scale(0.4)",
                          transitionDelay: `${(fly ? i : 2 - i) * 50}ms`,
                        }}
                        onMouseDown={(e) => e.stopPropagation()}
                        onDoubleClick={(e) => e.stopPropagation()}
                        onClick={(e) => {
                          e.stopPropagation();
                          setFly(false);
                          b.fn();
                        }}
                      >
                        <Icon className="h-3.5 w-3.5" />
                      </button>
                    );
                  })}
                <input
                  ref={bgInputRef}
                  type="file"
                  accept="image/*"
                  className="hidden"
                  onChange={(e) => {
                    const f = e.target.files?.[0];
                    e.target.value = "";
                    if (f) onFlyBg?.(f);
                  }}
                />
              </span>
            ) : null}
            <span
              data-status-icon={disabled ? "disabled" : status}
              title={disabled ? "Отключена" : cfg.label}
              className="group/st nodrag nopan relative flex h-7 w-7 items-center justify-center"
              onMouseDown={(e) => e.stopPropagation()}
              onClick={(e) => e.stopPropagation()}
              onDoubleClick={(e) => e.stopPropagation()}
            >
              {(() => {
                const hide = (running ? onStop : onRun) ? "transition-opacity duration-150 group-hover/st:opacity-0" : "";
                if (disabled) return <Ban className={cn("h-7 w-7 text-zinc-500", hide)} />;
                if (status === "done") return <CheckCircle2 className={cn("h-7 w-7 text-emerald-400", hide)} />;
                if (failed) return <XCircle className={cn("h-7 w-7 text-red-500", hide)} />;
                if (running) return <Loader2 className={cn("h-7 w-7 animate-spin text-yellow-400", hide)} />;
                return <Clock strokeWidth={2.6} className={cn("h-[21px] w-[21px] text-white/90", hide)} />;
              })()}
              {running ? (
                onStop ? (
                  <button
                    type="button"
                    data-action="stop"
                    title="Остановить весь проект"
                    className="absolute inset-0 flex items-center justify-center rounded-full bg-black/70 text-yellow-300 opacity-0 transition-opacity duration-150 group-hover/st:opacity-100"
                    onClick={(e) => {
                      e.stopPropagation();
                      onStop();
                    }}
                  >
                    <Square className="h-3 w-3 fill-current" />
                  </button>
                ) : null
              ) : onRun && !disabled ? (
                <button
                  type="button"
                  data-action="run"
                  title="Запустить"
                  className="absolute inset-0 flex items-center justify-center rounded-full bg-black/70 text-emerald-300 opacity-0 transition-opacity duration-150 group-hover/st:opacity-100"
                  onClick={(e) => {
                    e.stopPropagation();
                    onRun();
                  }}
                >
                  <Play className="h-3 w-3 fill-current" />
                </button>
              ) : null}
            </span>
            {expanded ? (
              <button
                type="button"
                title="Свернуть"
                className="nodrag nopan ml-1 flex h-6 w-6 items-center justify-center rounded border border-white/20 bg-black/50 text-white hover:border-white/60"
                onMouseDown={(e) => e.stopPropagation()}
                onClick={(e) => {
                  e.stopPropagation();
                  onToggleExpand();
                }}
              >
                <X className="h-3.5 w-3.5" />
              </button>
            ) : null}
          </span>
        </div>
        {failed ? (
          <div className="absolute inset-x-0 top-1/2 flex -translate-y-1/2 flex-col items-center gap-2 px-4 text-center">
            {error ? (
              <span className="line-clamp-3 text-[12px] text-red-400" title={error}>
                {truncate(error, 140)}
              </span>
            ) : null}
            {onRetry ? (
              <button
                type="button"
                className="nodrag nopan rounded border border-white/25 bg-black/60 px-3 py-1 text-[12px] text-white hover:border-white/60"
                onMouseDown={(e) => e.stopPropagation()}
                onClick={(e) => {
                  e.stopPropagation();
                  onRetry();
                }}
              >
                Повторить
              </button>
            ) : null}
          </div>
        ) : null}
        {running ? (
          <div className="absolute inset-x-0 bottom-0 h-[2px] bg-white/10">
            <div className="h-full bg-white" style={{ width: `${Math.max(4, Math.min(100, progress || 0))}%` }} />
          </div>
        ) : null}
        {running && progressText ? (
          <div className="absolute bottom-1.5 right-2.5 font-mono text-[11px] text-white/70">{truncate(progressText, 36)}</div>
        ) : null}
      <div className={`pointer-events-none absolute inset-x-0 bottom-0 z-20 flex items-center gap-2 px-3 pb-1.5 pt-2 [&>*]:pointer-events-auto`}>
        {groupId ? (
          <span
            className="inline-block h-2 w-2 shrink-0"
            style={{ background: `hsl(${groupHue(groupId)} 70% 65%)` }}
            title={`Группа: ${groupTitle ?? groupId}`}
          />
        ) : null}
        <div className="nodrag min-w-0 flex-1 font-mono text-[14px] text-white/85 [&_*]:!rounded-none [&_*]:!font-mono [&_*]:!text-[14px] [&_button]:!mt-0 [&_button]:!border-0 [&_button]:!bg-transparent [&_button]:!px-0 [&_button]:!shadow-none [&_button:hover]:!text-white"><span className="vp-hl inline-block max-w-full">{model}</span></div>
        {inlineStudio ? (
          <button
            type="button"
            title="Свернуть"
            className="nodrag nopan flex h-7 w-7 shrink-0 items-center justify-center rounded border border-white/15 text-white/80 hover:border-white/60 hover:text-white"
            onMouseDown={(e) => e.stopPropagation()}
            onClick={(e) => {
              e.stopPropagation();
              window.dispatchEvent(new CustomEvent("node-studio-close"));
            }}
          >
            <Minimize2 className="h-3.5 w-3.5" />
          </button>
        ) : null}
        {expanded && onView ? (
          <button
            type="button"
            title="Открыть полный просмотр результата"
            className="nodrag nopan flex h-7 shrink-0 items-center gap-1 rounded border border-white/15 px-2 text-[12px] text-white/80 hover:border-white/60"
            onMouseDown={(e) => e.stopPropagation()}
            onClick={(e) => {
              e.stopPropagation();
              onView();
            }}
          >
            <Eye className="h-3.5 w-3.5" /> Полный просмотр
          </button>
        ) : null}
      </div>
        </div>
      </div>
    </div>
  );
}

function buildSettingsLines(
  d: PipelineNodeData,
  slots: { title?: string; preferredFile?: string }[],
  isExcelGpt: boolean,
  isSdAgent: boolean,
  agentName?: string,
): string[] {
  const out: string[] = [];
  if (isSdAgent) {
    out.push(agentName && agentName !== "assemble" ? `Агент: ${SD_AGENT_LABELS[agentName] ?? agentName}` : "Сборщик сцен");
  }
  if (isExcelGpt) {
    out.push(`Режим: ${d.role ? roleChip(d.role) : workModeChip(d.workMode)}`);
    out.push(`Вход: ${excelGptAttachmentChipTitle(d.inputSource ?? "project_xlsx")}`);
    if (d.uploadedFileName) out.push(`Файл: ${d.uploadedFileName}`);
  }
  if (d.imageResolution || d.aspectRatio || d.imageQuality) {
    out.push([d.aspectRatio, d.imageResolution, d.imageQuality].filter(Boolean).join(" · "));
  }
  const main = slots.find((x) => x.title)?.title;
  if (main) out.push(main.startsWith("Промт") ? main : `Промт: ${main}`);
  if (d.slotIndex != null) out.push(`Слот: ${d.slotIndex}`);
  return out;
}
