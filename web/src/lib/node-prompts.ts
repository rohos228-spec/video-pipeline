/** Схема промтов для ноды (меню «V»). */

export type NodePromptKind = "gpt" | "text" | "blocks" | "excel";

export interface NodePromptSlot {
  id: string;
  title: string;
  kind: NodePromptKind;
  stepCode?: string;
  description?: string;
}

const BASE: Record<string, NodePromptSlot[]> = {
  plan: [
    { id: "main", title: "Промт плана", kind: "gpt", stepCode: "plan" },
    { id: "text", title: "Текст для GPT", kind: "text", stepCode: "plan" },
  ],
  script: [
    { id: "main", title: "Промт сценария", kind: "gpt", stepCode: "script" },
    { id: "text", title: "Текст для GPT", kind: "text", stepCode: "script" },
  ],
  split: [
    { id: "main", title: "Промт разбивки", kind: "gpt", stepCode: "split" },
    { id: "text", title: "Текст для GPT", kind: "text", stepCode: "split" },
  ],
  hero: [
    { id: "main", title: "Промт персонажа", kind: "gpt", stepCode: "hero" },
    { id: "style", title: "Стиль персонажа", kind: "gpt", stepCode: "hero" },
    { id: "text", title: "Текст для GPT", kind: "text", stepCode: "hero" },
  ],
  items: [
    { id: "main", title: "Промт предмета", kind: "gpt", stepCode: "items" },
    { id: "text", title: "Текст для GPT", kind: "text", stepCode: "items" },
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
    { id: "main", title: "Промт картинок", kind: "gpt", stepCode: "img_pr" },
    { id: "blocks", title: "Блоки стиля", kind: "blocks", stepCode: "img_pr" },
  ],
  images: [{ id: "outsee", title: "Outsee генерация", kind: "gpt", description: "Браузер outsee.io" }],
  animation_prompts: [
    { id: "main", title: "Промт анимации", kind: "gpt", stepCode: "anim_pr" },
  ],
  videos: [{ id: "outsee", title: "Outsee видео", kind: "gpt", description: "Veo 3.1" }],
  audio: [{ id: "tts", title: "ElevenLabs TTS", kind: "gpt" }],
  assemble: [{ id: "ffmpeg", title: "Сборка FFmpeg", kind: "gpt" }],
  publish: [{ id: "social", title: "Публикация", kind: "gpt" }],
};

export function defaultPromptSlots(nodeType: string): NodePromptSlot[] {
  return BASE[nodeType] ?? [{ id: "main", title: "Настройки ноды", kind: "gpt" }];
}

export function isEnrichNode(nodeType: string): boolean {
  return nodeType.startsWith("enrich");
}
