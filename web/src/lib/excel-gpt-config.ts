export type ExcelGptInputSource = "project_xlsx" | "upload" | "voiceover";

export interface ExcelGptNodeConfig {
  label?: string;
  inputSource?: ExcelGptInputSource;
  uploadedFileName?: string;
  slotIndex?: number;
}

export const EXCEL_GPT_NODE_TYPE = "excel_gpt";
export const EXCEL_GPT_STEP_CODE = "excel_gpt";

export function isExcelGptNode(nodeType: string): boolean {
  return nodeType === EXCEL_GPT_NODE_TYPE || nodeType.startsWith("enrich_");
}

export function attachmentLabel(source: ExcelGptInputSource, fileName?: string): string {
  if (source === "voiceover") return "voiceover.txt";
  if (source === "upload") return fileName?.trim() || "upload.xlsx";
  return "project.xlsx";
}

/** Номер слота 1..5 из node_key (n_excel_gpt_2) или meta/canvas. */
export function excelGptSlotIndex(
  nodeKey?: string | null,
  metaOrCanvasSlot?: number,
): number {
  if (typeof metaOrCanvasSlot === "number" && metaOrCanvasSlot >= 1 && metaOrCanvasSlot <= 5) {
    return metaOrCanvasSlot;
  }
  if (!nodeKey) return 1;
  const m = /excel_gpt_(\d+)/.exec(nodeKey) ?? /enrich_(\d+)/.exec(nodeKey);
  if (m) return Math.min(Math.max(parseInt(m[1], 10), 1), 5);
  return 1;
}

/** Папка промтов по slotIndex (legacy enrich_1..5). */
export function excelGptPromptStepCode(slotIndex?: number): string {
  if (typeof slotIndex === "number" && slotIndex >= 1 && slotIndex <= 5) {
    return `enrich_${slotIndex}`;
  }
  return EXCEL_GPT_STEP_CODE;
}

export function excelGptAttachmentChipTitle(source: ExcelGptInputSource): string {
  if (source === "voiceover") return "Voiceover";
  if (source === "upload") return "Загрузка";
  return "Excel";
}
