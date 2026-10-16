
export const ATTACH_OPTION = "Прикрепить файл";
const MAX_OPTIONS = 4;

interface ClarifyReply {
  explanation_ru?: string | null;
  needs_clarification?: boolean | string | null;
  clarification_options?: string[] | null;
  attachment_request?: { action_id: string; params?: Record<string, unknown>; formats?: string } | null;
}

export function clarificationQuestion(reply: ClarifyReply): string {
  const q = reply.needs_clarification;
  if (typeof q === "string" && q.trim()) return q.trim();
  return (reply.explanation_ru ?? "").trim();
}

export function clarificationOptions(reply: ClarifyReply): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const raw of reply.clarification_options ?? []) {
    const option = typeof raw === "string" ? raw.trim() : "";
    if (!option || seen.has(option)) continue;
    if (reply.attachment_request && option === ATTACH_OPTION) continue;
    seen.add(option);
    out.push(option);
    if (out.length >= MAX_OPTIONS) break;
  }
  return out;
}

export function clarifiedRequest(original: string, option: string): string {
  const base = original.trim().replace(/[.\s]+$/u, "");
  return `${base}. Уточнение: ${option.trim()}`;
}

export const ATTACH_BUTTON = "Прикрепить файл";
export const RESEND_WITH_FILES = "Составить план с файлом";

export function attachmentHint(request: NonNullable<ClarifyReply["attachment_request"]>, actionTitle?: string,
                               hasFiles = false): string {
  const what = actionTitle || request.action_id;
  const formats = request.formats ? ` (формат: ${request.formats})` : "";
  if (hasFiles) {
    return `Нужен файл для действия «${what}»${formats}. Прикреплённые файлы можно отправить снова: «${RESEND_WITH_FILES}». ` +
      `Неподходящий файл уберите и прикрепите другой кнопкой «${ATTACH_BUTTON}» в строке команды выше.`;
  }
  return `Нужен файл для действия «${what}»${formats}. Прикрепите его кнопкой «${ATTACH_BUTTON}» в строке команды выше, ` +
    `затем нажмите «${RESEND_WITH_FILES}».`;
}
