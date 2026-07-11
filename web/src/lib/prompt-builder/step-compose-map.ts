/** step_code (prompt_library) / node_type → папка prompts/steps/ */

import { excelGptSlotIndex } from "../excel-gpt-config";

const ENRICH_COMPOSE_BY_SLOT: Record<number, string> = {
  1: "05a_enrich_1",
  2: "05b_enrich_2",
  3: "05c_enrich_3",
  4: "05d_enrich_4",
  5: "05e_enrich_5",
};

export const NODE_TYPE_TO_COMPOSE_ID: Record<string, string> = {
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

export const STEP_CODE_TO_COMPOSE_ID: Record<string, string> = {
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
  img_pr: "06_image_prompts",
  anim_pr: "07_animation",
};

export const COMPOSE_STEP_LABELS: Record<string, string> = {
  "01_plan": "План ролика",
  "02_script": "Сценарий",
  "03_razbivka": "Разбивка",
  "04_hero": "Персонаж",
  "04b_items": "Предметы",
  "05a_enrich_1": "Excel #1",
  "05b_enrich_2": "Excel #2",
  "05c_enrich_3": "Excel #3",
  "05d_enrich_4": "Excel #4",
  "05e_enrich_5": "Excel #5",
  "06_image_prompts": "Промты картинок",
  "07_animation": "Промты анимации",
};

/** Все шаги на левой колонке конструктора (включая без blocks v2). */
export type PipelineRailNode = {
  nodeType: string;
  stepCode: string;
  composeId: string | null;
  label: string;
};

export const PIPELINE_RAIL_NODES: PipelineRailNode[] = [
  { nodeType: "plan", stepCode: "plan", composeId: "01_plan", label: "Общий план" },
  { nodeType: "script", stepCode: "script", composeId: "02_script", label: "Сценарий" },
  { nodeType: "split", stepCode: "split", composeId: "03_razbivka", label: "Разбивка" },
  { nodeType: "hero", stepCode: "hero", composeId: "04_hero", label: "Персонажи" },
  { nodeType: "items", stepCode: "items", composeId: "04b_items", label: "Предметы" },
  { nodeType: "enrich_1", stepCode: "enrich_1", composeId: "05a_enrich_1", label: "Excel #1" },
  { nodeType: "enrich_2", stepCode: "enrich_2", composeId: "05b_enrich_2", label: "Excel #2" },
  { nodeType: "enrich_3", stepCode: "enrich_3", composeId: "05c_enrich_3", label: "Excel #3" },
  { nodeType: "enrich_4", stepCode: "enrich_4", composeId: "05d_enrich_4", label: "Excel #4" },
  { nodeType: "enrich_5", stepCode: "enrich_5", composeId: "05e_enrich_5", label: "Excel #5" },
  { nodeType: "image_prompts", stepCode: "img_pr", composeId: "06_image_prompts", label: "Промты картинок" },
  { nodeType: "animation_prompts", stepCode: "anim_pr", composeId: "07_animation", label: "Промты анимации" },
  { nodeType: "audio", stepCode: "audio", composeId: null, label: "Озвучка" },
  { nodeType: "assemble", stepCode: "assemble", composeId: null, label: "Монтаж" },
];

/** Ноды с blocks v2 (для загрузки каталога). */
export const PIPELINE_BLOCK_NODES = PIPELINE_RAIL_NODES.filter(
  (n): n is PipelineRailNode & { composeId: string } => n.composeId != null,
);

export function composeStepIdForExcelGptNode(
  nodeKey?: string | null,
  slotIndex?: number,
): string | null {
  const slot = excelGptSlotIndex(nodeKey, slotIndex);
  return ENRICH_COMPOSE_BY_SLOT[slot] ?? null;
}

export function composeStepIdForNode(
  nodeType: string,
  stepCode?: string,
  nodeKey?: string | null,
  slotIndex?: number,
): string | null {
  if (nodeType === "excel_gpt") {
    return composeStepIdForExcelGptNode(nodeKey, slotIndex);
  }
  return (
    NODE_TYPE_TO_COMPOSE_ID[nodeType] ??
    (stepCode ? STEP_CODE_TO_COMPOSE_ID[stepCode] : undefined) ??
    null
  );
}

export function nodeSupportsBlocksV2(
  nodeType: string,
  stepCode?: string,
  nodeKey?: string | null,
  slotIndex?: number,
): boolean {
  return composeStepIdForNode(nodeType, stepCode, nodeKey, slotIndex) != null;
}

/** step_code enrich_N → compose id (для Prompt Builder из excel_gpt). */
export function composeStepIdForEnrichStep(stepCode: string): string | null {
  return STEP_CODE_TO_COMPOSE_ID[stepCode] ?? null;
}
