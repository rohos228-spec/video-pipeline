/** Константы листов Excel (v8 + legacy). */

export const SHEET_GENERAL_V8 = "Общий план";
export const SHEET_GENERAL_LEGACY = "Общий план ролика";
export const SHEET_PLAN_V8 = "план";
export const ROW_VOICEOVER_V8 = 49;

export function pickGeneralPlanSheet(sheets: string[]): string {
  if (sheets.includes(SHEET_GENERAL_V8)) return SHEET_GENERAL_V8;
  if (sheets.includes(SHEET_GENERAL_LEGACY)) return SHEET_GENERAL_LEGACY;
  return sheets[0] ?? "";
}

/** Лист Excel по умолчанию для ноды (студия V → вкладка Excel). */
export function pickDefaultSheetForNode(nodeType: string, sheets: string[]): string {
  if (nodeType === "plan") return pickGeneralPlanSheet(sheets);
  if (nodeType === "split" || nodeType === "script") {
    if (sheets.includes(SHEET_PLAN_V8)) return SHEET_PLAN_V8;
  }
  return pickGeneralPlanSheet(sheets) || sheets[0] || "";
}

export function nodeUsesRawXlsxGrid(nodeType: string): boolean {
  return nodeType === "plan" || nodeType === "split" || nodeType === "script";
}

export function projectHasXlsx(assets: { id: string; kind: string }[]): boolean {
  return assets.some((a) => a.id === "project.xlsx" || a.kind === "xlsx");
}

/** Ноды проверки (HITL) — без нижнего кружка результата. */
export function hideResultBadgeForNodeType(nodeType: string): boolean {
  return nodeType.startsWith("hitl_") || nodeType === "hitl_gate";
}
