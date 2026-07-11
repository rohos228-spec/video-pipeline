/** Маппинг типа ноды на код шага пайплайна (как в Telegram-меню). */

export const NODE_TO_STEP: Record<string, string> = {
  plan: "plan",
  script: "script",
  split: "split",
  hero: "hero",
  items: "items",
  excel_gpt: "excel_gpt",
  image_prompts: "img_pr",
  images: "img",
  animation_prompts: "anim_pr",
  videos: "video",
  music: "music",
  audio: "audio",
  assemble: "assemble",
  publish: "publish",
};

/** Шаги с папкой prompts/* (legacy .md варианты в Node Studio). */
export const STEPS_WITH_PROMPT_VARIANTS = new Set([
  "plan",
  "script",
  "split",
  "hero",
  "hero_style",
  "items",
  "excel_gpt",
  "img_pr",
  "anim_pr",
]);

export function stepCodeForNodeType(nodeType: string): string | undefined {
  if (nodeType.startsWith("hitl_")) return undefined;
  if (nodeType.startsWith("enrich_")) return "excel_gpt";
  return NODE_TO_STEP[nodeType];
}

export function stepHasPromptVariants(stepCode: string | undefined): boolean {
  return !!stepCode && STEPS_WITH_PROMPT_VARIANTS.has(stepCode);
}
