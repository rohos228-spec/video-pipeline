/** Маппинг типа ноды на код шага пайплайна (как в Telegram-меню). */

export const NODE_TO_STEP: Record<string, string> = {
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
  images: "img",
  animation_prompts: "anim_pr",
  videos: "video",
  audio: "audio",
  assemble: "assemble",
  publish: "publish",
};

export function stepCodeForNodeType(nodeType: string): string | undefined {
  if (nodeType.startsWith("hitl_")) return undefined;
  return NODE_TO_STEP[nodeType];
}
