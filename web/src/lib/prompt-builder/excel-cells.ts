import { agentForBlock } from "./agents-catalog";
import { MOCK_BLOCKS } from "./mock-data";
import type { PromptSelection, PromptTemplate } from "./types";

export type ExcelSheetCategory = {
  id: string;
  name: string;
};

export type ExcelCellDef = {
  id: string;
  sheetId: string;
  row: number;
  label: string;
  address: string;
  group?: string;
};

export type CellUsageEntry = {
  blockId: string;
  blockLabel: string;
  agentName?: string;
  slotKind?: string;
};

export type CellUsageMap = Record<string, CellUsageEntry[]>;

export const EXCEL_SHEETS: ExcelSheetCategory[] = [
  { id: "general", name: "Общий план" },
  { id: "plan", name: "план" },
  { id: "characters", name: "Персонажи" },
];

/** Строки листа «план» (v8) — каждая строка = ячейка проекта */
export const PLAN_CELLS: ExcelCellDef[] = [
  { id: "plan:r2", sheetId: "plan", row: 2, label: "id scene", address: "R2", group: "shot_01" },
  { id: "plan:r3", sheetId: "plan", row: 3, label: "id shot", address: "R3", group: "shot_01" },
  { id: "plan:r4", sheetId: "plan", row: 4, label: "id кадра", address: "R4", group: "shot_01" },
  { id: "plan:r5", sheetId: "plan", row: 5, label: "prev", address: "R5", group: "shot_01" },
  { id: "plan:r6", sheetId: "plan", row: 6, label: "next", address: "R6", group: "shot_01" },
  { id: "plan:r7", sheetId: "plan", row: 7, label: "персонажи", address: "R7", group: "shot_01" },
  { id: "plan:r8", sheetId: "plan", row: 8, label: "id героев", address: "R8", group: "shot_01" },
  { id: "plan:r9", sheetId: "plan", row: 9, label: "предметы", address: "R9", group: "shot_01" },
  { id: "plan:r10", sheetId: "plan", row: 10, label: "id предметов", address: "R10", group: "shot_01" },
  { id: "plan:r11", sheetId: "plan", row: 11, label: "действие", address: "R11", group: "shot_01" },
  { id: "plan:r12", sheetId: "plan", row: 12, label: "описание", address: "R12", group: "shot_01" },
  { id: "plan:r13", sheetId: "plan", row: 13, label: "камера", address: "R13", group: "shot_01" },
  { id: "plan:r14", sheetId: "plan", row: 14, label: "свет", address: "R14", group: "shot_01" },
  { id: "plan:r16", sheetId: "plan", row: 16, label: "id scene", address: "R16", group: "shot_02" },
  { id: "plan:r17", sheetId: "plan", row: 17, label: "id shot", address: "R17", group: "shot_02" },
  { id: "plan:r26", sheetId: "plan", row: 26, label: "описание", address: "R26", group: "shot_02" },
  { id: "plan:r28", sheetId: "plan", row: 28, label: "свет", address: "R28", group: "shot_02" },
  { id: "plan:r45", sheetId: "plan", row: 45, label: "Image prompt 1", address: "R45", group: "prompts" },
  { id: "plan:r46", sheetId: "plan", row: 46, label: "Image prompt 2", address: "R46", group: "prompts" },
  { id: "plan:r48", sheetId: "plan", row: 48, label: "Anim prompt 1", address: "R48", group: "prompts" },
  { id: "plan:r49", sheetId: "plan", row: 49, label: "Озвучка", address: "R49", group: "voice" },
  { id: "plan:r50", sheetId: "plan", row: 50, label: "Длительность", address: "R50", group: "voice" },
  { id: "plan:r52", sheetId: "plan", row: 52, label: "Действие", address: "R52", group: "enrich" },
  { id: "plan:r54", sheetId: "plan", row: 54, label: "Место", address: "R54", group: "analytics" },
  { id: "plan:r55", sheetId: "plan", row: 55, label: "Акцент", address: "R55", group: "analytics" },
  { id: "plan:r56", sheetId: "plan", row: 56, label: "Смысл сцены", address: "R56", group: "analytics" },
  { id: "plan:r57", sheetId: "plan", row: 57, label: "Тип сцены", address: "R57", group: "analytics" },
  { id: "plan:r58", sheetId: "plan", row: 58, label: "Особенность сцены", address: "R58", group: "analytics" },
  { id: "plan:r59", sheetId: "plan", row: 59, label: "Номер кластера", address: "R59", group: "analytics" },
  { id: "plan:r64", sheetId: "plan", row: 64, label: "Anim prompt 2", address: "R64", group: "prompts" },
];

