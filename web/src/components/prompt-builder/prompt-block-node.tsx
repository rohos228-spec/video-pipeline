"use client";

import { memo } from "react";
import { Handle, Position, type NodeProps } from "@xyflow/react";
import { cn } from "@/lib/utils";
import type { BlockKind } from "@/lib/prompt-builder/types";

export type PromptBlockNodeData = {
  slotId: string;
  kind: BlockKind;
  kindLabel: string;
  blockLabel: string;
  preview: string;
  tags: string[];
  fit: "great" | "ok" | "risky" | "blocked";
};

function PromptBlockNodeComponent({ data, selected }: NodeProps) {
  const d = data as PromptBlockNodeData;
  return (
    <div className="pb-node-wrap">
      <div className="pb-node-label">
        <span className="pb-node-cat">{d.kindLabel}</span>
        <span className="pb-node-name">{d.blockLabel}</span>
      </div>
      <div className={cn("pb-node-glass", selected && "pb-node-selected")}>
        <Handle type="target" position={Position.Left} id="in" />
        <div className="pb-node-body">
          {d.tags.length > 0 && (
            <div className="pb-node-tags">
              {d.tags.slice(0, 4).map((t) => (
                <span key={t} className="pb-node-tag">
                  {t}
                </span>
              ))}
            </div>
          )}
          <p className="pb-node-preview">{d.preview}</p>
        </div>
        <Handle type="source" position={Position.Right} id="out" />
        <span
          className={cn(
            "absolute right-2 top-2 pb-fit-dot-light",
            `pb-fit-${d.fit}`,
          )}
        />
      </div>
    </div>
  );
}

export const PromptBlockNode = memo(PromptBlockNodeComponent);

export const promptBlockNodeTypes = {
  promptBlock: PromptBlockNode,
};
