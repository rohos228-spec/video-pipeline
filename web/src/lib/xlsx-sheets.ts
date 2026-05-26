/** Константы листов Excel (v8 + legacy). */

export const SHEET_GENERAL_V8 = "Общий план";
export const SHEET_GENERAL_LEGACY = "Общий план ролика";
export const SHEET_PLAN_V8 = "план";
/** v8-шаблон: строка «промт для картинки» на листе «план». */
export const ROW_IMAGE_PROMPT_V8 = 45;
export const ROW_VOICEOVER_V8 = 49;

export function pickGeneralPlanSheet(sheets: string[]): string {
  if (sheets.includes(SHEET_GENERAL_V8)) return SHEET_GENERAL_V8;
  if (sheets.includes(SHEET_GENERAL_LEGACY)) return SHEET_GENERAL_LEGACY;
  return sheets[0] ?? "";
}

/** Лист Excel по умолчанию для ноды (студия V → вкладка Excel). */
export function pickDefaultSheetForNode(nodeType: string, sheets: string[]): string {
  if (nodeType === "plan") return pickGeneralPlanSheet(sheets);
  if (
    nodeType === "split" ||
    nodeType === "script" ||
    nodeType.startsWith("enrich_") ||
    nodeType === "image_prompts" ||
    nodeType === "images" ||
    nodeType === "animation_prompts" ||
    nodeType === "videos"
  ) {
    if (sheets.includes(SHEET_PLAN_V8)) return SHEET_PLAN_V8;
  }
  return pickGeneralPlanSheet(sheets) || sheets[0] || "";
}

/** Строки с хотя бы одной непустой ячейкой (для мини-превью). */
export function xlsxRowsWithContent(rows: string[][]): string[][] {
  return rows.filter((row) => row.some((cell) => String(cell ?? "").trim() !== ""));
}

export function nodeUsesRawXlsxGrid(nodeType: string): boolean {
  return (
    nodeType === "plan" ||
    nodeType === "split" ||
    nodeType === "script" ||
    nodeType === "images" ||
    nodeType === "animation_prompts" ||
    nodeType === "videos"
  );
}

/** Превью Excel в студии V: лист «план», строка промтов кадров. */
export function xlsxPreviewFocusForNode(nodeType: string): {
  startRow: number;
  maxRows: number;
} | null {
  if (nodeType === "images") {
    return { startRow: ROW_IMAGE_PROMPT_V8 - 1, maxRows: 6 };
  }
  if (nodeType === "animation_prompts" || nodeType === "videos") {
    return { startRow: ROW_VOICEOVER_V8 - 3, maxRows: 8 };
  }
  return null;
}

export function projectHasXlsx(assets: { id: string; kind: string }[]): boolean {
  return assets.some((a) => a.id === "project.xlsx" || a.kind === "xlsx");
}

/** Ноды проверки (HITL) — без нижнего кружка результата. */
export function hideResultBadgeForNodeType(nodeType: string): boolean {
  return nodeType.startsWith("hitl_") || nodeType === "hitl_gate";
}
