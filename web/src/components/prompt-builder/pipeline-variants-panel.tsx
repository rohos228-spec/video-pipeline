"use client";

import { useMemo, useState } from "react";
import { ChevronDown, GripVertical, Plus } from "lucide-react";
import { cn } from "@/lib/utils";
import type { BlockCompatibility } from "@/lib/prompt-builder/compatibility";
import { DND_BLOCK, DND_KIND } from "@/lib/prompt-builder/dnd";
import { iconForCategory } from "@/lib/prompt-builder/category-icons";
import { COMPOSE_STEP_LABELS } from "@/lib/prompt-builder/step-compose-map";
import type { BlockKindMeta, PromptSlot } from "@/lib/prompt-builder/types";

export function PipelineVariantsPanel({
  nodeLabel,
  composeId,
  categoryKinds,
  allBlocks,
  allSlots,
  rankedBySlot,
  placedBlockIds,
  onPickVariant,
  onCreateBlock,
  onAddOutsideCategory,
}: {
  nodeLabel?: string;
  composeId: string;
  categoryKinds: BlockKindMeta[];
  allBlocks: { id: string; kind: string; label: string; body: string }[];
  allSlots: PromptSlot[];
  rankedBySlot: Record<string, BlockCompatibility[]>;
  placedBlockIds: Set<string>;
  onPickVariant: (kind: string, blockId: string) => void;
  onCreateBlock?: (kind: string) => void;
  onAddOutsideCategory?: () => void;
}) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const title = nodeLabel ?? COMPOSE_STEP_LABELS[composeId] ?? composeId;
  const humanizeId = (id: string) =>
    id
      .replace(/[_-]+/g, " ")
      .replace(/\s+/g, " ")
      .trim()
      .replace(/^./, (ch) => ch.toUpperCase());
  const displayCategoryLabel = (kind: BlockKindMeta) =>
    kind.label && kind.label !== kind.id ? kind.label : humanizeId(kind.id);
  const displayBlock = (block: { id: string; label: string }) => {
    const label = block.label?.trim();
    if (label && label !== block.id && !/^content v\d+$/i.test(label)) {
      return { title: label, subtitle: block.id };
    }
    return { title: humanizeId(block.id), subtitle: block.id };
  };

  const toggle = (id: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const slotKinds = useMemo(() => new Set(allSlots.map((s) => s.kind)), [allSlots]);
  const kinds = categoryKinds.filter((k) => slotKinds.has(k.id) || allBlocks.some((b) => b.kind === k.id));

  return (
    <aside className="relative flex h-full min-w-0 flex-col border-l border-[var(--pb-border)] bg-[var(--pb-panel)]">
      <div className="border-b border-[var(--pb-border)] px-3 py-2">
        <p className="text-[9px] font-bold uppercase tracking-widest pb-text-dim">Варианты</p>
        <p className="mt-0.5 text-[11px] font-medium pb-text">{title}</p>
        {onAddOutsideCategory && (
          <button
            type="button"
            className="pb-btn-ghost mt-2 w-full justify-center px-2 py-1 text-[10px]"
            onClick={onAddOutsideCategory}
            title="Создать свободный блок и добавить его в центр"
          >
            <Plus className="h-3.5 w-3.5" />
            Блок вне категории
          </button>
        )}
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-2 space-y-2">
        {kinds.map((kind) => {
          const blocks = allBlocks.filter((b) => b.kind === kind.id);
          if (!blocks.length) return null;
          const open = expanded.has(kind.id);
          const slotForRank = allSlots.find((s) => s.kind === kind.id);

          return (
            <section
              key={kind.id}
              className={cn("pb-editor-cat-box", !open && "pb-editor-cat-collapsed")}
            >
              <header className="pb-editor-cat-head">
                <button type="button" className="pb-editor-cat-toggle" onClick={() => toggle(kind.id)}>
                  <span className="pb-editor-cat-label">{displayCategoryLabel(kind)}</span>
                  <ChevronDown className={cn("h-3.5 w-3.5 pb-text-dim transition-transform", open && "rotate-180")} />
                </button>
                {onCreateBlock && (
                  <button
                    type="button"
                    className="pb-btn-ghost mt-1 px-2 py-1 text-[9px]"
                    onClick={() => onCreateBlock(kind.id)}
                    title="Создать новый блок в этой категории"
                  >
                    <Plus className="h-3 w-3" />
                    Добавить в категорию
                  </button>
                )}
                {open && <p className="pb-editor-cat-desc mt-1">{kind.description}</p>}
              </header>
              {open && (
                <div className="pb-editor-cat-body space-y-1">
                  {blocks.map((b) => {
                    const ranked = slotForRank
                      ? rankedBySlot[slotForRank.slotId]?.find((r) => r.blockId === b.id)
                      : undefined;
                    const blocked = ranked?.level === "blocked";
                    const Icon = iconForCategory(kind.id);
                    const display = displayBlock(b);
                    return (
                      <button
                        key={b.id}
                        type="button"
                        draggable={!blocked}
                        disabled={blocked}
                        onClick={() => onPickVariant(kind.id, b.id)}
                        onDragStart={(e) => {
                          e.dataTransfer.setData(DND_BLOCK, b.id);
                          e.dataTransfer.setData(DND_KIND, kind.id);
                          e.dataTransfer.effectAllowed = "copy";
                        }}
                        className={cn(
                          "pb-neural-terminal w-full",
                          placedBlockIds.has(b.id) && "pb-neural-terminal-active",
                          blocked && "pointer-events-none opacity-35",
                        )}
                        style={{ height: 48 }}
                      >
                        <span
                          draggable={!blocked}
                          className="pb-neural-terminal-btn flex h-full w-full cursor-grab items-center gap-2 active:cursor-grabbing"
                        >
                          <span className="pb-neural-terminal-icon">
                            <Icon className="h-3.5 w-3.5" strokeWidth={1.5} />
                          </span>
                          <span className="min-w-0 flex-1 text-left">
                            <p className="pb-neural-terminal-title">{display.title}</p>
                            <p className="pb-neural-terminal-code">{display.subtitle}</p>
                          </span>
                          <GripVertical className="h-3 w-3 shrink-0 pb-text-dim" strokeWidth={1.5} />
                        </span>
                      </button>
                    );
                  })}
                </div>
              )}
            </section>
          );
        })}
      </div>
    </aside>
  );
}
