"use client";

import { useNodes, useInternalNode, ViewportPortal, type Node } from "@xyflow/react";
import { Sparkles } from "lucide-react";
import { cn } from "@/lib/utils";
import { useCanvasActionsOptional } from "./canvas-actions-context";
import type { PipelineNodeData } from "./pipeline-node";
import { nodeSupportsGptVerdict } from "@/lib/gpt-verdict-steps";

function NodeAiReviewMarker({
  nodeId,
  nodeKey,
  active,
  onOpen,
}: {
  nodeId: string;
  nodeKey: string;
  active: boolean;
  onOpen: () => void;
}) {
  const node = useInternalNode(nodeId);
  if (!node?.internals?.positionAbsolute) return null;

  const w = node.measured?.width ?? 260;
  const h = node.measured?.height ?? 80;
  const sx = node.internals.positionAbsolute.x + w;
  const sy = node.internals.positionAbsolute.y + h / 2;
  const bx = sx + 20;
  const by = sy;

  return (
    <>
      <div
        className={cn(
          "pointer-events-none absolute h-px border-t-2 border-dashed",
          active ? "border-violet-400/60" : "border-violet-400/40",
        )}
        style={{ left: sx + 2, top: by, width: 18, zIndex: 1000 }}
      />
      <button
        type="button"
        title="ИИ-проверка GPT — шаблоны, промт, запуск"
        className={cn(
          "node-ai-review-trigger nodrag nopan pointer-events-auto absolute flex h-8 w-8 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full border-2 shadow-lg transition",
          "hover:scale-110 hover:brightness-110",
          active
            ? "border-violet-300/80 bg-gradient-to-br from-violet-500/90 to-red-500/70 text-white shadow-violet-500/30"
            : "border-violet-400/50 bg-gradient-to-br from-violet-600/80 to-red-600/60 text-white shadow-violet-500/20",
        )}
        style={{ left: bx, top: by, zIndex: 1000 }}
        onMouseDown={(e) => e.stopPropagation()}
        onClick={(e) => {
          e.stopPropagation();
          onOpen();
        }}
      >
        <Sparkles className="h-4 w-4" />
      </button>
    </>
  );
}

/** Кружки ИИ-проверки поверх нод (ViewportPortal, z-index как у EdgeAiControls). */
export function NodeAiReviewControls() {
  const actions = useCanvasActionsOptional();
  const nodes = useNodes<Node<PipelineNodeData>>();

  if (!actions?.aiControl || !actions?.projectId) return null;

  const markers = nodes.filter((n) => nodeSupportsGptVerdict(n.data.type));

  if (markers.length === 0) return null;

  return (
    <ViewportPortal>
      <div className="pointer-events-none absolute inset-0">
        {markers.map((n) => (
          <NodeAiReviewMarker
            key={n.id}
            nodeId={n.id}
            nodeKey={n.data.nodeKey}
            active={actions.aiReviewNodeKey === n.data.nodeKey}
            onOpen={() => actions.onOpenAiReview(n.data.nodeKey, n.data.type)}
          />
        ))}
      </div>
    </ViewportPortal>
  );
}
