/** Схема промтов для ноды (меню «V»). */

import { excelGptPromptStepCode, excelGptSlotIndex, isExcelGptNode } from "./excel-gpt-config";
import { gptTextStepForNode, isHitlNodeType } from "./gpt-text-steps";
import { NODE_CATALOG } from "./node-catalog";
import { stepCodeForNodeType } from "./node-step-map";
import { nodeSupportsBlocksV2 } from "./prompt-builder/step-compose-map";

export type NodePromptKind = "gpt" | "text" | "blocks" | "excel" | "frame_prompts";

export interface NodePromptSlot {
  id: string;
  title: string;
  kind: NodePromptKind;
  stepCode?: string;
  description?: string;
  /** Пользовательский слот (+промт). */
  custom?: boolean;
}

const NO_EXCEL_NODE_TYPES = new Set(["topic", "excel_feed", "excel_gpt"]);

const BASE: Record<string, NodePromptSlot[]> = {
  topic: [],
  plan: [
    { id: "excel", title: "Excel таблица", kind: "excel", stepCode: "plan" },
    { id: "main", title: "Промт сценария", kind: "gpt", stepCode: "plan" },
  ],
  script: [
    { id: "excel", title: "Excel таблица", kind: "excel", stepCode: "script" },
    { id: "main", title: "Промт закадрового текста", kind: "gpt", stepCode: "script" },
  ],
  split: [
    { id: "excel", title: "Excel таблица", kind: "excel", stepCode: "split" },
    { id: "main", title: "Промт разбивки", kind: "gpt", stepCode: "split" },
  ],
  hero: [
    { id: "excel", title: "Excel таблица", kind: "excel", stepCode: "hero" },
    { id: "main", title: "Промт персонажа", kind: "gpt", stepCode: "hero" },
    { id: "style", title: "Стиль персонажа", kind: "gpt", stepCode: "hero_style" },
  ],
  items: [
    { id: "excel", title: "Excel таблица", kind: "excel", stepCode: "items" },
    { id: "main", title: "Промт предмета", kind: "gpt", stepCode: "items" },
  ],
  excel_gpt: [
    { id: "excel", title: "Excel", kind: "excel", stepCode: "excel_gpt" },
    { id: "main", title: "Промт GPT", kind: "gpt", stepCode: "excel_gpt" },
  ],
  image_prompts: [
    { id: "excel", title: "Excel таблица", kind: "excel", stepCode: "img_pr" },
    { id: "main", title: "Промт картинок", kind: "gpt", stepCode: "img_pr" },
  ],
  images: [
    { id: "excel", title: "Excel таблица", kind: "excel", stepCode: "img" },
    {
      id: "frame_prompts",
      title: "Промты кадров",
      kind: "frame_prompts",
      description: "image_prompt по кадрам — уходит в outsee",
    },
    {
      id: "master",
      title: "Мастер-промт",
      kind: "gpt",
      stepCode: "img_pr",
      description: "prompts/05_image_prompts (шаг 6)",
    },
  ],
  animation_prompts: [
    { id: "excel", title: "Excel таблица", kind: "excel", stepCode: "anim_pr" },
    { id: "main", title: "Промт анимации", kind: "gpt", stepCode: "anim_pr" },
  ],
  videos: [
    { id: "excel", title: "Excel таблица", kind: "excel", stepCode: "video" },
    { id: "outsee", title: "Генератор видео", kind: "gpt", description: "Veo 3.1" },
  ],
  audio: [
    { id: "excel", title: "Excel таблица", kind: "excel", stepCode: "audio" },
    { id: "tts", title: "ElevenLabs TTS", kind: "gpt" },
  ],
  music: [
    { id: "voiceover", title: "voiceover.txt", kind: "text", stepCode: "music" },
    { id: "gpt_text", title: "Текст для GPT (Suno)", kind: "text", stepCode: "music" },
  ],
  assemble: [
    { id: "excel", title: "Excel таблица", kind: "excel", stepCode: "assemble" },
    { id: "ffmpeg", title: "Сборка FFmpeg", kind: "gpt" },
  ],
  publish: [
    { id: "excel", title: "Excel таблица", kind: "excel", stepCode: "publish" },
    { id: "social", title: "Публикация", kind: "gpt" },
  ],
};

