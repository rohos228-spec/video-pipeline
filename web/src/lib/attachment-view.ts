
const MIB = 1024 * 1024;

export interface KindRule { title_ru: string; extensions: string[]; max_bytes: number }
export interface AttachmentRules { kinds: Record<string, KindRule> }

export const MAX_ATTACHMENTS = 5;

export const DEFAULT_RULES: AttachmentRules = {
  kinds: {
    image: { title_ru: "картинка", extensions: [".png", ".jpg", ".jpeg", ".webp"], max_bytes: 30 * MIB },
    video: { title_ru: "видео", extensions: [".mp4", ".mov", ".webm"], max_bytes: 200 * MIB },
    audio: { title_ru: "аудио", extensions: [".mp3", ".wav", ".m4a", ".ogg"], max_bytes: 100 * MIB },
    excel: { title_ru: "таблица Excel", extensions: [".xlsx", ".xls"], max_bytes: 25 * MIB },
    json: { title_ru: "JSON", extensions: [".json"], max_bytes: 5 * MIB },
    text: { title_ru: "текст (txt, md)", extensions: [".txt", ".md"], max_bytes: 1 * MIB },
  },
};

export type AttachmentStatus = "uploading" | "ready" | "error";

export interface AttachmentItem {
  key: string;
  name: string;
  size: number;
  status: AttachmentStatus;
  progress: number;
  kind?: string;
  attachmentId?: string;
  error?: string;
}

export function formatSize(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return "";
  if (bytes < 1024) return `${bytes} Б`;
  if (bytes < MIB) return `${Math.round(bytes / 1024)} КБ`;
  return `${(bytes / MIB).toFixed(1).replace(".", ",")} МБ`;
}

export function fileExtension(name: string): string {
  const base = name.replace(/\\/g, "/").split("/").pop() ?? "";
  const dot = base.lastIndexOf(".");
  return dot > 0 ? base.slice(dot).toLowerCase() : "";
}

export function acceptAttr(rules: AttachmentRules = DEFAULT_RULES): string {
  return Object.values(rules.kinds).flatMap((k) => k.extensions).join(",");
}

export function allowedList(rules: AttachmentRules = DEFAULT_RULES): string {
  return Object.values(rules.kinds).flatMap((k) => k.extensions).map((e) => e.slice(1)).join(", ");
}

export function kindOfFile(name: string, rules: AttachmentRules = DEFAULT_RULES): string | null {
  const ext = fileExtension(name);
  if (!ext) return null;
  for (const [kind, rule] of Object.entries(rules.kinds)) if (rule.extensions.includes(ext)) return kind;
  return null;
}

export function checkFile(file: { name: string; size: number }, rules: AttachmentRules = DEFAULT_RULES): string | null {
  const kind = kindOfFile(file.name, rules);
  if (!kind) {
    const ext = fileExtension(file.name);
    return `Тип файла «${ext || "без расширения"}» не разрешён. Можно: ${allowedList(rules)}.`;
  }
  if (file.size <= 0) return "Файл пустой.";
  const cap = rules.kinds[kind].max_bytes;
  if (file.size > cap) return `Файл ${formatSize(file.size)} больше лимита ${formatSize(cap)} для вида «${rules.kinds[kind].title_ru}».`;
  return null;
}

export function readyIds(items: AttachmentItem[]): string[] {
  return items.filter((i) => i.status === "ready" && i.attachmentId).map((i) => i.attachmentId as string);
}

export function isUploading(items: AttachmentItem[]): boolean {
  return items.some((i) => i.status === "uploading");
}

export function withoutUsed(items: AttachmentItem[], used: string[] | undefined | null): AttachmentItem[] {
  if (!used || used.length === 0) return items;
  const gone = new Set(used);
  return items.filter((i) => !(i.attachmentId && gone.has(i.attachmentId)));
}

export function itemStatusText(item: AttachmentItem): string {
  if (item.status === "uploading") return `загрузка ${Math.max(0, Math.min(100, Math.round(item.progress)))}%`;
  if (item.status === "error") return item.error || "ошибка загрузки";
  return "готов";
}
