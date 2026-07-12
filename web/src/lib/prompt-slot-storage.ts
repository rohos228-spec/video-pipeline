/** Per-slot привязка файлов промтов (meta.prompt_slot_variants). */

import { EXCEL_GPT_STEP_CODE } from "./excel-gpt-config";
import { isCustomPromptSlot, type NodePromptSlot } from "./node-prompts";

export type PromptSlotVariantsMeta = Record<string, Record<string, string>>;

export function getPromptSlotVariantsMeta(
  meta: Record<string, unknown> | undefined,
): PromptSlotVariantsMeta {
  return (meta?.prompt_slot_variants as PromptSlotVariantsMeta) ?? {};
}

/** Имя .md-файла, который редактирует этот слот (без расширения). */
export function preferredPromptFileName(slot: NodePromptSlot | null): string | undefined {
  if (!slot) return undefined;
  if (isCustomPromptSlot(slot)) return slot.id;
  return undefined;
}

/** Активный вариант для отображения / «Сделать активным» — отдельно для каждого слота. */
export function activeVariantForSlot(
  meta: Record<string, unknown> | undefined,
  nodeKey: string,
  slot: NodePromptSlot,
  promptOverrides: Record<string, unknown>,
  stepCode: string | undefined,
  globalActive?: Record<string, string>,
): string {
  const slotMap = getPromptSlotVariantsMeta(meta)[nodeKey];
  if (slotMap?.[slot.id]) return slotMap[slot.id];
  const preferred = preferredPromptFileName(slot);
  if (preferred) return preferred;
  if (stepCode && typeof promptOverrides[stepCode] === "string") {
    return promptOverrides[stepCode] as string;
  }
  if (stepCode && globalActive?.[stepCode]) {
    return globalActive[stepCode];
  }
  return "default";
}

export { excelGptPromptStepCode } from "./excel-gpt-config";

/** Активный вариант промта excel_gpt — единая папка 05_excel_gpt для всех нод. */
export function activeVariantForExcelGpt(
  meta: Record<string, unknown> | undefined,
  nodeKey: string,
  slot: NodePromptSlot,
  promptOverrides: Record<string, unknown>,
  _slotIndex?: number,
  globalActive?: Record<string, string>,
): string {
  return activeVariantForSlot(
    meta,
    nodeKey,
    slot,
    promptOverrides,
    EXCEL_GPT_STEP_CODE,
    globalActive,
  );
}

export function withSlotVariant(
  meta: Record<string, unknown>,
  nodeKey: string,
  slotId: string,
  variant: string,
): Record<string, unknown> {
  const all = { ...getPromptSlotVariantsMeta(meta) };
  const node = { ...(all[nodeKey] || {}) };
  node[slotId] = variant;
  all[nodeKey] = node;
  return { ...meta, prompt_slot_variants: all };
}
