"use client";

import { Fragment, useMemo, useState } from "react";
import { useInternalNode, ViewportPortal, type Edge } from "@xyflow/react";
import { Sparkles } from "lucide-react";
import { useCanvasActionsOptional } from "./canvas-actions-context";
import type { PipelineNodeData } from "./pipeline-node";
import { AiControlEdgeDialog } from "./ai-control-edge-dialog";
import { defaultPromptSlots } from "@/lib/node-prompts";
import { autoReviewKindForNodeType } from "@/lib/control-mode";

type EdgeHit = {
  edgeId: string;
  sourceKey: string;
  targetKey: string;
  targetType: string;
};

function EdgeAiMarker({
  edge,
  onSelect,
}: {
  edge: Edge;
  onSelect: (hit: EdgeHit) => void;
}) {
  const source = useInternalNode(edge.source);
  const target = useInternalNode(edge.target);
  if (!source?.internals?.positionAbsolute || !target?.internals?.positionAbsolute) {
    return null;
  }
  const tgtType = (target.data as PipelineNodeData)?.type;
  if (tgtType === "excel_feed") return null;
  if (!autoReviewKindForNodeType(tgtType)) return null;

  const sw = source.measured?.width ?? 260;
  const sh = source.measured?.height ?? 80;
  const th = target.measured?.height ?? 80;

  const sx = source.internals.positionAbsolute.x + sw;
  const sy = source.internals.positionAbsolute.y + sh / 2;
  const tx = target.internals.positionAbsolute.x;
  const ty = target.internals.positionAbsolute.y + th / 2;

  const x = (sx + tx) / 2;
  const y = (sy + ty) / 2;

  return (
    <button
      type="button"
      title="ИИ-контроль между нодами"
      className="nodrag nopan pointer-events-auto absolute flex h-7 w-7 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full border border-red-500/50 bg-gradient-to-br from-red-600/80 to-violet-600/70 text-white shadow-lg shadow-red-500/20 transition hover:scale-110"
      style={{ left: x, top: y, zIndex: 1000 }}
      onMouseDown={(e) => e.stopPropagation()}
      onClick={(e) => {
        e.stopPropagation();
        onSelect({
          edgeId: edge.id,
          sourceKey: (source.data as PipelineNodeData)?.nodeKey ?? source.id,
          targetKey: (target.data as PipelineNodeData)?.nodeKey ?? target.id,
          targetType: tgtType,
        });
      }}
    >
      <Sparkles className="h-3.5 w-3.5" />
    </button>
  );
}

export function EdgeAiControls({ edges }: { edges: Edge[] }) {
  const actions = useCanvasActionsOptional();
  const [active, setActive] = useState<EdgeHit | null>(null);

  const visibleEdges = useMemo(
    () => edges.filter((e) => e.source && e.target),
    [edges],
  );

  // Только режим «ИИ проверка» (meta.ai_control). «manual» — строка, не falsy!
  if (!actions?.aiControl || !actions?.projectId) return null;

  const firstSlot = active ? defaultPromptSlots(active.targetType)[0] : null;

  return (
    <Fragment>
      <ViewportPortal>
        <div className="pointer-events-none absolute inset-0">
          {visibleEdges.map((e) => (
            <EdgeAiMarker key={e.id} edge={e} onSelect={setActive} />
          ))}
        </div>
      </ViewportPortal>
      {active && firstSlot && (
        <AiControlEdgeDialog
          open
          onOpenChange={(o) => {
            if (!o) setActive(null);
          }}
          projectId={actions.projectId!}
          projectMeta={(actions.project?.meta || {}) as Record<string, unknown>}
          sourceKey={active.sourceKey}
          targetKey={active.targetKey}
          targetType={active.targetType}
          onOpenPrompt={(nodeKey, nodeType) => {
            setActive(null);
            const slot = defaultPromptSlots(nodeType)[0];
            if (slot) actions.onOpenPrompt(nodeKey, nodeType, slot);
          }}
          onOpenGptText={(nodeKey, nodeType) => {
            setActive(null);
            actions.onOpenGptText(nodeKey, nodeType);
          }}
        />
      )}
    </Fragment>
  );
}
