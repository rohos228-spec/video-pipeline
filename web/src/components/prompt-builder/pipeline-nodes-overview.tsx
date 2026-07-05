"use client";

import { useState } from "react";
import { GripVertical, Pencil } from "lucide-react";
import { cn } from "@/lib/utils";
import { isSlotEmpty, resolveSlotBlockId } from "@/lib/prompt-builder/compose";
import { DND_BLOCK, DND_KIND } from "@/lib/prompt-builder/dnd";
import type { BlockKindMeta, PromptSelection, PromptTemplate } from "@/lib/prompt-builder/types";
import { COMPOSE_STEP_LABELS } from "@/lib/prompt-builder/step-compose-map";

export type PipelineNodeView = {
  nodeType: string;
  stepCode: string;
  composeId: string | null;
  label?: string;
  template: PromptTemplate;
  categoryKinds: BlockKindMeta[];
};

function blockLabel(
  blockId: string,
  blocks: { id: string; label: string }[],
): string {
  return blocks.find((b) => b.id === blockId)?.label ?? blockId;
}

export function PipelineNodesOverview({
  nodes,
  blocks,
  selection,
  activeNodeType,
  onSelectNode,
  onEditNode,
  onDropBlock,
}: {
  nodes: PipelineNodeView[];
  blocks: { id: string; label: string; kind: string }[];
  selection: PromptSelection;
  activeNodeType: string;
  onSelectNode: (nodeType: string) => void;
  onEditNode: (nodeType: string) => void;
  onDropBlock?: (nodeType: string, kind: string, blockId: string) => void;
}) {
  const [dropOver, setDropOver] = useState<string | null>(null);

  const handleDrop = (nodeType: string, e: React.DragEvent) => {
    e.preventDefault();
    setDropOver(null);
    const blockId = e.dataTransfer.getData(DND_BLOCK);
    const kind = e.dataTransfer.getData(DND_KIND);
    if (!blockId || !kind || !onDropBlock) return;
    onSelectNode(nodeType);
    onDropBlock(nodeType, kind, blockId);
  };
  return (
    <div className="flex min-w-0 flex-1 flex-col gap-2 overflow-y-auto pr-1">
      <p className="mb-1 text-[7px] font-bold uppercase tracking-widest pb-text-dim">
        Ноды пайплайна
      </p>
      {nodes.map((node) => {
        const active = node.nodeType === activeNodeType;
        const label = node.label ?? COMPOSE_STEP_LABELS[node.composeId ?? ""] ?? node.template.label;
        const filled = node.template.slots.filter((s) => !isSlotEmpty(selection.slots, s));

        return (
          <div
            key={node.nodeType}
            className={cn(
              "pb-editor-cat-box group relative",
              active && "pb-glow-active",
              dropOver === node.nodeType && "pb-glow-drop",
            )}
            onDragOver={(e) => {
              if (!onDropBlock || !e.dataTransfer.types.includes(DND_BLOCK)) return;
              e.preventDefault();
              setDropOver(node.nodeType);
            }}
            onDragLeave={() => setDropOver((s) => (s === node.nodeType ? null : s))}
            onDrop={(e) => handleDrop(node.nodeType, e)}
          >
            <button
              type="button"
              onClick={() => onSelectNode(node.nodeType)}
              className="w-full px-3 py-2.5 text-left"
            >
              <p className="pb-editor-cat-label">{label}</p>
              <p className="mt-0.5 text-[9px] pb-text-dim">{node.stepCode}</p>
              <ul className="mt-2 space-y-1">
                {filled.length === 0 && (
                  <li className="text-[10px] italic pb-text-dim">— блоки не выбраны —</li>
                )}
                {filled.map((slot) => {
                  const blockId = resolveSlotBlockId(selection.slots, slot);
                  const kindLabel =
                    node.categoryKinds.find((k) => k.id === slot.kind)?.label ?? slot.kind;
                  return (
                    <li key={slot.slotId} className="text-[10px] pb-text-muted">
                      <span className="pb-text-dim">{kindLabel}:</span>{" "}
                      <span className="pb-text">{blockLabel(blockId, blocks)}</span>
                    </li>
                  );
                })}
              </ul>
            </button>
            <button
              type="button"
              title="Редактировать"
              aria-label="Редактировать"
              onClick={(e) => {
                e.stopPropagation();
                onSelectNode(node.nodeType);
                onEditNode(node.nodeType);
              }}
              className="pb-icon-btn absolute right-2 top-2 p-1"
            >
              <Pencil className="h-3 w-3" />
            </button>
          </div>
        );
      })}
    </div>
  );
}
