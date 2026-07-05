"use client";

import { Pencil } from "lucide-react";
import { cn } from "@/lib/utils";
import { MOCK_BLOCKS } from "@/lib/prompt-builder/mock-data";
import type { BlockKindMeta, BlockVariant, PromptSlot } from "@/lib/prompt-builder/types";
import { BLOCK_KINDS } from "@/lib/prompt-builder/types";
import { agentForBlock } from "@/lib/prompt-builder/agents-catalog";
import { abbrevLabel, iconForCategory } from "@/lib/prompt-builder/category-icons";

type BlockLookup = Pick<BlockVariant, "id" | "label" | "body" | "kind">;

/** Компактная карточка слота на пайплайне — без выбора, только поток */
export function SkeletonBlockCard({
  slot,
  index,
  selectedBlockId,
  activeVariant,
  onFocus,
  onEdit,
  blocks = MOCK_BLOCKS,
  categoryKinds = BLOCK_KINDS,
}: {
  slot: PromptSlot;
  index: number;
  selectedBlockId: string;
  activeVariant: boolean;
  onFocus: () => void;
  onEdit: () => void;
  blocks?: BlockLookup[];
  categoryKinds?: BlockKindMeta[];
}) {
  const kindLabel = categoryKinds.find((k) => k.id === slot.kind)?.label ?? slot.kind;
  const block = blocks.find((b) => b.id === selectedBlockId);
  const agent = slot.kind === "role" ? agentForBlock(selectedBlockId) : null;
  const Icon = iconForCategory(slot.kind);
  const title = agent?.name ?? block?.label ?? "—";

  return (
    <div
      id={`sk-slot-${slot.slotId}`}
      className={cn(
        "pb-neural-terminal group relative w-full",
        activeVariant && "pb-neural-terminal-active",
      )}
      style={{ animationDelay: `${index * 50}ms`, height: 54 }}
    >
      <button type="button" onClick={onFocus} className="pb-neural-terminal-btn h-full">
        <span className="pb-neural-terminal-icon">
          <Icon className="h-3.5 w-3.5" strokeWidth={1.5} />
        </span>
        <span className="min-w-0 flex-1 text-left">
          <span className="pb-neural-terminal-title">{title}</span>
          <span className="pb-neural-terminal-code">{abbrevLabel(kindLabel, slot.kind)}</span>
        </span>
        {slot.required && <span className="pb-neural-terminal-score text-[8px]">req</span>}
      </button>
      <button
        type="button"
        title="Редактировать"
        aria-label="Редактировать блоки"
        onClick={(e) => {
          e.stopPropagation();
          onEdit();
        }}
        className="pb-neural-terminal-edit opacity-30 group-hover:opacity-80"
      >
        <Pencil className="h-3 w-3" />
      </button>
    </div>
  );
}

export function buildSlotRelations(
  slotIds: string[],
  selection: Record<string, string>,
  blocks: Pick<BlockVariant, "id" | "pairsWell">[] = MOCK_BLOCKS,
): { from: string; to: string; kind: "flow" | "pair" | "require" }[] {
  const rels: { from: string; to: string; kind: "flow" | "pair" | "require" }[] = [];

  for (const sid of slotIds) {
    const bid = selection[sid];
    if (!bid) continue;
    const block = blocks.find((b) => b.id === bid);
    if (!block) continue;

    for (const pid of block.pairsWell ?? []) {
      const targetSlot = slotIds.find((s) => selection[s] === pid);
      if (targetSlot && targetSlot !== sid) {
        rels.push({ from: sid, to: targetSlot, kind: "pair" });
      }
    }
  }
  return rels;
}
