"use client";

import { useMemo, useState } from "react";
import { ChevronDown, ChevronUp, Copy } from "lucide-react";
import { cn } from "@/lib/utils";
import { BlockKindBadge } from "./block-kind-badge";
import {
  LEVEL_UI,
  rankBlocksForSlot,
  type BlockCompatibility,
} from "@/lib/prompt-builder/compatibility";
import { MOCK_BLOCKS, MOCK_TEMPLATES } from "@/lib/prompt-builder/mock-data";
import type { BlockKind, PromptTemplate } from "@/lib/prompt-builder/types";

export function BlockPicker({
  slotId,
  slotKind,
  template,
  selection,
  selectedBlockId,
  enabledDims,
  onSelect,
  showRisky,
}: {
  slotId: string;
  slotKind: BlockKind;
  template: PromptTemplate;
  selection: Record<string, string>;
  selectedBlockId: string;
  enabledDims: Set<string>;
  onSelect: (blockId: string) => void;
  showRisky: boolean;
}) {
  const [open, setOpen] = useState(false);

  const ranked = useMemo(
    () =>
      rankBlocksForSlot(
        slotKind,
        template,
        selection,
        MOCK_BLOCKS,
        MOCK_TEMPLATES,
        enabledDims,
        slotId,
      ),
    [slotKind, template, selection, enabledDims, slotId],
  );

  const visible = ranked.filter(
    (r) => showRisky || r.level === "great" || r.level === "ok",
  );

  const selected = MOCK_BLOCKS.find((b) => b.id === selectedBlockId);
  const selectedCompat = ranked.find((r) => r.blockId === selectedBlockId);

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className={cn(
          "flex w-full items-center justify-between gap-2 rounded-lg border px-3 py-2 text-left text-xs transition-colors",
          selectedCompat && LEVEL_UI[selectedCompat.level].className,
          !selectedCompat && "border-input bg-background",
        )}
      >
        <span className="min-w-0 truncate font-medium">{selected?.label ?? "—"}</span>
        <span className="flex shrink-0 items-center gap-1.5">
          {selectedCompat && (
            <span className="text-[9px] uppercase">{LEVEL_UI[selectedCompat.level].label}</span>
          )}
          {open ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
        </span>
      </button>

      {selectedCompat && selectedCompat.reasons[0] && !open && (
        <p className="mt-1 text-[10px] text-muted-foreground">{selectedCompat.reasons[0].detail}</p>
      )}

      {open && (
        <div className="absolute left-0 right-0 z-20 mt-1 max-h-64 overflow-y-auto rounded-lg border border-border bg-popover shadow-lg">
          {visible.map((item) => (
            <BlockOption
              key={item.blockId}
              item={item}
              active={item.blockId === selectedBlockId}
              onPick={() => {
                onSelect(item.blockId);
                setOpen(false);
              }}
            />
          ))}
          {!showRisky && ranked.some((r) => r.level === "risky" || r.level === "blocked") && (
            <p className="border-t border-border px-3 py-2 text-[10px] text-muted-foreground">
              Есть рискованные варианты — включите «Показать риск»
            </p>
          )}
        </div>
      )}
    </div>
  );
}

function BlockOption({
  item,
  active,
  onPick,
}: {
  item: BlockCompatibility;
  active: boolean;
  onPick: () => void;
}) {
  const block = MOCK_BLOCKS.find((b) => b.id === item.blockId)!;
  const ui = LEVEL_UI[item.level];
  const disabled = item.level === "blocked";

  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onPick}
      className={cn(
        "w-full border-b border-border/50 px-3 py-2 text-left last:border-0",
        active && "bg-primary/10",
        disabled ? "cursor-not-allowed opacity-50" : "hover:bg-muted/50",
      )}
    >
      <div className="flex items-center gap-2">
        <span className={cn("h-2 w-2 shrink-0 rounded-full", ui.dot)} />
        <span className="text-xs font-medium">{block.label}</span>
        <BlockKindBadge kind={block.kind} compact />
        <span className="ml-auto text-[9px] uppercase text-muted-foreground">{ui.label}</span>
      </div>
      <p className="mt-1 line-clamp-1 text-[10px] text-muted-foreground">{item.reasons[0]?.detail}</p>
      {item.nativeIn.length > 0 && (
        <p className="mt-0.5 text-[9px] text-[hsl(var(--info))]">
          Родной промт: {item.nativeIn.slice(0, 2).join(", ")}
          {item.nativeIn.length > 2 ? ` +${item.nativeIn.length - 2}` : ""}
        </p>
      )}
    </button>
  );
}

/** Скопировать блок из другого промта, если совместим */
export function BorrowFromPrompt({
  template,
  slotId,
  slotKind,
  selection,
  enabledDims,
  onApply,
}: {
  template: PromptTemplate;
  slotId: string;
  slotKind: BlockKind;
  selection: Record<string, string>;
  enabledDims: Set<string>;
  onApply: (blockId: string) => void;
}) {
  const [sourceId, setSourceId] = useState("");

  const suggestions = useMemo(() => {
    if (!sourceId) return [];
    const source = MOCK_TEMPLATES.find((t) => t.id === sourceId)!;
    const out: { blockId: string; label: string; compat: BlockCompatibility }[] = [];
    for (const s of source.slots) {
      if (s.kind !== slotKind) continue;
      const blockId = s.defaultBlockId;
      const ranked = rankBlocksForSlot(
        slotKind,
        template,
        selection,
        MOCK_BLOCKS,
        MOCK_TEMPLATES,
        enabledDims,
        slotId,
      );
      const compat = ranked.find((r) => r.blockId === blockId);
      if (compat && compat.level !== "blocked") {
        const block = MOCK_BLOCKS.find((b) => b.id === blockId)!;
        out.push({ blockId, label: block.label, compat });
      }
    }
    return out;
  }, [sourceId, slotKind, template, selection, enabledDims, slotId]);

  return (
    <div className="mt-2 rounded-lg border border-dashed border-border/80 p-2">
      <div className="flex items-center gap-2">
        <Copy className="h-3 w-3 text-muted-foreground" />
        <span className="text-[10px] text-muted-foreground">Взять блок из промта:</span>
        <select
          className="h-7 min-w-0 flex-1 rounded border border-input bg-background px-1.5 text-[10px]"
          value={sourceId}
          onChange={(e) => setSourceId(e.target.value)}
        >
          <option value="">— выберите —</option>
          {MOCK_TEMPLATES.filter((t) => t.id !== template.id).map((t) => (
            <option key={t.id} value={t.id}>
              {t.label}
            </option>
          ))}
        </select>
      </div>
      {suggestions.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1">
          {suggestions.map((s) => (
            <button
              key={s.blockId}
              type="button"
              onClick={() => onApply(s.blockId)}
              className={cn(
                "rounded-md border px-2 py-1 text-[10px]",
                LEVEL_UI[s.compat.level].className,
              )}
            >
              {s.label} · {LEVEL_UI[s.compat.level].label}
            </button>
          ))}
        </div>
      )}
      {sourceId && suggestions.length === 0 && (
        <p className="mt-1 text-[10px] text-destructive">Нет совместимых блоков этого типа из выбранного промта</p>
      )}
    </div>
  );
}
