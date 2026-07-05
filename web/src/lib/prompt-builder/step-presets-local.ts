import { api } from "@/lib/api";
import type { StepPresetsFile } from "./prompt-presets";
import animPrPresets from "./data/anim_pr-presets.json";
import enrich1Presets from "./data/enrich_1-presets.json";
import enrich2Presets from "./data/enrich_2-presets.json";
import enrich3Presets from "./data/enrich_3-presets.json";
import enrich4Presets from "./data/enrich_4-presets.json";
import enrich5Presets from "./data/enrich_5-presets.json";
import heroPresets from "./data/hero-presets.json";
import imgPrPresets from "./data/img_pr-presets.json";
import itemsPresets from "./data/items-presets.json";
import planPresets from "./data/plan-presets.json";
import scriptPresets from "./data/script-presets.json";
import splitPresets from "./data/split-presets.json";

/** Встроенные пресеты всех шагов — работают без перезапуска backend. */
export const LOCAL_STEP_PRESETS: Record<string, StepPresetsFile> = {
  plan: planPresets as StepPresetsFile,
  script: scriptPresets as StepPresetsFile,
  split: splitPresets as StepPresetsFile,
  hero: heroPresets as StepPresetsFile,
  items: itemsPresets as StepPresetsFile,
  enrich_1: enrich1Presets as StepPresetsFile,
  enrich_2: enrich2Presets as StepPresetsFile,
  enrich_3: enrich3Presets as StepPresetsFile,
  enrich_4: enrich4Presets as StepPresetsFile,
  enrich_5: enrich5Presets as StepPresetsFile,
  img_pr: imgPrPresets as StepPresetsFile,
  anim_pr: animPrPresets as StepPresetsFile,
};

function presetCount(data: StepPresetsFile | null | undefined): number {
  return data?.presets ? Object.keys(data.presets).length : 0;
}

/** API → локальный бандл, если сеть недоступна или ответ пустой. */
export async function fetchStepPresets(stepCode: string): Promise<StepPresetsFile | null> {
  const local = LOCAL_STEP_PRESETS[stepCode] ?? null;
  try {
    const remote = await api.promptStudioStepPresets(stepCode);
    if (remote && presetCount(remote) > 0) return remote;
  } catch {
    /* fallback ниже */
  }
  return local;
}
