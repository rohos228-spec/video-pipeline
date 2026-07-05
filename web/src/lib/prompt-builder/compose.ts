import { BLOCK_KINDS, type BlockVariant, type ComposeResult, type PromptSelection, type PromptTemplate } from "./types";

const VAR_RE = /\{\{([A-Z0-9_]+)\}\}/g;

export function getBlockById(blocks: BlockVariant[], id: string): BlockVariant | undefined {
  return blocks.find((b) => b.id === id);
}

export function blocksForKind(blocks: BlockVariant[], kind: string, stepCode: string): BlockVariant[] {
  return blocks.filter((b) => b.kind === kind && b.steps.includes(stepCode));
}

export function defaultSelection(template: PromptTemplate): PromptSelection {
  const slots: Record<string, string> = {};
  for (const slot of template.slots) {
    slots[slot.slotId] = slot.defaultBlockId;
  }
  return {
    templateId: template.id,
    slots,
    vars: {},
  };
}

/** Текущий blockId слота; пустая строка = слот очищен (только необязательные) */
export function resolveSlotBlockId(
  selection: Record<string, string>,
  slot: { slotId: string; defaultBlockId: string },
): string {
  const v = selection[slot.slotId];
  if (v === "") return "";
  if (v) return v;
  return slot.defaultBlockId;
}

export function isSlotEmpty(selection: Record<string, string>, slot: { slotId: string; required: boolean }): boolean {
  return !slot.required && selection[slot.slotId] === "";
}

export function composePrompt(
  template: PromptTemplate,
  allBlocks: BlockVariant[],
  selection: PromptSelection,
  globalVars: Record<string, string | number>,
): ComposeResult {
  const warnings: ComposeResult["warnings"] = [];
  const sections: ComposeResult["sections"] = [];
  const mergedVars = { ...globalVars, ...selection.vars };

  for (const slot of template.slots) {
    const blockId = selection.slots[slot.slotId] ?? slot.defaultBlockId;
    const block = getBlockById(allBlocks, blockId);
    if (!block) {
      warnings.push({ level: "error", message: `Блок «${blockId}» не найден` });
      continue;
    }
    if (!block.steps.includes(template.stepCode)) {
      warnings.push({
        level: "warn",
        message: `«${block.label}» обычно не используется в шаге ${template.stepCode}`,
      });
    }

    let body = block.body.replace(VAR_RE, (_, key: string) => String(mergedVars[key] ?? `{{${key}}}`));

    sections.push({ kind: block.kind, label: block.label, body });
  }

  if (mergedVars.PROJECT_TOPIC) {
    sections.unshift({
      kind: "technical",
      label: "Тема проекта",
      body: `ТЕМА: ${mergedVars.PROJECT_TOPIC}`,
    });
  }

  const text = sections
    .map((s) => {
      const header = BLOCK_KINDS.find((k) => k.id === s.kind)?.label.toUpperCase() ?? s.kind;
      return `## ${header} — ${s.label}\n\n${s.body}`;
    })
    .join("\n\n---\n\n");

  return {
    text,
    sections,
    warnings,
    charCount: text.length,
  };
}

export function blockUsageCount(blockId: string, templates: PromptTemplate[]): number {
  return templates.filter((t) => t.slots.some((s) => s.defaultBlockId === blockId)).length;
}
