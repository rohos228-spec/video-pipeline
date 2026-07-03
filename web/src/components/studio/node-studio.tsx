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
  Sparkles,
  Upload,
  X,
} from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { getNodeSpec } from "@/lib/node-catalog";
import { nodeTypeFromKey } from "@/lib/node-key";
import { stepCodeForNodeType, stepHasPromptVariants } from "@/lib/node-step-map";
import {
  defaultPromptSlots,
  isEnrichNode,
  nodeTypeRequiresExcel,
  pipelinePromptSlots,
  resolvePromptSlots,
  type NodePromptSlot,
} from "@/lib/node-prompts";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { formatNodeKeyLabel, humanizeSlug } from "@/lib/format-labels";
import { promptPathsForNode } from "@/lib/prompt-catalog";
import {
  activeVariantForSlot,
  preferredPromptFileName,
  withSlotVariant,
} from "@/lib/prompt-slot-storage";
import {
  nodeUsesRawXlsxGrid,
  pickDefaultSheetForNode,
  xlsxPreviewFocusForNode,
} from "@/lib/xlsx-sheets";
import { FramePromptsPanel } from "@/components/studio/frame-prompts-panel";
import { NodeStepParamsPanel } from "@/components/studio/node-step-params-panel";
import { PromptFilesPanel } from "@/components/studio/prompt-files-panel";
import { GptTextPanel } from "@/components/studio/gpt-text-panel";
import { BlocksWeightPanel } from "@/components/studio/blocks-weight-panel";
import { shouldShowStopBar } from "@/lib/project-running";

