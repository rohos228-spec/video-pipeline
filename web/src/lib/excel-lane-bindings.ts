import type { Edge, Node } from "@xyflow/react";
import type { PipelineNodeData } from "@/components/canvas/pipeline-node";

export type ExcelLaneBinding = {
  plan_node_id: string;
  topic_index: number;
  topic: string | null;
};

/** Связи Excel → plan/topic и порядок по Y для массовой генерации. */
export function buildExcelLaneBindings(
  nodes: Node<PipelineNodeData>[],
  edges: Edge[],
  topics: string[],
): ExcelLaneBinding[] {
  const excel = nodes.find((n) => (n.data as PipelineNodeData).type === "excel_feed");
  if (!excel) return [];

  const targets = edges
    .filter((e) => e.source === excel.id)
    .map((e) => nodes.find((n) => n.id === e.target))
    .filter((n): n is Node<PipelineNodeData> => {
      if (!n) return false;
      const t = (n.data as PipelineNodeData).type;
      return t === "plan" || t === "topic";
    })
    .sort((a, b) => a.position.y - b.position.y);

  return targets.map((n, i) => ({
    plan_node_id: n.id,
    topic_index: i,
    topic: topics[i]?.trim() || null,
  }));
}

export function topicsFromBindings(bindings: ExcelLaneBinding[]): string[] {
  return bindings
    .slice()
    .sort((a, b) => a.topic_index - b.topic_index)
    .map((b) => b.topic)
    .filter((t): t is string => Boolean(t?.trim()));
}
