"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, ArrowRight, Check, Loader2, Plus, X } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { errorMessageFromUnknown } from "@/lib/error-message";
import { cn } from "@/lib/utils";
import { BlockEditorCenter } from "./block-editor-menu";
import type { PipelineNodeView } from "./pipeline-nodes-overview";
import {
  PromptStructureGraph,
} from "./prompt-structure-graph";
import type { BlockMenuTarget } from "./block-context-menu";
import { BlockActionBar } from "./block-action-bar";
import { EditableLabel } from "./editable-label";
import { PipelineVariantsPanel } from "./pipeline-variants-panel";
import { PromptBuilderLeftRail } from "./prompt-builder-left-rail";
import { PromptRightPanel } from "./prompt-right-panel";
import {
  blocksMapFromSelection,
  displaySlotsForGraph,
  extraSlotsFromProject,
  loadRealPromptBuilder,
  normalizePromptSlotState,
  selectionFromProject,
} from "@/lib/prompt-builder/real-catalog";
import { categoryMetaFor } from "@/lib/prompt-builder/category-meta";
import {
  presetComposePercent,
  resolvePromptPreset,
  selectionFromPromptPreset,
} from "@/lib/prompt-builder/prompt-presets";
import type { StepPresetsFile } from "@/lib/prompt-builder/prompt-presets";
import { fetchStepPresets } from "@/lib/prompt-builder/step-presets-local";
import {
  COMPOSE_STEP_LABELS,
  PIPELINE_RAIL_NODES,
  composeStepIdForNode,
} from "@/lib/prompt-builder/step-compose-map";
import { isSlotEmpty, resolveSlotBlockId } from "@/lib/prompt-builder/compose";
import { CRITERIA_DIMENSIONS, rankBlocksForSlot } from "@/lib/prompt-builder/compatibility";
import {
  agentCountForCells,
  computeProjectCellUsage,
  getCellsForBlock,
} from "@/lib/prompt-builder/excel-cells";
import { agentForBlock } from "@/lib/prompt-builder/agents-catalog";
import type {
  BlockKind,
  BlockVariant,
  PromptSelection,
  PromptSlot,
} from "@/lib/prompt-builder/types";
import "./prompt-builder-aaa.css";

const DEFAULT_DIMS = new Set(CRITERIA_DIMENSIONS.filter((d) => d.toggleable).map((d) => d.id));
const FREE_BLOCK_CATEGORY = "custom_free";