type StudioTab = "settings" | "prompts" | "results" | "excel";

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

  const [tab, setTab] = useState<StudioTab>(initialTab);
  const [activeSlotId, setActiveSlotId] = useState<string | null>(null);
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
  const generationRunning = shouldShowStopBar(
    project.data?.status,
    project.data?.generation_active,
  );
  const artifacts = useQuery({
    queryKey: ["artifacts", projectId, nodeType],
    queryFn: () => api.listArtifacts({ project_id: projectId! }),
    enabled: open && projectId != null,
  });
  const promptCatalog = useQuery({
    queryKey: ["prompt-studio-catalog"],
    queryFn: () => api.promptStudioCatalog(),
    enabled: open,
    staleTime: 5 * 60_000,
  });
  const blocksV2StepId = promptCatalog.data?.node_type_to_step[nodeType];

  const allSlots = useMemo(() => {
    if (promptSlotsProp?.length) return resolvePromptSlots(nodeType, promptSlotsProp);
    const meta = (project.data?.meta || {}) as { custom_prompts?: Record<string, NodePromptSlot[]> };
    if (nodeKey && meta.custom_prompts?.[nodeKey]) {
      return resolvePromptSlots(nodeType, meta.custom_prompts[nodeKey]);
    }
    return resolvePromptSlots(nodeType, null);
  }, [project.data?.meta, nodeKey, nodeType, promptSlotsProp]);

  const showExcel =
    nodeTypeRequiresExcel(nodeType) ||
    allSlots.some((s) => s.kind === "excel") ||
    isEnrichNode(nodeType) ||
    tab === "excel";
  const rawGrid = nodeUsesRawXlsxGrid(nodeType);
  const xlsxFocus = xlsxPreviewFocusForNode(nodeType);

  const xlsxSheetsMeta = useQuery({
    queryKey: ["xlsx-sheets", projectId],
    queryFn: () => api.previewProjectXlsx(projectId!, { maxRows: 1 }),
    enabled: open && projectId != null && showExcel,
  });

  const xlsxPreview = useQuery({
    queryKey: ["xlsx-preview", projectId, xlsxSheet, rawGrid, xlsxFocus?.startRow],
    queryFn: () =>
      api.previewProjectXlsx(projectId!, {
        sheet: xlsxSheet || undefined,
        raw: rawGrid || Boolean(xlsxFocus),
        maxRows: xlsxFocus?.maxRows ?? (rawGrid ? 200 : 40),
        maxCols: rawGrid || xlsxFocus ? 24 : 80,
        startRow: xlsxFocus?.startRow,
      }),
    enabled:
      open &&
      projectId != null &&
      (tab === "excel" || isEnrichNode(nodeType)) &&
      Boolean(xlsxSheet || xlsxSheetsMeta.data?.sheets?.length),
  });

  const pipelineSlots = useMemo(() => pipelinePromptSlots(allSlots), [allSlots]);

  useEffect(() => {
    if (!open) return;
    setTab(initialTab);
  }, [open, initialTab]);

  useEffect(() => {
    if (!open) return;
    if (promptFocus?.id) {
      setActiveSlotId(promptFocus.id);
      return;
    }
    const firstPrompt = pipelineSlots.find((s) => s.kind !== "excel");
    setActiveSlotId(firstPrompt?.id ?? pipelineSlots[0]?.id ?? null);
  }, [promptFocus, pipelineSlots, open]);

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

  useEffect(() => {
    if (promptFocus && open) {
      setTab(promptFocus.kind === "excel" ? "excel" : "prompts");
    }
  }, [promptFocus, open]);

  const activeSlot =
    allSlots.find((s) => s.id === activeSlotId) ??
    (promptFocus?.kind === "text" ? promptFocus : null) ??
    pipelineSlots[0] ??
    null;

  const activeStepCode = slotStepCode(activeSlot, stepCode);
  const promptPaths = promptPathsForNode(nodeType);
  const metaRecord = (project.data?.meta || {}) as Record<string, unknown>;
  const promptOverrides = (project.data?.prompt_overrides || {}) as Record<string, unknown>;
  const activeVariant =
    activeSlot && nodeKey
      ? activeVariantForSlot(metaRecord, nodeKey, activeSlot, promptOverrides, activeStepCode)
      : "default";
  const preferredFile = preferredPromptFileName(activeSlot);

  const activateVariant = useMutation({
    mutationFn: async (variant: string) => {
      if (!projectId || !activeStepCode || !nodeKey || !activeSlot) {
        return Promise.reject(new Error("no step"));
      }
      const meta = withSlotVariant(metaRecord, nodeKey, activeSlot.id, variant);
      await api.patchProject(projectId, { meta });
      return api.patchProjectPromptConfig(projectId, {
        legacy: { [activeStepCode]: variant },
      });
    },
    onSuccess: () => {
      toast.success("Активный промт обновлён");
      qc.invalidateQueries({ queryKey: ["project", projectId] });
    },
    onError: (e) => toast.error(String(e)),
  });

  const runStep = useMutation({
    mutationFn: () => api.runProjectStep(projectId!, stepCode!),
    onSuccess: () => {
      toast.success(`Шаг «${spec.label}» запущен`);
      qc.invalidateQueries({ queryKey: ["project", projectId] });
    },
    onError: (e) => toast.error(String(e)),
  });

  const reloadXlsx = useMutation({
    mutationFn: () => api.reloadProjectXlsx(projectId!),
    onSuccess: () => {
      toast.success("Таблица перечитана из файла");
      qc.invalidateQueries({ queryKey: ["xlsx-preview", projectId] });
      qc.invalidateQueries({ queryKey: ["xlsx-sheets", projectId] });
      qc.invalidateQueries({ queryKey: ["xlsx-general-plan", projectId] });
    },
    onError: (e) => toast.error(String(e)),
  });

  const uploadXlsx = useMutation({
    mutationFn: (file: File) => api.uploadProjectXlsx(projectId!, file),
    onSuccess: () => {
      toast.success("Excel загружен");
      qc.invalidateQueries({ queryKey: ["xlsx-preview", projectId] });
      qc.invalidateQueries({ queryKey: ["xlsx-sheets", projectId] });
      qc.invalidateQueries({ queryKey: ["xlsx-general-plan", projectId] });
    },
    onError: (e) => toast.error(String(e)),
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
    (nodeType === "plan" || nodeType === "script" || nodeType === "split");

  const showGptTextPanel = activeSlot?.kind === "text" && activeStepCode && projectId;
  const showFramePromptsPanel =
    activeSlot?.kind === "frame_prompts" && projectId != null;
  const showFilesPanel =
    activeSlot?.kind === "gpt" &&
    Boolean(activeStepCode) &&
    stepHasPromptVariants(activeStepCode);
  const showBlocksPanel = activeSlot?.kind === "blocks";

  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  if (!nodeKey || !mounted || !open) return null;

  const closeNow = (e: SyntheticEvent) => {
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
                  <Sparkles className="h-4 w-4 text-amber-400" />
                  {spec.label}
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
                      if (slot.kind === "excel") setTab("excel");
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
                  ) : showFilesPanel && activeStepCode ? (
                    <div className="flex flex-col gap-4">
                      <PromptFilesPanel
                        key={`files-${nodeKey}-${activeSlot?.id}-${activeStepCode}`}
                        stepCode={activeStepCode}
                        slotId={activeSlot?.id}
                        preferredFile={preferredFile}
                        folderHint={
                          activeSlot?.stepCode && activeSlot.stepCode !== stepCode
                            ? activeSlot.stepCode
                            : (promptPaths.legacyDir ?? activeStepCode)
                        }
                        activeVariant={activeVariant}
                        onActivateVariant={(variant) => activateVariant.mutate(variant)}
                        activating={activateVariant.isPending}
                      />
                      {projectId != null && blocksV2StepId && (
                        <BlocksWeightPanel
                          key={`blocks-${nodeKey}-${blocksV2StepId}`}
                          projectId={projectId}
                          stepId={blocksV2StepId}
                          promptOverrides={promptOverrides}
                        />
                      )}
                    </div>
                  ) : showBlocksPanel ? (
                    <p className="text-sm text-muted-foreground">
                      Генерация через outsee.io в Chrome. Промты кадров — слот
                      «Промты кадров»; файлы мастер-промта — слот «Мастер-промт».
                    </p>
                  ) : (
                    <p className="text-sm text-muted-foreground">
                      Для этой ноды нет редактируемых промтов на этом шаге.
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
                      className="h-8 max-w-xs rounded-md border border-input bg-background px-2 text-xs"
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
                    <div className="overflow-auto rounded-xl border border-white/10">
                      {rawGrid ? (
                        <table className="min-w-max border-collapse text-left text-xs">
                          <tbody>
                            {(xlsxPreview.data?.rows ?? []).map((row, ri) => (
                              <tr key={ri} className="border-b border-white/5 hover:bg-white/[0.02]">
                                <td className="sticky left-0 z-10 border-r border-white/10 bg-card/95 px-2 py-1.5 text-[10px] text-muted-foreground">
                                  {ri + 1}
                                </td>
                                {row.map((cell, ci) => (
                                  <td
                                    key={ci}
                                    className="max-w-[320px] min-w-[80px] whitespace-pre-wrap border-r border-white/5 px-2 py-1.5 align-top"
                                  >
                                    {cell || "\u00a0"}
                                  </td>
                                ))}
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      ) : (
                        <table className="w-full min-w-[480px] text-left text-[10px]">
                          <thead>
                            <tr className="border-b border-white/10 bg-white/5">
                              {(xlsxPreview.data?.headers ?? []).map((h, i) => (
                                <th key={i} className="px-2 py-1.5 font-medium">
                                  {h}
                                </th>
                              ))}
                            </tr>
                          </thead>
                          <tbody>
                            {(xlsxPreview.data?.rows ?? []).map((row, ri) => (
                              <tr key={ri} className="border-b border-white/5">
                                {row.map((cell, ci) => (
                                  <td key={ci} className="max-w-[140px] truncate px-2 py-1 text-muted-foreground">
                                    {cell}
                                  </td>
                                ))}
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      )}
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
