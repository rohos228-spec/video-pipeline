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
