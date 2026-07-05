"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, GripVertical, MoreVertical, Pencil, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { api } from "@/lib/api";
import { categoryMetaFor, groupCategoriesByTier, sortCategoriesByTier } from "@/lib/prompt-builder/category-meta";
import { abbrevLabel } from "@/lib/prompt-builder/category-icons";
import { BlockContextMenu, type BlockMenuTarget } from "./block-context-menu";
import { BlockActionBar } from "./block-action-bar";
import { EditableLabel } from "./editable-label";
import { PromptContextMenu, type PromptMenuTarget } from "./prompt-context-menu";
import {
  orderedPresetIds,
  presetAliasIds,
  resolvePromptPreset,
  type StepPresetsFile,
} from "@/lib/prompt-builder/prompt-presets";
import {
  blockVariantsForKind,
  categoryKindIdsForRail,
  mergeFullCatalogBlocks,
  railBlockDisplay,
} from "@/lib/prompt-builder/category-rail-blocks";
import { isSlotEmpty, resolveSlotBlockId } from "@/lib/prompt-builder/compose";
import type { BlockKindMeta, PromptSelection, PromptSlot } from "@/lib/prompt-builder/types";

export type InlinePreviewState = {
  title: string;
  subtitle?: string;
  description: string;
  kind?: string;
  blockId?: string;
  promptName?: string;
};

export type BlockRow = {
  id: string;
  label: string;
  kind: string;
  body?: string;
};

function promptTitle(name: string): string {
  const base = name.replace(/\.md$/i, "");
  const map: Record<string, string> = {
    default: "Default",
    norm: "Norm",
    scenario_agent: "Scenario",
    promt_stiven_king: "King",
    reworked_default_cats_pixel_blocks_v2: "Default pixel",
    reworked_norm_nano_banana_blocks_v2: "Norm",
    reworked_pixel_v8_cinematic_blocks_v2: "Pixel v8",
    reworked_trash_polka_v25_blocks_v2: "Polka v2.5",
    reworked_trash_polka_short_blocks_v2: "Polka short",
    reworked_plasticine_blocks_v2: "Пластилин",
    reworked_knitted_2d_blocks_v2: "Вязаный 2D",
    reworked_noir_bloody_blocks_v2: "Noir bloody",
    reworked_dark_ominous_noir_blocks_v2: "Doc noir",
  };
  if (map[base]) return map[base];
  if (base.includes("zinser") || base.includes("Зинзер")) return "Zinser";
  if (base.includes("long") || base.includes("zakadrovyu")) return "Long";
  if (base.includes("editor") || base.includes("Новый промт")) return "Editor";
  if (base.includes("polka") || base.includes("полька")) return "Polka";
  if (base.includes("plasticine") || base.includes("пластилin")) return "Clay";
  if (base.includes("knitted") || base.includes("вязан")) return "Textile";
  const short = base.replace(/^reworked_/, "").replace(/_blocks_v2$/, "");
  if (short.length <= 14) return short;
  return short.slice(0, 12) + "…";
}

function promptNote(name: string): string {
  const n = name.toLowerCase();
  if (n.includes("default") && n.includes("pixel")) return "коты pixel";
  if (n === "default" || n.includes("default_cats")) return "коты pixel";
  if (n.includes("norm")) return "nano 16 полей";
  if (n.includes("pixel_v8") || n.includes("v8_cinematic")) return "mature pixel";
  if (n.includes("trash_polka_v25") || n.includes("11.6")) return "polka + RU";
  if (n.includes("polka") || n.includes("полька")) return "trash polka";
  if (n.includes("plasticine") || n.includes("пластилин")) return "clay 2D";
  if (n.includes("knitted") || n.includes("вязан")) return "textile";
  if (n.includes("noir_bloody") || n.includes("кровав")) return "crime noir";
  if (n.includes("dark_ominous") || n.includes("зловещ")) return "doc mystery";
  if (n.includes("scenario")) return "pipeline";
  if (n.includes("long") || n.includes("zakadrovyu")) return "long cells";
  if (n.includes("zinser") || n.includes("зинзер")) return "anti-GPT";
  if (n.includes("king") || n.includes("stiven")) return "placeholder";
  if (n.includes("editor") || n.includes("новый")) return "универсальный";
  return "промт";
}

function stepAbbr(label: string): string {
  const map: Record<string, string> = {
    Сценарий: "СЦЕ",
    "Общий план": "ПЛН",
    "План ролика": "ПЛН",
    Разбивка: "РЗБ",
    Персонажи: "ГР",
    Персонаж: "ГР",
    "Промты картинок": "IMG",
    "Промты анимации": "ANI",
  };
  return map[label] ?? abbrevLabel(label, label.slice(0, 3).toUpperCase());
}

const HOVER_HIDE_MS = 380;
const DRAG_MIME = "application/x-pb-block";

type PromptTile = {
  id: string;
  label: string;
  note: string;
  isDefault?: boolean;
};

function shortPresetNote(text?: string): string {
  if (!text) return "пресет";
  const one = text.replace(/\s+/g, " ").trim();
  if (one.length <= 36) return one;
  return one.slice(0, 34) + "…";
}

