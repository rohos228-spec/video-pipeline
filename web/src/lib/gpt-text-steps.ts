/** Шаги с «сопр. сообщением» для ChatGPT (зеркало app.services.gpt_text_builder.SUPPORTED_STEPS). */

export const GPT_TEXT_STEPS = new Set([
  "plan",
  "script",
  "split",
  "hero",
  "img_pr",
  "anim_pr",
  "music",
  "excel_gpt",
]);

/** step_code для gpt_text_overrides по типу ноды. */
export const NODE_TYPE_TO_GPT_TEXT_STEP: Record<string, string> = {
  plan: "plan",
  script: "script",
  split: "split",
  hero: "hero",
  excel_gpt: "excel_gpt",
  enrich_1: "excel_gpt",
  enrich_2: "excel_gpt",
  enrich_3: "excel_gpt",
  enrich_4: "excel_gpt",
  enrich_5: "excel_gpt",
  image_prompts: "img_pr",
  animation_prompts: "anim_pr",
  music: "music",
};

export function isHitlNodeType(nodeType: string): boolean {
  return nodeType.startsWith("hitl_") || nodeType === "hitl_gate";
}

export function gptTextStepForNode(nodeType: string): string | undefined {
  if (isHitlNodeType(nodeType)) return undefined;
  if (nodeType.startsWith("enrich_")) return "excel_gpt";
  return NODE_TYPE_TO_GPT_TEXT_STEP[nodeType];
}

export function nodeSupportsGptText(nodeType: string): boolean {
  const step = gptTextStepForNode(nodeType);
  return !!step && GPT_TEXT_STEPS.has(step);
}
