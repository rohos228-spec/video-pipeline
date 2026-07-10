import { api } from "@/lib/api";
import type { StepPresetsFile } from "./prompt-presets";

function emptyStepPresets(stepCode: string): StepPresetsFile {
  return {
    step_code: stepCode,
    presets: {
      default: { label: "По умолчанию", blocks: {} },
    },
  };
}

/** Встроенные пресеты всех шагов — fallback, если API недоступен. */
export const LOCAL_STEP_PRESETS: Record<string, StepPresetsFile> = {
  plan: emptyStepPresets("plan"),
  script: emptyStepPresets("script"),
  split: emptyStepPresets("split"),
  hero: emptyStepPresets("hero"),
  items: emptyStepPresets("items"),
  enrich_1: emptyStepPresets("enrich_1"),
  enrich_2: emptyStepPresets("enrich_2"),
  enrich_3: emptyStepPresets("enrich_3"),
  enrich_4: emptyStepPresets("enrich_4"),
  enrich_5: emptyStepPresets("enrich_5"),
  img_pr: emptyStepPresets("img_pr"),
  anim_pr: emptyStepPresets("anim_pr"),
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
