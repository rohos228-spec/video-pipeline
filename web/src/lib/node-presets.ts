/** Именованные пресеты ноды (project.meta.node_presets). */

import type { NodePromptSlot } from "./node-prompts";
import { getPromptSlotVariantsMeta } from "./prompt-slot-storage";
import type { PromptStyleConfig } from "./prompt-styles";

export interface NodePresetFileRef {
  slotId: string;
  stepCode?: string;
  fileName: string;
  folderHint?: string;
}

export interface NodePresetSnapshot {
  id: string;
  name: string;
  createdAt: string;
  /** Способ восприятия данных (0–10). */
  perceptionScore: number;
  customPrompts: NodePromptSlot[];
  promptSlotVariants: Record<string, string>;
  promptStyles: Record<string, PromptStyleConfig>;
  promptOverrides: Record<string, string>;
  disabled?: boolean;
  files: NodePresetFileRef[];
}

export type NodePresetsMeta = Record<string, NodePresetSnapshot[]>;

export function readNodePresets(
  meta: Record<string, unknown> | undefined,
  nodeKey: string,
): NodePresetSnapshot[] {
  const all = (meta?.node_presets || {}) as NodePresetsMeta;
  return all[nodeKey] ?? [];
}

export function clampPerceptionScore(n: number): number {
  if (!Number.isFinite(n)) return 5;
  return Math.max(0, Math.min(10, Math.round(n)));
}

export function buildPresetSnapshot(opts: {
  name: string;
  nodeKey: string;
  meta: Record<string, unknown>;
  promptOverrides: Record<string, unknown>;
  slots: NodePromptSlot[];
  fileRefs: NodePresetFileRef[];
  perceptionScore: number;
  disabled?: boolean;
}): NodePresetSnapshot {
  const variants = getPromptSlotVariantsMeta(opts.meta)[opts.nodeKey] ?? {};
  const stylesAll = (opts.meta.prompt_styles || {}) as Record<
    string,
    Record<string, PromptStyleConfig>
  >;
  const nodeStyles = stylesAll[opts.nodeKey] ?? {};
  const overrides: Record<string, string> = {};
  for (const [k, v] of Object.entries(opts.promptOverrides)) {
    if (typeof v === "string") overrides[k] = v;
  }
  return {
    id: `preset_${Date.now()}`,
    name: opts.name.trim(),
    createdAt: new Date().toISOString(),
    perceptionScore: clampPerceptionScore(opts.perceptionScore),
    customPrompts: opts.slots.map((s) => ({ ...s })),
    promptSlotVariants: { ...variants },
    promptStyles: { ...nodeStyles },
    promptOverrides: overrides,
    disabled: opts.disabled,
    files: opts.fileRefs,
  };
}

export function upsertNodePresetInMeta(
  meta: Record<string, unknown>,
  nodeKey: string,
  preset: NodePresetSnapshot,
): Record<string, unknown> {
  const all = { ...((meta.node_presets || {}) as NodePresetsMeta) };
  const list = [...(all[nodeKey] ?? [])];
  const idx = list.findIndex((p) => p.id === preset.id);
  if (idx >= 0) list[idx] = preset;
  else list.push(preset);
  all[nodeKey] = list;
  return { ...meta, node_presets: all };
}

export function applyPresetToMeta(
  meta: Record<string, unknown>,
  nodeKey: string,
  preset: NodePresetSnapshot,
): Record<string, unknown> {
  const custom = { ...((meta.custom_prompts || {}) as Record<string, NodePromptSlot[]>) };
  custom[nodeKey] = preset.customPrompts.map((s) => ({ ...s }));

  const variantsAll = { ...getPromptSlotVariantsMeta(meta) };
  variantsAll[nodeKey] = { ...preset.promptSlotVariants };

  const stylesAll = { ...((meta.prompt_styles || {}) as Record<string, Record<string, PromptStyleConfig>>) };
  stylesAll[nodeKey] = { ...preset.promptStyles };

  const perceptionAll = { ...((meta.node_perception || {}) as Record<string, number>) };
  perceptionAll[nodeKey] = preset.perceptionScore;

  return {
    ...meta,
    custom_prompts: custom,
    prompt_slot_variants: variantsAll,
    prompt_styles: stylesAll,
    node_perception: perceptionAll,
  };
}

export function readNodePerception(
  meta: Record<string, unknown> | undefined,
  nodeKey: string,
): number {
  const all = (meta?.node_perception || {}) as Record<string, number>;
  const v = all[nodeKey];
  return v == null ? 5 : clampPerceptionScore(v);
}

export function setNodePerceptionInMeta(
  meta: Record<string, unknown>,
  nodeKey: string,
  score: number,
): Record<string, unknown> {
  const all = { ...((meta.node_perception || {}) as Record<string, number>) };
  all[nodeKey] = clampPerceptionScore(score);
  return { ...meta, node_perception: all };
}

/** Собрать ссылки на активные .md по слотам. */
export function collectPresetFileRefs(
  meta: Record<string, unknown>,
  nodeKey: string,
  slots: NodePromptSlot[],
  promptOverrides: Record<string, unknown>,
  folderHints: Record<string, string | undefined>,
): NodePresetFileRef[] {
  const refs: NodePresetFileRef[] = [];
  for (const slot of slots) {
    if (slot.kind !== "gpt" || !slot.stepCode) continue;
    const variant = getPromptSlotVariantsMeta(meta)[nodeKey]?.[slot.id];
    const fromOverride =
      typeof promptOverrides[slot.stepCode] === "string"
        ? (promptOverrides[slot.stepCode] as string)
        : undefined;
    const fileName = variant ?? fromOverride ?? slot.id;
    refs.push({
      slotId: slot.id,
      stepCode: slot.stepCode,
      fileName: fileName.endsWith(".md") ? fileName.replace(/\.md$/, "") : fileName,
      folderHint: folderHints[slot.stepCode],
    });
  }
  return refs;
}
