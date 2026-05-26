/** Группы промтов по папкам prompts/ для UI ноды. */

import { promptPathsForNode } from "./prompt-catalog";
import { humanizeSlug } from "./format-labels";
import type { NodePromptSlot } from "./node-prompts";

export interface PromptFolderGroup {
  id: string;
  label: string;
  folderPath: string;
  stepCode: string;
  kind: "legacy" | "steps" | "check";
}

const FOLDER_LABEL_RU: Record<string, string> = {
  "01_plan": "План",
  "02_script": "Сценарий",
  "03_razbivka": "Разбивка",
  "04_hero": "Персонажи",
  "04b_items": "Предметы",
  "05a_enrich_1": "Дополнение 1",
  "05b_enrich_2": "Дополнение 2",
  "05c_enrich_3": "Дополнение 3",
  "05d_enrich_4": "Дополнение 4",
  "05e_enrich_5": "Дополнение 5",
  "05_image_prompts": "Промты картинок",
  "06_image_prompts": "Промты картинок (шаг 6)",
  "07_animation": "Промты анимации",
  check_plan: "Проверка плана",
  check_script: "Проверка сценария",
  check_hero: "Проверка персонажей",
  check_images: "Проверка картинок",
  check_videos: "Проверка видео",
  check_final: "Проверка финала",
};

export function translateFolderName(folderPath: string): string {
  return FOLDER_LABEL_RU[folderPath] ?? humanizeSlug(folderPath.replace(/^\d+[a-z]?_/, ""));
}

export function promptFolderGroupsForNode(nodeType: string): PromptFolderGroup[] {
  const paths = promptPathsForNode(nodeType);
  const groups: PromptFolderGroup[] = [];
  const seen = new Set<string>();

  const push = (g: PromptFolderGroup) => {
    const key = `${g.kind}:${g.folderPath}:${g.stepCode}`;
    if (seen.has(key)) return;
    seen.add(key);
    groups.push(g);
  };

  if (paths.stepCode && paths.legacyDir) {
    push({
      id: `legacy-${paths.stepCode}`,
      label: translateFolderName(paths.legacyDir),
      folderPath: paths.legacyDir,
      stepCode: paths.stepCode,
      kind: "legacy",
    });
  }

  if (paths.stepsV2Dir) {
    const step = paths.stepCode ?? nodeType;
    push({
      id: `steps-${step}`,
      label: translateFolderName(paths.stepsV2Dir),
      folderPath: paths.stepsV2Dir,
      stepCode: step,
      kind: "steps",
    });
  }

  if (paths.checkDir) {
    push({
      id: `check-${paths.checkDir}`,
      label: translateFolderName(paths.checkDir),
      folderPath: paths.checkDir,
      stepCode: paths.checkDir,
      kind: "check",
    });
  }

  return groups;
}

/** Слоты, относящиеся к папке (по stepCode). */
export function slotsForFolderGroup(
  slots: NodePromptSlot[],
  group: PromptFolderGroup,
): NodePromptSlot[] {
  return slots.filter((s) => {
    if (s.kind === "excel") return false;
    if (s.stepCode === group.stepCode) return true;
    if (group.kind === "check" && s.kind === "gpt") return false;
    return false;
  });
}
