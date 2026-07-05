"use client";

import { useMemo, useState } from "react";
import { ChevronDown, GripVertical, Trash2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { agentForBlock } from "@/lib/prompt-builder/agents-catalog";
import type { BlockCompatibility } from "@/lib/prompt-builder/compatibility";
import { isSlotEmpty, resolveSlotBlockId } from "@/lib/prompt-builder/compose";
import { DND_BLOCK, DND_KIND, DND_SLOT } from "@/lib/prompt-builder/dnd";
import type { BlockKind, BlockKindMeta, PromptSlot } from "@/lib/prompt-builder/types";

type BlockRow = { id: string; kind: string; label: string; body: string };

function blockHelpers(allBlocks: BlockRow[], agentLookup?: (id: string) => { name?: string; short?: string } | null) {
  const byId = new Map(allBlocks.map((b) => [b.id, b]));
  return {
    title(blockId: string) {
      if (!blockId) return "— пусто —";
      return agentLookup?.(blockId)?.name ?? byId.get(blockId)?.label ?? blockId;
    },
    summary(blockId: string) {
      if (!blockId) return "";
      const block = byId.get(blockId);
      const agent = agentLookup?.(blockId);
      return agent?.short ?? block?.body.slice(0, 100) ?? "";
    },
  };
}

function kindsForSlots(slots: PromptSlot[], categoryKinds: BlockKindMeta[]) {
  const ids = new Set(slots.map((s) => s.kind));
  return categoryKinds.filter((k) => ids.has(k.id));
}

function CategoryBox({
  kind,
  children,
  className,
  collapsible = false,
  collapsed = false,
  onToggle,
  dropOver = false,
  onDragOver,
  onDragLeave,
  onDrop,
}: {
  kind: BlockKindMeta;
  children: React.ReactNode;
  className?: string;
  collapsible?: boolean;
  collapsed?: boolean;
  onToggle?: () => void;
  dropOver?: boolean;
  onDragOver?: (e: React.DragEvent) => void;
  onDragLeave?: () => void;
  onDrop?: (e: React.DragEvent) => void;
}) {
  return (
    <section
      className={cn("pb-editor-cat-box", collapsed && "pb-editor-cat-collapsed", dropOver && "pb-glow-drop", className)}
      onDragOver={onDragOver}
      onDragLeave={onDragLeave}
      onDrop={onDrop}
    >
      <header className="pb-editor-cat-head">
        {collapsible ? (
          <button type="button" className="pb-editor-cat-toggle" onClick={onToggle}>
            <span className="pb-editor-cat-label">{kind.label}</span>
            <ChevronDown className={cn("h-3.5 w-3.5 shrink-0 pb-text-dim transition-transform", !collapsed && "rotate-180")} />
          </button>
        ) : (
          <span className="pb-editor-cat-label">{kind.label}</span>
        )}
        {!collapsible && <p className="pb-editor-cat-desc">{kind.description}</p>}
        {collapsible && !collapsed && <p className="pb-editor-cat-desc mt-1">{kind.description}</p>}
      </header>
      <div className="pb-editor-cat-body">{children}</div>
    </section>
  );
}

function PlacedBlockRow({
  blockId,
  empty,
  canRemove,
  isActive,
  isOver,
  title,
  summary,
  onFocus,
  onRemove,
  onDragStart,
  onDragOver,
  onDragLeave,
  onDrop,
}: {
  blockId: string;
  empty: boolean;
  canRemove: boolean;
  isActive: boolean;
  isOver: boolean;
  title: string;
  summary: string;
  onFocus: () => void;
  onRemove: () => void;
  onDragStart: (e: React.DragEvent) => void;
  onDragOver: (e: React.DragEvent) => void;
  onDragLeave: () => void;
  onDrop: (e: React.DragEvent) => void;
}) {
  return (
    <div
      draggable={!empty && Boolean(blockId)}
      onClick={onFocus}
      onDragStart={onDragStart}
      onDragOver={onDragOver}
      onDragLeave={onDragLeave}
      onDrop={onDrop}
      className={cn(
        "pb-editor-placed group/row flex items-start gap-1.5",
        isActive && "pb-glow-active",
        isOver && "pb-glow-drop",
        empty && "pb-editor-slot-empty",
      )}
    >
      <GripVertical className="mt-0.5 h-3.5 w-3.5 shrink-0 pb-text-dim" strokeWidth={1.5} />
      <div className="min-w-0 flex-1">
        <p className={cn("text-[11px] font-medium leading-snug tracking-[-0.01em]", empty ? "pb-text-dim italic" : "pb-text")}>
          {empty ? "Перетащите сюда" : title}
        </p>
        {!empty && summary && (
          <p className="mt-1 line-clamp-2 text-[10px] leading-relaxed pb-text-muted">{summary}</p>
        )}
      </div>
      {canRemove && !empty && (
        <button
          type="button"
          title="Удалить"
          aria-label="Удалить блок"
          onClick={(e) => {
            e.stopPropagation();
            onRemove();
          }}
          className="pb-icon-btn shrink-0 p-1"
        >
          <Trash2 className="h-3 w-3" />
        </button>
      )}
    </div>
  );
}

function VariantRow({
  title,
  summary,
  isPlaced,
  blocked,
  hint,
  onPick,
  onDragStart,
}: {
  title: string;
  summary: string;
  isPlaced: boolean;
  blocked?: boolean;
  hint?: string;
  onPick: () => void;
  onDragStart: (e: React.DragEvent) => void;
}) {
  return (
    <button
      type="button"
      draggable={!blocked}
      title={hint}
      onClick={onPick}
      onDragStart={onDragStart}
      className={cn(
        "pb-editor-variant group/var flex w-full items-start gap-1.5 text-left",
        isPlaced && "pb-editor-variant-placed",
        blocked && "pointer-events-none",
      )}
      style={blocked ? { opacity: 0.35 } : undefined}
    >
      <GripVertical className="mt-0.5 h-3.5 w-3.5 shrink-0 pb-text-dim" strokeWidth={1.5} />
      <div className="min-w-0 flex-1">
        <p className="text-[11px] font-medium leading-snug tracking-[-0.01em] pb-text">{title}</p>
        <p className="mt-1 line-clamp-2 text-[10px] leading-relaxed pb-text-muted">{summary}</p>
      </div>
    </button>
  );
}

export function BlockEditorCenter({
  allSlots,
  selection,
  activeSlotId,
  rankedBySlot,
  categoryKinds,
  allBlocks,
  useAgentLabels = false,
  onBack,
  onFocusSlot,
  onSelectBlock,
  onSwapBlocks,
  onRemoveSlot,
  onAddBlockToKind,
  onMoveBlock,
}: {
  allSlots: PromptSlot[];
  selection: Record<string, string>;
  activeSlotId: string | null;
  rankedBySlot: Record<string, BlockCompatibility[]>;
  categoryKinds: BlockKindMeta[];
  allBlocks: BlockRow[];
  useAgentLabels?: boolean;
  onBack?: () => void;
  onFocusSlot: (slotId: string) => void;
  onSelectBlock: (slotId: string, blockId: string) => void;
  onSwapBlocks: (slotA: string, slotB: string) => void;
  onRemoveSlot: (slotId: string) => void;
  onAddBlockToKind: (kind: BlockKind, blockId: string) => void;
  onMoveBlock: (fromSlotId: string, toSlotId: string) => void;
}) {
  const [dragOver, setDragOver] = useState<string | null>(null);
  const [expandedVariants, setExpandedVariants] = useState<Set<string>>(new Set());

  const { title: blockTitle, summary: blockSummary } = useMemo(
    () => blockHelpers(allBlocks, useAgentLabels ? (id) => agentForBlock(id) ?? null : undefined),
    [allBlocks, useAgentLabels],
  );

  const kinds = useMemo(() => kindsForSlots(allSlots, categoryKinds), [allSlots, categoryKinds]);

  const blocksByKind = useMemo(() => {
    const map = new Map<BlockKind, BlockRow[]>();
    for (const k of categoryKinds) {
      map.set(k.id, allBlocks.filter((b) => b.kind === k.id));
    }
    return map;
  }, [categoryKinds, allBlocks]);

  const placedBlockIds = useMemo(() => {
    const set = new Set<string>();
    for (const slot of allSlots) {
      if (isSlotEmpty(selection, slot)) continue;
      const id = resolveSlotBlockId(selection, slot);
      if (id) set.add(id);
    }
    return set;
  }, [allSlots, selection]);

  const handleDropOnSlot = (slotId: string, slotKind: BlockKind, e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragOver(null);
    const fromSlot = e.dataTransfer.getData(DND_SLOT);
    const block = e.dataTransfer.getData(DND_BLOCK);
    const targetSlot = allSlots.find((s) => s.slotId === slotId);
    if (!targetSlot) return;

    if (fromSlot && fromSlot !== slotId) {
      if (isSlotEmpty(selection, targetSlot)) {
        onMoveBlock(fromSlot, slotId);
      } else {
        onSwapBlocks(fromSlot, slotId);
      }
      return;
    }

    if (block) {
      const b = allBlocks.find((x) => x.id === block);
      if (!b || b.kind !== slotKind) return;
      if (isSlotEmpty(selection, targetSlot)) {
        onSelectBlock(slotId, block);
      } else {
        onAddBlockToKind(slotKind, block);
      }
    }
  };

  const handleDropOnKind = (kind: BlockKind, e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(null);
    const block = e.dataTransfer.getData(DND_BLOCK);
    if (!block) return;
    const b = allBlocks.find((x) => x.id === block);
    if (b?.kind === kind) onAddBlockToKind(kind, block);
  };

  const toggleVariants = (kindId: string) => {
    setExpandedVariants((prev) => {
      const next = new Set(prev);
      if (next.has(kindId)) next.delete(kindId);
      else next.add(kindId);
      return next;
    });
  };

  return (
    <div className="pb-editor-center pb-settings-fade min-h-0 min-w-0 flex-1 overflow-hidden px-3">
      <div className="mb-4 flex shrink-0 items-center justify-between border-b border-[var(--pb-border)] pb-3">
        <div className="flex items-center gap-3">
          {onBack && (
            <button type="button" className="pb-btn-ghost" onClick={onBack}>
              Назад
            </button>
          )}
          <div>
            <p className="pb-title">Редактор скелета</p>
            <p className="pb-subtitle mt-0.5">Перетащите вариант вправо</p>
          </div>
        </div>
      </div>

      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto pb-2 pr-1">
        <div className="mb-1 grid grid-cols-[1fr_1.15fr] gap-3 px-0.5">
          <p className="pb-label-caps">Варианты</p>
          <p className="pb-label-caps text-[var(--pb-accent)]">Актуальные</p>
        </div>

        <div className="pb-stagger space-y-4">
        {kinds.map((kind) => {
          const slots = allSlots.filter((s) => s.kind === kind.id);
          const variantBlocks = blocksByKind.get(kind.id) ?? [];
          const kindDropOver = dragOver === `kind-${kind.id}`;
          const variantsOpen = expandedVariants.has(kind.id);

          return (
            <div key={kind.id} className="pb-editor-cat-row pb-editor-cat-row--edit-right">
              <CategoryBox
                kind={kind}
                collapsible
                collapsed={!variantsOpen}
                onToggle={() => toggleVariants(kind.id)}
                className="pb-editor-col-variants"
              >
                <div className="space-y-1">
                  {variantBlocks.map((b) => {
                    const slotForRank = allSlots.find((s) => s.kind === b.kind);
                    const ranked = slotForRank
                      ? rankedBySlot[slotForRank.slotId]?.find((r) => r.blockId === b.id)
                      : undefined;
                    return (
                      <VariantRow
                        key={b.id}
                        title={b.label}
                        summary={blockSummary(b.id)}
                        isPlaced={placedBlockIds.has(b.id)}
                        blocked={ranked?.level === "blocked"}
                        hint={ranked?.reasons[0]?.detail ?? blockSummary(b.id)}
                        onPick={() => onAddBlockToKind(kind.id, b.id)}
                        onDragStart={(e) => {
                          e.dataTransfer.setData(DND_BLOCK, b.id);
                          e.dataTransfer.setData(DND_KIND, kind.id);
                          e.dataTransfer.effectAllowed = "copy";
                        }}
                      />
                    );
                  })}
                </div>
              </CategoryBox>

              <CategoryBox
                kind={kind}
                dropOver={kindDropOver}
                className="pb-editor-col-actual"
                onDragOver={(e) => {
                  if (!e.dataTransfer.types.includes(DND_BLOCK)) return;
                  e.preventDefault();
                  setDragOver(`kind-${kind.id}`);
                }}
                onDragLeave={() => setDragOver((s) => (s === `kind-${kind.id}` ? null : s))}
                onDrop={(e) => handleDropOnKind(kind.id, e)}
              >
                {slots.map((slot) => {
                  const blockId = resolveSlotBlockId(selection, slot);
                  const empty = isSlotEmpty(selection, slot);
                  const canRemove = !slot.required || slot.slotId.startsWith("extra_");
                  return (
                    <PlacedBlockRow
                      key={slot.slotId}
                      blockId={blockId}
                      empty={empty}
                      canRemove={canRemove}
                      isActive={activeSlotId === slot.slotId}
                      isOver={dragOver === slot.slotId}
                      title={blockTitle(blockId)}
                      summary={blockSummary(blockId)}
                      onFocus={() => onFocusSlot(slot.slotId)}
                      onRemove={() => onRemoveSlot(slot.slotId)}
                      onDragStart={(e) => {
                        if (empty || !blockId) {
                          e.preventDefault();
                          return;
                        }
                        e.dataTransfer.setData(DND_SLOT, slot.slotId);
                        e.dataTransfer.setData(DND_BLOCK, blockId);
                        e.dataTransfer.effectAllowed = "move";
                      }}
                      onDragOver={(e) => {
                        e.preventDefault();
                        e.stopPropagation();
                        setDragOver(slot.slotId);
                      }}
                      onDragLeave={() => setDragOver((s) => (s === slot.slotId ? null : s))}
                      onDrop={(e) => handleDropOnSlot(slot.slotId, slot.kind, e)}
                    />
                  );
                })}
              </CategoryBox>
            </div>
          );
        })}
        </div>
      </div>
    </div>
  );
}
