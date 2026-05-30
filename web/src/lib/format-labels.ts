/** Русские подписи для UI — без технических slug с подчёркиваниями. */

import type { ProjectStatus } from "./types";
import type { NodeCategory } from "./node-catalog";

const PROJECT_STATUS: Partial<Record<ProjectStatus, string>> = {
  new: "новый",
  planning: "сценарий",
  plan_ready: "сценарий готов",
  scripting: "закадровый текст",
  script_ready: "закадровый текст готов",
  splitting: "разбивка",
  frames_ready: "кадры готовы",
  generating_hero: "персонажи",
  hero_ready: "персонажи готовы",
  generating_items: "предметы",
  items_ready: "предметы готовы",
  generating_image_prompts: "промты картинок",
  image_prompts_ready: "промты картинок готовы",
  generating_images: "картинки",
  images_ready: "картинки готовы",
  generating_animation_prompts: "промты анимации",
  animation_prompts_ready: "промты анимации готовы",
  generating_videos: "видео",
  videos_ready: "видео готово",
  generating_audio: "озвучка",
  audio_ready: "озвучка готова",
  assembling: "сборка",
  assembled: "собрано",
  publishing: "публикация",
  published: "опубликовано",
  paused: "пауза",
  failed: "ошибка",
};

const NODE_CATEGORY: Record<NodeCategory, string> = {
  planning: "планирование",
  objects: "объекты",
  enrich: "дополнение Excel",
  media: "медиа",
  audio: "аудио",
  assembly: "сборка",
  publish: "публикация",
  hitl: "проверка",
};

const RUN_STATUS: Record<string, string> = {
  new: "новый",
  running: "работает",
  paused: "пауза",
  completed: "завершён",
  cancelled: "отменён",
  failed: "ошибка",
};

const HERO_MODE: Record<string, string> = {
  auto: "авто",
  hero: "с персонажем",
  no_hero: "без персонажа",
};

export function formatProjectStatus(status: string): string {
  return PROJECT_STATUS[status as ProjectStatus] ?? humanizeSlug(status);
}

export function formatNodeCategory(cat: string): string {
  return NODE_CATEGORY[cat as NodeCategory] ?? humanizeSlug(cat);
}

export function formatRunStatus(status: string): string {
  return RUN_STATUS[status] ?? humanizeSlug(status);
}

export function formatHeroMode(mode: string): string {
  return HERO_MODE[mode] ?? humanizeSlug(mode);
}

export function formatStepCode(code: string): string {
  const map: Record<string, string> = {
    plan: "сценарий",
    script: "закадровый текст",
    split: "разбивка",
    hero: "персонажи",
    items: "предметы",
    enrich_1: "дополнение 1",
    enrich_2: "дополнение 2",
    enrich_3: "дополнение 3",
    enrich_4: "дополнение 4",
    enrich_5: "дополнение 5",
    img_pr: "промты картинок",
    images: "картинки",
    anim_pr: "промты анимации",
    videos: "видео",
    audio: "озвучка",
    assemble: "сборка",
    publish: "публикация",
  };
  return map[code] ?? humanizeSlug(code);
}

/** Заменяет _ на пробелы, убирает лишние технические префиксы. */
export function humanizeSlug(value: string): string {
  return value
    .replace(/^n_/, "")
    .replace(/_/g, " ")
    .replace(/\bhitl\b/gi, "проверка")
    .replace(/\bgpt\b/gi, "GPT")
    .replace(/\boutsee\b/gi, "генератор")
    .trim();
}

export function formatNodeKeyLabel(key: string): string {
  return humanizeSlug(key);
}

const STYLE_PRESET_RU: Record<string, string> = {
  cats_pixelart_short: "Коты пиксель-арт (shorts)",
  humans_documentary: "Документальный (люди)",
};

export function formatStylePresetLabel(preset: { id: string; label?: string }): string {
  if (preset.label && !/^[A-Za-z][A-Za-z0-9 _-]+$/.test(preset.label.trim())) {
    return preset.label;
  }
  return STYLE_PRESET_RU[preset.id] ?? humanizeSlug(preset.id);
}