export const CELL_GROUP_LABELS: Record<string, string> = {
  meta: "Основное",
  structure: "Структура",
  shot_01: "Shot 01",
  shot_02: "Shot 02",
  prompts: "Промты",
  voice: "Озвучка",
  enrich: "Enrich",
  analytics: "Аналитика",
  identity: "Идентичность",
  look: "Образ",
};

const SHEET_GROUP_ORDER: Record<string, string[]> = {
  general: ["meta", "structure"],
  plan: ["shot_01", "shot_02", "prompts", "voice", "enrich", "analytics"],
  characters: ["identity", "look"],
};

export type CellGroupSection = {
  groupId: string;
  label: string;
  cells: ExcelCellDef[];
};

export function groupedCellsForSheet(sheetId: string): CellGroupSection[] {
  const cells = cellsForSheet(sheetId);
  const order = SHEET_GROUP_ORDER[sheetId] ?? [];
  const byGroup = new Map<string, ExcelCellDef[]>();

  for (const cell of cells) {
    const g = cell.group ?? "other";
    if (!byGroup.has(g)) byGroup.set(g, []);
    byGroup.get(g)!.push(cell);
  }

  const sections: CellGroupSection[] = [];
  for (const groupId of order) {
    const groupCells = byGroup.get(groupId);
    if (!groupCells?.length) continue;
    sections.push({
      groupId,
      label: CELL_GROUP_LABELS[groupId] ?? groupId,
      cells: groupCells,
    });
    byGroup.delete(groupId);
  }

  for (const [groupId, groupCells] of byGroup) {
    sections.push({
      groupId,
      label: CELL_GROUP_LABELS[groupId] ?? groupId,
      cells: groupCells,
    });
  }

  return sections;
}

export const GENERAL_CELLS: ExcelCellDef[] = [
  { id: "general:topic", sheetId: "general", row: 1, label: "Тема", address: "R1", group: "meta" },
  { id: "general:duration", sheetId: "general", row: 2, label: "Длительность", address: "R2", group: "meta" },
  { id: "general:format", sheetId: "general", row: 3, label: "Формат", address: "R3", group: "meta" },
  { id: "general:heroes", sheetId: "general", row: 4, label: "Герои", address: "R4", group: "meta" },
  { id: "general:blocks", sheetId: "general", row: 7, label: "Блоки", address: "R7", group: "structure" },
  { id: "general:visual", sheetId: "general", row: 8, label: "Визуал", address: "R8", group: "structure" },
];

export const CHARACTER_CELLS: ExcelCellDef[] = [
  { id: "chars:r1", sheetId: "characters", row: 1, label: "id", address: "R1", group: "identity" },
  { id: "chars:r3", sheetId: "characters", row: 3, label: "Имя", address: "R3", group: "identity" },
  { id: "chars:r4", sheetId: "characters", row: 4, label: "Внешность", address: "R4", group: "look" },
  { id: "chars:r5", sheetId: "characters", row: 5, label: "Одежда", address: "R5", group: "look" },
  { id: "chars:r6", sheetId: "characters", row: 6, label: "Характер", address: "R6", group: "look" },
  { id: "chars:r7", sheetId: "characters", row: 7, label: "Правила", address: "R7", group: "look" },
];

export const ALL_EXCEL_CELLS: ExcelCellDef[] = [
  ...GENERAL_CELLS,
  ...PLAN_CELLS,
  ...CHARACTER_CELLS,
];

const SHOT01 = PLAN_CELLS.filter((c) => c.group === "shot_01").map((c) => c.id);
const SHOT02 = PLAN_CELLS.filter((c) => c.group === "shot_02").map((c) => c.id);
const ANALYTICS = PLAN_CELLS.filter((c) => c.group === "analytics").map((c) => c.id);