export function defaultPromptSlots(nodeType: string): NodePromptSlot[] {
  if (isHitlNodeType(nodeType)) return [];
  const base = BASE[nodeType];
  if (base?.length) return base;
  if (nodeTypeRequiresExcel(nodeType)) {
    return [excelSlotForNodeType(nodeType), { id: "main", title: "Настройки ноды", kind: "gpt" }];
  }
  return [{ id: "main", title: "Настройки ноды", kind: "gpt" }];
}

export function nodeTypeRequiresExcel(nodeType: string): boolean {
  if (!nodeType || isHitlNodeType(nodeType)) return false;
  if (NO_EXCEL_NODE_TYPES.has(nodeType)) return false;
  return nodeType in NODE_CATALOG;
}

export function excelSlotForNodeType(nodeType: string): NodePromptSlot {
  return {
    id: "excel",
    title: "Excel таблица",
    kind: "excel",
    stepCode: stepCodeForNodeType(nodeType) ?? nodeType,
  };
}

function migrateLegacyPromptSlotKinds(slots: NodePromptSlot[]): NodePromptSlot[] {
  return slots.map((s) => {
    if (s.kind === "text" && s.id !== "gpt_text") {
      return { ...s, kind: "gpt" };
    }
    return s;
  });
}

export function mergePromptSlotsWithDefaults(
  nodeType: string,
  slots: NodePromptSlot[],
): NodePromptSlot[] {
  const defaults = defaultPromptSlots(nodeType);
  if (!defaults.length) return migrateLegacyPromptSlotKinds(slots);
  if (!slots.length) return defaults;

  slots = migrateLegacyPromptSlotKinds(slots).filter((s) => s.id !== "verdict");

  const byId = new Map(slots.map((s) => [s.id, s]));
  const merged: NodePromptSlot[] = [];

  for (const d of defaults) {
    if (d.kind === "excel") continue;
    const custom = byId.get(d.id);
    if (custom) {
      const stepCode =
        isExcelGptNode(nodeType) && custom.stepCode?.startsWith("enrich_")
          ? custom.stepCode
          : (d.stepCode ?? custom.stepCode);
      merged.push({
        ...d,
        ...custom,
        id: d.id,
        kind: d.kind,
        stepCode,
        title: custom.custom ? custom.title : d.title,
        description: d.description ?? custom.description,
      });
      byId.delete(d.id);
    } else {
      merged.push(d);
    }
  }

  for (const s of slots) {
    if (s.kind === "excel" || s.kind === "text") continue;
    if (merged.some((m) => m.id === s.id)) continue;
    merged.push(s);
  }

  const excel =
    slots.find((s) => s.kind === "excel") ??
    defaults.find((s) => s.kind === "excel");

  return excel ? [excel, ...merged] : merged;
}

export function ensureBlocksPromptSlot(
  nodeType: string,
  slots: NodePromptSlot[],
  nodeKey?: string,
  slotIndex?: number,
): NodePromptSlot[] {
  if (!nodeSupportsBlocksV2(nodeType, undefined, nodeKey, slotIndex)) return slots;
  if (slots.some((s) => s.kind === "blocks")) return slots;
  const blocksStep = isExcelGptNode(nodeType)
    ? excelGptPromptStepCode(excelGptSlotIndex(nodeKey, slotIndex))
    : stepCodeForNodeType(nodeType);
  const slot: NodePromptSlot = {
    id: "blocks_builder",
    title: "Конструктор промта",
    kind: "blocks",
    stepCode: blocksStep,
  };
  const excelIdx = slots.findIndex((s) => s.kind === "excel");
  if (excelIdx >= 0) {
    const next = [...slots];
    next.splice(excelIdx + 1, 0, slot);
    return next;
  }
  return [slot, ...slots];
}

function normalizeExcelGptSlots(slots: NodePromptSlot[]): NodePromptSlot[] {
  const excel = slots.find((s) => s.kind === "excel");
  const others = slots.filter((s) => s.kind !== "excel" && s.kind !== "text");
  const out: NodePromptSlot[] = [];
  if (excel) out.push(excel);
  const seen = new Set<string>();
  for (const s of others) {
    if (seen.has(s.id)) continue;
    seen.add(s.id);
    out.push(s);
  }
  return out.length ? out : slots;
}

