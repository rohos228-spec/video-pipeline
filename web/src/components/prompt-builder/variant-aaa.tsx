"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { SkeletonBlockCard, buildSlotRelations } from "./skeleton-block-card";
import { PromptBuilderLeftRail } from "./prompt-builder-left-rail";
import { ExcelCellsInline } from "./excel-impact-panel";
import { PromptRightPanel } from "./prompt-right-panel";
import { BlockEditorCenter } from "./block-editor-menu";
import { MOCK_BLOCKS, MOCK_TEMPLATES } from "@/lib/prompt-builder/mock-data";
import { defaultSelection, isSlotEmpty, resolveSlotBlockId } from "@/lib/prompt-builder/compose";
import { CRITERIA_DIMENSIONS, rankBlocksForSlot } from "@/lib/prompt-builder/compatibility";
import {
  agentCountForCells,
  computeProjectCellUsage,
  getCellsForBlock,
} from "@/lib/prompt-builder/excel-cells";
import { agentForBlock } from "@/lib/prompt-builder/agents-catalog";
import { DEFAULT_ORCHESTRATOR_VARS } from "@/lib/prompt-builder/orchestrator-vars";
import type { BlockKind, PromptSelection, PromptSlot } from "@/lib/prompt-builder/types";
import { BLOCK_KINDS } from "@/lib/prompt-builder/types";
import "./prompt-builder-aaa.css";

const DEFAULT_DIMS = new Set(CRITERIA_DIMENSIONS.filter((d) => d.toggleable).map((d) => d.id));

const CARD_H = 88;
const CARD_GAP = 12;
const CANVAS_W = 320;

type PreviewState = {
  title: string;
  subtitle?: string;
  description: string;
  kind?: BlockKind;
};

function VerticalConnections({
  template,
  relations,
  slotIds,
}: {
  template: { slots: { slotId: string; kind: string }[] };
  relations: ReturnType<typeof buildSlotRelations>;
  slotIds: string[];
}) {
  const cx = CANVAS_W / 2;
  const yTops: number[] = [];
  let y = 16;
  for (const slot of template.slots) {
    yTops.push(y);
    y += CARD_H + CARD_GAP;
  }
  const yCenter = (i: number) => yTops[i]! + CARD_H / 2;
  const indexOf = (id: string) => slotIds.indexOf(id);

  return (
    <svg className="pointer-events-none absolute left-0 top-0 h-full w-full" aria-hidden>
      {template.slots.slice(0, -1).map((slot, i) => (
        <line
          key={`f-${slot.slotId}`}
          x1={cx}
          y1={yCenter(i) + CARD_H / 2 - 6}
          x2={cx}
          y2={yCenter(i + 1) - CARD_H / 2 + 6}
          className="pb-conn-flow"
        />
      ))}
      {relations.map((r, i) => {
        const fi = indexOf(r.from);
        const ti = indexOf(r.to);
        if (fi < 0 || ti < 0) return null;
        const y1 = yCenter(fi);
        const y2 = yCenter(ti);
        const off = 72;
        return (
          <path
            key={`r-${i}`}
            d={`M ${cx + off} ${y1} C ${cx + off + 40} ${y1}, ${cx + off + 40} ${y2}, ${cx + off} ${y2}`}
            className={r.kind === "pair" ? "pb-conn-pair" : "pb-conn-require"}
            fill="none"
          />
        );
      })}
    </svg>
  );
}

