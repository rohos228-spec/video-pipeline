// Planner clarification (POST /plans/from-text with needs_clarification): pure helpers, no imports
// so the offline contract test can load this file on its own.

export const ATTACH_OPTION = "Прикрепить файл";
const MAX_OPTIONS = 4;

interface ClarifyReply {
  explanation_ru?: string | null;
  needs_clarification?: boolean | string | null;
  clarification_options?: string[] | null;
  attachment_request?: { action_id: string; params?: Record<string, unknown>; formats?: string } | null;
}

/** The question the planner asks: the server sends it as the text of needs_clarification. */
export function clarificationQuestion(reply: ClarifyReply): string {
  const q = reply.needs_clarification;
  if (typeof q === "string" && q.trim()) return q.trim();
  return (reply.explanation_ru ?? "").trim();
}

/** Short answers to show as buttons. The «Прикрепить файл» option is not an answer, so it is dropped when a file is requested. */
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

/** Text re-sent to /plans/from-text: the original request plus the chosen answer. */
export function clarifiedRequest(original: string, option: string): string {
  const base = original.trim().replace(/[.\s]+$/u, "");
  return `${base}. Уточнение: ${option.trim()}`;
}

export const ATTACH_BUTTON = "Прикрепить файл";
export const RESEND_WITH_FILES = "Составить план с файлом";

/** Подсказка под уточнением, когда планировщик ждёт файл: что нужно и что нажать. */
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
