/**
 * Связь нод → шаги пайплайна → папки prompts/ (legacy + steps/).
 * Используется в Node Studio и меню V.
 */

import { NODE_TO_STEP, STEPS_WITH_PROMPT_VARIANTS } from "./node-step-map";

/** Папка legacy prompts (01_plan, 02_script, …) */
export const LEGACY_STEP_FOLDER: Record<string, string> = {
  plan: "01_plan",
  script: "02_script",
  split: "03_razbivka",
  hero: "04_hero",
  hero_style: "04_hero_style",
  items: "04b_items",
  enrich_1: "05a_enrich_1",
  enrich_2: "05b_enrich_2",
  enrich_3: "05c_enrich_3",
  enrich_4: "05d_enrich_4",
  enrich_5: "05e_enrich_5",
  img_pr: "05_image_prompts",
  anim_pr: "07_animation",
};

/** Папка blocks/steps v2 (prompts/steps/) */
export const STEPS_V2_FOLDER: Record<string, string> = {
  plan: "01_plan",
  script: "02_script",
  split: "03_razbivka",
  hero: "04_hero",
  items: "04b_items",
  enrich_1: "05a_enrich_1",
  enrich_2: "05b_enrich_2",
  enrich_3: "05c_enrich_3",
  enrich_4: "05d_enrich_4",
  enrich_5: "05e_enrich_5",
  image_prompts: "06_image_prompts",
  animation_prompts: "07_animation",
};

export const CHECK_PROMPT_FOLDER: Record<string, string> = {
  plan: "check_plan",
  script: "check_script",
  hero: "check_hero",
  images: "check_images",
  videos: "check_videos",
  final: "check_final",
};

export function stepCodeForNode(nodeType: string): string | undefined {
  if (nodeType.startsWith("hitl_")) return undefined;
  return NODE_TO_STEP[nodeType];
}

export function legacyPromptFolder(stepCode: string): string | undefined {
  return LEGACY_STEP_FOLDER[stepCode];
}

export function stepsV2Folder(nodeType: string): string | undefined {
  return STEPS_V2_FOLDER[nodeType];
}

export function hasLegacyVariants(stepCode: string | undefined): boolean {
  return !!stepCode && STEPS_WITH_PROMPT_VARIANTS.has(stepCode);
}

export function promptPathsForNode(nodeType: string): {
  stepCode?: string;
  legacyDir?: string;
  stepsV2Dir?: string;
  checkDir?: string;
} {
  const stepCode = stepCodeForNode(nodeType);
  return {
    stepCode,
    legacyDir: stepCode ? legacyPromptFolder(stepCode) : undefined,
    stepsV2Dir: stepsV2Folder(nodeType),
    checkDir: CHECK_PROMPT_FOLDER[nodeType] ?? (stepCode ? CHECK_PROMPT_FOLDER[stepCode] : undefined),
  };
}
