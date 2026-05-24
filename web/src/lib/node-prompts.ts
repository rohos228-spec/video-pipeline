/** Схема промтов для ноды (меню «V»). */

import { gptTextStepForNode, isHitlNodeType } from "./gpt-text-steps";

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
    { id: "main", title: "Промт персонажа", kind: "gpt", stepCode: "hero" },
    { id: "style", title: "Стиль персонажа", kind: "gpt", stepCode: "hero_style" },
  ],
  items: [{ id: "main", title: "Промт предмета", kind: "gpt", stepCode: "items" }],
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
  images: [{ id: "outsee", title: "Генератор изображений", kind: "gpt", description: "Браузер outsee.io" }],
  animation_prompts: [{ id: "main", title: "Промт анимации", kind: "gpt", stepCode: "anim_pr" }],
  videos: [{ id: "outsee", title: "Генератор видео", kind: "gpt", description: "Veo 3.1" }],
  audio: [{ id: "tts", title: "ElevenLabs TTS", kind: "gpt" }],
  assemble: [{ id: "ffmpeg", title: "Сборка FFmpeg", kind: "gpt" }],
  publish: [{ id: "social", title: "Публикация", kind: "gpt" }],
};

export function defaultPromptSlots(nodeType: string): NodePromptSlot[] {
  if (isHitlNodeType(nodeType)) return [];
  return BASE[nodeType] ?? [{ id: "main", title: "Настройки ноды", kind: "gpt" }];
}

/** Промты в горизонтальной схеме меню V (без «текста для GPT»). */
export function pipelinePromptSlots(slots: NodePromptSlot[]): NodePromptSlot[] {
  return slots.filter((s) => s.kind !== "text");
}

export function isCustomPromptSlot(slot: NodePromptSlot): boolean {
  return slot.custom === true || slot.id.startsWith("custom_");
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
