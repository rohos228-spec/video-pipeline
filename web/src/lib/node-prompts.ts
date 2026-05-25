/** Схема промтов для ноды (меню «V»). */

import { gptTextStepForNode, isHitlNodeType } from "./gpt-text-steps";
import { NODE_CATALOG } from "./node-catalog";
import { stepCodeForNodeType } from "./node-step-map";

export type NodePromptKind = "gpt" | "text" | "blocks" | "excel";

export interface NodePromptSlot {
  id: string;
  title: string;
  kind: NodePromptKind;
  stepCode?: string;
  description?: string;
  /** Пользовательский слот (+промт). */
  custom?: boolean;
}

const NO_EXCEL_NODE_TYPES = new Set(["topic", "excel_feed"]);

const BASE: Record<string, NodePromptSlot[]> = {
  topic: [],
  plan: [
    { id: "excel", title: "Excel таблица", kind: "excel", stepCode: "plan" },
    { id: "main", title: "Промт плана", kind: "gpt", stepCode: "plan" },
  ],
  script: [
    { id: "excel", title: "Excel таблица", kind: "excel", stepCode: "script" },
    { id: "main", title: "Промт сценария", kind: "gpt", stepCode: "script" },
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
  enrich_1: [
    { id: "excel", title: "Excel таблица", kind: "excel", stepCode: "enrich_1" },
    { id: "main", title: "Промт дополнения 1", kind: "gpt", stepCode: "enrich_1" },
  ],
  enrich_2: [
    { id: "excel", title: "Excel таблица", kind: "excel", stepCode: "enrich_2" },
    { id: "main", title: "Промт дополнения 2", kind: "gpt", stepCode: "enrich_2" },
  ],
  enrich_3: [
    { id: "excel", title: "Excel таблица", kind: "excel", stepCode: "enrich_3" },
    { id: "main", title: "Промт дополнения 3", kind: "gpt", stepCode: "enrich_3" },
  ],
  enrich_4: [
    { id: "excel", title: "Excel таблица", kind: "excel", stepCode: "enrich_4" },
    { id: "main", title: "Промт дополнения 4", kind: "gpt", stepCode: "enrich_4" },
  ],
  enrich_5: [
    { id: "excel", title: "Excel таблица", kind: "excel", stepCode: "enrich_5" },
    { id: "main", title: "Промт дополнения 5", kind: "gpt", stepCode: "enrich_5" },
  ],
  image_prompts: [
    { id: "excel", title: "Excel таблица", kind: "excel", stepCode: "img_pr" },
    { id: "main", title: "Промт картинок", kind: "gpt", stepCode: "img_pr" },
  ],
  images: [
    { id: "excel", title: "Excel таблица", kind: "excel", stepCode: "img" },
    { id: "outsee", title: "Генератор изображений", kind: "gpt", description: "Браузер outsee.io" },
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

/** Во всех рабочих нодах пайплайна Excel обязателен первым. */
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

/** Единая схема слотов: Excel всегда #1 (даже если custom_prompts его выкинул). */
export function resolvePromptSlots(
  nodeType: string,
  slots?: NodePromptSlot[] | null,
): NodePromptSlot[] {
  const raw = slots?.length ? [...slots] : [...defaultPromptSlots(nodeType)];
  const rest = raw.filter((s) => s.kind !== "text" && s.kind !== "excel");

  if (!nodeTypeRequiresExcel(nodeType)) {
    return raw.filter((s) => s.kind !== "text");
  }

  const excel =
    raw.find((s) => s.kind === "excel") ??
    defaultPromptSlots(nodeType).find((s) => s.kind === "excel") ??
    excelSlotForNodeType(nodeType);

  return [excel, ...rest];
}

/** Промты в горизонтальной схеме меню V (без «текста для GPT»). */
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
  return nodeType.startsWith("enrich");
}
