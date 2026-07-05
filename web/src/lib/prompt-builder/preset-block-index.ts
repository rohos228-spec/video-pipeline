import type { StepPresetsFile } from "./prompt-presets";

/** blockId → id пресетов, где блок используется в категории kind */
export function presetIdsUsingBlock(
  stepPresets: StepPresetsFile | null | undefined,
  kind: string,
  blockId: string,
): string[] {
  if (!stepPresets?.presets) return [];
  const out: string[] = [];
  for (const [presetId, preset] of Object.entries(stepPresets.presets)) {
    if (preset.blocks?.[kind] === blockId) out.push(presetId);
  }
  return out;
}

export function blockIdInPreset(
  stepPresets: StepPresetsFile | null | undefined,
  presetId: string | null | undefined,
  kind: string,
): string | null {
  if (!presetId || !kind) return null;
  for (const [id, preset] of Object.entries(stepPresets?.presets ?? {})) {
    if (id === presetId) {
      const bid = preset.blocks?.[kind];
      return typeof bid === "string" && bid ? bid : null;
    }
    if (preset.aliases?.includes(presetId)) {
      const bid = preset.blocks?.[kind];
      return typeof bid === "string" && bid ? bid : null;
    }
  }
  return null;
}

export function allBlockIdsForKindFromPresets(
  stepPresets: StepPresetsFile | null | undefined,
  kind: string,
): Set<string> {
  const ids = new Set<string>();
  if (!stepPresets?.presets) return ids;
  for (const preset of Object.values(stepPresets.presets)) {
    const bid = preset.blocks?.[kind];
    if (typeof bid === "string" && bid) ids.add(bid);
  }
  return ids;
}

/** Блоки категории kind, используемые хотя бы одним пресетом шага. */
export function usedBlockIdsForKindFromPresets(
  stepPresets: StepPresetsFile | null | undefined,
  kind: string,
): Set<string> {
  const ids = new Set<string>();
  if (!stepPresets?.presets) return ids;
  for (const preset of Object.values(stepPresets.presets)) {
    if ((preset.omit_slots ?? []).includes(kind)) continue;
    const bid = preset.blocks?.[kind];
    if (typeof bid === "string" && bid) ids.add(bid);
  }
  return ids;
}

export type PresetCategoryBlock = {
  presetId: string;
  omitted: boolean;
  blockId: string | null;
};

/** Блок пресета в категории kind (или omitted). */
export function presetBlockInCategory(
  stepPresets: StepPresetsFile | null | undefined,
  presetId: string,
  kind: string,
): PresetCategoryBlock {
  const preset = stepPresets?.presets?.[presetId];
  if (!preset) return { presetId, omitted: true, blockId: null };
  const omitted = (preset.omit_slots ?? []).includes(kind);
  if (omitted) return { presetId, omitted: true, blockId: null };
  const bid = preset.blocks?.[kind];
  return {
    presetId,
    omitted: false,
    blockId: typeof bid === "string" && bid ? bid : null,
  };
}
