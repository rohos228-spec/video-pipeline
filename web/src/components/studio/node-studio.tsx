"use client";

import type { SyntheticEvent } from "react";
import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Blocks,
  Download,
  FileSpreadsheet,
  FileText,
  Loader2,
  Play,
  RefreshCw,
  Save,
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
import { defaultPromptSlots, isEnrichNode, type NodePromptSlot } from "@/lib/node-prompts";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { cn } from "@/lib/utils";
import { formatNodeKeyLabel, humanizeSlug } from "@/lib/format-labels";
import { promptPathsForNode } from "@/lib/prompt-catalog";
import {
  getPromptStyle,
  setPromptStyleInMeta,
  slotSupportsStyles,
} from "@/lib/prompt-styles";
import { PromptStylePanel } from "@/components/studio/prompt-style-panel";
import { PromptFilesPanel } from "@/components/studio/prompt-files-panel";
import { isProjectRunningStatus } from "@/lib/project-running";
import { StopGenerationBar } from "@/components/studio/stop-generation-bar";

type StudioTab = "settings" | "prompts" | "results" | "excel";

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
  const [composed, setComposed] = useState("");
  const [legacyVariant, setLegacyVariant] = useState("default");
  const [activeSlotId, setActiveSlotId] = useState<string | null>(null);
  const [xlsxSheet, setXlsxSheet] = useState<string>("");
  const fileRef = useRef<HTMLInputElement>(null);

  const qc = useQueryClient();
  const project = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => api.getProject(projectId!),
    enabled: open && projectId != null,
    refetchInterval: (q) =>
      open && isProjectRunningStatus(q.state.data?.status) ? 1500 : false,
  });
  const generationRunning = isProjectRunningStatus(project.data?.status);
  const catalog = useQuery({
    queryKey: ["prompt-studio-catalog"],
    queryFn: api.promptStudioCatalog,
    enabled: open,
  });
  const variants = useQuery({
    queryKey: ["prompt-variants", stepCode],
    queryFn: () =>
      fetch(`/api/prompt-studio/variants/${stepCode}`).then((r) => {
        if (!r.ok) return [] as string[];
        return r.json() as Promise<string[]>;
      }),
    enabled: open && stepHasPromptVariants(stepCode),
  });
  const artifacts = useQuery({
    queryKey: ["artifacts", projectId, nodeType],
    queryFn: () => api.listArtifacts({ project_id: projectId! }),
    enabled: open && projectId != null,
  });
  const xlsxPreview = useQuery({
    queryKey: ["xlsx-preview", projectId, xlsxSheet],
    queryFn: () => api.previewProjectXlsx(projectId!, xlsxSheet || undefined),
    enabled: open && projectId != null && (tab === "excel" || isEnrichNode(nodeType)),
  });

  const customSlots = useMemo(() => {
    if (promptSlotsProp?.length) return promptSlotsProp;
    const meta = (project.data?.meta || {}) as { custom_prompts?: Record<string, NodePromptSlot[]> };
    if (nodeKey && meta.custom_prompts?.[nodeKey]) return meta.custom_prompts[nodeKey];
    return defaultPromptSlots(nodeType);
  }, [project.data?.meta, nodeKey, nodeType, promptSlotsProp]);

  useEffect(() => {
    if (!open) return;
    setTab(initialTab);
  }, [open, initialTab]);

  useEffect(() => {
    if (!open) return;
    const po = (project.data?.prompt_overrides || {}) as Record<string, unknown>;
    if (stepCode && typeof po[stepCode] === "string") {
      setLegacyVariant(po[stepCode] as string);
    }
  }, [open, project.data, stepCode]);

  useEffect(() => {
    if (promptFocus?.id) setActiveSlotId(promptFocus.id);
    else if (customSlots[0]?.id) setActiveSlotId(customSlots[0].id);
  }, [promptFocus, customSlots, open]);

  useEffect(() => {
    if (xlsxPreview.data?.active_sheet && !xlsxSheet) {
      setXlsxSheet(xlsxPreview.data.active_sheet);
    }
  }, [xlsxPreview.data, xlsxSheet]);

  useEffect(() => {
    if (promptFocus && open) {
      setTab(promptFocus.kind === "excel" ? "excel" : "prompts");
    }
  }, [promptFocus, open]);

  const activeSlot = customSlots.find((s) => s.id === activeSlotId) ?? customSlots[0] ?? null;
  const promptPaths = promptPathsForNode(nodeType);
  const metaRecord = (project.data?.meta || {}) as Record<string, unknown>;
  const slotStyle = activeSlot
    ? getPromptStyle(metaRecord, nodeKey ?? "", activeSlot.id)
    : {};

  const persistMetaStyles = async (nextConfig: typeof slotStyle) => {
    if (!projectId || !nodeKey || !activeSlot) return;
    const meta = setPromptStyleInMeta(metaRecord, nodeKey, activeSlot.id, nextConfig);
    await api.patchProject(projectId, { meta });
    await qc.invalidateQueries({ queryKey: ["project", projectId] });
  };

  const compose = useMutation({
    mutationFn: () =>
      api.composePrompt({
        node_type: nodeType,
        project_id: projectId ?? undefined,
        style_preset: slotStyle.style_preset,
        blocks: slotStyle.blocks && Object.keys(slotStyle.blocks).length ? slotStyle.blocks : undefined,
      }),
    onSuccess: (r) => {
      setComposed(r.text);
      toast.success("Промт собран");
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

  const saveConfig = useMutation({
    mutationFn: () =>
      api.patchProjectPromptConfig(projectId!, {
        style_profile: slotStyle.style_preset,
        blocks: slotStyle.blocks ?? {},
        use_blocks_v2: true,
        legacy: stepCode ? { [stepCode]: legacyVariant } : {},
      }),
    onSuccess: () => {
      toast.success("Настройки ноды сохранены");
      qc.invalidateQueries({ queryKey: ["project", projectId] });
    },
    onError: (e) => toast.error(String(e)),
  });

  const reloadXlsx = useMutation({
    mutationFn: () => api.reloadProjectXlsx(projectId!),
    onSuccess: () => {
      toast.success("Таблица перечитана из файла");
      qc.invalidateQueries({ queryKey: ["xlsx-preview", projectId] });
    },
    onError: (e) => toast.error(String(e)),
  });

  const uploadXlsx = useMutation({
    mutationFn: (file: File) => api.uploadProjectXlsx(projectId!, file),
    onSuccess: () => {
      toast.success("Excel загружен");
      qc.invalidateQueries({ queryKey: ["xlsx-preview", projectId] });
    },
    onError: (e) => toast.error(String(e)),
  });

  const blockCategories = catalog.data?.block_categories ?? {};
  const presets = catalog.data?.style_presets ?? [];

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

  const showExcel = isEnrichNode(nodeType) || tab === "excel";
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  if (!nodeKey || !mounted || !open) return null;

  // Universal close handler — используется из крестика, backdrop и Esc.
  // Принудительно вызывает onOpenChange(false). Родитель (studio-workspace)
  // в closeStudio() также снимет выделение ноды и поднимет suppress-таймер.
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
            {/* Крестик закрытия: native <button> вместо shadcn Button, чтобы
                исключить сюрпризы от Radix Slot / cva-вариантов. Перехватываем
                все варианты pointer/mouse-событий + onClickCapture, чтобы клик
                гарантированно дошёл до onClick и не потерялся. */}
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
                  {promptPaths.stepsV2Dir && (
                    <Badge variant="muted" className="text-[9px] font-mono">
                      prompts/steps/{promptPaths.stepsV2Dir}
                    </Badge>
                  )}
                  {promptPaths.checkDir && (
                    <Badge variant="muted" className="text-[9px] font-mono">
                      prompts/check/{promptPaths.checkDir}
                    </Badge>
                  )}
                </div>
              </div>
              <div className="flex w-full flex-col gap-2 sm:flex-row sm:flex-wrap">
                {generationRunning && projectId != null && (
                  <StopGenerationBar
                    projectId={projectId}
                    visible
                    className="!relative !bottom-auto !left-auto !z-auto !w-full !translate-x-0 sm:min-w-[280px]"
                  />
                )}
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
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => saveConfig.mutate()}
                  disabled={!projectId || saveConfig.isPending}
                >
                  {saveConfig.isPending ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <Save className="h-3.5 w-3.5" />
                  )}
                  Сохранить
                </Button>
              </div>
            </div>
            <div className="mt-3 flex flex-wrap gap-1">
              {(
                [
                  ["settings", "Настройки", Settings2],
                  ["prompts", "Промты GPT", Blocks],
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
            {tab === "prompts" && (
              <div className="mt-3 flex flex-wrap gap-1 border-t border-white/5 pt-3">
                {customSlots.map((slot) => (
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
                  <p>
                    Стили задаются отдельно для каждого промта на вкладке «Промты GPT». Папки шаблонов
                    привязаны к шагу пайплайна (см. бейджи сверху).
                  </p>
                  {nodeDisabled && (
                    <p className="text-amber-400">Нода отключена в графе — шаг не запустится.</p>
                  )}
                </div>
              )}

              {tab === "prompts" && (
                <div className="flex flex-col gap-4">
                  {activeSlot && slotSupportsStyles(activeSlot) && (
                    <PromptStylePanel
                      slot={activeSlot}
                      config={slotStyle}
                      blockCategories={blockCategories}
                      catalogPresets={presets}
                      onChange={(next) => void persistMetaStyles(next)}
                    />
                  )}
                  {stepCode && stepHasPromptVariants(stepCode) && (
                    <section>
                      <h3 className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
                        Вариант промта
                      </h3>
                      <select
                        className="mt-2 h-9 w-full rounded-md border border-input bg-background px-2 text-sm"
                        value={legacyVariant}
                        onChange={(e) => setLegacyVariant(e.target.value)}
                      >
                        {(variants.data ?? ["default"]).map((v) => (
                          <option key={v} value={v}>
                            {humanizeSlug(v)}
                          </option>
                        ))}
                      </select>
                    </section>
                  )}
                  {stepCode && (
                    <PromptFilesPanel
                      stepCode={stepCode}
                      folderHint={promptPaths.legacyDir ?? stepCode}
                    />
                  )}
                  <div className="flex flex-wrap gap-2">
                    <Button size="sm" onClick={() => compose.mutate()} disabled={compose.isPending}>
                      {compose.isPending ? (
                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      ) : (
                        <Blocks className="h-3.5 w-3.5" />
                      )}
                      Собрать промт
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => {
                        const blob = new Blob([composed], { type: "text/plain" });
                        const a = document.createElement("a");
                        a.href = URL.createObjectURL(blob);
                        a.download = `${nodeType}-prompt.txt`;
                        a.click();
                      }}
                      disabled={!composed}
                    >
                      <Download className="h-3.5 w-3.5" />
                      Скачать
                    </Button>
                  </div>
                  <Textarea
                    value={composed}
                    onChange={(e) => setComposed(e.target.value)}
                    rows={18}
                    className="font-mono text-[11px] leading-relaxed"
                    placeholder="Соберите промт — здесь финальный текст для ChatGPT"
                  />
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
                  {(xlsxPreview.data?.sheets?.length ?? 0) > 0 && (
                    <select
                      className="h-8 max-w-xs rounded-md border border-input bg-background px-2 text-xs"
                      value={xlsxSheet || xlsxPreview.data?.active_sheet}
                      onChange={(e) => setXlsxSheet(e.target.value)}
                    >
                      {(xlsxPreview.data?.sheets ?? []).map((s) => (
                        <option key={s} value={s}>
                          {s}
                        </option>
                      ))}
                    </select>
                  )}
                  <div className="overflow-auto rounded-xl border border-white/10">
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
                    {!xlsxPreview.data?.rows?.length && (
                      <p className="p-4 text-xs text-muted-foreground">
                        Таблица пуста или ещё не создана — запустите шаг обогащения.
                      </p>
                    )}
                  </div>
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
