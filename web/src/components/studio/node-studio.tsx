"use client";

import type { SyntheticEvent } from "react";
import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Download,
  FileSpreadsheet,
  FileText,
  Loader2,
  Play,
  RefreshCw,
  Settings2,
  Upload,
  X,
} from "lucide-react";
import { toast } from "sonner";
import { errorMessageFromUnknown } from "@/lib/error-message";
import { api } from "@/lib/api";
import { getNodeSpec } from "@/lib/node-catalog";
import { getNodeIcon } from "@/lib/node-icons";
import { nodeTypeFromKey } from "@/lib/node-key";
import { stepCodeForNodeType, stepHasPromptVariants } from "@/lib/node-step-map";
import {
  defaultPromptSlots,
  excelGptEnrichStepCode,
  nodeTypeRequiresExcel,
  pipelinePromptSlots,
  resolvePromptSlots,
  resolvePromptSlotsForNode,
  type NodePromptSlot,
} from "@/lib/node-prompts";
import { excelGptSlotIndex, isExcelGptNode, type ExcelGptNodeConfig } from "@/lib/excel-gpt-config";
import { ExcelGptSettingsPanel } from "@/components/studio/excel-gpt-settings-panel";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { formatNodeKeyLabel, humanizeSlug } from "@/lib/format-labels";
import { cn } from "@/lib/utils";
import { promptPathsForNode, legacyPromptFolder } from "@/lib/prompt-catalog";
import {
  activeVariantForExcelGpt,
  activeVariantForSlot,
  preferredPromptFileName,
  withSlotVariant,
} from "@/lib/prompt-slot-storage";
import {
  pickDefaultSheetForNode,
  xlsxPreviewFocusForNode,
} from "@/lib/xlsx-sheets";
import { FramePromptsPanel } from "@/components/studio/frame-prompts-panel";
import { NodeStepParamsPanel } from "@/components/studio/node-step-params-panel";
import { PromptFilesPanel } from "@/components/studio/prompt-files-panel";
import { GptTextPanel } from "@/components/studio/gpt-text-panel";
import { PromptBuilderStudio } from "@/components/prompt-builder/prompt-builder-studio";
import { nodeSupportsBlocksV2 } from "@/lib/prompt-builder/step-compose-map";
import { shouldShowStopBar } from "@/lib/project-running";

type StudioTab = "settings" | "prompts" | "results" | "excel";
type PromptEditMode = "classic" | "constructor";

function slotStepCode(slot: NodePromptSlot | null, nodeStepCode: string | undefined): string | undefined {
  return slot?.stepCode ?? nodeStepCode;
}

