"use client";

import { Fragment, useMemo, useState } from "react";
import { useNodes, useReactFlow, type Edge } from "@xyflow/react";
import { Sparkles } from "lucide-react";
import { useCanvasActionsOptional } from "./canvas-actions-context";
import { readControlMode } from "@/lib/control-mode";
import type { PipelineNodeData } from "./pipeline-node";
import { AiControlEdgeDialog } from "./ai-control-edge-dialog";
import { defaultPromptSlots } from "@/lib/node-prompts";

type EdgeHit = {
  edgeId: string;
  x: number;
  y: number;
  sourceKey: string;
  targetKey: string;
  targetType: string;
};

export function EdgeAiControls({ edges }: { edges: Edge[] }) {
  const actions = useCanvasActionsOptional();
  const nodes = useNodes();
  const { getNode } = useReactFlow();
  const [active, setActive] = useState<EdgeHit | null>(null);

  const aiControl = readControlMode(
    (actions?.project?.meta || {}) as Record<string, unknown>,
  );

  const markers = useMemo((): EdgeHit[] => {
    if (!aiControl) return [];
    const out: EdgeHit[] = [];
    for (const e of edges) {
      const src = getNode(e.source);
      const tgt = getNode(e.target);
      if (!src || !tgt) continue;
      const tgtType = (tgt.data as PipelineNodeData)?.type;
      if (tgtType === "excel_feed") continue;
      const w = (src.measured?.width ?? 260) / 2;
      const tw = (tgt.measured?.width ?? 260) / 2;
      const sx = src.position.x + w;
      const sy = src.position.y + (src.measured?.height ?? 80) / 2;
      const tx = tgt.position.x - tw;
      const ty = tgt.position.y + (tgt.measured?.height ?? 80) / 2;
      out.push({
        edgeId: e.id,
        x: (sx + tx) / 2,
        y: (sy + ty) / 2,
        sourceKey: (src.data as PipelineNodeData)?.nodeKey ?? src.id,
        targetKey: (tgt.data as PipelineNodeData)?.nodeKey ?? tgt.id,
        targetType: tgtType,
      });
    }
    return out;
  }, [aiControl, edges, getNode, nodes]);

  if (!aiControl || !actions?.projectId) return null;

  const firstSlot = active
    ? defaultPromptSlots(active.targetType)[0]
    : null;

  return (
    <Fragment>
      <div className="pointer-events-none absolute inset-0 z-[5]">
        {markers.map((m) => (
          <button
            key={m.edgeId}
            type="button"
            title="ИИ-контроль между нодами"
            className="pointer-events-auto absolute flex h-7 w-7 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full border border-red-500/50 bg-gradient-to-br from-red-600/80 to-violet-600/70 text-white shadow-lg shadow-red-500/20 transition hover:scale-110"
            style={{ left: m.x, top: m.y }}
            onMouseDown={(e) => e.stopPropagation()}
            onClick={(e) => {
              e.stopPropagation();
              setActive(m);
            }}
          >
            <Sparkles className="h-3.5 w-3.5" />
          </button>
        ))}
      </div>
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
