"use client";

import { motion, AnimatePresence } from "framer-motion";
import { ChevronRight, Settings2 } from "lucide-react";
import { cn } from "@/lib/utils";
import {
  LEVEL_UI,
  rankBlocksForSlot,
  type BlockCompatibility,
} from "@/lib/prompt-builder/compatibility";
import { MOCK_BLOCKS, MOCK_TEMPLATES } from "@/lib/prompt-builder/mock-data";
import { BLOCK_KINDS, type BlockKind, type PromptTemplate } from "@/lib/prompt-builder/types";

const KIND_LABEL: Record<BlockKind, string> = Object.fromEntries(
  BLOCK_KINDS.map((k) => [k.id, k.label]),
) as Record<BlockKind, string>;

export function BlockCard({
  index,
  slotId,
  slotKind,
  template,
  selection,
  selectedBlockId,
  enabledDims,
  expanded,
  highlighted,
  onToggle,
  onSelectBlock,
  onHighlight,
}: {
  index: number;
  slotId: string;
  slotKind: BlockKind;
  template: PromptTemplate;
  selection: Record<string, string>;
  selectedBlockId: string;
  enabledDims: Set<string>;
  expanded: boolean;
  highlighted: boolean;
  onToggle: () => void;
  onSelectBlock: (blockId: string) => void;
  onHighlight: () => void;
}) {
  const block = MOCK_BLOCKS.find((b) => b.id === selectedBlockId)!;
  const ranked = rankBlocksForSlot(
    slotKind,
    template,
    selection,
    MOCK_BLOCKS,
    MOCK_TEMPLATES,
    enabledDims,
    slotId,
  );
  const compat = ranked.find((r) => r.blockId === selectedBlockId);
  const fit = compat?.level ?? "ok";

  return (
    <motion.div
      layout
      initial={{ opacity: 0, scale: 0.97, y: 8 }}
      animate={{ opacity: 1, scale: 1, y: 0 }}
      transition={{ delay: index * 0.04, duration: 0.25, ease: [0.22, 1, 0.36, 1] }}
      className={cn(
        "pb-card pb-card-animate-in overflow-hidden rounded-md",
        highlighted && "pb-card-selected",
        expanded && "pb-card-expanded",
      )}
      style={{ animationDelay: `${index * 40}ms` }}
    >
      <button
        type="button"
        onClick={() => {
          onHighlight();
          onToggle();
        }}
        className="flex w-full items-center gap-2 px-2.5 py-2 text-left"
      >
        <motion.span
          animate={{ rotate: expanded ? 90 : 0 }}
          transition={{ duration: 0.18 }}
          className="text-white/30"
        >
          <ChevronRight className="h-3 w-3" />
        </motion.span>
        <span className={cn("pb-fit-dot", `pb-fit-${fit}`)} />
        <div className="min-w-0 flex-1">
          <div className="flex items-baseline gap-2">
            <span className="pb-kind-label">{KIND_LABEL[slotKind]}</span>
            <span className="truncate text-[11px] font-medium text-white/85">{block.label}</span>
          </div>
        </div>
        <Settings2 className={cn("h-3 w-3 shrink-0", expanded ? "text-amber-400/70" : "text-white/20")} />
      </button>

      <AnimatePresence>
        {expanded && (
          <motion.div
            key="settings"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.22, ease: [0.22, 1, 0.36, 1] }}
            className="overflow-hidden"
          >
            <BlockCardSettings
              slotKind={slotKind}
              template={template}
              ranked={ranked.filter((r) => r.level !== "blocked")}
              selectedBlockId={selectedBlockId}
              blockBody={block.body}
              onSelect={onSelectBlock}
            />
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
}

function BlockCardSettings({
  slotKind,
  template,
  ranked,
  selectedBlockId,
  blockBody,
  onSelect,
}: {
  slotKind: BlockKind;
  template: PromptTemplate;
  ranked: BlockCompatibility[];
  selectedBlockId: string;
  blockBody: string;
  onSelect: (id: string) => void;
}) {
  return (
    <div className="pb-settings-panel px-2.5 py-2">
      <p className="mb-2 line-clamp-2 text-[10px] leading-relaxed text-white/40">{blockBody}</p>

      <p className="pb-kind-label mb-1">Вариант</p>
      <ul className="max-h-32 space-y-0.5 overflow-y-auto pb-scroll">
        {ranked.slice(0, 12).map((item) => {
          const b = MOCK_BLOCKS.find((x) => x.id === item.blockId)!;
          const active = item.blockId === selectedBlockId;
          return (
            <li key={item.blockId}>
              <button
                type="button"
                onClick={() => onSelect(item.blockId)}
                className={cn(
                  "flex w-full items-center gap-1.5 rounded px-1.5 py-1 text-left text-[10px]",
                  active ? "bg-white/[0.06] text-white/90" : "text-white/50 hover:bg-white/[0.03]",
                )}
              >
                <span className={cn("pb-fit-dot", `pb-fit-${item.level}`)} />
                <span className="min-w-0 flex-1 truncate">{b.label}</span>
                <span className="shrink-0 text-[8px] uppercase opacity-40">
                  {LEVEL_UI[item.level].label}
                </span>
              </button>
            </li>
          );
        })}
      </ul>

      <p className="pb-kind-label mb-1 mt-2">Из другого промта</p>
      <select
        className="h-7 w-full rounded border border-white/[0.08] bg-black/20 px-1.5 text-[10px] text-white/70"
        defaultValue=""
        onChange={(e) => {
          const tid = e.target.value;
          if (!tid) return;
          const src = MOCK_TEMPLATES.find((t) => t.id === tid);
          const match = src?.slots.find((s) => s.kind === slotKind);
          if (match && ranked.some((r) => r.blockId === match.defaultBlockId)) {
            onSelect(match.defaultBlockId);
          }
          e.target.value = "";
        }}
      >
        <option value="">—</option>
        {MOCK_TEMPLATES.map((t) => (
          <option key={t.id} value={t.id}>
            {t.label}
          </option>
        ))}
      </select>
    </div>
  );
}
