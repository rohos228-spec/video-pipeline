import { excelGptSlotIndex, isExcelGptNode, EXCEL_GPT_STEP_CODE, type ExcelGptNodeConfig } from "@/lib/excel-gpt-config";
import { nodeTypeFromKey } from "@/lib/node-key";
import { pipelinePromptSlots, resolvePromptSlotsForNode, type NodePromptSlot } from "@/lib/node-prompts";
import { stepCodeForNodeType } from "@/lib/node-step-map";
import { activeVariantForExcelGpt, activeVariantForSlot } from "@/lib/prompt-slot-storage";

/** Active prompt variant of a node, same default-slot logic as the node menu (NodeStudio). */
export function computeNodeActiveVariant(
  meta: Record<string, unknown>,
  promptOverrides: Record<string, unknown>,
  nodeKey: string,
  globalActive: unknown,
): string {
  const nodeType = nodeTypeFromKey(nodeKey);
  const custom = (meta as { custom_prompts?: Record<string, NodePromptSlot[]> }).custom_prompts;
  const slots = pipelinePromptSlots(resolvePromptSlotsForNode(nodeKey, nodeType, custom));
  const slot = slots.find((s) => s.kind !== "excel") ?? slots.find((s) => s.kind === "excel") ?? slots[0] ?? null;
  if (!slot) return "default";
  const stepCode = slot.stepCode ?? stepCodeForNodeType(nodeType);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const ga = globalActive as any;
  if (isExcelGptNode(nodeType) && slot.kind === "gpt") {
    const cfg = ((meta as { excel_gpt_nodes?: Record<string, ExcelGptNodeConfig> }).excel_gpt_nodes?.[nodeKey] ?? {}) as ExcelGptNodeConfig;
    return String(activeVariantForExcelGpt(meta, nodeKey, slot, promptOverrides, excelGptSlotIndex(nodeKey, cfg.slotIndex), ga) || "default");
  }
  void EXCEL_GPT_STEP_CODE;
  return String(activeVariantForSlot(meta, nodeKey, slot, promptOverrides, stepCode, ga) || "default");
}