/** Пять legacy-папок промтов Excel: prompts/05a_enrich_1 … 05e_enrich_5. */

import type { NodePromptSlot } from "./node-prompts";

export const ENRICH_FOLDER_META: { stepCode: string; folder: string; label: string }[] = [
  { stepCode: "enrich_1", folder: "05a_enrich_1", label: "Доп. Excel 1" },
  { stepCode: "enrich_2", folder: "05b_enrich_2", label: "Доп. Excel 2" },
  { stepCode: "enrich_3", folder: "05c_enrich_3", label: "Доп. Excel 3" },
  { stepCode: "enrich_4", folder: "05d_enrich_4", label: "Доп. Excel 4" },
  { stepCode: "enrich_5", folder: "05e_enrich_5", label: "Доп. Excel 5" },
];

/** Слот V-menu на каждую папку enrich_1..5 (внутри — все .md этой папки). */
export function enrichFolderPromptSlots(): NodePromptSlot[] {
  return ENRICH_FOLDER_META.map(({ stepCode, folder, label }, i) => ({
    id: stepCode,
    title: label,
    kind: "gpt" as const,
    stepCode,
    description: `prompts/${folder}`,
  }));
}

export function enrichFolderCount(): number {
  return ENRICH_FOLDER_META.length;
}
