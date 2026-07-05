"use client";

import { useMemo } from "react";
import {
  LEVEL_UI,
  rankBlocksForSlot,
} from "@/lib/prompt-builder/compatibility";
import { MOCK_BLOCKS, MOCK_TEMPLATES } from "@/lib/prompt-builder/mock-data";
import type { PromptSlot, PromptTemplate } from "@/lib/prompt-builder/types";
import { BLOCK_KINDS } from "@/lib/prompt-builder/types";

export function BlockSettingsPanel({
  slot,
  template,
  selection,
  selectedBlockId,
  enabledDims,
  onSelectBlock,
}: {
  slot: PromptSlot;
  template: PromptTemplate;
  selection: Record<string, string>;
  selectedBlockId: string;
  enabledDims: Set<string>;
  onSelectBlock: (blockId: string) => void;
}) {
  const block = MOCK_BLOCKS.find((b) => b.id === selectedBlockId)!;
  const kindLabel = BLOCK_KINDS.find((k) => k.id === slot.kind)?.label ?? slot.kind;

  const ranked = useMemo(
    () =>
      rankBlocksForSlot(
        slot.kind,
        template,
        selection,
        MOCK_BLOCKS,
        MOCK_TEMPLATES,
        enabledDims,
        slot.slotId,
      ).filter((r) => r.level !== "blocked"),
    [slot, template, selection, enabledDims],
  );

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <p className="pb-panel-title">{kindLabel}</p>
      <p className="mt-0.5 text-[11px] font-medium text-black/70">{block.label}</p>
      <p className="mt-2 line-clamp-3 text-[10px] leading-relaxed text-black/45">{block.body}</p>

      <p className="pb-panel-title mt-4 mb-1">Вариант</p>
      <ul className="min-h-0 flex-1 space-y-0.5 overflow-y-auto">
        {ranked.slice(0, 14).map((item) => {
          const b = MOCK_BLOCKS.find((x) => x.id === item.blockId)!;
          const active = item.blockId === selectedBlockId;
          return (
            <li key={item.blockId}>
              <button
                type="button"
                onClick={() => onSelectBlock(item.blockId)}
                className={`flex w-full items-center gap-1.5 rounded px-1.5 py-1 text-left text-[10px] ${
                  active ? "bg-black/[0.06] text-black/80" : "text-black/50 hover:bg-black/[0.03]"
                }`}
              >
                <span className={`pb-fit-dot-light pb-fit-${item.level}`} />
                <span className="min-w-0 flex-1 truncate">{b.label}</span>
                <span className="text-[8px] uppercase opacity-40">{LEVEL_UI[item.level].label}</span>
              </button>
            </li>
          );
        })}
      </ul>

      <p className="pb-panel-title mt-3 mb-1">Из промта</p>
      <select
        className="h-7 w-full rounded border border-black/[0.08] bg-white/50 px-1.5 text-[10px] text-black/60"
        defaultValue=""
        onChange={(e) => {
          const tid = e.target.value;
          if (!tid) return;
          const src = MOCK_TEMPLATES.find((t) => t.id === tid);
          const match = src?.slots.find((s) => s.kind === slot.kind);
          if (match && ranked.some((r) => r.blockId === match.defaultBlockId)) {
            onSelectBlock(match.defaultBlockId);
          }
          e.target.value = "";
        }}
      >
        <option value="">—</option>
        {MOCK_TEMPLATES.filter((t) => t.id !== template.id).map((t) => (
          <option key={t.id} value={t.id}>
            {t.label}
          </option>
        ))}
      </select>
    </div>
  );
}
