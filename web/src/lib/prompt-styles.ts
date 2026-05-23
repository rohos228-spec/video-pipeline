/** Стили привязаны к конкретному промту конкретной ноды (meta.prompt_styles). */

import type { NodePromptSlot } from "./node-prompts";

export interface CustomStylePreset {
  id: string;
  label: string;
  blocks: Record<string, string>;
  vars?: Record<string, string | number>;
}

export interface PromptStyleConfig {
  style_preset?: string;
  blocks?: Record<string, string>;
  custom_styles?: CustomStylePreset[];
}

export type PromptStylesMeta = Record<string, Record<string, PromptStyleConfig>>;

export function slotSupportsStyles(slot: NodePromptSlot | null | undefined): boolean {
  if (!slot) return false;
  return slot.kind === "gpt" || slot.kind === "blocks" || slot.kind === "text";
}

export function getPromptStyle(
  meta: Record<string, unknown> | undefined,
  nodeKey: string,
  slotId: string,
): PromptStyleConfig {
  const all = (meta?.prompt_styles || {}) as PromptStylesMeta;
  return all[nodeKey]?.[slotId] ?? {};
}

export function setPromptStyleInMeta(
  meta: Record<string, unknown>,
  nodeKey: string,
  slotId: string,
  patch: Partial<PromptStyleConfig>,
): Record<string, unknown> {
  const all = { ...((meta.prompt_styles || {}) as PromptStylesMeta) };
  const node = { ...(all[nodeKey] || {}) };
  node[slotId] = { ...(node[slotId] || {}), ...patch };
  all[nodeKey] = node;
  return { ...meta, prompt_styles: all };
}
