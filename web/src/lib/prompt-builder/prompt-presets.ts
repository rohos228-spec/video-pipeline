import type { PromptSelection, PromptSlot, PromptTemplate } from "./types";
import { DEFAULT_VARS } from "./real-catalog";

export type PromptStepPreset = {
  id: string;
  label: string;
  description?: string;
  blocks: Record<string, string>;
  extra_blocks?: Record<string, string>;
  omit_slots?: string[];
  vars?: Record<string, string | number>;
};

export type StepPresetsFile = {
  step_code: string;
  compose_step_id?: string;
  label?: string;
  /** Явный порядок пресетов в левой колонке (из prompts/step-presets/*.json). */
  preset_order?: string[];
  presets: Record<string, Omit<PromptStepPreset, "id"> & { aliases?: string[] }>;
};

/** Порядок id пресетов: preset_order → default первым → порядок ключей в JSON. */
export function orderedPresetIds(data: StepPresetsFile | null | undefined): string[] {
  const presets = data?.presets;
  if (!presets) return [];
  const keys = Object.keys(presets);
  if (keys.length === 0) return [];

  const result: string[] = [];
  const used = new Set<string>();
  const add = (id: string) => {
    if (presets[id] && !used.has(id)) {
      result.push(id);
      used.add(id);
    }
  };

  if (Array.isArray(data.preset_order) && data.preset_order.length > 0) {
    for (const id of data.preset_order) {
      if (typeof id === "string") add(id);
    }
  } else {
    add("default");
  }
  for (const id of keys) add(id);
  return result;
}

/** Все id и aliases пресетов шага — для фильтрации legacy-файлов. */
export function presetAliasIds(data: StepPresetsFile | null | undefined): Set<string> {
  const out = new Set<string>();
  if (!data?.presets) return out;
  for (const [id, preset] of Object.entries(data.presets)) {
    out.add(id);
    for (const alias of preset.aliases ?? []) {
      if (alias.trim()) out.add(alias.trim());
    }
  }
  return out;
}

export function resolvePromptPreset(
  data: StepPresetsFile | undefined,
  promptName: string,
): PromptStepPreset | null {
  if (!data?.presets) return null;
  const name = promptName.trim();
  for (const [id, preset] of Object.entries(data.presets)) {
    if (id === name) {
      return { id, ...preset, blocks: preset.blocks ?? {} };
    }
    if (preset.aliases?.some((a) => a === name)) {
      return { id, ...preset, blocks: preset.blocks ?? {} };
    }
  }
  return null;
}

/** Доля заполненных категорий пресета (0–100), без omit_slots. */
export function presetComposePercent(preset: PromptStepPreset | null | undefined): number {
  if (!preset?.blocks) return 0;
  const omit = new Set(preset.omit_slots ?? []);
  const kinds = Object.keys(preset.blocks).filter((k) => !omit.has(k));
  if (kinds.length === 0) return 100;
  const filled = kinds.filter((k) => {
    const bid = preset.blocks[k];
    return typeof bid === "string" && bid.length > 0;
  }).length;
  return Math.round((filled / kinds.length) * 100);
}

/** Применить пресет к selection + extra-слоты (например segmentation для long). */
export function selectionFromPromptPreset(
  template: PromptTemplate,
  preset: PromptStepPreset,
): { selection: PromptSelection; extras: PromptSlot[] } {
  const omit = new Set(preset.omit_slots ?? []);
  const slots: Record<string, string> = {};

  for (const slot of template.slots) {
    if (omit.has(slot.kind)) {
      slots[slot.slotId] = "";
      continue;
    }
    const blockId = preset.blocks[slot.kind];
    slots[slot.slotId] = blockId ?? slot.defaultBlockId;
  }

  const extras: PromptSlot[] = [];
  for (const [kind, blockId] of Object.entries(preset.extra_blocks ?? {})) {
    if (template.slots.some((s) => s.kind === kind)) {
      const slot = template.slots.find((s) => s.kind === kind);
      if (slot) slots[slot.slotId] = blockId;
      continue;
    }
    const slotId = `extra_${kind}_preset`;
    slots[slotId] = blockId;
    extras.push({ slotId, kind, required: false, defaultBlockId: blockId });
  }

  return {
    selection: {
      templateId: template.id,
      slots,
      vars: { ...DEFAULT_VARS, ...preset.vars },
    },
    extras,
  };
}