function safeBlockId(input: string): string {
  const cyr: Record<string, string> = {
    а: "a", б: "b", в: "v", г: "g", д: "d", е: "e", ё: "e", ж: "zh", з: "z",
    и: "i", й: "y", к: "k", л: "l", м: "m", н: "n", о: "o", п: "p", р: "r",
    с: "s", т: "t", у: "u", ф: "f", х: "h", ц: "c", ч: "ch", ш: "sh",
    щ: "sch", ъ: "", ы: "y", ь: "", э: "e", ю: "yu", я: "ya",
  };
  const ascii = input
    .trim()
    .toLowerCase()
    .replace(/[а-яё]/g, (ch) => cyr[ch] ?? "")
    .replace(/[^a-z0-9_-]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .replace(/_{2,}/g, "_")
    .slice(0, 70);
  const cleaned = ascii.replace(/^[^a-z0-9]+/, "");
  return cleaned || `item_${Date.now()}`;
}

function updateBlockMarkdownLabel(body: string, label: string): string {
  const lines = body.split("\n");
  if (lines[0]?.trim().startsWith("#")) {
    lines[0] = `# ${label}`;
    return lines.join("\n");
  }
  return `# ${label}\n${body}`;
}

const LEGACY_STEP_FOLDERS: Record<string, string> = {
  plan: "01_plan",
  script: "02_script",
  split: "03_razbivka",
  hero: "04_hero",
  hero_style: "04_hero_style",
  items: "04b_items",
  enrich_1: "05a_enrich_1",
  enrich_2: "05b_enrich_2",
  enrich_3: "05c_enrich_3",
  enrich_4: "05d_enrich_4",
  enrich_5: "05e_enrich_5",
  img_pr: "05_image_prompts",
  anim_pr: "07_animation",
};

type PreviewState = {
  title: string;
  subtitle?: string;
  description: string;
  kind?: string;
  blockId?: string;
  itemId?: number;
  filePath?: string;
  editable?: boolean;
  fromHover?: boolean;
};

type BuilderHistorySnapshot = {
  selection: PromptSelection;
  extraSlots: PromptSlot[];
  promptVariant?: string;
};

export function PromptBuilderStudio({
  projectId,
  nodeType: entryNodeType,
  stepCode: entryStepCode,
  fullscreen = false,
  onClose,
  onOpenProjects,
}: {
  projectId: number;
  nodeType: string;
  stepCode: string;
  fullscreen?: boolean;
  onClose?: () => void;
  onOpenProjects?: () => void;
}) {
  const qc = useQueryClient();
  const [activeNodeType, setActiveNodeType] = useState(entryNodeType);
  const [editorOpen, setEditorOpen] = useState(false);
  const activeNodeDef =
    PIPELINE_RAIL_NODES.find((n) => n.nodeType === activeNodeType) ??
    PIPELINE_RAIL_NODES.find((n) => n.composeId) ??
    PIPELINE_RAIL_NODES[0];
  const composeStepId = activeNodeDef.composeId ?? composeStepIdForNode(entryNodeType, entryStepCode);
  const blocksV2 = Boolean(composeStepId);

  const project = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => api.getProject(projectId),
  });

  const catalog = useQuery({
    queryKey: ["prompt-studio-catalog", composeStepId],
    queryFn: () => loadRealPromptBuilder(composeStepId!, activeNodeDef!.stepCode),
    enabled: Boolean(composeStepId),
  });

  const libraryItems = useQuery({
    queryKey: ["library-items"],
    queryFn: () => api.listLibraryItems(),
    staleTime: 30_000,
  });

  const stepPresetsQuery = useQuery({
    queryKey: ["step-presets", activeNodeDef.stepCode],
    queryFn: () => fetchStepPresets(activeNodeDef.stepCode),
    enabled: blocksV2,
    staleTime: 60_000,
  });

  const stepPresetsData = stepPresetsQuery.data ?? null;

  const pipelineNodesQuery = useQuery({
    queryKey: ["prompt-pipeline-nodes"],
    queryFn: async (): Promise<PipelineNodeView[]> =>
      Promise.all(
        PIPELINE_RAIL_NODES.map(async (n) => {
          if (n.composeId) {
            const data = await loadRealPromptBuilder(n.composeId, n.stepCode);
            return {
              nodeType: n.nodeType,
              stepCode: n.stepCode,
              composeId: n.composeId,
              label: n.label,
              template: data.template,
              categoryKinds: data.categoryKinds,
            };
          }
          return {
            nodeType: n.nodeType,
            stepCode: n.stepCode,
            composeId: null,
            label: n.label,
            template: {
              id: n.nodeType,
              label: n.label,
              stepCode: n.stepCode,
              category: "legacy",
              slots: [],
            },
            categoryKinds: [],
          };
        }),
      ),
    staleTime: 60_000,
  });

  const [selection, setSelection] = useState<PromptSelection | null>(null);
  const [activeSlotId, setActiveSlotId] = useState<string | null>(null);
  const [selectedCellId, setSelectedCellId] = useState<string | null>(null);
  const [preview, setPreview] = useState<PreviewState | null>(null);
  const [previewPosition, setPreviewPosition] = useState({ x: 360, y: 120 });
  const [blockMenuBusy, setBlockMenuBusy] = useState<string | null>(null);
  const lastPointerRef = useRef({ x: 360, y: 120 });
  const previewDragRef = useRef<{
    pointerId: number;
    startX: number;
    startY: number;
    originX: number;
    originY: number;
  } | null>(null);
  const previewDockHover = useRef(false);
  const hoverHideTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [previewEditing, setPreviewEditing] = useState(false);
  const [previewDraft, setPreviewDraft] = useState("");
  const [showHistory, setShowHistory] = useState(false);
  const [addPartOpen, setAddPartOpen] = useState(false);
  const [addPartKind, setAddPartKind] = useState("block");
  const [addPartTitle, setAddPartTitle] = useState("");
  const [addPartPath, setAddPartPath] = useState("");
  const [addPartContent, setAddPartContent] = useState("");
  const [extraSlotsByStep, setExtraSlotsByStep] = useState<Record<string, PromptSlot[]>>({});
  const [localPromptVariants, setLocalPromptVariants] = useState<Record<string, string | undefined>>({});
  const [undoStack, setUndoStack] = useState<BuilderHistorySnapshot[]>([]);
  const [redoStack, setRedoStack] = useState<BuilderHistorySnapshot[]>([]);
  const [addMenuOpen, setAddMenuOpen] = useState(false);
  const [mounted, setMounted] = useState(false);
  const hydrated = useRef(false);
  const cleanedStepRef = useRef<string | null>(null);
  const pendingBlockAdd = useRef<{ kind: BlockKind; blockId: string } | null>(null);
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pendingSaveRef = useRef<PromptSelection | null>(null);
  const syncHintTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [syncHint, setSyncHint] = useState<{ pct: number } | null>(null);

  useEffect(() => setMounted(true), []);

  useEffect(() => {
    if (!fullscreen) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, [fullscreen]);

  useEffect(() => {
    if (!fullscreen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      if (preview?.fromHover) {
        setPreview(null);
        return;
      }
      if (activeSlotId || preview) {
        setActiveSlotId(null);
        setPreview(null);
        return;
      }
      onClose?.();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [fullscreen, preview, activeSlotId, onClose]);

  const template = catalog.data?.template ?? null;
  const blocks = catalog.data?.blocks ?? [];
  const categoryKinds = catalog.data?.categoryKinds ?? [];
  const catalogBlockIndex = catalog.data?.blockCategoryIndex;
  const stepBlockCategories = catalog.data?.stepBlockCategories;
  const extraSlots = composeStepId ? (extraSlotsByStep[composeStepId] ?? []) : [];
  const dims = useMemo(() => new Set([...DEFAULT_DIMS, "pipeline"]), []);

  const allSlots = useMemo(
    () => (template ? [...template.slots, ...extraSlots] : []),
    [template, extraSlots],
  );

  useEffect(() => {
    setPreviewDraft(preview?.description ?? "");
    setShowHistory(false);
  }, [preview?.kind, preview?.blockId, preview?.description]);

  useEffect(() => {
    hydrated.current = false;
    cleanedStepRef.current = null;
    setSelection(null);
    setActiveSlotId(null);
    setEditorOpen(false);
    setPreview(null);
    setPreviewDraft("");
    setPreviewEditing(false);
    setShowHistory(false);
    setSelectedCellId(null);
    setLocalPromptVariants({});
    setUndoStack([]);
    setRedoStack([]);
  }, [composeStepId, projectId, fullscreen]);

  useEffect(() => {
    setActiveNodeType(entryNodeType);
  }, [entryNodeType, entryStepCode]);

  const save = useMutation({
    mutationFn: async (sel: PromptSelection) => {
      if (!template) return;
      return api.patchProjectPromptConfig(projectId, {
        blocks: blocksMapFromSelection(template, sel.slots),
        vars: sel.vars,
        use_blocks_v2: true,
      });
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["project", projectId] });
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  const flashSyncHint = useCallback((pct: number) => {
    setSyncHint({ pct });
    if (syncHintTimerRef.current) clearTimeout(syncHintTimerRef.current);
    syncHintTimerRef.current = setTimeout(() => setSyncHint(null), 2200);
  }, []);

  useEffect(
    () => () => {
      if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
      if (syncHintTimerRef.current) clearTimeout(syncHintTimerRef.current);
    },
    [],
  );

  useEffect(() => {
    const rememberPointer = (event: globalThis.PointerEvent) => {
      lastPointerRef.current = { x: event.clientX, y: event.clientY };
    };
    window.addEventListener("pointerdown", rememberPointer, true);
    return () => window.removeEventListener("pointerdown", rememberPointer, true);
  }, []);

  useEffect(() => {
    if (!preview) return;
    const nextX = Math.min(Math.max(lastPointerRef.current.x + 14, 16), window.innerWidth - 380);
    const nextY = Math.min(Math.max(lastPointerRef.current.y + 14, 64), window.innerHeight - 220);
    setPreviewPosition({ x: nextX, y: nextY });
  }, [preview?.blockId, preview?.itemId, preview?.title]);

  const previewVersions = useQuery({
    queryKey: ["library-versions", preview?.itemId],
    queryFn: () => api.listLibraryVersions(preview!.itemId!),
    enabled: Boolean(preview?.itemId && showHistory),
  });

  const updateLibraryItem = useMutation({
    mutationFn: (payload: { itemId: number; title?: string; content: string }) =>
      api.updateLibraryItem(payload.itemId, {
        title: payload.title,
        content: payload.content,
        message: "edited in Prompt Builder",
      }),
    onSuccess: (item) => {
      setPreview((prev) =>
        prev
          ? {
              ...prev,
              title: item.title,
              description: item.content,
              filePath: item.file_path,
              editable: true,
            }
          : prev,
      );
      setPreviewEditing(false);
      qc.invalidateQueries({ queryKey: ["library-items"] });
      qc.invalidateQueries({ queryKey: ["prompt-studio-catalog"] });
      qc.invalidateQueries({ queryKey: ["prompt-files-graph"] });
      toast.success("Сохранено в локальную библиотеку");
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  const createLibraryItem = useMutation({
    mutationFn: () =>
      api.createLibraryItem({
        kind: addPartKind,
        title: addPartTitle,
        file_path: addPartPath,
        key: addPartPath,
        content: addPartContent,
        message: "created in Prompt Builder",
      }),
    onSuccess: (item) => {
      setAddPartOpen(false);
      setAddPartTitle("");
      setAddPartPath("");
      setAddPartContent("");
      qc.invalidateQueries({ queryKey: ["library-items"] });
      qc.invalidateQueries({ queryKey: ["prompt-studio-catalog"] });
      qc.invalidateQueries({ queryKey: ["prompt-files-graph"] });
      setPreview({
        title: item.title,
        subtitle: item.kind,
        description: item.content,
        kind: item.kind,
        itemId: item.id,
        filePath: item.file_path,
        editable: true,
      });
      toast.success("Часть добавлена");
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  const saveLibraryConfig = useMutation({
    mutationFn: () =>
      api.saveLibraryConfig({
        project_id: projectId,
        name: `${project.data?.slug ?? "project"}-config`,
      }),
    onSuccess: () => toast.success("Конфигурация сохранена"),
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  const restoreLibraryVersion = useMutation({
    mutationFn: (version: number) => api.restoreLibraryVersion(preview!.itemId!, version),
    onSuccess: (item) => {
      setPreview((prev) =>
        prev
          ? {
              ...prev,
              title: item.title,
              description: item.content,
              filePath: item.file_path,
              editable: true,
            }
          : prev,
      );
      qc.invalidateQueries({ queryKey: ["library-versions", item.id] });
      qc.invalidateQueries({ queryKey: ["library-items"] });
      qc.invalidateQueries({ queryKey: ["prompt-studio-catalog"] });
      toast.success("Версия восстановлена");
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  const savePromptBundle = useMutation({
    mutationFn: () =>
      api.savePromptBundle({
        project_id: projectId,
        step_id: composeStepId ?? undefined,
        step_code: activeNodeDef.stepCode,
        node_type: activeNodeType,
        source_name: activePromptVariant,
        title: `${project.data?.slug ?? "project"}-${activeNodeDef.stepCode}`,
      }),
    onSuccess: () => toast.success("Пакет промта сохранён: исходник, финал и блоки"),
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  useEffect(() => {
    if (!template || !project.data || hydrated.current) return;
    const po = project.data.prompt_overrides as Record<string, unknown>;
    const raw = selectionFromProject(template, po);
    const { selection: normSel, extras, changed } = normalizePromptSlotState(template, raw);
    setSelection(normSel);
    setExtraSlotsByStep((prev) => ({
      ...prev,
      [template.id]: extras,
    }));
    hydrated.current = true;
    if (changed && cleanedStepRef.current !== template.id) {
      cleanedStepRef.current = template.id;
      save.mutate(normSel);
    }
  }, [template, project.data, save]);

  const persistedPromptVariant = useMemo(() => {
    const po = (project.data?.prompt_overrides ?? {}) as Record<string, unknown>;
    const v = po[activeNodeDef.stepCode];
    return typeof v === "string" ? v : undefined;
  }, [project.data, activeNodeDef.stepCode]);

  const activePromptVariant = localPromptVariants[activeNodeDef.stepCode] ?? persistedPromptVariant;

  const cloneSelection = (sel: PromptSelection): PromptSelection => ({
    templateId: sel.templateId,
    slots: { ...sel.slots },
    vars: { ...sel.vars },
  });

  const snapshotCurrent = useCallback((): BuilderHistorySnapshot | null => {
    if (!selection) return null;
    return {
      selection: cloneSelection(selection),
      extraSlots: extraSlots.map((slot) => ({ ...slot })),
      promptVariant: activePromptVariant,
    };
  }, [selection, extraSlots, activePromptVariant]);

  const pushUndoSnapshot = useCallback(() => {
    const snapshot = snapshotCurrent();
    if (!snapshot) return;
    setUndoStack((prev) => [...prev.slice(-49), snapshot]);
    setRedoStack([]);
  }, [snapshotCurrent]);

  const persistSnapshot = useCallback(
    (snapshot: BuilderHistorySnapshot) => {
      if (!template) return;
      if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
      pendingSaveRef.current = snapshot.selection;
      api
        .patchProjectPromptConfig(projectId, {
          blocks: blocksMapFromSelection(template, snapshot.selection.slots),
          vars: snapshot.selection.vars,
          use_blocks_v2: true,
          legacy: snapshot.promptVariant
            ? { [activeNodeDef.stepCode]: snapshot.promptVariant }
            : {},
        })
        .then(() => qc.invalidateQueries({ queryKey: ["project", projectId] }))
        .catch((e) => toast.error(errorMessageFromUnknown(e)));
    },
    [activeNodeDef.stepCode, projectId, qc, template],
  );

  const applyHistorySnapshot = useCallback(
    (snapshot: BuilderHistorySnapshot) => {
      setSelection(cloneSelection(snapshot.selection));
      if (composeStepId) {
        setExtraSlotsByStep((prev) => ({
          ...prev,
          [composeStepId]: snapshot.extraSlots.map((slot) => ({ ...slot })),
        }));
      }
      setLocalPromptVariants((prev) => ({
        ...prev,
        [activeNodeDef.stepCode]: snapshot.promptVariant,
      }));
      persistSnapshot(snapshot);
      flashSyncHint(100);
    },
    [activeNodeDef.stepCode, composeStepId, flashSyncHint, persistSnapshot],
  );

  const undoBuilderAction = useCallback(() => {
    const current = snapshotCurrent();
    setUndoStack((prev) => {
      const target = prev.at(-1);
      if (!target || !current) return prev;
      setRedoStack((redo) => [...redo.slice(-49), current]);
      applyHistorySnapshot(target);
      return prev.slice(0, -1);
    });
  }, [applyHistorySnapshot, snapshotCurrent]);

  const redoBuilderAction = useCallback(() => {
    const current = snapshotCurrent();
    setRedoStack((prev) => {
      const target = prev.at(-1);
      if (!target || !current) return prev;
      setUndoStack((undo) => [...undo.slice(-49), current]);
      applyHistorySnapshot(target);
      return prev.slice(0, -1);
    });
  }, [applyHistorySnapshot, snapshotCurrent]);

  const scheduleSave = useCallback(
    (next: PromptSelection, options?: { recordHistory?: boolean }) => {
      if (options?.recordHistory !== false) pushUndoSnapshot();
      setSelection(next);
      pendingSaveRef.current = next;
      if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
      saveTimerRef.current = setTimeout(() => {
        const payload = pendingSaveRef.current;
        if (payload) save.mutate(payload);
      }, 450);
    },
    [pushUndoSnapshot, save],
  );

  const findSlot = (slotId: string) => allSlots.find((s) => s.slotId === slotId);

  const previewAssignedBlock = (
    slotId: string,
    block: { id: string; kind: string; label: string; body?: string },
  ) => {
    const agent = agentForBlock(block.id);
    const item = libraryItemForBlock(block);
    setActiveSlotId(slotId);
    setPreview({
      title: agent?.name ?? block.label,
      subtitle: block.kind,
      description: agent?.description ?? block.body ?? "",
      kind: block.kind,
      blockId: block.id,
      itemId: item?.id,
      filePath: item?.file_path,
      editable: Boolean(item),
    });
  };

  const rankedBySlot = useMemo(() => {
    if (!selection || !template) return {};
    const map: Record<string, ReturnType<typeof rankBlocksForSlot>> = {};
    for (const slot of allSlots) {
      map[slot.slotId] = rankBlocksForSlot(
        slot.kind,
        template,
        selection.slots,
        blocks,
        [template],
        dims,
        slot.slotId,
      );
    }
    return map;
  }, [allSlots, template, selection, blocks, dims]);

  const cellUsage = useMemo(
    () => (selection && template ? computeProjectCellUsage(selection, template) : {}),
    [selection, template],
  );

  const activeBlockId = useMemo(() => {
    if (!activeSlotId || !selection) return null;
    const slot = findSlot(activeSlotId);
    if (!slot || isSlotEmpty(selection.slots, slot)) return null;
    return resolveSlotBlockId(selection.slots, slot) || null;
  }, [activeSlotId, selection, allSlots]);

  const activeBlock = activeBlockId ? blocks.find((b) => b.id === activeBlockId) : null;
  const activeAgent = activeBlockId ? agentForBlock(activeBlockId) : null;

  const highlightCellIds = useMemo(() => {
    if (!activeBlockId || !template) return [];
    return getCellsForBlock(activeBlockId, template.stepCode);
  }, [activeBlockId, template]);

  const activeAgentCount = useMemo(
    () => agentCountForCells(highlightCellIds, cellUsage),
    [highlightCellIds, cellUsage],
  );

  const blockRows = useMemo(
    () => blocks.map((b: BlockVariant) => ({ id: b.id, kind: b.kind, label: b.label, body: b.body })),
    [blocks],
  );

  const allCatalogBlockRows = useMemo(
    () =>
      (catalog.data?.allCatalogBlocks ?? []).map((b: BlockVariant) => ({
        id: b.id,
        kind: b.kind,
        label: b.label,
        body: b.body,
      })),
    [catalog.data?.allCatalogBlocks],
  );

  const libraryItemByKey = useMemo(() => {
    const map = new Map<string, NonNullable<typeof libraryItems.data>[number]>();
    for (const item of libraryItems.data ?? []) map.set(item.key, item);
    return map;
  }, [libraryItems.data]);

  const libraryItemForBlock = (block?: { id: string; kind: string } | null) => {
    if (!block) return undefined;
    return libraryItemByKey.get(`prompts/blocks/${block.kind}/${block.id}.md`);
  };

  const placedBlockIds = useMemo(() => {
    const set = new Set<string>();
    if (!selection) return set;
    for (const slot of allSlots) {
      if (isSlotEmpty(selection.slots, slot)) continue;
      const id = resolveSlotBlockId(selection.slots, slot);
      if (id) set.add(id);
    }
    return set;
  }, [allSlots, selection]);

  const selectPromptVariant = useCallback(
    (name: string) => {
      if (!template) return;
      pushUndoSnapshot();
      const preset = resolvePromptPreset(stepPresetsData ?? undefined, name);
      const base =
        selection ??
        selectionFromProject(
          template,
          (project.data?.prompt_overrides ?? {}) as Record<string, unknown>,
        );

      let nextSelection = base;
      if (preset) {
        const applied = selectionFromPromptPreset(template, preset);
        nextSelection = {
          ...applied.selection,
          vars: { ...base.vars, ...applied.selection.vars },
        };
        if (composeStepId) {
          setExtraSlotsByStep((prev) => ({
            ...prev,
            [composeStepId]: applied.extras,
          }));
        }
        toast.success(`Пресет «${preset.label}» — блоки обновлены`);
      } else {
        toast.message(`Промт «${name}» — пресет не найден, сохранено имя`);
      }

      setSelection(nextSelection);
      setLocalPromptVariants((prev) => ({ ...prev, [activeNodeDef.stepCode]: name }));
      setRedoStack([]);
      api
        .patchProjectPromptConfig(projectId, {
          blocks: blocksMapFromSelection(template, nextSelection.slots),
          vars: nextSelection.vars,
          use_blocks_v2: true,
          legacy: { [activeNodeDef.stepCode]: name },
        })
        .then(() => qc.invalidateQueries({ queryKey: ["project", projectId] }))
        .catch((e) => toast.error(errorMessageFromUnknown(e)));
    },
    [
      template,
      selection,
      project.data,
      stepPresetsData,
      composeStepId,
      projectId,
      activeNodeDef.stepCode,
      qc,
      pushUndoSnapshot,
    ],
  );

  const focusSlot = (slotId: string) => {
    setActiveSlotId(slotId);
    if (!selection) return;
    const slot = findSlot(slotId);
    if (!slot || isSlotEmpty(selection.slots, slot)) {
      setPreview(null);
      return;
    }
    const blockId = resolveSlotBlockId(selection.slots, slot);
    const block =
      blocks.find((b) => b.id === blockId) ??
      allCatalogBlockRows.find((b) => b.id === blockId);
    const agent = agentForBlock(blockId);
    const item = libraryItemForBlock(block);
    setPreview({
      title: agent?.name ?? block?.label ?? "",
      subtitle: block?.kind,
      description: agent?.description ?? block?.body ?? "",
      kind: block?.kind,
      itemId: item?.id,
      filePath: item?.file_path,
      editable: Boolean(item),
    });
  };

  const selectBlock = (slotId: string, blockId: string) => {
    if (!selection) return;
    const block = blocks.find((b) => b.id === blockId);
    const next = { ...selection, slots: { ...selection.slots, [slotId]: blockId } };
    scheduleSave(next);
    if (block) previewAssignedBlock(slotId, block);
    else setActiveSlotId(slotId);
  };

  const swapBlocks = (slotA: string, slotB: string) => {
    if (!selection) return;
    const a = selection.slots[slotA];
    const b = selection.slots[slotB];
    if (!a || !b) return;
    scheduleSave({ ...selection, slots: { ...selection.slots, [slotA]: b, [slotB]: a } });
  };

  const moveBlock = (fromSlotId: string, toSlotId: string) => {
    if (!selection) return;
    const block = selection.slots[fromSlotId];
    if (!block) return;
    scheduleSave({
      ...selection,
      slots: { ...selection.slots, [toSlotId]: block, [fromSlotId]: "" },
    });
    setActiveSlotId(toSlotId);
  };

  const clearBlock = (slotId: string) => {
    if (!selection) return;
    const slot = findSlot(slotId);
    if (!slot || slot.required) return;
    scheduleSave({ ...selection, slots: { ...selection.slots, [slotId]: "" } });
    if (activeSlotId === slotId) setPreview(null);
  };

  const removeSlot = (slotId: string) => {
    if (!selection || !composeStepId) return;
    const slot = findSlot(slotId);
    if (!slot) return;
    if (slotId.startsWith("extra_")) {
      setExtraSlotsByStep((prev) => ({
        ...prev,
        [composeStepId]: (prev[composeStepId] ?? []).filter((s) => s.slotId !== slotId),
      }));
      const nextSlots = { ...selection.slots };
      delete nextSlots[slotId];
      scheduleSave({ ...selection, slots: nextSlots });
      if (activeSlotId === slotId) {
        setActiveSlotId(null);
        setPreview(null);
      }
      return;
    }
    if (!slot.required) clearBlock(slotId);
  };

  const addResolvedBlockToKind = (
    kind: string,
    blockId: string,
    block: { id: string; kind: string; label: string; body?: string },
  ) => {
    if (!selection || !composeStepId) return;

    const existingExtra = allSlots.find(
      (s) =>
        s.slotId.startsWith("extra_") &&
        s.kind === kind &&
        selection.slots[s.slotId] === blockId,
    );
    if (existingExtra) {
      previewAssignedBlock(existingExtra.slotId, block);
      return;
    }

    const slotId = `extra_${kind}_${Date.now()}`;
    const nextSlots = { ...selection.slots, [slotId]: blockId };
    setExtraSlotsByStep((prev) => ({
      ...prev,
      [composeStepId]: [
        ...(prev[composeStepId] ?? []),
        { slotId, kind, required: false, defaultBlockId: blockId },
      ],
    }));
    scheduleSave({ ...selection, slots: nextSlots });
    previewAssignedBlock(slotId, block);
    toast.success("Блок добавлен в центр");
  };

  const addBlockToKind = (kind: BlockKind, blockId: string) => {
    const block =
      blocks.find((b) => b.id === blockId && b.kind === kind) ??
      allCatalogBlockRows.find((b) => b.id === blockId && b.kind === kind);
    if (!block) return;
    addResolvedBlockToKind(kind, blockId, block);
  };

  const addBlockToKindRef = useRef(addBlockToKind);
  addBlockToKindRef.current = addBlockToKind;

  useEffect(() => {
    if (!pendingBlockAdd.current || !selection || !template) return;
    const pending = pendingBlockAdd.current;
    pendingBlockAdd.current = null;
    addBlockToKindRef.current(pending.kind, pending.blockId);
  }, [selection, template]);

  const dropBlockOnNode = (nodeType: string, kind: BlockKind, blockId: string) => {
    if (nodeType !== activeNodeType) {
      pendingBlockAdd.current = { kind, blockId };
      setActiveNodeType(nodeType);
      return;
    }
    addBlockToKind(kind, blockId);
  };

  const openEditorForNode = (targetNodeType: string) => {
    setActiveNodeType(targetNodeType);
    if (!fullscreen) setEditorOpen(true);
  };

  const handleHeaderBack = () => {
    if (editorOpen) {
      setEditorOpen(false);
      return;
    }
    onClose?.();
  };

  const setVar = (key: string, value: string | number) => {
    if (!selection) return;
    scheduleSave({ ...selection, vars: { ...selection.vars, [key]: value } });
  };

  const portalWrap = (node: ReactNode) => {
    if (fullscreen && mounted) return createPortal(node, document.body);
    return node;
  };

  const stepDisplayLabel =
    activeNodeDef.label ??
    (composeStepId ? COMPOSE_STEP_LABELS[composeStepId] : undefined) ??
    template?.label ??
    activeNodeType;

  const refreshBlockCatalog = useCallback(async () => {
    await qc.refetchQueries({ queryKey: ["prompt-studio-catalog", composeStepId] });
    void qc.invalidateQueries({ queryKey: ["library-items"] });
    void qc.invalidateQueries({ queryKey: ["block-activity"] });
  }, [qc, composeStepId]);

  const openBlockPreview = useCallback(
    async (
      block: { id: string; kind: string; label: string; body?: string },
      fromHover = false,
    ) => {
      const key = `prompts/blocks/${block.kind}/${block.id}.md`;
      const item = libraryItemByKey.get(key);
      setPreview({
        title: block.label || block.id,
        subtitle: block.kind,
        description: block.body || "Загрузка…",
        kind: block.kind,
        blockId: block.id,
        itemId: item?.id,
        filePath: item?.file_path ?? key,
        editable: true,
        fromHover,
      });
      setPreviewEditing(false);
      try {
        const full = await api.getPromptBlock(block.kind, block.id);
        setPreview((prev) =>
          prev?.blockId === block.id && prev?.kind === block.kind
            ? { ...prev, description: full.body }
            : prev,
        );
      } catch {
        /* keep cached body */
      }
    },
    [libraryItemByKey],
  );

  const createBlockAndAdd = useCallback(
    async (kind: string) => {
      if (!selection || !composeStepId) return;
      const rawName = window.prompt(
        kind === FREE_BLOCK_CATEGORY
          ? "Название свободного блока"
          : `Название нового блока в категории ${kind}`,
      );
      if (!rawName) return;
      const blockId = safeBlockId(rawName);
      const functionText = window.prompt(
        "Функция/описание блока: что он должен делать в промте?",
      );
      if (functionText === null) return;
      const content = `# ${rawName.trim()}\n\n## Функция\n\n${
        functionText.trim() || "Опишите правила и смысл этого блока."
      }\n`;
      try {
        const created = await api.createPromptBlock(kind, {
          block_id: blockId,
          content,
          message: "created in Prompt Builder",
        });
        const row = {
          id: created.id,
          kind,
          label: created.label || rawName.trim() || created.id,
          body: content,
        };
        addResolvedBlockToKind(kind, created.id, row);
        await refreshBlockCatalog();
        await openBlockPreview(row, false);
        toast.success(
          kind === FREE_BLOCK_CATEGORY
            ? "Свободный блок создан и добавлен в центр"
            : "Блок создан в категории и добавлен в центр",
        );
      } catch (e) {
        toast.error(errorMessageFromUnknown(e));
      }
    },
    [
      addResolvedBlockToKind,
      composeStepId,
      openBlockPreview,
      refreshBlockCatalog,
      selection,
    ],
  );

  const createPromptPresetFromCurrent = useCallback(async () => {
    if (!template || !selection) return;
    const rawName = window.prompt("Название нового промта");
    if (!rawName) return;
    const presetId = safeBlockId(rawName);
    const description = window.prompt(
      "Функция/описание промта: для чего он нужен?",
    );
    if (description === null) return;
    const currentBlocks = blocksMapFromSelection(template, selection.slots);
    const blocksForPreset = Object.fromEntries(
      Object.entries(currentBlocks)
        .map(([kind, value]) => (value ? ([kind, value] as const) : null))
        .filter((entry): entry is readonly [string, string] => Boolean(entry)),
    );
    try {
      await api.createStepPreset(activeNodeDef.stepCode, presetId, {
        label: rawName.trim(),
        description: description.trim(),
        blocks: blocksForPreset,
      });
      await qc.invalidateQueries({ queryKey: ["step-presets", activeNodeDef.stepCode] });
      selectPromptVariant(presetId);
      toast.success("Новый промт создан");
    } catch (e) {
      toast.error(errorMessageFromUnknown(e));
    }
  }, [activeNodeDef.stepCode, qc, selectPromptVariant, selection, template]);

  const savePromptBlockMutation = useMutation({
    mutationFn: (payload: { kind: string; blockId: string; content: string }) =>
      api.savePromptBlock(payload.kind, payload.blockId, {
        content: payload.content,
        message: "edited in Prompt Builder",
      }),
    onSuccess: (result) => {
      setPreview((prev) =>
        prev
          ? {
              ...prev,
              title: result.label,
              description: previewDraft,
            }
          : prev,
      );
      setPreviewEditing(false);
      refreshBlockCatalog();
      toast.success("Блок сохранён");
    },
    onError: (e) => toast.error(errorMessageFromUnknown(e)),
  });

  const runBlockAction = useCallback(
    async (action: string, fn: () => Promise<void>) => {
      setBlockMenuBusy(action);
      try {
        await fn();
      } catch (e) {
        const msg = errorMessageFromUnknown(e);
        if (msg.includes("405") || msg.toLowerCase().includes("method not allowed")) {
          toast.error("Backend устарел — перезапустите: VP.ps1 start");
        } else {
          toast.error(msg);
        }
      } finally {
        setBlockMenuBusy(null);
      }
    },
    [],
  );

  const getBlockBody = useCallback(
    async (kind: string, blockId: string) => {
      const local = blocks.find((b) => b.kind === kind && b.id === blockId);
      if (local?.body) return local.body;
      return (await api.getPromptBlock(kind, blockId)).body;
    },
    [blocks],
  );

  const handleBlockDuplicate = useCallback(
    (target: BlockMenuTarget) =>
      void runBlockAction("duplicate", async () => {
        const body = await getBlockBody(target.kind, target.blockId);
        let newId = `${target.blockId}_copy`;
        let n = 2;
        while (blocks.some((b) => b.kind === target.kind && b.id === newId)) {
          newId = `${target.blockId}_copy_${n++}`;
        }
        await api.createPromptBlock(target.kind, {
          block_id: newId,
          content: body,
          message: `duplicate ${target.blockId}`,
        });
        await refreshBlockCatalog();
        await openBlockPreview(
          { id: newId, kind: target.kind, label: newId, body },
          false,
        );
        toast.success(`Создан дубликат: ${newId}`);
      }),
    [blocks, getBlockBody, openBlockPreview, refreshBlockCatalog, runBlockAction],
  );

  const handleBlockEdit = useCallback(
    (target: BlockMenuTarget) => {
      void runBlockAction("edit", async () => {
        const block = blocks.find((b) => b.id === target.blockId && b.kind === target.kind);
        await openBlockPreview(
          block ?? {
            id: target.blockId,
            kind: target.kind,
            label: target.label,
          },
          false,
        );
        setPreviewEditing(true);
        setPreviewDraft(
          block?.body ?? (await getBlockBody(target.kind, target.blockId)),
        );
      });
    },
    [blocks, getBlockBody, openBlockPreview, runBlockAction],
  );

  const handleBlockRename = useCallback(
    (target: BlockMenuTarget, newId: string) => {
      const trimmed = newId.trim().replace(/\s+/g, "_");
      if (!/^[a-z0-9][a-z0-9_-]{0,79}$/i.test(trimmed)) {
        toast.error("ID: латиница, цифры, _ и -");
        return;
      }
      void runBlockAction("rename", async () => {
        await api.renamePromptBlock(target.kind, target.blockId, {
          new_block_id: trimmed,
          message: `rename ${target.blockId}`,
        });
        if (selection && template) {
          const nextSlots = { ...selection.slots };
          let changed = false;
          for (const slot of allSlots) {
            if (slot.kind === target.kind && nextSlots[slot.slotId] === target.blockId) {
              nextSlots[slot.slotId] = trimmed;
              changed = true;
            }
          }
          if (changed) scheduleSave({ ...selection, slots: nextSlots });
        }
        if (preview?.blockId === target.blockId && preview?.kind === target.kind) {
          setPreview((p) => (p ? { ...p, blockId: trimmed, title: trimmed } : p));
        }
        await refreshBlockCatalog();
        const queryKey = ["step-presets", activeNodeDef.stepCode] as const;
        const presetsFile = qc.getQueryData<StepPresetsFile | null>(queryKey);
        if (presetsFile?.presets) {
          for (const [presetId, preset] of Object.entries(presetsFile.presets)) {
            if (preset.blocks?.[target.kind] === target.blockId) {
              await api.patchStepPreset(activeNodeDef.stepCode, presetId, {
                blocks: { [target.kind]: trimmed },
              });
            }
          }
          await qc.invalidateQueries({ queryKey: [...queryKey] });
        }
        toast.success(`Переименовано: ${trimmed}`);
      });
    },
    [activeNodeDef.stepCode, allSlots, preview?.blockId, preview?.kind, qc, refreshBlockCatalog, runBlockAction, scheduleSave, selection, template],
  );

  const handleBlockDelete = useCallback(
    (target: BlockMenuTarget) => {
      void runBlockAction("delete", async () => {
        await api.deletePromptBlock(target.kind, target.blockId);
        if (selection && template) {
          const nextSlots = { ...selection.slots };
          let changed = false;
          for (const slot of allSlots) {
            if (slot.kind === target.kind && nextSlots[slot.slotId] === target.blockId) {
              nextSlots[slot.slotId] = "";
              changed = true;
            }
          }
          if (changed) scheduleSave({ ...selection, slots: nextSlots });
        }
        if (preview?.blockId === target.blockId && preview?.kind === target.kind) {
          setPreview(null);
        }
        await refreshBlockCatalog();
        await qc.invalidateQueries({ queryKey: ["step-presets", activeNodeDef.stepCode] });
        toast.success(`Файл блока удалён: ${target.blockId}`);
      });
    },
    [
      activeNodeDef.stepCode,
      allSlots,
      preview?.blockId,
      preview?.kind,
      qc,
      refreshBlockCatalog,
      runBlockAction,
      scheduleSave,
      selection,
      template,
    ],
  );

  const handlePresetBlockAssign = useCallback(
    async (presetId: string, kind: string, blockId: string) => {
      const queryKey = ["step-presets", activeNodeDef.stepCode] as const;
      qc.setQueryData<StepPresetsFile | null>(queryKey, (old) => {
        if (!old?.presets?.[presetId]) return old ?? null;
        const preset = old.presets[presetId];
        return {
          ...old,
          presets: {
            ...old.presets,
            [presetId]: {
              ...preset,
              blocks: { ...preset.blocks, [kind]: blockId },
              omit_slots: (preset.omit_slots ?? []).filter((s) => s !== kind),
            },
          },
        };
      });
      const updated = qc.getQueryData<StepPresetsFile | null>(queryKey);
      const preset = resolvePromptPreset(updated ?? undefined, presetId);
      flashSyncHint(presetComposePercent(preset));
      try {
        await api.patchStepPreset(activeNodeDef.stepCode, presetId, {
          blocks: { [kind]: blockId },
        });
      } catch (e) {
        toast.error(errorMessageFromUnknown(e));
        await qc.invalidateQueries({ queryKey: [...queryKey] });
      }
    },
    [activeNodeDef.stepCode, flashSyncHint, qc],
  );

  const handlePresetBlockRemove = useCallback(
    async (presetId: string, kind: string) => {
      const queryKey = ["step-presets", activeNodeDef.stepCode] as const;
      qc.setQueryData<StepPresetsFile | null>(queryKey, (old) => {
        if (!old?.presets?.[presetId]) return old ?? null;
        const preset = old.presets[presetId];
        const nextBlocks = { ...(preset.blocks ?? {}) };
        delete nextBlocks[kind];
        return {
          ...old,
          presets: {
            ...old.presets,
            [presetId]: {
              ...preset,
              blocks: nextBlocks,
              omit_slots: (preset.omit_slots ?? []).filter((s) => s !== kind),
            },
          },
        };
      });
      const updated = qc.getQueryData<StepPresetsFile | null>(queryKey);
      const preset = resolvePromptPreset(updated ?? undefined, presetId);
      flashSyncHint(presetComposePercent(preset));
      try {
        await api.patchStepPreset(activeNodeDef.stepCode, presetId, {
          blocks: { [kind]: null },
        });
        toast.success("Блок убран из пресета");
      } catch (e) {
        toast.error(errorMessageFromUnknown(e));
        await qc.invalidateQueries({ queryKey: [...queryKey] });
      }
    },
    [activeNodeDef.stepCode, flashSyncHint, qc],
  );

  const handleRailBlockDelete = useCallback(
    (target: BlockMenuTarget, presetId: string | null | undefined) => {
      if (!presetId) {
        handleBlockDelete(target);
        return;
      }
      const preset = resolvePromptPreset(stepPresetsData ?? undefined, presetId);
      const assigned = preset?.blocks?.[target.kind];
      if (assigned === target.blockId) {
        void handlePresetBlockRemove(presetId, target.kind);
        return;
      }
      handleBlockDelete(target);
    },
    [handleBlockDelete, handlePresetBlockRemove, stepPresetsData],
  );

  const handlePresetLabelSave = useCallback(
    async (presetId: string, label: string) => {
      try {
        await api.patchStepPreset(activeNodeDef.stepCode, presetId, { label });
        await qc.invalidateQueries({ queryKey: ["step-presets", activeNodeDef.stepCode] });
        toast.success("Название пресета сохранено");
      } catch (e) {
        toast.error(errorMessageFromUnknown(e));
      }
    },
    [activeNodeDef.stepCode, qc],
  );

  const handlePromptDelete = useCallback(
    async (presetId: string) => {
      if (presetId === "default") {
        toast.error("Default-промт нельзя удалить");
        return;
      }
      try {
        await api.deleteStepPreset(activeNodeDef.stepCode, presetId);
        await qc.invalidateQueries({ queryKey: ["step-presets", activeNodeDef.stepCode] });
        if (activePromptVariant === presetId) {
          selectPromptVariant("default");
        }
        setPreview((prev) =>
          prev?.subtitle?.includes("промт") && prev.title === presetId ? null : prev,
        );
        toast.success("Промт удалён");
      } catch (e) {
        toast.error(errorMessageFromUnknown(e));
      }
    },
    [activeNodeDef.stepCode, activePromptVariant, qc, selectPromptVariant],
  );

  const handleBlockLabelSave = useCallback(
    async (kind: string, blockId: string, label: string) => {
      try {
        const body = await getBlockBody(kind, blockId);
        await api.savePromptBlock(kind, blockId, {
          content: updateBlockMarkdownLabel(body, label),
          message: "label edit in Prompt Builder",
        });
        if (preview?.blockId === blockId && preview?.kind === kind) {
          setPreview((p) => (p ? { ...p, title: label } : p));
        }
        await refreshBlockCatalog();
        toast.success("Название блока сохранено");
      } catch (e) {
        toast.error(errorMessageFromUnknown(e));
      }
    },
    [getBlockBody, preview?.blockId, preview?.kind, refreshBlockCatalog],
  );

  const previewPrompt = useCallback(
    async (name: string) => {
      const preset = resolvePromptPreset(stepPresetsData ?? undefined, name);
      if (preset) {
        setPreview({
          title: preset.label ?? name,
          subtitle: "промт · полный текст",
          description: "Сборка полного текста…",
          editable: false,
        });
        try {
          const composed = await api.composePrompt({
            project_id: projectId,
            step_id: composeStepId ?? undefined,
            blocks: preset.blocks,
            vars: selection?.vars,
          });
          setPreview((prev) =>
            prev?.title === (preset.label ?? name)
              ? { ...prev, description: composed.text }
              : prev,
          );
        } catch {
          const blockCount = Object.keys(preset.blocks ?? {}).length;
          setPreview((prev) =>
            prev?.title === (preset.label ?? name)
              ? {
                  ...prev,
                  description:
                    preset.description ??
                    `Пресет с ${blockCount} блоками. Полный текст не удалось собрать.`,
                }
              : prev,
          );
        }
        return;
      }
      try {
        const file = await api.getPromptFile(activeNodeDef.stepCode, name);
        setPreview({
          title: file.name,
          subtitle: `промт · ${activeNodeDef.stepCode}`,
          description: file.content.slice(0, 4000),
          editable: false,
        });
      } catch (e) {
        toast.error(errorMessageFromUnknown(e));
      }
    },
    [stepPresetsData, activeNodeDef.stepCode, composeStepId, projectId, selection?.vars],
  );

  const previewBlock = useCallback(
    (block: { id: string; kind: string; label: string; body?: string }) => {
      void openBlockPreview(block, false);
    },
    [openBlockPreview],
  );

  if (!blocksV2) {
    return portalWrap(
      <p className="p-6 text-sm pb-text-muted">Для этого шага нет блочного шаблона.</p>,
    );
  }

  if (
    (blocksV2 && (catalog.isLoading || !selection || !template)) ||
    project.isLoading
  ) {
    return portalWrap(
      <div
        className={`flex items-center gap-2 py-8 text-sm pb-text-muted ${fullscreen ? "pb-graph-root h-full justify-center rounded-2xl" : ""}`}
      >
        <Loader2 className="h-4 w-4 animate-spin" />
        Загрузка блоков…
      </div>,
    );
  }

  if (blocksV2 && catalog.isError) {
    return portalWrap(
      <p className="p-6 text-sm text-destructive">Не удалось загрузить каталог блоков.</p>,
    );
  }

  const shellClass = fullscreen
    ? "pb-graph-root pb-shell-premium pb-fullscreen-studio fixed inset-0 z-[9999] flex h-[100dvh] w-[100vw] max-h-[100dvh] flex-col overflow-hidden"
    : "pb-graph-root flex min-h-[560px] flex-col overflow-hidden";

  const graphProps = {
    fillViewport: fullscreen,
    stepCode: activeNodeDef.stepCode,
    blocks: blockRows,
    selection: selection ?? { templateId: activeNodeType, slots: {}, vars: {} },
    categoryKinds,
    allSlots,
    activeStepLabel: stepDisplayLabel,
    activePromptVariant,
    activeSlotId,
    previewOpen: Boolean(preview),
    previewKind: preview?.kind ?? null,
    previewBlockId: preview?.blockId ?? null,
    blocksV2,
    onApplyPrompt: selectPromptVariant,
    onPreviewPrompt: previewPrompt,
    onRenamePrompt: handlePresetLabelSave,
    onDeletePrompt: handlePromptDelete,
    onPreviewBlock: previewBlock,
    onDismissPreview: () => setPreview(null),
    onOpenBlockPreview: (block: { id: string; kind: string; label: string; body?: string }) =>
      void openBlockPreview(block, false),
    onPresetBlockAssign: handlePresetBlockAssign,
    onPresetBlockRemove: handlePresetBlockRemove,
    onRemoveSlot: removeSlot,
    onRailBlockDelete: handleRailBlockDelete,
    onPresetLabelSave: handlePresetLabelSave,
    onBlockLabelSave: handleBlockLabelSave,
    onBlockDuplicate: handleBlockDuplicate,
    onBlockEdit: handleBlockEdit,
    onBlockRename: handleBlockRename,
    onBlockDelete: handleBlockDelete,
    blockMenuBusy,
    onEditStep: () => openEditorForNode(activeNodeType),
    onFocusSlot: focusSlot,
    onPickBlock: addBlockToKind,
    stepPresets: stepPresetsData,
    projectId,
    composeStepId: composeStepId ?? undefined,
    catalogBlockIndex,
    catalogBlocks: allCatalogBlockRows,
    stepBlockCategories,
  };

  const variantsCategoryKinds = (() => {
    const ids = new Set(categoryKinds.map((k) => k.id));
    for (const slot of extraSlots) ids.add(slot.kind);
    if (allCatalogBlockRows.some((b) => b.kind === FREE_BLOCK_CATEGORY)) {
      ids.add(FREE_BLOCK_CATEGORY);
    }
    return categoryMetaFor([...ids]);
  })();

  const variantPanelBlocks = allCatalogBlockRows.map((b) => ({
    id: b.id,
    kind: b.kind,
    label: b.label,
    body: b.body ?? "",
  }));

  const canUndo = undoStack.length > 0;
  const canRedo = redoStack.length > 0;
  const historyControls = (
    <div className="flex items-center gap-1">
      <button
        type="button"
        className={cn("pb-btn-ghost px-2 py-1", !canUndo && "opacity-40")}
        disabled={!canUndo}
        title="Действие назад"
        onClick={undoBuilderAction}
      >
        <ArrowLeft className="h-3.5 w-3.5" />
        Назад
      </button>
      <button
        type="button"
        className={cn("pb-btn-ghost px-2 py-1", !canRedo && "opacity-40")}
        disabled={!canRedo}
        title="Действие вперёд"
        onClick={redoBuilderAction}
      >
        Вперёд
        <ArrowRight className="h-3.5 w-3.5" />
      </button>
    </div>
  );

  const addControls = (
    <div className="relative">
      <button
        type="button"
        className="pb-btn-ghost px-2 py-1"
        title="Добавить промт или блок"
        onClick={() => setAddMenuOpen((v) => !v)}
      >
        <Plus className="h-3.5 w-3.5" />
      </button>
      {addMenuOpen && (
        <div className="pb-add-menu">
          <button
            type="button"
            onClick={() => {
              setAddMenuOpen(false);
              void createPromptPresetFromCurrent();
            }}
          >
            Новый промт
          </button>
          <button
            type="button"
            onClick={() => {
              setAddMenuOpen(false);
              void createBlockAndAdd(FREE_BLOCK_CATEGORY);
            }}
          >
            Новый блок
          </button>
        </div>
      )}
    </div>
  );

  const startPreviewDrag = (event: ReactPointerEvent<HTMLDivElement>) => {
    if ((event.target as HTMLElement).closest("button,input,textarea")) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    previewDragRef.current = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      originX: previewPosition.x,
      originY: previewPosition.y,
    };
  };

  const movePreviewDrag = (event: ReactPointerEvent<HTMLDivElement>) => {
    const drag = previewDragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    const nextX = Math.min(
      Math.max(drag.originX + event.clientX - drag.startX, 16),
      window.innerWidth - 360,
    );
    const nextY = Math.min(
      Math.max(drag.originY + event.clientY - drag.startY, 64),
      window.innerHeight - 160,
    );
    setPreviewPosition({ x: nextX, y: nextY });
  };

  const endPreviewDrag = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (previewDragRef.current?.pointerId === event.pointerId) {
      previewDragRef.current = null;
    }
  };

  const previewContent = preview ? (
    <>
      <div
        className="pb-preview-float-drag flex shrink-0 items-start gap-3 border-b border-[var(--pb-border)] px-4 py-3"
        onPointerDown={startPreviewDrag}
        onPointerMove={movePreviewDrag}
        onPointerUp={endPreviewDrag}
        onPointerCancel={endPreviewDrag}
      >
        <div className="min-w-0 flex-1">
          {preview.kind && preview.blockId ? (
            <EditableLabel
              value={preview.title || preview.blockId}
              className="truncate text-[12px] font-semibold pb-text block"
              inputClassName="text-[12px]"
              onSave={(label) => handleBlockLabelSave(preview.kind!, preview.blockId!, label)}
            />
          ) : (
            <EditableLabel
              value={preview.title || "Содержимое блока"}
              className="truncate text-[12px] font-semibold pb-text block"
              inputClassName="text-[12px]"
              onSave={(label) => setPreview((p) => (p ? { ...p, title: label } : p))}
            />
          )}
          {preview.subtitle && (
            <p className="mt-0.5 truncate text-[9px] pb-text-muted">{preview.subtitle}</p>
          )}
          {preview.filePath && (
            <p className="mt-1 truncate text-[8px] pb-text-dim">{preview.filePath}</p>
          )}
        </div>
        <button
          type="button"
          className="pb-btn-ghost p-1.5"
          title="Закрыть"
          aria-label="Закрыть"
          onClick={() => {
            setActiveSlotId(null);
            setPreview(null);
          }}
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>
      <div className="flex shrink-0 flex-wrap gap-1.5 border-b border-[var(--pb-border)] px-4 py-2">
        {preview.kind && preview.blockId && (
          <BlockActionBar
            compact
            target={{
              kind: preview.kind,
              blockId: preview.blockId,
              label: preview.title,
            }}
            busyAction={blockMenuBusy}
            onDuplicate={handleBlockDuplicate}
            onEdit={handleBlockEdit}
            onRename={handleBlockRename}
            onDelete={handleBlockDelete}
          />
        )}
        {(preview.itemId || (preview.kind && preview.blockId)) && (
          <>
            <button
              type="button"
              className="pb-btn-ghost"
              onClick={() => setPreviewEditing((v) => !v)}
            >
              {previewEditing ? "Просмотр" : "Редактировать"}
            </button>
            <button
              type="button"
              className="pb-btn-ghost"
              disabled={
                updateLibraryItem.isPending || savePromptBlockMutation.isPending
              }
              onClick={() => {
                if (preview.kind && preview.blockId) {
                  savePromptBlockMutation.mutate({
                    kind: preview.kind,
                    blockId: preview.blockId,
                    content: previewDraft,
                  });
                  return;
                }
                if (preview.itemId) {
                  updateLibraryItem.mutate({
                    itemId: preview.itemId,
                    title: preview.title,
                    content: previewDraft,
                  });
                }
              }}
            >
              Сохранить
            </button>
            <button
              type="button"
              className="pb-btn-ghost"
              onClick={() => window.open(api.downloadLibraryItemUrl(preview.itemId!), "_blank")}
            >
              Скачать
            </button>
            <button
              type="button"
              className="pb-btn-ghost"
              onClick={() => setShowHistory((v) => !v)}
            >
              История
            </button>
          </>
        )}
      </div>
      {showHistory && preview.itemId && (
        <div className="max-h-36 shrink-0 overflow-y-auto border-b border-[var(--pb-border)] px-4 py-2">
          {previewVersions.isLoading ? (
            <p className="text-[9px] pb-text-dim">Загрузка истории…</p>
          ) : (
            <div className="space-y-1.5">
              {(previewVersions.data ?? []).map((v) => (
                <button
                  key={v.id}
                  type="button"
                  className="w-full rounded-md border border-[var(--pb-border)] px-2 py-1 text-left text-[9px] pb-text-muted hover:bg-white/[0.04]"
                  onClick={() => restoreLibraryVersion.mutate(v.version)}
                >
                  v{v.version} · {v.message ?? "version"} · {new Date(v.created_at).toLocaleString()}
                </button>
              ))}
            </div>
          )}
        </div>
      )}
      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
        {previewEditing ? (
          <textarea
            className="pb-library-textarea h-full min-h-[260px]"
            value={previewDraft}
            onChange={(e) => setPreviewDraft(e.target.value)}
          />
        ) : (
          <p className="whitespace-pre-wrap text-[11px] leading-relaxed pb-text-muted">
            {preview.description || "У блока нет текста для предпросмотра."}
          </p>
        )}
      </div>
    </>
  ) : null;

  const previewFloating = preview ? (
    <div
      className="pb-preview-float-window"
      style={{ left: previewPosition.x, top: previewPosition.y }}
      onClick={(e) => e.stopPropagation()}
    >
      {previewContent}
    </div>
  ) : null;

  if (fullscreen) {
    return portalWrap(
      <div className={shellClass}>
        <header className="pb-header-premium relative z-10 flex shrink-0 items-center justify-between px-4">
          <div className="flex items-center gap-3">
            <button type="button" onClick={onClose} className="pb-btn-ghost">
              <ArrowLeft className="h-3.5 w-3.5" />
              Студия
            </button>
            <span className="h-4 w-px bg-[var(--pb-border)]" />
            <div>
              <p className="pb-title">Скелет промта</p>
              <p className="pb-subtitle mt-0.5">
                {stepDisplayLabel}
              </p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            {historyControls}
            {addControls}
            {save.isPending && (
              <span className="flex items-center gap-1.5 text-[11px] pb-text-dim">
                <Loader2 className="h-3.5 w-3.5 animate-spin text-[var(--pb-accent)]" />
                …
              </span>
            )}
            {!save.isPending && syncHint && (
              <span className="pb-sync-hint text-[11px]">
                <Check className="mr-1 inline h-3 w-3" />
                {syncHint.pct}%
              </span>
            )}
            {onClose && (
              <button
                type="button"
                onClick={onClose}
                className="pb-btn-ghost p-1.5"
                title="Закрыть"
                aria-label="Закрыть"
              >
                <X className="h-4 w-4" />
              </button>
            )}
          </div>
        </header>

        <div className="pb-graph-body relative flex min-h-0 flex-1 flex-col overflow-hidden px-2 pb-1 pt-0">
          <PromptStructureGraph {...graphProps} />
          {previewFloating}
        </div>

      </div>,
    );
  }

  if (!blocksV2 || !template || !selection) {
    return portalWrap(
      <p className="p-6 text-sm pb-text-muted">
        Для этого шага откройте конструктор из ноды с блочным шаблоном.
      </p>,
    );
  }

  const rightCol = editorOpen ? "minmax(300px,24vw)" : "minmax(280px,22vw)";
  const gridClass = cn(
    "h-full min-h-0",
    onOpenProjects
      ? `grid grid-cols-[40px_minmax(0,1fr)_${rightCol}]`
      : `grid grid-cols-[minmax(0,1fr)_${rightCol}]`,
  );

  const settingsPanel = (
    <PromptRightPanel
      template={template}
      allSlots={allSlots}
      selection={selection.slots}
      activeSlotId={activeSlotId}
      activeBlockId={activeBlockId}
      previewTitle={preview?.title ?? activeAgent?.name ?? activeBlock?.label}
      previewSubtitle={preview?.subtitle}
      previewDescription={preview?.description ?? activeAgent?.description ?? activeBlock?.body}
      previewKind={preview?.kind ?? activeBlock?.kind}
      highlightCellIds={highlightCellIds}
      activeAgentCount={activeBlockId ? activeAgentCount : undefined}
      vars={selection.vars}
      onChangeVar={setVar}
      selectedCellId={selectedCellId}
      usage={cellUsage}
      blocks={blocks}
      categoryKinds={categoryKinds}
      onSelectSlot={focusSlot}
      onOpenEditor={(slotId) => {
        focusSlot(slotId);
        setEditorOpen(true);
      }}
    />
  );

  const editorPanel = (
    <BlockEditorCenter
      allSlots={allSlots}
      selection={selection.slots}
      activeSlotId={activeSlotId}
      rankedBySlot={rankedBySlot}
      categoryKinds={categoryKinds}
      allBlocks={blockRows}
      useAgentLabels
      onBack={fullscreen ? undefined : () => setEditorOpen(false)}
      onFocusSlot={focusSlot}
      onSelectBlock={selectBlock}
      onSwapBlocks={swapBlocks}
      onRemoveSlot={removeSlot}
      onAddBlockToKind={addBlockToKind}
      onMoveBlock={moveBlock}
    />
  );

  const rightPanel = editorOpen ? (
    <div className="pb-right-stack pb-animate-in-right">
      <div className="pb-right-editor">{editorPanel}</div>
      <div className="pb-right-settings">{settingsPanel}</div>
    </div>
  ) : (
    <div className="pb-animate-in-right h-full min-h-0">
      <PipelineVariantsPanel
        composeId={composeStepId!}
        nodeLabel={stepDisplayLabel}
        categoryKinds={variantsCategoryKinds}
        allBlocks={variantPanelBlocks}
        allSlots={allSlots}
        rankedBySlot={rankedBySlot}
        placedBlockIds={placedBlockIds}
        onPickVariant={addBlockToKind}
        onCreateBlock={createBlockAndAdd}
        onAddOutsideCategory={() => createBlockAndAdd(FREE_BLOCK_CATEGORY)}
      />
    </div>
  );

  const centerPanel = (
    <div className="relative flex min-h-0 min-w-0 flex-col overflow-hidden border-r border-[var(--pb-border)]">
      <div className="flex min-h-0 flex-1 overflow-hidden px-4 py-4">
        {editorOpen ? (
          editorPanel
        ) : (
          <PromptStructureGraph {...graphProps} />
        )}
      </div>
    </div>
  );

  return portalWrap(
    <div className={shellClass}>
      <header className="pb-header-premium relative z-10 flex shrink-0 items-center justify-between px-4">
        <div className="flex items-center gap-3">
          <button type="button" onClick={handleHeaderBack} className="pb-btn-ghost">
            <ArrowLeft className="h-3.5 w-3.5" />
            {editorOpen ? "Назад" : "Студия"}
          </button>
          <span className="h-4 w-px bg-[var(--pb-border)]" />
          <div>
            <p className="pb-title">Скелет промта</p>
            <p className="pb-subtitle mt-0.5">
              {stepDisplayLabel}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-3">
          {historyControls}
          {addControls}
          {save.isPending && (
            <span className="flex items-center gap-1.5 text-[11px] pb-text-dim">
              <Loader2 className="h-3.5 w-3.5 animate-spin text-[var(--pb-accent)]" />
              …
            </span>
          )}
          {!save.isPending && syncHint && (
            <span className="pb-sync-hint text-[11px]">
              <Check className="mr-1 inline h-3 w-3" />
              {syncHint.pct}%
            </span>
          )}
          {onClose && (
            <button
              type="button"
              onClick={onClose}
              className="pb-btn-ghost p-1.5"
              title="Закрыть"
              aria-label="Закрыть"
            >
              <X className="h-4 w-4" />
            </button>
          )}
        </div>
      </header>

      <div className="relative min-h-0 flex-1">
        <div className={gridClass}>
          {onOpenProjects && (
            <PromptBuilderLeftRail
              templates={[template]}
              activeTemplateId={template.id}
              onPickTemplate={() => {}}
              onOpenProjects={onOpenProjects}
            />
          )}

          {centerPanel}

          {rightPanel}
        </div>
        {previewFloating}
      </div>
    </div>,
  );
}
