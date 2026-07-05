"use client";

import { cn } from "@/lib/utils";
import { BLOCK_KINDS, type BlockKind } from "@/lib/prompt-builder/types";

export function BlockKindBadge({
  kind,
  compact,
  className,
}: {
  kind: BlockKind;
  compact?: boolean;
  className?: string;
}) {
  const meta = BLOCK_KINDS.find((k) => k.id === kind)!;
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-md px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide",
        className,
      )}
      style={{
        backgroundColor: `color-mix(in srgb, ${meta.color} 18%, transparent)`,
        color: meta.color,
        border: `1px solid color-mix(in srgb, ${meta.color} 35%, transparent)`,
      }}
      title={meta.description}
    >
      {compact ? meta.short : meta.label}
    </span>
  );
}

export function BlockKindLegend() {
  return (
    <div className="flex flex-wrap gap-1.5">
      {BLOCK_KINDS.map((k) => (
        <BlockKindBadge key={k.id} kind={k.id} compact />
      ))}
    </div>
  );
}
