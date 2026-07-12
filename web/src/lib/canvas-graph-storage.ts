/** Граф канваса проекта — источник истины для позиций и связей. */

import type { WorkflowEdge, WorkflowNode } from "@/lib/types";

export type ProjectCanvasGraph = {
  workflow_id: number;
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
  saved_at: string;
};

export function readCanvasGraph(
  meta: Record<string, unknown> | null | undefined,
  workflowId: number,
): ProjectCanvasGraph | null {
  const raw = meta?.canvas_graph;
  if (!raw || typeof raw !== "object") return null;
  const cg = raw as Record<string, unknown>;
  const nodes = cg.nodes;
  if (!Array.isArray(nodes) || nodes.length === 0) return null;
  const wfId = Number(cg.workflow_id);
  if (Number.isFinite(wfId) && wfId !== workflowId) return null;
  const edges = Array.isArray(cg.edges) ? (cg.edges as WorkflowEdge[]) : [];
  return {
    workflow_id: workflowId,
    nodes: nodes as WorkflowNode[],
    edges,
    saved_at: typeof cg.saved_at === "string" ? cg.saved_at : "",
  };
}

export function buildCanvasGraph(
  workflowId: number,
  nodes: WorkflowNode[],
  edges: WorkflowEdge[],
): ProjectCanvasGraph {
  return {
    workflow_id: workflowId,
    nodes,
    edges,
    saved_at: new Date().toISOString(),
  };
}
