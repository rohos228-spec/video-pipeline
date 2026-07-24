export type ExcelGptInputSource =
  | "project_xlsx"
  | "upload"
  | "voiceover"
  | "image"
  | "hero_refs"
  | "scene_images";

/** Роль ноды относительно пайплайна. */
export type ExcelGptWorkMode = "assist" | "review" | "transform";

export interface ExcelGptNodeConfig {
  label?: string;
  inputSource?: ExcelGptInputSource;
  uploadedFileName?: string;
  /** Превью загруженного изображения (/api/files?path=…). */
  uploadedPreviewUrl?: string | null;
  slotIndex?: number;
  workMode?: ExcelGptWorkMode;
  lastReplyPath?: string;
  lastReplyAt?: string;
}

export const EXCEL_GPT_NODE_TYPE = "excel_gpt";
export const EXCEL_GPT_STEP_CODE = "excel_gpt";

export const WORK_MODE_OPTIONS: {
  value: ExcelGptWorkMode;
  title: string;
  hint: string;
}[] = [
  {
    value: "assist",
    title: "Участвует в работе",
    hint: "Шлёт ввод в GPT вместе с промтом — как этап пайплайна (Excel и др.).",
  },
  {
    value: "review",
    title: "Проверяет результат",
    hint: "Берёт уже готовые файлы/картинки и просит GPT проверить или дать вердикт.",
  },
  {
    value: "transform",
    title: "Преобразует ввод",
    hint: "Принимает файл или изображение и просит GPT изменить / описать / извлечь.",
  },
];

export const INPUT_SOURCE_OPTIONS: {
  value: ExcelGptInputSource;
  title: string;
  group: "data" | "media";
}[] = [
  { value: "project_xlsx", title: "project.xlsx", group: "data" },
  { value: "upload", title: "Свой файл", group: "data" },
  { value: "voiceover", title: "voiceover.txt", group: "data" },
  { value: "image", title: "Изображение", group: "media" },
  { value: "hero_refs", title: "Рефы персонажей", group: "media" },
  { value: "scene_images", title: "Картинки кадров", group: "media" },
];

export function isExcelGptNode(nodeType: string): boolean {
  return nodeType === EXCEL_GPT_NODE_TYPE || nodeType.startsWith("enrich_");
}

export function isImageUploadName(fileName?: string | null): boolean {
  if (!fileName) return false;
  return /\.(png|jpe?g|webp|gif)$/i.test(fileName);
}

export function attachmentLabel(source: ExcelGptInputSource, fileName?: string): string {
  if (source === "voiceover") return "voiceover.txt";
  if (source === "hero_refs") return "characters/*";
  if (source === "scene_images") return "scenes/*";
  if (source === "image") return fileName?.trim() || "image.png";
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

/** Единая папка промтов для всех нод «Работа с GPT». */
export function excelGptPromptStepCode(_slotIndex?: number): string {
  return EXCEL_GPT_STEP_CODE;
}

export function excelGptAttachmentChipTitle(source: ExcelGptInputSource): string {
  if (source === "voiceover") return "Voiceover";
  if (source === "upload") return "Загрузка";
  if (source === "image") return "Изображение";
  if (source === "hero_refs") return "Рефы";
  if (source === "scene_images") return "Кадры";
  return "Excel";
}

export function workModeLabel(mode?: ExcelGptWorkMode | null): string {
  const found = WORK_MODE_OPTIONS.find((o) => o.value === (mode || "assist"));
  return found?.title ?? "Участвует в работе";
}

/** Короткий chip на карточке канваса. */
export function workModeChip(mode?: ExcelGptWorkMode | null): string {
  const m = mode || "assist";
  if (m === "review") return "Проверка";
  if (m === "transform") return "Преобразование";
  return "Участие";
}