export function NodeStudio({
  open,
  onOpenChange,
  projectId,
  nodeKey,
  initialTab = "settings",
  promptFocus,
  nodeDisabled = false,
  promptSlots: promptSlotsProp,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  projectId: number | null;
  nodeKey: string | null;
  initialTab?: StudioTab;
  promptFocus?: NodePromptSlot | null;
  nodeDisabled?: boolean;
  promptSlots?: NodePromptSlot[];
}) {
  const nodeType = nodeTypeFromKey(nodeKey);
  const spec = getNodeSpec(nodeType);
  const stepCode = stepCodeForNodeType(nodeType);
  const NodeIcon = getNodeIcon(spec.iconKey);

  const [tab, setTab] = useState<StudioTab>(initialTab);
  const [activeSlotId, setActiveSlotId] = useState<string | null>(null);
  const [promptMode, setPromptMode] = useState<PromptEditMode>("classic");
  const [xlsxSheet, setXlsxSheet] = useState<string>("");
  const fileRef = useRef<HTMLInputElement>(null);

  const qc = useQueryClient();
  const project = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => api.getProject(projectId!),
    enabled: open && projectId != null,
    refetchInterval: (q) =>
      open && shouldShowStopBar(q.state.data?.status, q.state.data?.generation_active)
        ? 1500
        : false,
  });
  const globalActivePrompts = useQuery({
    queryKey: ["prompt-global-active"],
    queryFn: () => api.getGlobalActivePrompts(),
    enabled: open,
    staleTime: 5000,
  });
  const generationRunning = shouldShowStopBar(
    project.data?.status,
    project.data?.generation_active,
  );

  const excelGptConfig = useMemo((): ExcelGptNodeConfig => {
    if (!nodeKey || !isExcelGptNode(nodeType)) return {};
    const meta = (project.data?.meta || {}) as {
      excel_gpt_nodes?: Record<string, ExcelGptNodeConfig>;
    };
    const cfg = meta.excel_gpt_nodes?.[nodeKey] ?? {};
    const slotIndex = excelGptSlotIndex(nodeKey, cfg.slotIndex);
    return {
      label: cfg.label ?? spec.label,
      inputSource: cfg.inputSource ?? "project_xlsx",
      uploadedFileName: cfg.uploadedFileName,
      slotIndex,
    };
  }, [project.data?.meta, nodeKey, nodeType, spec.label]);

  const [excelConfig, setExcelConfig] = useState<ExcelGptNodeConfig>(excelGptConfig);
  useEffect(() => {
    setExcelConfig(excelGptConfig);
  }, [excelGptConfig]);

  const patchExcelNodeData = (patch: Partial<ExcelGptNodeConfig>) => {
    if (!nodeKey) return;
    setExcelConfig((prev) => ({ ...prev, ...patch }));
    window.dispatchEvent(
      new CustomEvent("canvas-patch-node-data", {
        detail: { nodeKey, patch },
      }),
    );
  };
  const artifacts = useQuery({
    queryKey: ["artifacts", projectId, nodeType],
    queryFn: () => api.listArtifacts({ project_id: projectId! }),
    enabled: open && projectId != null,
  });
  const allSlots = useMemo(() => {
    if (promptSlotsProp?.length) return resolvePromptSlots(nodeType, promptSlotsProp, nodeKey ?? undefined);
    const meta = (project.data?.meta || {}) as { custom_prompts?: Record<string, NodePromptSlot[]> };
    if (nodeKey) return resolvePromptSlotsForNode(nodeKey, nodeType, meta.custom_prompts);
    return resolvePromptSlots(nodeType, null);
  }, [project.data?.meta, nodeKey, nodeType, promptSlotsProp]);

  const showExcel =
    !isExcelGptNode(nodeType) &&
    (nodeTypeRequiresExcel(nodeType) ||
      allSlots.some((s) => s.kind === "excel") ||
      tab === "excel");

  const xlsxSheetsMeta = useQuery({
    queryKey: ["xlsx-sheets", projectId],
    queryFn: () => api.previewProjectXlsx(projectId!, { maxRows: 1 }),
    enabled: open && projectId != null && showExcel,
  });

  const xlsxFocus = useMemo(() => xlsxPreviewFocusForNode(nodeType), [nodeType]);

  const xlsxPreview = useQuery({
    queryKey: ["xlsx-preview", projectId, xlsxSheet, xlsxFocus?.startRow],
    queryFn: () =>
      api.previewProjectXlsx(projectId!, {
        sheet: xlsxSheet || undefined,
        raw: true,
        maxRows: xlsxFocus?.maxRows ?? 500,
        maxCols: 200,
        startRow: xlsxFocus?.startRow ?? 1,
      }),
    enabled:
      open &&
      projectId != null &&
      tab === "excel" &&
      Boolean(xlsxSheet || xlsxSheetsMeta.data?.sheets?.length),
  });

  const pipelineSlots = useMemo(() => pipelinePromptSlots(allSlots), [allSlots]);

  useEffect(() => {
    if (!open) return;
    if (promptFocus) {
      setTab(promptFocus.kind === "excel" ? "excel" : "prompts");
      return;
    }
    setTab(initialTab);
  }, [open, initialTab, promptFocus]);

  useEffect(() => {
    if (!open) return;
    if (promptFocus?.id) {
      setActiveSlotId(promptFocus.id);
      return;
    }
    const firstPrompt = pipelineSlots.find((s) => s.kind !== "excel");
    if (firstPrompt) {
      setActiveSlotId(firstPrompt.id);
      return;
    }
    const excel = pipelineSlots.find((s) => s.kind === "excel");
    if (excel) {
      setActiveSlotId(excel.id);
    } else {
      setActiveSlotId(pipelineSlots[0]?.id ?? null);
    }
  }, [promptFocus, pipelineSlots, open, nodeKey]);

  useEffect(() => {
    if (!open || tab !== "excel") return;
    const sheets = xlsxSheetsMeta.data?.sheets ?? [];
    if (!sheets.length) return;
    setXlsxSheet((prev) => {
      if (prev && sheets.includes(prev)) return prev;
      return pickDefaultSheetForNode(nodeType, sheets);
    });
  }, [open, tab, nodeType, xlsxSheetsMeta.data?.sheets]);

  useEffect(() => {
    if (!open) setXlsxSheet("");
  }, [open, nodeKey]);

  const activeSlot =
    allSlots.find((s) => s.id === activeSlotId) ??
    (promptFocus?.kind === "text" ? promptFocus : null) ??
    pipelineSlots[0] ??
    null;

  const activeStepCode = slotStepCode(activeSlot, stepCode);
  const enrichStepCode = excelGptEnrichStepCode(nodeKey ?? undefined, excelConfig.slotIndex);
  const promptStepCode =
    isExcelGptNode(nodeType) && activeSlot?.kind === "gpt"
      ? enrichStepCode
      : activeStepCode;
  const promptPaths = promptPathsForNode(nodeType);
  const metaRecord = (project.data?.meta || {}) as Record<string, unknown>;
  const promptOverrides = (project.data?.prompt_overrides || {}) as Record<string, unknown>;
  const activeVariant =
    activeSlot && nodeKey
      ? isExcelGptNode(nodeType) && activeSlot.kind === "gpt"
        ? activeVariantForExcelGpt(
            metaRecord,
            nodeKey,
            activeSlot,
            promptOverrides,
            excelConfig.slotIndex,
            globalActivePrompts.data,
          )
        : activeVariantForSlot(
            metaRecord,
            nodeKey,
            activeSlot,
            promptOverrides,
            promptStepCode,
            globalActivePrompts.data,
          )
      : "default";
  const preferredFile = preferredPromptFileName(activeSlot);

  const activateVariant = useMutation({
    mutationFn: async (variant: string) => {
      if (!projectId || !promptStepCode || !nodeKey || !activeSlot) {
        return Promise.reject(new Error("no step"));
      }
      const meta = withSlotVariant(metaRecord, nodeKey, activeSlot.id, variant);
      const prompt_overrides = {
        ...((project.data?.prompt_overrides || {}) as Record<string, unknown>),
        [promptStepCode]: variant,
      };
      await api.patchProject(projectId, { meta, prompt_overrides });
    },
    onSuccess: () => {
      toast.success("Активный промт обновлён");
      qc.invalidateQueries({ queryKey: ["project", projectId] });
      qc.invalidateQueries({ queryKey: ["prompt-global-active"] });
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  const runStep = useMutation({
    mutationFn: () => api.runProjectStep(projectId!, stepCode!, { nodeKey: nodeKey ?? undefined }),
    onSuccess: () => {
      toast.success(`Шаг «${spec.label}» запущен`);
      qc.invalidateQueries({ queryKey: ["project", projectId] });
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  const reloadXlsx = useMutation({
    mutationFn: () => api.reloadProjectXlsx(projectId!),
    onSuccess: () => {
      toast.success("Таблица перечитана из файла");
      qc.invalidateQueries({ queryKey: ["xlsx-preview", projectId] });
      qc.invalidateQueries({ queryKey: ["xlsx-sheets", projectId] });
      qc.invalidateQueries({ queryKey: ["xlsx-general-plan", projectId] });
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  const uploadXlsx = useMutation({
    mutationFn: (file: File) => api.uploadProjectXlsx(projectId!, file),
    onSuccess: () => {
      toast.success("Excel загружен");
      qc.invalidateQueries({ queryKey: ["xlsx-preview", projectId] });
      qc.invalidateQueries({ queryKey: ["xlsx-sheets", projectId] });
      qc.invalidateQueries({ queryKey: ["xlsx-general-plan", projectId] });
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  const filteredArtifacts = useMemo(() => {
    const list = artifacts.data ?? [];
    if (nodeType.includes("image") || nodeType === "images") {
      return list.filter((a) => a.kind.includes("image") || a.kind.includes("scene"));
    }
    if (nodeType.includes("video") || nodeType === "videos") {
      return list.filter((a) => a.kind.includes("video"));
    }
    if (nodeType === "hero" || nodeType === "items") {
      return list.filter((a) => a.kind.includes("hero") || a.kind.includes("item"));
    }
    return list.slice(0, 12);
  }, [artifacts.data, nodeType]);

  const showStepParams =
    projectId != null &&
    (nodeType === "plan" ||
      nodeType === "script" ||
      nodeType === "split" ||
      nodeType === "audio" ||
      nodeType === "assemble");

  const showGptTextPanel = activeSlot?.kind === "text" && activeStepCode && projectId;
  const showFramePromptsPanel =
    activeSlot?.kind === "frame_prompts" && projectId != null;
  const showFilesPanel =
    activeSlot?.kind === "gpt" &&
    Boolean(promptStepCode) &&
    stepHasPromptVariants(promptStepCode);
  const supportsPromptConstructor =
    projectId != null &&
    Boolean(promptStepCode) &&
    nodeSupportsBlocksV2(
      nodeType,
      promptStepCode,
      nodeKey,
      isExcelGptNode(nodeType) ? excelConfig.slotIndex : undefined,
    );
  const builderNodeType = isExcelGptNode(nodeType)
    ? (promptStepCode ?? enrichStepCode)
    : nodeType;

  useEffect(() => {
    if (open) setPromptMode("classic");
  }, [open, activeSlotId, nodeKey]);

  const [mounted, setMounted] = useState(false);
  const backdropGuardUntil = useRef(0);
  useEffect(() => setMounted(true), []);

  useEffect(() => {
    if (open) backdropGuardUntil.current = Date.now() + 500;
  }, [open]);

  if (!nodeKey || !mounted || !open) return null;

  const closeNow = (e: SyntheticEvent) => {
    if (Date.now() < backdropGuardUntil.current) return;
    e.preventDefault();
    e.stopPropagation();
    onOpenChange(false);
  };

  return createPortal(
    <>
      <button
        type="button"
        aria-label="Закрыть студию"
        className="fixed inset-0 z-[90] bg-black/45 backdrop-blur-[2px]"
        onPointerDown={(e) => {
          e.preventDefault();
          e.stopPropagation();
        }}
        onMouseDown={(e) => {
          e.preventDefault();
          e.stopPropagation();
        }}
        onClick={closeNow}
      />
      <aside
        className="premium-sheet fixed right-0 top-0 z-[100] flex h-full w-[min(920px,92vw)] flex-col border-l border-white/10 shadow-2xl"
        role="dialog"
        aria-modal="true"
      >
        <div className="flex h-full flex-col">
          <header className="relative shrink-0 border-b border-white/10 bg-gradient-to-r from-amber-500/5 via-transparent to-violet-500/5 px-5 py-4">
            <button
              type="button"
              aria-label="Закрыть студию"
              title="Закрыть (Esc)"
              className="absolute right-3 top-3 z-[210] inline-flex h-9 w-9 cursor-pointer items-center justify-center rounded-md bg-background/90 text-foreground/90 ring-1 ring-white/10 transition hover:bg-destructive hover:text-destructive-foreground"
              onPointerDown={(e) => e.stopPropagation()}
              onMouseDown={(e) => e.stopPropagation()}
              onClickCapture={closeNow}
              onClick={closeNow}
            >
              <X className="h-5 w-5" />
            </button>
            <div className="flex items-start justify-between gap-4 pr-12">
              <div>
                <h2 className="flex items-center gap-2 text-lg font-semibold">
                  <span
                    className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl shadow-inner"
                    style={{
                      background: `linear-gradient(135deg, hsl(${spec.accent} / 0.25), hsl(${spec.accent} / 0.08))`,
                      color: `hsl(${spec.accent})`,
                    }}
                  >
                    <NodeIcon className="h-4 w-4" />
                  </span>
                  {excelConfig.label?.trim() || spec.label}
                </h2>
                <p className="text-xs text-muted-foreground">{spec.description}</p>
                <div className="mt-2 flex flex-wrap gap-1.5">
                  <Badge variant="muted" className="text-[10px]">
                    {formatNodeKeyLabel(nodeKey)}
                  </Badge>
                  {promptPaths.legacyDir && (
                    <Badge variant="muted" className="text-[9px] font-mono">
                      prompts/{promptPaths.legacyDir}
                    </Badge>
                  )}
                </div>
              </div>
              <div className="flex w-full flex-col gap-2 sm:flex-row sm:flex-wrap">
                {stepCode && (
                  <Button
                    size="sm"
                    variant="default"
                    onClick={() => runStep.mutate()}
                    disabled={
                      !projectId || runStep.isPending || nodeDisabled || generationRunning
                    }
                    title={
                      generationRunning
                        ? "Шаг уже выполняется — нажмите ⏹ Остановить"
                        : nodeDisabled
                          ? "Нода отключена в графе"
                          : undefined
                    }
                  >
                    {runStep.isPending ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <Play className="h-3.5 w-3.5" />
                    )}
                    Запустить шаг
                  </Button>
                )}
              </div>
            </div>
            <div className="mt-3 flex flex-wrap gap-1">
              {(
                [
                  ["settings", "Настройки", Settings2],
                  ["prompts", "Промты GPT", FileText],
                  ...(showExcel ? [["excel", "Excel", FileSpreadsheet] as const] : []),
                  ["results", "Результаты", FileText],
                ] as const
              ).map(([id, label, Icon]) => (
                <Button
                  key={id}
                  type="button"
                  size="sm"
                  variant={tab === id ? "default" : "ghost"}
                  className="gap-1.5 text-xs"
                  onClick={() => setTab(id)}
                >
                  <Icon className="h-3.5 w-3.5" />
                  {label}
                </Button>
              ))}
            </div>
            {tab === "prompts" && pipelineSlots.length > 0 && !showGptTextPanel && (
              <div className="mt-3 flex flex-wrap gap-1 border-t border-white/5 pt-3">
                {pipelineSlots.map((slot) => (
                  <Button
                    key={slot.id}
                    size="sm"
                    variant={activeSlotId === slot.id ? "default" : "outline"}
                    className="h-7 text-[10px]"
                    onClick={() => {
                      setActiveSlotId(slot.id);
                      if (slot.kind === "excel" && !isExcelGptNode(nodeType)) setTab("excel");
                      if (slot.kind === "excel" && isExcelGptNode(nodeType)) setTab("settings");
                    }}
                  >
                    {slot.title}
                  </Button>
                ))}
              </div>
            )}
          </header>

          <ScrollArea className="flex-1">
            <div className="p-5">
              {tab === "settings" && (
                <div className="flex flex-col gap-4 text-sm text-muted-foreground">
                  {isExcelGptNode(nodeType) && projectId && nodeKey ? (
                    <ExcelGptSettingsPanel
                      projectId={projectId}
                      nodeKey={nodeKey}
                      config={excelConfig}
                      onConfigChange={patchExcelNodeData}
                    />
                  ) : null}
                  {showStepParams ? (
                    <NodeStepParamsPanel projectId={projectId!} nodeType={nodeType} />
                  ) : null}
                  <p>
                    Мастер-промты выбираются через «Файлы промтов» на вкладке «Промты GPT».
                    Сопроводительный текст для ChatGPT редактируется отдельно — кнопка «Текстовый
                    вариант» в меню V.
                  </p>
                  {nodeDisabled && (
                    <p className="text-amber-400">Нода отключена в графе — шаг не запустится.</p>
                  )}
                </div>
              )}

              {tab === "prompts" && (
                <div className="flex flex-col gap-4">
                  {activeSlot && !showGptTextPanel && showFilesPanel && (
                    <p className="text-xs text-muted-foreground">
                      Редактируется:{" "}
                      <span className="font-medium text-foreground">{activeSlot.title}</span>
                    </p>
                  )}
                  {showFilesPanel && supportsPromptConstructor && (
                    <div className="flex gap-1 rounded-lg border border-white/10 bg-white/[0.02] p-1">
                      <button
                        type="button"
                        onClick={() => setPromptMode("classic")}
                        className={cn(
                          "flex-1 rounded-md px-3 py-2 text-xs font-medium transition",
                          promptMode === "classic"
                            ? "bg-primary/15 text-foreground"
                            : "text-muted-foreground hover:bg-white/[0.04]",
                        )}
                      >
                        Классический промт
                      </button>
                      <button
                        type="button"
                        onClick={() => setPromptMode("constructor")}
                        className={cn(
                          "flex-1 rounded-md px-3 py-2 text-xs font-medium transition",
                          promptMode === "constructor"
                            ? "bg-primary/15 text-foreground"
                            : "text-muted-foreground hover:bg-white/[0.04]",
                        )}
                      >
                        Конструктор промтов
                      </button>
                    </div>
                  )}
                  {showGptTextPanel ? (
                    <GptTextPanel
                      key={`gpt-${activeSlot?.id}-${activeStepCode}`}
                      projectId={projectId}
                      stepCode={activeStepCode}
                    />
                  ) : showFramePromptsPanel ? (
                    <FramePromptsPanel
                      key={`frame-prompts-${projectId}`}
                      projectId={projectId}
                      field="image_prompt"
                    />
                  ) : showFilesPanel &&
                    promptMode === "constructor" &&
                    supportsPromptConstructor &&
                    projectId &&
                    promptStepCode ? (
                    <div className="min-h-[min(70vh,720px)] overflow-hidden rounded-xl border border-white/10">
                      <PromptBuilderStudio
                        key={`builder-${nodeKey}-${promptStepCode}`}
                        projectId={projectId}
                        nodeType={builderNodeType}
                        stepCode={promptStepCode}
                        fullscreen={false}
                      />
                    </div>
                  ) : showFilesPanel && promptStepCode ? (
                    <PromptFilesPanel
                      key={`files-${nodeKey}-${activeSlot?.id}-${promptStepCode}`}
                      stepCode={promptStepCode}
                      slotId={activeSlot?.id}
                      preferredFile={preferredFile}
                      folderHint={
                        legacyPromptFolder(promptStepCode) ??
                        (activeSlot?.stepCode && activeSlot.stepCode !== stepCode
                          ? activeSlot.stepCode
                          : (promptPaths.legacyDir ?? promptStepCode))
                      }
                      activeVariant={activeVariant}
                      onActivateVariant={(variant) => activateVariant.mutate(variant)}
                      activating={activateVariant.isPending}
                    />
                  ) : activeSlot?.kind === "excel" ? (
                    <div className="flex flex-col gap-3 text-sm text-muted-foreground">
                      <p>
                        Для этой ноды мастер-промт задаётся через{" "}
                        <span className="font-medium text-foreground">project.xlsx</span> — откройте
                        вкладку «Excel».
                      </p>
                      <Button
                        size="sm"
                        variant="outline"
                        className="w-fit"
                        onClick={() => setTab("excel")}
                      >
                        Открыть Excel
                      </Button>
                    </div>
                  ) : (
                    <p className="text-sm text-muted-foreground">
                      Для этой ноды нет редактируемых промтов на этом шаге. Добавьте слот через «+
                      ещё» в меню V.
                    </p>
                  )}
                </div>
              )}

              {tab === "excel" && projectId && (
                <div className="flex flex-col gap-3">
                  <div className="flex flex-wrap gap-2">
                    <Button size="sm" variant="outline" asChild>
                      <a href={api.downloadProjectXlsx(projectId)} download>
                        <Download className="h-3.5 w-3.5" />
                        Скачать Excel
                      </a>
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => fileRef.current?.click()}
                      disabled={uploadXlsx.isPending}
                    >
                      <Upload className="h-3.5 w-3.5" />
                      Загрузить
                    </Button>
                    <input
                      ref={fileRef}
                      type="file"
                      accept=".xlsx"
                      className="hidden"
                      onChange={(e) => {
                        const f = e.target.files?.[0];
                        if (f) uploadXlsx.mutate(f);
                      }}
                    />
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => reloadXlsx.mutate()}
                      disabled={reloadXlsx.isPending}
                    >
                      <RefreshCw className="h-3.5 w-3.5" />
                      Перечитать
                    </Button>
                  </div>
                  {(xlsxSheetsMeta.data?.sheets?.length ?? 0) > 0 && (
                    <select
                      className="studio-select h-8 max-w-xs rounded-md border border-input bg-card px-2 text-xs"
                      value={xlsxSheet || pickDefaultSheetForNode(nodeType, xlsxSheetsMeta.data?.sheets ?? [])}
                      onChange={(e) => setXlsxSheet(e.target.value)}
                    >
                      {(xlsxSheetsMeta.data?.sheets ?? []).map((s) => (
                        <option key={s} value={s}>
                          {s}
                        </option>
                      ))}
                    </select>
                  )}
                  {(xlsxSheetsMeta.isLoading || xlsxPreview.isLoading) && (
                    <div className="flex items-center justify-center py-12">
                      <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
                    </div>
                  )}
                  {!xlsxSheetsMeta.isLoading && !xlsxPreview.isLoading && (
                    <div className="max-h-[min(70vh,720px)] overflow-auto rounded-xl border border-white/10">
                      {xlsxFocus?.hint ? (
                        <p className="border-b border-white/10 bg-amber-500/10 px-3 py-2 text-[11px] text-amber-100/90">
                          {xlsxFocus.hint}
                        </p>
                      ) : null}
                      <table className="min-w-max border-collapse text-left text-xs">
                        <tbody>
                          {(xlsxPreview.data?.rows ?? []).map((row, ri) => (
                            <tr key={ri} className="border-b border-white/5 hover:bg-white/[0.02]">
                              <td className="sticky left-0 z-10 border-r border-white/10 bg-card/95 px-2 py-1.5 text-[10px] text-muted-foreground">
                                {(xlsxFocus?.startRow ?? 1) + ri}
                              </td>
                              {row.map((cell, ci) => (
                                <td
                                  key={ci}
                                  className="min-w-[72px] max-w-[420px] whitespace-pre-wrap border-r border-white/5 px-2 py-1.5 align-top"
                                >
                                  {cell || "\u00a0"}
                                </td>
                              ))}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                      {!xlsxPreview.data?.rows?.length && (
                        <p className="p-4 text-xs text-muted-foreground">
                          {nodeType === "plan"
                            ? "Лист «Общий план» пуст или Excel ещё не создан — запустите шаг или загрузите project.xlsx."
                            : "Таблица пуста или ещё не создана."}
                          {nodeType === "plan" && project.data?.general_plan?.trim() ? (
                            <span className="mt-2 block whitespace-pre-wrap text-foreground/90">
                              Текст плана в БД: {project.data.general_plan}
                            </span>
                          ) : null}
                        </p>
                      )}
                    </div>
                  )}
                </div>
              )}

              {tab === "results" && (
                <div className="grid gap-3 sm:grid-cols-2">
                  {filteredArtifacts.length === 0 ? (
                    <p className="text-sm text-muted-foreground">Артефактов пока нет.</p>
                  ) : (
                    filteredArtifacts.map((a) => (
                      <div
                        key={a.id}
                        className="rounded-xl border border-white/10 bg-white/5 p-2"
                      >
                        <div className="text-[10px] uppercase text-muted-foreground">
                          {humanizeSlug(a.kind)}
                        </div>
                        {a.path.match(/\.(mp4|webm)$/i) ? (
                          <video
                            controls
                            className="mt-1 max-h-40 w-full rounded"
                            src={api.artifactFileUrl(a.uuid)}
                          />
                        ) : (
                          <img
                            alt=""
                            className="mt-1 max-h-40 w-full rounded object-contain"
                            src={api.artifactFileUrl(a.uuid)}
                          />
                        )}
                        <a
                          href={api.artifactFileUrl(a.uuid)}
                          download
                          className="mt-2 inline-flex items-center gap-1 text-[10px] text-primary hover:underline"
                        >
                          <Download className="h-3 w-3" />
                          Скачать
                        </a>
                      </div>
                    ))
                  )}
                </div>
              )}
            </div>
          </ScrollArea>
        </div>
      </aside>
    </>,
    document.body,
  );
}