function resolvedPresetId(
  stepPresets: StepPresetsFile | null | undefined,
  promptName: string | undefined,
): string | null {
  if (!promptName) return null;
  return resolvePromptPreset(stepPresets ?? undefined, promptName)?.id ?? promptName;
}

function tileLabel(
  stepPresets: StepPresetsFile | null | undefined,
  promptId: string | null | undefined,
  tiles: PromptTile[],
): string {
  if (!promptId) return "";
  const tile = tiles.find((t) => t.id === promptId);
  if (tile) return tile.label;
  const preset = resolvePromptPreset(stepPresets ?? undefined, promptId);
  if (preset?.label) return preset.label;
  return promptTitle(promptId);
}

export function PromptStructureGraph({
  stepCode,
  blocks,
  selection,
  categoryKinds,
  allSlots,
  activeStepLabel,
  activePromptVariant,
  previewOpen,
  previewKind,
  previewBlockId,
  onSelectPrompt,
  onApplyPrompt,
  onPreviewPrompt,
  onRenamePrompt,
  onDeletePrompt,
  onPreviewBlock,
  onDismissPreview,
  onOpenBlockPreview,
  onPresetBlockAssign,
  onPresetBlockRemove,
  onRemoveSlot,
  onRailBlockDelete,
  onPresetLabelSave,
  onBlockLabelSave,
  onBlockDuplicate,
  onBlockEdit,
  onBlockRename,
  onBlockDelete,
  blockMenuBusy,
  onEditStep,
  onFocusSlot,
  onPickBlock,
  blocksV2 = true,
  fillViewport = false,
  stepPresets,
  projectId,
  composeStepId,
  catalogBlockIndex: catalogBlockIndexProp,
  catalogBlocks: catalogBlocksProp,
  stepBlockCategories: stepBlockCategoriesProp,
}: {
  stepCode: string;
  blocks: BlockRow[];
  selection: PromptSelection;
  categoryKinds: BlockKindMeta[];
  allSlots: PromptSlot[];
  activeStepLabel: string;
  activePromptVariant?: string;
  previewOpen?: boolean;
  previewKind?: string | null;
  previewBlockId?: string | null;
  activeSlotId?: string | null;
  onSelectPrompt?: (name: string) => void;
  onApplyPrompt?: (name: string) => void;
  onPreviewPrompt?: (name: string) => void;
  onRenamePrompt?: (name: string, label: string) => void;
  onDeletePrompt?: (name: string) => void;
  onPreviewBlock?: (block: BlockRow) => void;
  onDismissPreview?: () => void;
  onOpenBlockPreview?: (block: BlockRow) => void;
  onPresetBlockAssign?: (presetId: string, kind: string, blockId: string) => void;
  onPresetBlockRemove?: (presetId: string, kind: string) => void;
  onRemoveSlot?: (slotId: string) => void;
  onRailBlockDelete?: (target: BlockMenuTarget, presetId: string | null | undefined) => void;
  onPresetLabelSave?: (presetId: string, label: string) => void;
  onBlockLabelSave?: (kind: string, blockId: string, label: string) => void;
  onBlockDuplicate?: (target: BlockMenuTarget) => void;
  onBlockEdit?: (target: BlockMenuTarget) => void;
  onBlockRename?: (target: BlockMenuTarget, newId: string) => void;
  onBlockDelete?: (target: BlockMenuTarget) => void;
  blockMenuBusy?: string | null;
  onEditStep?: () => void;
  onFocusSlot?: (slotId: string) => void;
  onPickBlock?: (kind: string, blockId: string) => void;
  blocksV2?: boolean;
  fillViewport?: boolean;
  stepPresets?: StepPresetsFile | null;
  projectId?: number;
  composeStepId?: string;
  catalogBlockIndex?: Record<string, string[]>;
  catalogBlocks?: BlockRow[];
  stepBlockCategories?: Record<string, string[]>;
}) {
  const [selectedPrompt, setSelectedPrompt] = useState<string | null>(null);
  const [selectedKind, setSelectedKind] = useState<string | null>(null);
  const [selectedVariantId, setSelectedVariantId] = useState<string | null>(null);
  const [ctxMenu, setCtxMenu] = useState<{
    target: BlockMenuTarget;
    x: number;
    y: number;
    sourceSlotId?: string;
  } | null>(null);
  const [promptCtxMenu, setPromptCtxMenu] = useState<{
    target: PromptMenuTarget;
    x: number;
    y: number;
  } | null>(null);
  const [dragKind, setDragKind] = useState<string | null>(null);
  const [dropKind, setDropKind] = useState<string | null>(null);
  const [canvasDropActive, setCanvasDropActive] = useState(false);
  const [assignFlash, setAssignFlash] = useState<{ kind: string; blockId: string } | null>(null);
  const [dropRejectedKind, setDropRejectedKind] = useState<string | null>(null);
  const [selectedRailBlock, setSelectedRailBlock] = useState<BlockMenuTarget | null>(null);
  const assignFlashTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const rejectFlashTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const dragPayloadRef = useRef<{ kind: string; blockId: string } | null>(null);
  const queryClient = useQueryClient();
  const hoverPromptHideTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const cancelHoverPromptClear = useCallback(() => {
    if (hoverPromptHideTimer.current) {
      clearTimeout(hoverPromptHideTimer.current);
      hoverPromptHideTimer.current = null;
    }
  }, []);

  const scheduleHoverPromptClear = useCallback(() => {
    cancelHoverPromptClear();
  }, [cancelHoverPromptClear]);

  const logBlockActivity = (
    eventType: "block_selected" | "block_viewed",
    category: string,
    blockId: string,
  ) => {
    void api
      .promptStudioLogBlockActivity({
        event_type: eventType,
        category,
        block_id: blockId,
        project_id: projectId,
        step_id: composeStepId,
        step_code: stepCode,
        prompt_variant: selectedPrompt ?? activePromptVariant,
      })
      .then(() => {
        void queryClient.invalidateQueries({ queryKey: ["block-activity"] });
      })
      .catch(() => undefined);
  };

  const promptFiles = useQuery({
    queryKey: ["prompt-files-graph", stepCode],
    queryFn: () => api.listPromptFiles(stepCode),
    enabled: Boolean(stepCode) && blocksV2 && !stepPresets?.presets,
  });

  const catalogQuery = useQuery({
    queryKey: ["prompt-studio-catalog-blocks", composeStepId],
    queryFn: () => api.promptStudioCatalog(),
    enabled: blocksV2 && !catalogBlockIndexProp,
    staleTime: 60_000,
  });

  const blockCategoryIndex = catalogBlockIndexProp ?? catalogQuery.data?.block_categories;

  const catalogBlocksDto = useMemo(() => {
    if (catalogBlocksProp?.length) {
      return catalogBlocksProp.map((b) => ({
        category: b.kind,
        id: b.id,
        label: b.label,
        body: b.body,
      }));
    }
    return catalogQuery.data?.blocks;
  }, [catalogBlocksProp, catalogQuery.data?.blocks]);

  const stepBlockCategories =
    stepBlockCategoriesProp ?? catalogQuery.data?.step_block_categories;

  const fullCatalogBlocks = useMemo(
    (): BlockRow[] => mergeFullCatalogBlocks(blocks, catalogBlocksDto, blockCategoryIndex),
    [blocks, catalogBlocksDto, blockCategoryIndex],
  );

  const promptTiles = useMemo((): PromptTile[] => {
    const presetIds = stepPresets?.presets ? orderedPresetIds(stepPresets) : [];
    if (presetIds.length > 0) {
      return presetIds.map((id) => {
        const preset = stepPresets!.presets[id]!;
        return {
          id,
          label: preset.label ?? id,
          note: shortPresetNote(preset.description),
          isDefault: id === "default",
        };
      });
    }

    const aliasIds = presetAliasIds(stepPresets);
    const seen = new Set<string>();
    const tiles: PromptTile[] = [];
    for (const file of promptFiles.data ?? []) {
      if (file.name.startsWith("_") || file.name.startsWith("reworked_")) continue;
      const canonical = resolvePromptPreset(stepPresets ?? undefined, file.name)?.id ?? file.name;
      if (aliasIds.has(file.name) || seen.has(canonical)) continue;
      seen.add(canonical);
      tiles.push({
        id: canonical,
        label: promptTitle(file.name),
        note: promptNote(file.name),
        isDefault: file.is_default,
      });
    }
    return tiles;
  }, [stepPresets, promptFiles.data]);

  useEffect(() => {
    if (promptTiles.length === 0) return;
    const appliedId = resolvedPresetId(stepPresets, activePromptVariant);
    const preferred =
      appliedId && promptTiles.some((t) => t.id === appliedId)
        ? appliedId
        : promptTiles.find((t) => t.isDefault)?.id ?? promptTiles[0]!.id;
    setSelectedPrompt((prev) =>
      prev && promptTiles.some((t) => t.id === prev) ? prev : preferred,
    );
  }, [promptTiles, activePromptVariant, stepPresets]);

  useEffect(() => {
    setSelectedKind(null);
    setSelectedVariantId(null);
    setSelectedRailBlock(null);
  }, [stepCode]);

  useEffect(() => () => cancelHoverPromptClear(), [cancelHoverPromptClear]);

  const displayPromptId = selectedPrompt;
  const displayPreset = useMemo(
    () =>
      displayPromptId ? resolvePromptPreset(stepPresets ?? undefined, displayPromptId) : null,
    [displayPromptId, stepPresets],
  );

  const isBrowsingPreset =
    Boolean(
      selectedPrompt &&
        resolvedPresetId(stepPresets, activePromptVariant) !== selectedPrompt,
    );

  const blockIdForPreset = (
    kind: string,
    preset: ReturnType<typeof resolvePromptPreset>,
    omit: Set<string>,
  ): string | null => {
    if (omit.has(kind)) return null;
    const bid = preset?.blocks?.[kind];
    return typeof bid === "string" && bid ? bid : null;
  };

  const blockRowForKind = (kind: string, blockId: string): BlockRow =>
    fullCatalogBlocks.find((b) => b.id === blockId && b.kind === kind) ??
    blocks.find((b) => b.id === blockId && b.kind === kind) ?? {
      id: blockId,
      kind,
      label: blockId,
    };

  const variantBlocks = useMemo((): BlockRow[] => {
    if (!selectedKind) return [];
    return blockVariantsForKind(
      selectedKind,
      fullCatalogBlocks,
      blockCategoryIndex ?? {},
      stepPresets,
      blockRowForKind,
    );
  }, [selectedKind, fullCatalogBlocks, blockCategoryIndex, stepPresets, blocks]);

  const centerSlotEntries = useMemo(() => {
    const metas = new Map(
      [...categoryKinds, ...categoryMetaFor(allSlots.map((slot) => slot.kind))].map((m) => [
        m.id,
        m,
      ]),
    );
    return allSlots
      .map((slot) => {
        if (isSlotEmpty(selection.slots, slot)) return null;
        const blockId = resolveSlotBlockId(selection.slots, slot);
        if (!blockId) return null;
        const block = blockRowForKind(slot.kind, blockId);
        return {
          slot,
          kindMeta: metas.get(slot.kind) ?? categoryMetaFor([slot.kind])[0]!,
          blockId,
          block,
          removable: slot.slotId.startsWith("extra_"),
        };
      })
      .filter((entry): entry is NonNullable<typeof entry> => entry != null)
      .sort((a, b) => {
        const at = a.kindMeta.tier ?? 99;
        const bt = b.kindMeta.tier ?? 99;
        if (at !== bt) return at - bt;
        return allSlots.indexOf(a.slot) - allSlots.indexOf(b.slot);
      });
  }, [allSlots, categoryKinds, fullCatalogBlocks, blocks, selection.slots]);

  const centerBlockIdsByKind = useMemo(() => {
    const map = new Map<string, Set<string>>();
    for (const entry of centerSlotEntries) {
      const set = map.get(entry.slot.kind) ?? new Set<string>();
      set.add(entry.blockId);
      map.set(entry.slot.kind, set);
    }
    return map;
  }, [centerSlotEntries]);

  const centerSlotGroups = useMemo(() => {
    const groups: {
      kind: string;
      kindMeta: (typeof centerSlotEntries)[number]["kindMeta"];
      entries: typeof centerSlotEntries;
    }[] = [];
    const byKind = new Map<string, (typeof groups)[number]>();
    for (const entry of centerSlotEntries) {
      let group = byKind.get(entry.slot.kind);
      if (!group) {
        group = {
          kind: entry.slot.kind,
          kindMeta: entry.kindMeta,
          entries: [],
        };
        byKind.set(entry.slot.kind, group);
        groups.push(group);
      }
      group.entries.push(entry);
    }
    return groups;
  }, [centerSlotEntries]);

  const centerBlockIdForKind = useCallback(
    (kind: string) => centerBlockIdsByKind.get(kind)?.values().next().value ?? null,
    [centerBlockIdsByKind],
  );

  const centerHighlightBlockId = selectedKind ? centerBlockIdForKind(selectedKind) : null;

  const railCategoryKinds = useMemo(() => {
    const ids = categoryKindIdsForRail(composeStepId, stepBlockCategories, categoryKinds);
    return categoryMetaFor(ids);
  }, [categoryKinds, composeStepId, stepBlockCategories]);

  const categoriesRail = useMemo(() => {
    const tierGroups = groupCategoriesByTier(sortCategoriesByTier(railCategoryKinds));

    return tierGroups
      .map((group) => ({
        tier: group.tier,
        tierLabel: group.tierLabel,
        categories: group.items
          .map((kindMeta) => {
            const kind = kindMeta.id;
            const blockVariants = blockVariantsForKind(
              kind,
              fullCatalogBlocks,
              blockCategoryIndex ?? {},
              stepPresets,
              blockRowForKind,
            );
            if (blockVariants.length === 0) return null;

            return {
              kindMeta,
              kind,
              blockVariants,
              assignedBlockId: centerBlockIdForKind(kind),
              isSelectedKind: selectedKind === kind,
            };
          })
          .filter((cat): cat is NonNullable<typeof cat> => cat != null),
      }))
      .filter((g) => g.categories.length > 0);
  }, [
    railCategoryKinds,
    fullCatalogBlocks,
    blockCategoryIndex,
    stepPresets,
    selectedKind,
    centerBlockIdForKind,
    blocks,
  ]);

  const selectedKindMeta = selectedKind
    ? railCategoryKinds.find((k) => k.id === selectedKind) ??
      categoryKinds.find((k) => k.id === selectedKind)
    : undefined;
  const activeBlockId = selectedKind ? centerBlockIdForKind(selectedKind) : null;

  useEffect(() => {
    if (!selectedKind) {
      setSelectedVariantId(null);
      return;
    }
    setSelectedVariantId((prev) => {
      if (
        centerHighlightBlockId &&
        variantBlocks.some((b) => b.id === centerHighlightBlockId)
      ) {
        return centerHighlightBlockId;
      }
      if (prev && variantBlocks.some((b) => b.id === prev)) return prev;
      if (activeBlockId && variantBlocks.some((b) => b.id === activeBlockId)) return activeBlockId;
      return null;
    });
  }, [selectedKind, variantBlocks, centerHighlightBlockId, activeBlockId]);

  const openBlockPreviewPane = (block: BlockRow) => {
    setSelectedKind(block.kind);
    setSelectedVariantId(block.id);
    onOpenBlockPreview?.(block);
  };

  const currentBlockInPreset = (presetId: string | null | undefined, kind: string) => {
    if (!presetId) return null;
    const preset = resolvePromptPreset(stepPresets ?? undefined, presetId);
    if (!preset) return null;
    const omit = new Set(preset.omit_slots ?? []);
    return blockIdForPreset(kind, preset, omit);
  };

  const assignBlockToKind = (kind: string, blockId: string, presetId?: string | null): boolean => {
    const pid = presetId ?? selectedPrompt;
    if (!pid) return false;

    const current = currentBlockInPreset(pid, kind);
    if (current && current !== blockId) {
      setDropRejectedKind(kind);
      if (rejectFlashTimer.current) clearTimeout(rejectFlashTimer.current);
      rejectFlashTimer.current = setTimeout(() => setDropRejectedKind(null), 700);
      return false;
    }
    if (current === blockId) {
      setSelectedKind(kind);
      setSelectedVariantId(blockId);
      return true;
    }

    onPresetBlockAssign?.(pid, kind, blockId);
    setSelectedKind(kind);
    setSelectedVariantId(blockId);
    setAssignFlash({ kind, blockId });
    if (assignFlashTimer.current) clearTimeout(assignFlashTimer.current);
    assignFlashTimer.current = setTimeout(() => setAssignFlash(null), 900);
    return true;
  };

  useEffect(
    () => () => {
      if (assignFlashTimer.current) clearTimeout(assignFlashTimer.current);
      if (rejectFlashTimer.current) clearTimeout(rejectFlashTimer.current);
    },
    [],
  );

  const removeBlockFromPreset = (kind: string, presetId?: string | null) => {
    const pid = presetId ?? selectedPrompt;
    if (!pid) return;
    if (!currentBlockInPreset(pid, kind)) return;
    onPresetBlockRemove?.(pid, kind);
    setSelectedRailBlock(null);
    setSelectedVariantId(null);
  };

  const browsePrompt = (name: string) => {
    setSelectedPrompt(name);
    onSelectPrompt?.(name);
  };

  const startBlockDrag = (e: React.DragEvent, kind: string, blockId: string) => {
    dragPayloadRef.current = { kind, blockId };
    setDragKind(kind);
    setSelectedKind(kind);
    e.dataTransfer.setData(DRAG_MIME, JSON.stringify({ kind, blockId }));
    e.dataTransfer.setData("text/plain", blockId);
    e.dataTransfer.effectAllowed = "copy";
  };

  const finishBlockDrag = () => {
    dragPayloadRef.current = null;
    setDragKind(null);
    setDropKind(null);
    setCanvasDropActive(false);
  };

  const resolveDropPayload = (e: React.DragEvent) =>
    parseDragPayload(e) ?? dragPayloadRef.current;

  const handleCanvasDrop = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    const payload = resolveDropPayload(e);
    finishBlockDrag();
    if (!payload) return;
    onPickBlock?.(payload.kind, payload.blockId);
  };

  const openBlockContextMenu = (
    e: React.MouseEvent,
    block: BlockRow,
    sourceSlotId?: string,
  ) => {
    e.preventDefault();
    e.stopPropagation();
    setCtxMenu({
      target: { kind: block.kind, blockId: block.id, label: block.label || block.id },
      x: e.clientX,
      y: e.clientY,
      sourceSlotId,
    });
  };

  const openPromptContextMenu = (e: React.MouseEvent, tile: PromptTile) => {
    e.preventDefault();
    e.stopPropagation();
    setPromptCtxMenu({
      target: { promptId: tile.id, label: tile.label },
      x: e.clientX,
      y: e.clientY,
    });
  };

  const handleDismissIfOpen = (): boolean => {
    if (!previewOpen) return false;
    onDismissPreview?.();
    return true;
  };

  const parseDragPayload = (e: React.DragEvent): { kind: string; blockId: string } | null => {
    try {
      const raw = e.dataTransfer.getData(DRAG_MIME);
      if (!raw) return null;
      const data = JSON.parse(raw) as { kind?: string; blockId?: string };
      if (data.kind && data.blockId) return { kind: data.kind, blockId: data.blockId };
    } catch {
      /* ignore */
    }
    return null;
  };

  return (
    <div className={cn("pb-neural-root flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden", fillViewport && "h-full")}>
      {!blocksV2 ? (
        <div className="flex flex-1 flex-col items-center justify-center gap-2 px-6 text-center">
          <p className="text-[12px] font-medium pb-text">Классический GPT-промт</p>
          <button type="button" className="pb-btn-ghost text-[10px]" onClick={onEditStep}>
            <Pencil className="mr-1 inline h-3 w-3" />
            Редактор
          </button>
        </div>
      ) : (
        <div
          className={cn(
            "pb-neural-layout pb-source-canvas pb-graph-open min-h-0 flex-1 overflow-hidden",
            fillViewport && "h-full",
          )}
          onMouseEnter={cancelHoverPromptClear}
          onMouseLeave={scheduleHoverPromptClear}
        >
          <section className="pb-graph-col flex min-h-0 flex-col gap-2 overflow-hidden">
            <p className="pb-label-caps shrink-0 px-1">Промты</p>
            <div className="pb-scroll-fade min-h-0 flex-1 overflow-y-auto">
              {promptFiles.isLoading && promptTiles.length === 0 ? (
                <p className="px-1 text-[9px] pb-text-dim">Загрузка…</p>
              ) : promptTiles.length === 0 ? (
                <p className="px-1 text-[9px] pb-text-dim">Нет пресетов для шага</p>
              ) : (
                <div className="pb-source-style-list">
                  {promptTiles.map((tile) => {
                    const active = selectedPrompt === tile.id;
                    const applied =
                      resolvedPresetId(stepPresets, activePromptVariant) === tile.id;
                    return (
                      <button
                        key={tile.id}
                        type="button"
                        className={cn(
                          "pb-source-prompt pb-source-prompt-tile relative",
                          active && "pb-source-prompt-active",
                          applied && "pb-source-prompt-applied",
                        )}
                        onMouseEnter={() => cancelHoverPromptClear()}
                        onMouseLeave={scheduleHoverPromptClear}
                        onContextMenu={(e) => openPromptContextMenu(e, tile)}
                        onClick={() => {
                          if (handleDismissIfOpen()) return;
                          browsePrompt(tile.id);
                          onPreviewPrompt?.(tile.id);
                        }}
                        title={`${tile.label} · клик — полный текст`}
                      >
                        <EditableLabel
                          value={tile.label}
                          className="pb-source-prompt-name block w-full"
                          inputClassName="text-center text-[10px]"
                          onSave={(label) => onPresetLabelSave?.(tile.id, label)}
                        />
                        <span className="pb-source-prompt-note">{tile.note}</span>
                        {applied && <span className="pb-source-prompt-badge">✓</span>}
                        {onDeletePrompt && tile.id !== "default" && (
                          <span
                            role="button"
                            tabIndex={0}
                            className="pb-source-prompt-menu"
                            title="Меню промта"
                            onClick={(e) => {
                              e.stopPropagation();
                              openPromptContextMenu(e, tile);
                            }}
                            onKeyDown={(e) => {
                              if (e.key === "Enter" || e.key === " ") {
                                e.preventDefault();
                                e.stopPropagation();
                                const rect = e.currentTarget.getBoundingClientRect();
                                setPromptCtxMenu({
                                  target: { promptId: tile.id, label: tile.label },
                                  x: rect.right,
                                  y: rect.bottom,
                                });
                              }
                            }}
                          >
                            <MoreVertical className="h-3 w-3" />
                          </span>
                        )}
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
          </section>

          <section
            className="pb-graph-col pb-graph-col-center flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden"
            onMouseEnter={cancelHoverPromptClear}
          >
            <div className="flex shrink-0 items-center gap-2 px-2 pt-1">
              <p className="pb-label-caps">
                Центр сборки
                {displayPromptId && (
                  <span className="ml-1.5 font-normal normal-case tracking-normal pb-text-dim">
                    · {tileLabel(stepPresets, displayPromptId, promptTiles)}
                  </span>
                )}
              </p>
            </div>
            <div
              className={cn(
                "pb-neural-canvas-v3 pb-scroll-fade relative flex h-full min-h-0 flex-1 flex-col rounded-xl",
                canvasDropActive && dragKind && "pb-canvas-drop-active",
              )}
              onDragOver={(e) => {
                if (!dragKind) return;
                e.preventDefault();
                e.dataTransfer.dropEffect = "copy";
                setCanvasDropActive(true);
              }}
              onDragLeave={(e) => {
                const rel = e.relatedTarget as Node | null;
                if (rel && e.currentTarget.contains(rel)) return;
                setCanvasDropActive(false);
              }}
              onDrop={handleCanvasDrop}
            >
              {canvasDropActive && dragKind && selectedPrompt && (
                <div className="pb-canvas-drop-hint">
                  Отпустите — добавить блок в центр текущей сборки
                </div>
              )}
              {!selectedPrompt ? (
                <p className="px-3 py-6 text-[10px] italic pb-text-dim">Выберите промт слева</p>
              ) : (
                <div className="flex h-full min-h-0 flex-1 flex-col gap-2 px-3 pb-2 pt-2">
                  <div className="pb-scroll-fade min-h-0 min-w-0 flex-1 overflow-y-auto">
                    <div className="pb-slot-group-grid">
                      {centerSlotGroups.map((group) => (
                        <div key={group.kind} className="pb-slot-kind-group">
                          <div className="pb-slot-card-category-title">{group.kindMeta.label}</div>
                          <div className="pb-slot-grid pb-slot-grid-center">
                            {group.entries.map(({ slot, kindMeta: kind, blockId: displayId, block, removable }) => {
                            const active =
                              selectedKind === kind.id && selectedVariantId === displayId;
                            const isPreviewing =
                              previewKind === kind.id ||
                              (previewBlockId != null && previewBlockId === displayId);
                            const canDrop = dragKind === kind.id;
                            return (
                              <div
                                key={slot.slotId}
                                className={cn(
                                  "pb-slot-card-wrap relative",
                                  canDrop && "pb-slot-card-drop-target",
                                  dropKind === kind.id && "pb-slot-card-drop-hover",
                                  dropRejectedKind === kind.id && "pb-slot-card-drop-rejected",
                                )}
                                onDragOver={(e) => {
                                  if (!dragKind || dragKind !== kind.id) return;
                                  e.preventDefault();
                                  e.dataTransfer.dropEffect = "copy";
                                  setDropKind(kind.id);
                                  if (dropRejectedKind === kind.id) setDropRejectedKind(null);
                                }}
                                onDragLeave={() => setDropKind(null)}
                                onDrop={(e) => {
                                  e.preventDefault();
                                  e.stopPropagation();
                                  const payload = resolveDropPayload(e);
                                  finishBlockDrag();
                                  if (!payload || payload.kind !== kind.id) return;
                                  onPickBlock?.(kind.id, payload.blockId);
                                }}
                              >
                                <button
                                  type="button"
                                  className={cn(
                                    "pb-slot-card pb-slot-card-compact w-full",
                                    active && "pb-slot-card-active",
                                    displayId && "pb-slot-card-assigned",
                                    isPreviewing && "pb-slot-card-previewing",
                                    assignFlash?.kind === kind.id && "pb-slot-card-just-assigned",
                                  )}
                                  onContextMenu={(e) => {
                                    if (block) openBlockContextMenu(e, block, slot.slotId);
                                  }}
                                  onClick={() => {
                                    if (handleDismissIfOpen()) return;
                                    setSelectedKind(kind.id);
                                    setSelectedVariantId(displayId);
                                    onFocusSlot?.(slot.slotId);
                                    if (block) openBlockPreviewPane(block);
                                  }}
                                  title={kind.description ?? kind.label}
                                >
                                  <span className="pb-slot-card-label">{kind.label}</span>
                                  <span className="pb-slot-card-value">
                                    {block ? (
                                      <>
                                        <span className="block truncate">{block.id}</span>
                                        {isPreviewing && previewOpen && (
                                          <span className="pb-block-applied-tag mt-0.5">просмотр</span>
                                        )}
                                      </>
                                    ) : (
                                      "не задан"
                                    )}
                                  </span>
                                </button>
                                {displayId && removable && (
                                  <button
                                    type="button"
                                    className="pb-slot-card-remove"
                                    title="Меню блока"
                                    onClick={(e) => {
                                      e.stopPropagation();
                                      if (block) openBlockContextMenu(e, block, slot.slotId);
                                    }}
                                  >
                                    <MoreVertical className="h-2.5 w-2.5" />
                                  </button>
                                )}
                              </div>
                            );
                          })}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              )}
            </div>
          </section>

          <section
            className="pb-graph-col pb-graph-variants-col pb-categories-rail flex min-h-0 min-w-0 flex-col gap-2 overflow-hidden border-l border-white/[0.06] pl-2"
            onMouseEnter={cancelHoverPromptClear}
          >
            <p className="pb-label-caps shrink-0 px-1">
              Категории
              {catalogQuery.isLoading && !catalogBlockIndexProp && (
                <span className="ml-1 font-normal normal-case pb-text-dim">· каталог…</span>
              )}
            </p>

            <div className="pb-categories-rail-scroll pb-scroll-fade min-h-0 flex-1 px-1 pb-2">
              {categoriesRail.length === 0 ? (
                <p className="px-1 text-[9px] pb-text-dim">Нет категорий</p>
              ) : (
                categoriesRail.map((group) => (
                  <div key={group.tier} className="pb-category-tier-group">
                    <p className="pb-category-tier-label">{group.tierLabel}</p>
                    {group.categories.map((cat) => (
                        <div
                          key={cat.kind}
                          className={cn(
                            "pb-category-rail-card",
                            cat.isSelectedKind && "pb-category-rail-card-active",
                            assignFlash?.kind === cat.kind && "pb-category-rail-card-just-assigned",
                            dropRejectedKind === cat.kind && "pb-category-rail-card-drop-rejected",
                          )}
                        >
                          <div className="pb-category-rail-head">
                            <button
                              type="button"
                              className="pb-category-rail-title min-w-0 flex-1 text-left"
                              onClick={() => {
                                setSelectedKind(cat.kind);
                                if (cat.assignedBlockId) {
                                  setSelectedVariantId(cat.assignedBlockId);
                                }
                              }}
                            >
                              <span className="block text-[9px] font-semibold">{cat.kindMeta.label}</span>
                              {cat.kindMeta.description && (
                                <span className="block truncate text-[7px] pb-text-dim">
                                  {cat.kindMeta.description}
                                </span>
                              )}
                            </button>
                            <span className="pb-prompt-rail-count">{cat.blockVariants.length}</span>
                          </div>

                          {cat.assignedBlockId && (
                            <p className="pb-category-in-preset-line">
                              в центре · {cat.assignedBlockId}
                            </p>
                          )}

                          <div className="pb-category-rail-body">
                            <div className="pb-category-rail-prompts">
                              {cat.blockVariants.map((block) => {
                                const isInCenter =
                                  centerBlockIdsByKind.get(cat.kind)?.has(block.id) ?? false;
                                const justAssigned =
                                  assignFlash?.kind === cat.kind &&
                                  assignFlash.blockId === block.id;
                                const isPreviewing =
                                  previewOpen &&
                                  previewKind === cat.kind &&
                                  previewBlockId === block.id;
                                const display = railBlockDisplay(block, cat.blockVariants);
                                const railTarget: BlockMenuTarget = {
                                  kind: cat.kind,
                                  blockId: block.id,
                                  label: display.title,
                                };
                                const isRowSelected =
                                  selectedRailBlock?.kind === cat.kind &&
                                  selectedRailBlock.blockId === block.id;
                                return (
                                  <div key={block.id} className="pb-rail-block-stack">
                                    <div
                                      className={cn(
                                        "pb-category-prompt-row",
                                        isInCenter && "pb-category-prompt-row-active",
                                        justAssigned && "pb-category-prompt-row-applied-flash",
                                        isPreviewing && "pb-category-prompt-row-previewing",
                                        isRowSelected && "pb-category-prompt-row-menu-open",
                                      )}
                                    >
                                      <button
                                        type="button"
                                        className="pb-drag-handle"
                                        draggable
                                        title="Перетащите на пустой слот в центре"
                                        onDragStart={(e) => {
                                          e.stopPropagation();
                                          startBlockDrag(e, cat.kind, block.id);
                                        }}
                                        onDragEnd={finishBlockDrag}
                                      >
                                        <GripVertical className="h-3 w-3" />
                                      </button>
                                      <button
                                        type="button"
                                        className="pb-category-prompt-body min-w-0 flex-1 text-left"
                                        onClick={() => {
                                          setSelectedRailBlock(railTarget);
                                          setSelectedKind(cat.kind);
                                          setSelectedVariantId(block.id);
                                          onPickBlock?.(cat.kind, block.id);
                                        }}
                                        onContextMenu={(e) => openBlockContextMenu(e, block)}
                                      >
                                        <span className="pb-category-prompt-name block">{display.title}</span>
                                        {display.subtitle && (
                                          <span className="pb-category-prompt-variant">{display.subtitle}</span>
                                        )}
                                        {isInCenter && (
                                          <span className="pb-block-applied-tag">в центре</span>
                                        )}
                                        {isPreviewing && !isInCenter && (
                                          <span className="pb-block-preview-tag">просмотр</span>
                                        )}
                                      </button>
                                    </div>
                                    {isRowSelected && onBlockDuplicate && onBlockEdit && onBlockRename && (onRailBlockDelete || onBlockDelete) && (
                                      <BlockActionBar
                                        compact
                                        target={railTarget}
                                        busyAction={blockMenuBusy ?? null}
                                        onDuplicate={onBlockDuplicate}
                                        onEdit={onBlockEdit}
                                        onRename={onBlockRename}
                                        onDelete={(t) =>
                                          onRailBlockDelete
                                            ? onRailBlockDelete(t, selectedPrompt)
                                            : onBlockDelete?.(t)
                                        }
                                      />
                                    )}
                                  </div>
                                );
                              })}
                            </div>
                          </div>
                        </div>
                      ))}
                  </div>
                ))
              )}
            </div>
          </section>
        </div>
      )}

      <BlockContextMenu
        target={ctxMenu?.target ?? null}
        position={ctxMenu ? { x: ctxMenu.x, y: ctxMenu.y } : null}
        busyAction={blockMenuBusy ?? null}
        onClose={() => setCtxMenu(null)}
        onPreview={(t) => {
          const block = blocks.find((b) => b.id === t.blockId && b.kind === t.kind) ?? {
            id: t.blockId,
            kind: t.kind,
            label: t.label,
          };
          setSelectedKind(t.kind);
          setSelectedVariantId(t.blockId);
          onPreviewBlock?.(block);
          onOpenBlockPreview?.(block);
          setCtxMenu(null);
        }}
        onDuplicate={(t) => {
          onBlockDuplicate?.(t);
          setCtxMenu(null);
        }}
        onEdit={(t) => {
          onBlockEdit?.(t);
          setCtxMenu(null);
        }}
        onRenameLabel={(t) => {
          const next = window.prompt("Новое название блока", t.label || t.blockId);
          if (next?.trim()) onBlockLabelSave?.(t.kind, t.blockId, next.trim());
          setCtxMenu(null);
        }}
        onRename={(t, newId) => {
          onBlockRename?.(t, newId);
          setCtxMenu(null);
        }}
        onDelete={(t) => {
          if (ctxMenu?.sourceSlotId) onRemoveSlot?.(ctxMenu.sourceSlotId);
          else if (onRailBlockDelete) onRailBlockDelete(t, selectedPrompt);
          else onBlockDelete?.(t);
          setCtxMenu(null);
          setSelectedRailBlock(null);
        }}
      />

      <PromptContextMenu
        target={promptCtxMenu?.target ?? null}
        position={promptCtxMenu ? { x: promptCtxMenu.x, y: promptCtxMenu.y } : null}
        onClose={() => setPromptCtxMenu(null)}
        onRename={
          onRenamePrompt
            ? (t) => {
                const next = window.prompt("Новое название промта", t.label);
                if (next?.trim()) onRenamePrompt(t.promptId, next.trim());
                setPromptCtxMenu(null);
              }
            : undefined
        }
        onDelete={
          onDeletePrompt
            ? (t) => {
                onDeletePrompt(t.promptId);
                setPromptCtxMenu(null);
              }
            : undefined
        }
      />
    </div>
  );
}