export function VariantAaa() {
  const [templateId, setTemplateId] = useState("tpl_img_knitted");
  const [selection, setSelection] = useState<PromptSelection>(() => ({
    ...defaultSelection(MOCK_TEMPLATES.find((t) => t.id === "tpl_img_knitted")!),
    vars: { ...DEFAULT_ORCHESTRATOR_VARS },
  }));
  const [activeSlotId, setActiveSlotId] = useState<string | null>(null);
  const [editorOpen, setEditorOpen] = useState(false);
  const [extraSlots, setExtraSlots] = useState<PromptSlot[]>([]);
  const [selectedCellId, setSelectedCellId] = useState<string | null>(null);
  const [preview, setPreview] = useState<PreviewState | null>(null);
  const dims = useMemo(() => new Set([...DEFAULT_DIMS, "pipeline"]), []);

  const template = MOCK_TEMPLATES.find((t) => t.id === templateId)!;

  const allSlots = useMemo(
    () => [...template.slots, ...extraSlots],
    [template.slots, extraSlots],
  );

  const slotIds = allSlots.map((s) => s.slotId);

  const findSlot = (slotId: string) => allSlots.find((s) => s.slotId === slotId);

  const relations = useMemo(
    () => buildSlotRelations(slotIds, selection.slots),
    [slotIds, selection.slots],
  );

  const cellUsage = useMemo(
    () => computeProjectCellUsage(selection, template),
    [selection, template],
  );

  const rankedBySlot = useMemo(() => {
    const map: Record<string, ReturnType<typeof rankBlocksForSlot>> = {};
    for (const slot of allSlots) {
      map[slot.slotId] = rankBlocksForSlot(
        slot.kind,
        template,
        selection.slots,
        MOCK_BLOCKS,
        MOCK_TEMPLATES,
        dims,
        slot.slotId,
      );
    }
    return map;
  }, [allSlots, template, selection.slots, dims]);

  const activeBlockId = useMemo(() => {
    if (!activeSlotId) return null;
    const slot = findSlot(activeSlotId);
    if (!slot || isSlotEmpty(selection.slots, slot)) return null;
    return resolveSlotBlockId(selection.slots, slot) || null;
  }, [activeSlotId, selection.slots, allSlots]);

  const activeBlock = activeBlockId ? MOCK_BLOCKS.find((b) => b.id === activeBlockId) : null;
  const activeAgent = activeBlockId ? agentForBlock(activeBlockId) : null;

  const highlightCellIds = useMemo(() => {
    if (!activeBlockId) return [];
    return getCellsForBlock(activeBlockId, template.stepCode);
  }, [activeBlockId, template.stepCode]);

  const activeAgentCount = useMemo(
    () => agentCountForCells(highlightCellIds, cellUsage),
    [highlightCellIds, cellUsage],
  );

  const pickTemplate = (id: string) => {
    const t = MOCK_TEMPLATES.find((x) => x.id === id)!;
    setTemplateId(id);
    setSelection({ ...defaultSelection(t), vars: { ...DEFAULT_ORCHESTRATOR_VARS, ...selection.vars } });
    setExtraSlots([]);
    setActiveSlotId(null);
    setEditorOpen(false);
    setSelectedCellId(null);
    setPreview(null);
  };

  const focusSlot = (slotId: string) => {
    setActiveSlotId(slotId);
    const slot = findSlot(slotId);
    if (!slot || isSlotEmpty(selection.slots, slot)) {
      setPreview(null);
      return;
    }
    const blockId = resolveSlotBlockId(selection.slots, slot);
    const block = MOCK_BLOCKS.find((b) => b.id === blockId);
    const agent = agentForBlock(blockId);
    setPreview({
      title: agent?.name ?? block?.label ?? "",
      subtitle: block?.kind,
      description: agent?.description ?? block?.body ?? "",
      kind: block?.kind,
    });
  };

  const selectBlock = (slotId: string, blockId: string) => {
    setSelection((s) => ({ ...s, slots: { ...s.slots, [slotId]: blockId } }));
    setActiveSlotId(slotId);
    const block = MOCK_BLOCKS.find((b) => b.id === blockId);
    const agent = agentForBlock(blockId);
    setPreview({
      title: agent?.name ?? block?.label ?? "",
      subtitle: block?.kind,
      description: agent?.description ?? block?.body ?? "",
      kind: block?.kind,
    });
  };

  const swapBlocks = (slotA: string, slotB: string) => {
    setSelection((s) => {
      const a = s.slots[slotA];
      const b = s.slots[slotB];
      if (!a || !b) return s;
      const blockA = MOCK_BLOCKS.find((x) => x.id === a);
      const blockB = MOCK_BLOCKS.find((x) => x.id === b);
      const slotMetaA = findSlot(slotA);
      const slotMetaB = findSlot(slotB);
      if (!blockA || !blockB || !slotMetaA || !slotMetaB) return s;
      if (blockA.kind !== slotMetaB.kind || blockB.kind !== slotMetaA.kind) return s;
      return { ...s, slots: { ...s.slots, [slotA]: b, [slotB]: a } };
    });
  };

  const clearBlock = (slotId: string) => {
    const slot = findSlot(slotId);
    if (!slot || slot.required) return;
    setSelection((s) => ({ ...s, slots: { ...s.slots, [slotId]: "" } }));
    if (activeSlotId === slotId) setPreview(null);
  };

  const removeSlot = (slotId: string) => {
    const slot = findSlot(slotId);
    if (!slot) return;
    if (slotId.startsWith("extra_")) {
      setExtraSlots((list) => list.filter((s) => s.slotId !== slotId));
      setSelection((s) => {
        const next = { ...s.slots };
        delete next[slotId];
        return { ...s, slots: next };
      });
      if (activeSlotId === slotId) {
        setActiveSlotId(null);
        setPreview(null);
      }
      return;
    }
    if (!slot.required) clearBlock(slotId);
  };

  const addBlockToKind = (kind: BlockKind, blockId: string) => {
    const block = MOCK_BLOCKS.find((b) => b.id === blockId);
    if (!block || block.kind !== kind) return;

    const emptyTemplate = allSlots.find(
      (s) => s.kind === kind && !s.required && isSlotEmpty(selection.slots, s),
    );
    if (emptyTemplate) {
      selectBlock(emptyTemplate.slotId, blockId);
      return;
    }

    const slotId = `extra_${kind}_${Date.now()}`;
    setExtraSlots((list) => [
      ...list,
      { slotId, kind, required: false, defaultBlockId: blockId },
    ]);
    selectBlock(slotId, blockId);
  };

  const moveBlock = (fromSlotId: string, toSlotId: string) => {
    setSelection((s) => {
      const block = s.slots[fromSlotId];
      if (!block) return s;
      const fromMeta = findSlot(fromSlotId);
      const toMeta = findSlot(toSlotId);
      if (!fromMeta || !toMeta || fromMeta.kind !== toMeta.kind) return s;
      return {
        ...s,
        slots: { ...s.slots, [toSlotId]: block, [fromSlotId]: "" },
      };
    });
    setActiveSlotId(toSlotId);
  };

  const openEditor = (slotId: string) => {
    focusSlot(slotId);
    setEditorOpen(true);
  };

  const setVar = (key: string, value: string | number) => {
    setSelection((s) => ({ ...s, vars: { ...s.vars, [key]: value } }));
  };

  const mockBlockRows = useMemo(
    () => MOCK_BLOCKS.map((b) => ({ id: b.id, kind: b.kind, label: b.label, body: b.body })),
    [],
  );

  const skeletonHeight = allSlots.length * (CARD_H + CARD_GAP) + 32;

  return (
    <div className="pb-graph-root flex min-h-screen flex-col">
      <header className="relative z-10 flex h-9 shrink-0 items-center justify-between border-b border-[var(--pb-border)] bg-[var(--pb-header)] px-3">
        <div className="flex items-center gap-2">
          <Link href="/" className="flex items-center gap-1 text-[10px] pb-text-muted hover:pb-text">
            <ArrowLeft className="h-3 w-3" />
            Студия
          </Link>
          <span className="pb-text-dim">|</span>
          <span className="text-[11px] font-semibold pb-text">Скелет промта</span>
        </div>
        <span className="max-w-[40%] truncate text-[9px] uppercase tracking-widest pb-text-dim">
          {template.label}
        </span>
      </header>

      <div className="relative min-h-0 flex-1">
        <div className="grid h-full grid-cols-[40px_minmax(0,1fr)_minmax(280px,340px)]">
          <PromptBuilderLeftRail
            templates={MOCK_TEMPLATES}
            activeTemplateId={templateId}
            onPickTemplate={pickTemplate}
            onOpenProjects={() => {
              window.location.href = "/";
            }}
          />

          <div className="relative flex min-h-0 min-w-0 flex-col overflow-hidden">
            <div className="flex min-h-0 flex-1 items-stretch gap-4 overflow-y-auto px-5 py-5">
              <ExcelCellsInline
                usage={cellUsage}
                highlightCellIds={highlightCellIds}
                selectedCellId={selectedCellId}
                onSelectCell={setSelectedCellId}
              />

              {editorOpen ? (
                <BlockEditorCenter
                  allSlots={allSlots}
                  selection={selection.slots}
                  activeSlotId={activeSlotId}
                  rankedBySlot={rankedBySlot}
                  categoryKinds={BLOCK_KINDS}
                  allBlocks={mockBlockRows}
                  useAgentLabels
                  onBack={() => setEditorOpen(false)}
                  onFocusSlot={focusSlot}
                  onSelectBlock={selectBlock}
                  onSwapBlocks={swapBlocks}
                  onRemoveSlot={removeSlot}
                  onAddBlockToKind={addBlockToKind}
                  onMoveBlock={moveBlock}
                />
              ) : (
                <div className="relative shrink-0" style={{ width: CANVAS_W, minHeight: skeletonHeight }}>
                  <VerticalConnections
                    template={{ slots: allSlots }}
                    relations={relations}
                    slotIds={slotIds}
                  />
                  <div className="relative z-[1] flex flex-col" style={{ gap: CARD_GAP }}>
                    {allSlots.map((slot, idx) => {
                      if (isSlotEmpty(selection.slots, slot)) return null;
                      const blockId = resolveSlotBlockId(selection.slots, slot);
                      return (
                        <SkeletonBlockCard
                          key={slot.slotId}
                          slot={slot}
                          index={idx}
                          selectedBlockId={blockId}
                          activeVariant={activeSlotId === slot.slotId}
                          onFocus={() => focusSlot(slot.slotId)}
                          onEdit={() => openEditor(slot.slotId)}
                        />
                      );
                    })}
                  </div>
                </div>
              )}
            </div>
          </div>

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
            onSelectSlot={focusSlot}
            onOpenEditor={openEditor}
          />
        </div>
      </div>
    </div>
  );
}
