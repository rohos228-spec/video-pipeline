/** Выбранный шаблон GPT-проверки по шагу (meta.gpt_verdict_templates). */

export type GptVerdictTemplatesMeta = Record<string, string>;

export function readGptVerdictTemplates(
  meta: Record<string, unknown> | undefined,
): GptVerdictTemplatesMeta {
  const raw = meta?.gpt_verdict_templates;
  if (!raw || typeof raw !== "object") return {};
  return raw as GptVerdictTemplatesMeta;
}

export function readGptVerdictTemplate(
  meta: Record<string, unknown> | undefined,
  stepCode: string,
): string {
  const name = readGptVerdictTemplates(meta)[stepCode];
  return name?.trim() || "default";
}

export function writeGptVerdictTemplate(
  meta: Record<string, unknown>,
  stepCode: string,
  templateName: string,
): Record<string, unknown> {
  return {
    ...meta,
    gpt_verdict_templates: {
      ...readGptVerdictTemplates(meta),
      [stepCode]: templateName,
    },
  };
}
