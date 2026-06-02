/** Шаги с GPT-проверкой «Вердикт: …» в Studio. */

export const GPT_VERDICT_STEPS = new Set([
  "plan",
  "script",
  "split",
  "hero",
  "items",
  "img_pr",
  "images",
]);

export function stepSupportsGptVerdict(stepCode: string | undefined): boolean {
  return !!stepCode && GPT_VERDICT_STEPS.has(stepCode);
}

export function nodeSupportsGptVerdict(nodeType: string): boolean {
  const map: Record<string, string> = {
    plan: "plan",
    script: "script",
    split: "split",
    hero: "hero",
    items: "items",
    image_prompts: "img_pr",
    images: "images",
  };
  const step = map[nodeType];
  return stepSupportsGptVerdict(step);
}

export function verdictStepForNode(nodeType: string): string | undefined {
  const map: Record<string, string> = {
    plan: "plan",
    script: "script",
    split: "split",
    hero: "hero",
    items: "items",
    image_prompts: "img_pr",
    images: "images",
  };
  return map[nodeType];
}
