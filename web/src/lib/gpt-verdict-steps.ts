/** Шаги с GPT-проверкой «Вердикт: …» в Studio. */

export const GPT_VERDICT_STEPS = new Set([
  "plan",
  "script",
  "split",
  "hero",
  "items",
  "enrich_1",
  "enrich_2",
  "enrich_3",
  "enrich_4",
  "enrich_5",
  "img_pr",
  "anim_pr",
  "images",
]);

const NODE_TO_VERDICT_STEP: Record<string, string> = {
  plan: "plan",
  script: "script",
  split: "split",
  hero: "hero",
  items: "items",
  enrich_1: "enrich_1",
  enrich_2: "enrich_2",
  enrich_3: "enrich_3",
  enrich_4: "enrich_4",
  enrich_5: "enrich_5",
  image_prompts: "img_pr",
  animation_prompts: "anim_pr",
  images: "images",
};

export function stepSupportsGptVerdict(stepCode: string | undefined): boolean {
  return !!stepCode && GPT_VERDICT_STEPS.has(stepCode);
}

export function nodeSupportsGptVerdict(nodeType: string): boolean {
  return stepSupportsGptVerdict(NODE_TO_VERDICT_STEP[nodeType]);
}

export function verdictStepForNode(nodeType: string): string | undefined {
  return NODE_TO_VERDICT_STEP[nodeType];
}