const BLOCK_CELL_MAP: Record<string, string[]> = {
  role_plan_architect: GENERAL_CELLS.map((c) => c.id),
  role_shorts_writer: ["general:blocks", "general:topic", "plan:r49"],
  role_xlsx_agent: [...SHOT01, ...SHOT02, "plan:r52"],
  role_enrich_orchestrator: [...SHOT01, ...SHOT02, "plan:r52", ...ANALYTICS],
  role_image_prompter: ["plan:r45", "plan:r46", "plan:r12", "plan:r26"],
  role_animation_director: ["plan:r48", "plan:r64", "plan:r13"],
  role_hero_designer: ["chars:r3", "chars:r4", "chars:r5", "chars:r6", "plan:r7"],
  role_qa_reviewer: ["plan:r45", "plan:r49", "general:blocks"],
  tech_xlsx_row52: [...SHOT01, "plan:r52"],
  tech_image_len: ["plan:r45", "plan:r46"],
  tech_anim_8sec: ["plan:r48", "plan:r64", "plan:r50"],
  tech_60sec_vertical: ["general:duration", "plan:r50"],
  tech_voiceover_chars: ["plan:r49", "general:blocks"],
  rules_anti_doubles: [...SHOT01, ...ANALYTICS],
  rules_table_v7: [...SHOT01, ...SHOT02],
  rules_character_agent: CHARACTER_CELLS.map((c) => c.id),
  rules_no_style_mix: ["plan:r45", "plan:r46"],
  rules_ai_cliches_ru: ["general:blocks", "plan:r49"],
  feat_knitted_2d: ["plan:r45", "plan:r46"],
  feat_clay_plasticine: ["plan:r45"],
  feat_trash_polka: ["plan:r45", "plan:r46"],
  feat_pixelart_cinematic: ["plan:r45", "plan:r46", "plan:r48"],
  feat_camera_slow_push: ["plan:r48", "plan:r64", "plan:r13"],
  feat_anthro_cats: ["general:heroes", "plan:r7", "chars:r4"],
  feat_dark_bloody: ["plan:r45"],
  narr_detective: ["general:topic", "general:blocks"],
  narr_hook_insight: ["general:blocks"],
  narr_documentary_calm: ["general:blocks", "plan:r49"],
  narr_steven_king: ["plan:r49"],
  neg_no_text_logos: ["plan:r45", "plan:r46", "plan:r48"],
  neg_no_humans: ["plan:r45", "plan:r46"],
  out_plan_timeline: GENERAL_CELLS.map((c) => c.id),
  out_script_voiceover: ["plan:r49"],
  out_image_prompt_template: ["plan:r45", "plan:r46"],
};

const STEP_CELL_FALLBACK: Record<string, string[]> = {
  plan: GENERAL_CELLS.map((c) => c.id),
  script: ["plan:r49", "general:blocks"],
  split: ["plan:r49", "plan:r50"],
  enrich_1: [...SHOT01, ...SHOT02, "plan:r52"],
  enrich_2: ["plan:r8", ...CHARACTER_CELLS.map((c) => c.id)],
  enrich_3: ANALYTICS,
  enrich_5: ANALYTICS,
  img_pr: ["plan:r45", "plan:r46"],
  hero: ["chars:r3", "chars:r4", "plan:r45"],
  anim_pr: ["plan:r48", "plan:r64"],
};

export function cellsForSheet(sheetId: string): ExcelCellDef[] {
  return ALL_EXCEL_CELLS.filter((c) => c.sheetId === sheetId);
}

export function cellById(id: string): ExcelCellDef | undefined {
  return ALL_EXCEL_CELLS.find((c) => c.id === id);
}

export function getCellsForBlock(blockId: string, stepCode?: string): string[] {
  const explicit = BLOCK_CELL_MAP[blockId];
  if (explicit?.length) return explicit;

  const block = MOCK_BLOCKS.find((b) => b.id === blockId);
  if (!block) return [];

  const fromSteps = block.steps.flatMap((s) => STEP_CELL_FALLBACK[s] ?? []);
  if (fromSteps.length) return [...new Set(fromSteps)];

  if (stepCode && STEP_CELL_FALLBACK[stepCode]) {
    return STEP_CELL_FALLBACK[stepCode]!;
  }

  return [];
}

export function computeProjectCellUsage(
  selection: PromptSelection,
  template: PromptTemplate,
): CellUsageMap {
  const usage: CellUsageMap = {};

  for (const slot of template.slots) {
    const blockId = selection.slots[slot.slotId] ?? slot.defaultBlockId;
    const block = MOCK_BLOCKS.find((b) => b.id === blockId);
    if (!block) continue;

    const cellIds = getCellsForBlock(blockId, template.stepCode);
    const agent = agentForBlock(blockId);

    for (const cellId of cellIds) {
      if (!usage[cellId]) usage[cellId] = [];
      const exists = usage[cellId]!.some((e) => e.blockId === blockId);
      if (!exists) {
        usage[cellId]!.push({
          blockId,
          blockLabel: block.label,
          agentName: agent?.name,
          slotKind: slot.kind,
        });
      }
    }
  }

  return usage;
}

export function agentCountForCells(cellIds: string[], usage: CellUsageMap): number {
  const agents = new Set<string>();
  for (const id of cellIds) {
    for (const entry of usage[id] ?? []) {
      agents.add(entry.agentName ?? entry.blockLabel);
    }
  }
  return agents.size;
}
