/** Per-slot привязка файлов промтов (meta.prompt_slot_variants). */

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
): string {
  const slotMap = getPromptSlotVariantsMeta(meta)[nodeKey];
  if (slotMap?.[slot.id]) return slotMap[slot.id];
  const preferred = preferredPromptFileName(slot);
  if (preferred) return preferred;
  if (stepCode && typeof promptOverrides[stepCode] === "string") {
    return promptOverrides[stepCode] as string;
  }
  return "default";
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
