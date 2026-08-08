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
  /* Canon C palette: Idea→Script→Scenes→Frames→Motion→Edit→Export */
  excel_feed: {
    type: "excel_feed",
    label: "Excel — темы",
    description: "Загрузка topics.xlsx и связи к нодам «План» для массовой генерации.",
    category: "planning",
    accent: "88 70% 52%",
    iconKey: "plan",
  },
  storage: {
    type: "storage",
    label: "Хранилище",
    description:
      "Принимает все файлы со входящих стрелок (и загрузку). Своя папка на каждую ноду.",
    category: "objects",
    accent: "200 55% 55%",
    iconKey: "package",
  },
  topic: {
    type: "topic",
    label: "Тема ролика",
    description: "Тема, с которой начинается ролик (как в боте перед планом).",
    category: "planning",
    accent: "25 45% 72%",
    iconKey: "plan",
  },
  plan: {
    type: "plan",
    label: "Сценарий",
    description: "Концепт ролика: тема, аудитория, цепляющий хук.",
    category: "planning",
    accent: "25 45% 72%",
    iconKey: "plan",
  },
  script: {
    type: "script",
    label: "Закадровый текст",
    description: "Закадровый текст 1000–1300 знаков, кадровая разбивка.",
    category: "planning",
    accent: "190 70% 55%",
    iconKey: "script",
  },
  split: {
    type: "split",
    label: "Разбивка",
    description: "Раскадровка на 15–30 кадров по 2–4 сек.",
    category: "planning",
    accent: "200 65% 58%",
    iconKey: "split",
  },
  scene_design: {
    type: "scene_design",
    label: "Сцены (агенты)",
    description:
      "Legacy-нода старых канвасов: весь мульти-агентный дизайн сцен одной нодой. Новые канвасы — веер sd_agent ×5 + sd_assemble.",
    category: "planning",
    accent: "175 60% 55%",
    iconKey: "sparkles",
  },
  sd_agent: {
    type: "sd_agent",
    label: "GPT-агент сцен",
    description:
      "Работа с GPT: категорийный агент дизайна сцен (data.agent: characters/world/style/camera/action). Свой промт prompts/scene_design/<агент>.md — кнопка GPT на ноде. 5 нод веера работают параллельно, ▶ на ноде — перезапуск только этого агента.",
    category: "planning",
    accent: "270 55% 64%",
    iconKey: "sparkles",
  },
  sd_assemble: {
    type: "sd_assemble",
    label: "GPT-сборка сцен",
    description:
      "Работа с GPT: финальный агент-сборщик (промт prompts/scene_design/assemble.md): staging-ячейки агентов → scene_registry + атрибуты кадров. Перезапуск не трогает чекпоинты агентов.",
    category: "planning",
    accent: "270 55% 64%",
    iconKey: "sparkles",
  },
  hero: {
    type: "hero",
    label: "Персонажи",
    description: "Reference-картинки героев (Nano Banana 2).",
    category: "objects",
    accent: "195 75% 58%",
    iconKey: "user-round",
  },
  items: {
    type: "items",
    label: "Предметы",
    description: "Reference-картинки повторяющихся предметов.",
    category: "objects",
    accent: "195 75% 58%",
    iconKey: "package",
  },
  excel_gpt: {
    type: "excel_gpt",
    label: "Работа с GPT",
    description:
      "Оператор GPT: роли, файлы со стрелок, сверка с диском. API без браузера. Пульт — меню V.",
    category: "enrich",
    accent: "270 55% 64%",
    iconKey: "sparkles",
  },
  image_prompts: {
    type: "image_prompts",
    label: "Промты картинок",
    description: "Генерация image-prompt'ов для каждого кадра.",
    category: "media",
    accent: "270 50% 62%",
    iconKey: "wand",
  },
  images: {
    type: "images",
    label: "Картинки",
    description: "Генерация изображений на outsee.io.",
    category: "media",
    accent: "270 50% 62%",
    iconKey: "image",
  },
  animation_prompts: {
    type: "animation_prompts",
    label: "Промты анимации",
    description: "Промты анимации через ChatGPT (по кадрам).",
    category: "media",
    accent: "88 80% 55%",
    iconKey: "wand",
  },
  videos: {
    type: "videos",
    label: "Видео",
    description: "Генерация 8-сек клипов из картинок.",
    category: "media",
    accent: "88 80% 55%",
    iconKey: "film",
  },
  music: {
    type: "music",
    label: "Музыка",
    description: "Фоновая музыка через GPT + Suno (Outsee).",
    category: "audio",
    accent: "300 55% 62%",
    iconKey: "music",
  },
  audio: {
    type: "audio",
    label: "Озвучка",
    description: "ElevenLabs TTS + Whisper-субтитры.",
    category: "audio",
    accent: "320 60% 62%",
    iconKey: "audio-waveform",
  },
  assemble: {
    type: "assemble",
    label: "Сборка",
    description: "FFmpeg: видео + аудио + субтитры → mp4.",
    category: "assembly",
    accent: "35 90% 56%",
    iconKey: "scissors",
  },
  publish: {
    type: "publish",
    label: "Публикация",
    description: "TikTok / YT Shorts / IG Reels / VK / Likee.",
    category: "publish",
    accent: "0 72% 58%",
    iconKey: "send",
  },
  hitl_hero: {
    type: "hitl_hero",
    label: "Проверка персонажей",
    description: "Одобрение референсов героев.",
    category: "hitl",
    accent: "0 0% 58%",
    iconKey: "check-square",
  },
  hitl_images: {
    type: "hitl_images",
    label: "Проверка картинок",
    description: "Одобрение всех картинок кадров.",
    category: "hitl",
    accent: "0 0% 58%",
    iconKey: "check-square",
  },
  hitl_videos: {
    type: "hitl_videos",
    label: "Проверка видео",
    description: "Одобрение всех клипов.",
    category: "hitl",
    accent: "0 0% 58%",
    iconKey: "check-square",
  },
  hitl_final: {
    type: "hitl_final",
    label: "Проверка финала",
    description: "Одобрение финального ролика.",
    category: "hitl",
    accent: "0 0% 58%",
    iconKey: "check-square",
  },
};

/** Имена категорийных агентов scene_design (data.sd_agent → по-русски). */
export const SD_AGENT_LABELS: Record<string, string> = {
  characters: "персонажи",
  world: "мир",
  style: "стиль",
  camera: "камера",
  action: "действие",
  assemble: "сборка",
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