function applyExcelGptNodeContext(
  slots: NodePromptSlot[],
  nodeKey?: string,
  slotIndex?: number,
): NodePromptSlot[] {
  const slotIdx = excelGptSlotIndex(nodeKey, slotIndex);
  const enrichStep = excelGptPromptStepCode(slotIdx);
  return slots.map((s) => {
    if (s.kind !== "gpt" && s.kind !== "blocks") return s;
    if (
      s.id === "main" ||
      s.id === "blocks_builder" ||
      s.stepCode === "excel_gpt" ||
      s.stepCode?.startsWith("enrich_")
    ) {
      return {
        ...s,
        stepCode: enrichStep,
        title:
          s.kind === "blocks"
            ? s.title
            : s.custom
              ? s.title
              : "Промт GPT",
      };
    }
    if (!s.stepCode) return { ...s, stepCode: enrichStep };
    return s;
  });
}

export function resolvePromptSlots(
  nodeType: string,
  slots?: NodePromptSlot[] | null,
  nodeKey?: string,
  slotIndex?: number,
): NodePromptSlot[] {
  const raw = slots?.length
    ? mergePromptSlotsWithDefaults(nodeType, [...slots])
    : [...defaultPromptSlots(nodeType)];
  const rest = raw.filter((s) => s.kind !== "text" && s.kind !== "excel" && s.id !== "verdict");

  let result: NodePromptSlot[];
  if (!nodeTypeRequiresExcel(nodeType)) {
    const filtered = raw.filter((s) => s.kind !== "text");
    if (isExcelGptNode(nodeType)) {
      const normalized = normalizeExcelGptSlots(filtered);
      result = applyExcelGptNodeContext(normalized, nodeKey, slotIndex);
    } else {
      result = filtered;
    }
  } else {
    const excel =
      raw.find((s) => s.kind === "excel") ??
      defaultPromptSlots(nodeType).find((s) => s.kind === "excel") ??
      excelSlotForNodeType(nodeType);
    result = [excel, ...rest];
  }
  return result.filter((s) => s.kind !== "blocks");
}

export function customPromptsForExcelGptNode(
  custom: Record<string, NodePromptSlot[]> | undefined,
  nodeKey: string,
): NodePromptSlot[] | undefined {
  if (!custom) return undefined;
  if (custom[nodeKey]?.length) return custom[nodeKey];
  const slot = excelGptSlotIndex(nodeKey);
  const candidates = [
    nodeKey,
    `n_enrich_${slot}`,
    `n_excel_gpt_${slot}`,
    `enrich_${slot}`,
  ];
  for (const k of candidates) {
    if (custom[k]?.length) return custom[k];
  }
  for (const [k, v] of Object.entries(custom)) {
    if (!v?.length) continue;
    if (k.includes(`enrich_${slot}`) || k.includes(`excel_gpt_${slot}`)) return v;
  }
  return undefined;
}

export function resolvePromptSlotsForNode(
  nodeKey: string,
  nodeType: string,
  custom?: Record<string, NodePromptSlot[]> | null,
  slotIndex?: number,
): NodePromptSlot[] {
  const stored = customPromptsForExcelGptNode(custom ?? undefined, nodeKey);
  return resolvePromptSlots(nodeType, stored ?? null, nodeKey, slotIndex);
}

export function pipelinePromptSlots(slots: NodePromptSlot[]): NodePromptSlot[] {
  return slots.filter((s) => s.kind !== "text");
}

/** @deprecated use resolvePromptSlots */
export function orderedMenuPromptSlots(
  nodeType: string,
  slots: NodePromptSlot[],
): NodePromptSlot[] {
  return resolvePromptSlots(nodeType, slots);
}

export function excelPromptSlot(slots: NodePromptSlot[]): NodePromptSlot | undefined {
  return slots.find((s) => s.kind === "excel");
}

export function isCustomPromptSlot(slot: NodePromptSlot): boolean {
  return slot.custom === true || slot.id.startsWith("custom_");
}

export function isExcelPromptSlot(slot: NodePromptSlot): boolean {
  return slot.kind === "excel" || slot.id === "excel";
}

export function gptTextSlotForNode(nodeType: string): NodePromptSlot | null {
  const stepCode = gptTextStepForNode(nodeType);
  if (!stepCode) return null;
  return {
    id: "gpt_text",
    title: "Текст для GPT",
    kind: "text",
    stepCode,
  };
}

export function isEnrichNode(nodeType: string): boolean {
  return nodeType === "excel_gpt" || nodeType.startsWith("enrich");
}

/** step_code папки промтов для excel_gpt (enrich_1..5 по node_key). */
export function excelGptEnrichStepCode(nodeKey?: string, slotIndex?: number): string {
  return excelGptPromptStepCode(excelGptSlotIndex(nodeKey, slotIndex));
}
