import type { Node } from "@xyflow/react";
import { getNodeSpec } from "@/lib/node-catalog";
import type { PipelineNodeData } from "@/components/canvas/pipeline-node";
import type { WorkflowNode } from "@/lib/types";

function migrateNodeType(type: string): string {
  if (type.startsWith("enrich_")) return "excel_gpt";
  return type;
}

function slotFromLegacyType(type: string): number | undefined {
  if (!type.startsWith("enrich_")) return undefined;
  const n = Number.parseInt(type.replace("enrich_", ""), 10);
  return Number.isFinite(n) && n >= 1 && n <= 5 ? n : undefined;
}

export function workflowNodeFromCanvas(n: Node): WorkflowNode {
  const d = n.data as PipelineNodeData;
  const type = migrateNodeType(d.type);
  const spec = getNodeSpec(type);
  const label = (typeof d.label === "string" && d.label.trim()) || spec.label;
  const data: Record<string, unknown> = {
    label,
    description: d.description ?? spec.description,
  };
  const legacySlot = slotFromLegacyType(d.type);
  if (legacySlot != null) data.slotIndex = legacySlot;
  if (typeof d.slotIndex === "number") data.slotIndex = d.slotIndex;
  if (typeof d.inputSource === "string") data.inputSource = d.inputSource;
  if (typeof d.uploadedFileName === "string") data.uploadedFileName = d.uploadedFileName;
  return {
    id: n.id,
    type,
    position: n.position,
    data,
  };
}

export function assignExcelGptSlotIndices(nodes: WorkflowNode[]): WorkflowNode[] {
  const excel = nodes
    .filter((n) => n.type === "excel_gpt")
    .sort((a, b) => (a.position?.x ?? 0) - (b.position?.x ?? 0));
  const slotById = new Map<string, number>();
  excel.forEach((n, i) => slotById.set(n.id, Math.min(i + 1, 5)));
  return nodes.map((n) => {
    if (n.type !== "excel_gpt") return n;
    const slotIndex = slotById.get(n.id) ?? 1;
    return {
      ...n,
      data: {
        ...(n.data || {}),
        slotIndex,
        label: (n.data?.label as string) || `Доп. Excel #${slotIndex}`,
      },
    };
  });
}

export function migrateWorkflowNodes(nodes: WorkflowNode[]): WorkflowNode[] {
  const migrated = nodes.map((n) => {
    if (!n.type.startsWith("enrich_")) return n;
    const slot = slotFromLegacyType(n.type);
    return {
      ...n,
      type: "excel_gpt",
      data: {
        ...(n.data || {}),
        slotIndex: slot ?? (n.data?.slotIndex as number | undefined),
        label: (n.data?.label as string) || (slot ? `Доп. Excel #${slot}` : "Доп. Excel"),
      },
    };
  });
  return assignExcelGptSlotIndices(migrated);
}
