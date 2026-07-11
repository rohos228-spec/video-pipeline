/**
 * Канонический список типов нод пайплайна и их метаданные (label, icon-key,
 * категория, цвет акцента). Используется и в палитре, и в кастом-нодах.
 */

import type { NodeType } from "./types";

export type NodeCategory =
  | "planning"
  | "objects"
  | "enrich"
  | "media"
  | "audio"
  | "assembly"
  | "publish"
  | "hitl";

export interface NodeSpec {
  type: NodeType;
  label: string;
  description: string;
  category: NodeCategory;
  accent: string; // hsl tuple для левого бордера
  iconKey:
    | "plan" | "script" | "split" | "user-round" | "package"
    | "wand" | "image" | "film" | "audio-waveform"
    | "scissors" | "send" | "check-square" | "sparkles" | "music";
}

export const NODE_CATALOG: Record<string, NodeSpec> = {
  excel_feed: {
    type: "excel_feed",
    label: "Excel — темы",
    description: "Загрузка topics.xlsx и связи к нодам «План» для массовой генерации.",
    category: "planning",
    accent: "142 70% 45%",
    iconKey: "plan",
  },
  topic: {
    type: "topic",
    label: "Тема ролика",
    description: "Тема, с которой начинается ролик (как в боте перед планом).",
    category: "planning",
    accent: "263 75% 65%",
    iconKey: "plan",
  },
  plan: {
    type: "plan",
    label: "Сценарий",
    description: "Концепт ролика: тема, аудитория, цепляющий хук.",
    category: "planning",
    accent: "263 75% 65%",
    iconKey: "plan",
  },
  script: {
    type: "script",
    label: "Закадровый текст",
    description: "Закадровый текст 1000–1300 знаков, кадровая разбивка.",
    category: "planning",
    accent: "263 75% 65%",
    iconKey: "script",
  },
  split: {
    type: "split",
    label: "Разбивка",
    description: "Раскадровка на 15–30 кадров по 2–4 сек.",
    category: "planning",
    accent: "263 75% 65%",
    iconKey: "split",
  },
  hero: {
    type: "hero",
    label: "Персонажи",
    description: "Reference-картинки героев (Nano Banana 2).",
    category: "objects",
    accent: "199 89% 60%",
    iconKey: "user-round",
  },
  items: {
    type: "items",
    label: "Предметы",
    description: "Reference-картинки повторяющихся предметов.",
    category: "objects",
    accent: "199 89% 60%",
    iconKey: "package",
  },
  excel_gpt: {
    type: "excel_gpt",
    label: "Доп. Excel",
    description: "Универсальная нода: Excel / загрузка / voiceover + ChatGPT.",
    category: "enrich",
    accent: "38 92% 60%",
    iconKey: "sparkles",
  },
  image_prompts: {
    type: "image_prompts",
    label: "Промты картинок",
    description: "Генерация image-prompt'ов для каждого кадра.",
    category: "media",
    accent: "142 60% 50%",
    iconKey: "wand",
  },
  images: {
    type: "images",
    label: "Картинки",
    description: "Генерация изображений на outsee.io.",
    category: "media",
    accent: "142 60% 50%",
    iconKey: "image",
  },
  animation_prompts: {
    type: "animation_prompts",
    label: "Промты анимации",
    description: "Промты анимации через ChatGPT (по кадрам).",
    category: "media",
    accent: "142 60% 50%",
    iconKey: "wand",
  },
  videos: {
    type: "videos",
    label: "Видео",
    description: "Генерация 8-сек клипов из картинок.",
    category: "media",
    accent: "142 60% 50%",
    iconKey: "film",
  },
  music: {
    type: "music",
    label: "Музыка",
    description: "Фоновая музыка через GPT + Suno (Outsee).",
    category: "audio",
    accent: "292 85% 62%",
    iconKey: "music",
  },
  audio: {
    type: "audio",
    label: "Озвучка",
    description: "ElevenLabs TTS + Whisper-субтитры.",
    category: "audio",
    accent: "330 75% 65%",
    iconKey: "audio-waveform",
  },
  assemble: {
    type: "assemble",
    label: "Сборка",
    description: "FFmpeg: видео + аудио + субтитры → mp4.",
    category: "assembly",
    accent: "12 80% 60%",
    iconKey: "scissors",
  },
  publish: {
    type: "publish",
    label: "Публикация",
    description: "TikTok / YT Shorts / IG Reels / VK / Likee.",
    category: "publish",
    accent: "47 95% 60%",
    iconKey: "send",
  },
  hitl_hero: {
    type: "hitl_hero",
    label: "Проверка персонажей",
    description: "Одобрение референсов героев.",
    category: "hitl",
    accent: "0 0% 55%",
    iconKey: "check-square",
  },
  hitl_images: {
    type: "hitl_images",
    label: "Проверка картинок",
    description: "Одобрение всех картинок кадров.",
    category: "hitl",
    accent: "0 0% 55%",
    iconKey: "check-square",
  },
  hitl_videos: {
    type: "hitl_videos",
    label: "Проверка видео",
    description: "Одобрение всех клипов.",
    category: "hitl",
    accent: "0 0% 55%",
    iconKey: "check-square",
  },
  hitl_final: {
    type: "hitl_final",
    label: "Проверка финала",
    description: "Одобрение финального ролика.",
    category: "hitl",
    accent: "0 0% 55%",
    iconKey: "check-square",
  },
};

export function formatNodeTypeLabel(type: string): string {
  const spec = NODE_CATALOG[type];
  if (spec) return spec.label;
  return type
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase())
    .replace(/\bHitl\b/gi, "Проверка")
    .replace(/\bGpt\b/gi, "GPT");
}

export function getNodeSpec(type: string): NodeSpec {
  return (
    NODE_CATALOG[type] ?? {
      type,
      label: formatNodeTypeLabel(type),
      description: "",
      category: "planning",
      accent: "0 0% 55%",
      iconKey: "plan",
    }
  );
}
