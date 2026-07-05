"use client";

import { cn } from "@/lib/utils";
import type { BlockCompatibility } from "@/lib/prompt-builder/compatibility";
import { abbrevLabel, iconForCategory } from "@/lib/prompt-builder/category-icons";
import type { BlockKind, BlockKindMeta } from "@/lib/prompt-builder/types";
import { FloatingPanel } from "./floating-panel";

export function NeuralVariantPicker({
  open,
  kind,
  categoryKinds,
  blocks,
  placedBlockIds,
  ranked,
  onPick,
  onClose,
}: {
  open: boolean;
  kind: BlockKind | null;
  categoryKinds: BlockKindMeta[];
  blocks: { id: string; kind: string; label: string; body: string }[];
  placedBlockIds: Set<string>;
  ranked?: BlockCompatibility[];
  onPick: (blockId: string) => void;
  onClose: () => void;
}) {
  if (!kind) return null;
  const meta = categoryKinds.find((k) => k.id === kind);
  const variantBlocks = blocks.filter((b) => b.kind === kind);
  const Icon = iconForCategory(kind);

  return (
    <FloatingPanel
      open={open}
      title={meta?.label ?? kind}
      subtitle={meta?.description}
      initialPosition={{ x: 400, y: 96 }}
      initialSize={{ w: 280, h: Math.min(420, 80 + variantBlocks.length * 52) }}
      onClose={onClose}
    >
      <div className="space-y-1.5">
        {variantBlocks.map((b) => {
          const rankedRow = ranked?.find((r) => r.blockId === b.id);
          const blocked = rankedRow?.level === "blocked";
          return (
            <button
              key={b.id}
              type="button"
              disabled={blocked}
              onClick={() => {
                onPick(b.id);
                onClose();
              }}
              className={cn(
                "pb-neural-terminal w-full",
                placedBlockIds.has(b.id) && "pb-neural-terminal-active",
                blocked && "pointer-events-none opacity-35",
              )}
              style={{ height: 48 }}
            >
              <span className="pb-neural-terminal-btn flex h-full w-full items-center gap-2">
                <span className="pb-neural-terminal-icon">
                  <Icon className="h-3.5 w-3.5" strokeWidth={1.5} />
                </span>
                <span className="min-w-0 flex-1 text-left">
                  <p className="pb-neural-terminal-title">{b.label}</p>
                  <p className="pb-neural-terminal-code">{abbrevLabel(meta?.label ?? kind, kind)}</p>
                </span>
              </span>
            </button>
          );
        })}
      </div>
    </FloatingPanel>
  );
}
